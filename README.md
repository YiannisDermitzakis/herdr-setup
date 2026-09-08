# herdr-setup

Reproduce a [Herdr](https://herdr.dev) configuration on another machine, and keep
several machines in step. Herdr has no export or sync of its own: this is a
small, dependency-light tool that copies a Herdr configuration out of a host
into a git checkout, and back into another host. It also onboards the coding
agents found on a host, so that a Herdr server restart resumes their sessions
instead of stranding them in a bare shell.

## The external-checkout model

The checkout is cloned anywhere, and Herdr does not know it exists. Each
command copies files between the checkout's `manifest/` and the host's real
Herdr configuration.

Copying, not symlinking, is deliberate. A plugin rewrites `config.toml` by
writing a temporary file and renaming it over the original, which replaces a
symlink with a plain file the first time it runs. A symlinked configuration
would break on the first plugin that touched a setting.

The consequence: a host is only as current as its last `apply`. `diff` is how
you ask whether it has fallen behind.

## Install

Clone the repository and put the `herdr-setup` entrypoint on `PATH`:

```
git clone https://github.com/YiannisDermitzakis/herdr-setup.git
export PATH="$PWD/herdr-setup:$PATH"
```

It finds its own `lib/` and `adapters/` next to itself, following symlinks, so
a symlink from a directory already on `PATH` works too.

### Prerequisites

- **[Herdr](https://herdr.dev)** itself, already installed and configured on
  the host. This tool never installs, upgrades, or reconfigures it.
- **git**, for `absorb`'s manifest-cleanliness check and for resolving plugin
  refs in `diff`.
- **[uv](https://docs.astral.sh/uv/)**, which pins and runs this tool's own
  Python. Every Python file here carries its own PEP 723 interpreter
  requirement, so two hosts run the exact same interpreter regardless of
  what they happen to have installed system-wide.

The shell floor is bash 3.2, which is what macOS ships.

## Commands

### `diff` — report drift (read-only)

```
$ herdr-setup diff
```

Reports which manifest plugins are missing, at their pinned ref, or moved to
a newer commit; which host plugins the manifest does not name (unmanaged, not
an error); how the host's `config.toml` differs from the manifest once
plugin-written blocks are set aside; and each installed agent integration's
state. Writes nothing. Exit `0` when the host matches the manifest, `1` when
it drifts, `2` on error.

### `apply` — bring a host up to the manifest

```
$ herdr-setup apply
$ herdr-setup apply --yes       # accept every plugin's trust preview
$ herdr-setup apply --dry-run   # print what would change, do nothing
```

Installs missing manifest plugins, reinstalls those whose ref moved, and
splices `manifest/config.toml` into the host's own config while leaving
plugin-written blocks exactly where they were. Prints the diff of the config
change before writing it, dry runs included, and backs the previous content
up to a timestamped file first. A write that would remove lines needs `--yes`
or a yes answer at the terminal — refused, it writes nothing. Exit `0` on
success, `2` on a manifest error, `3` if the Herdr command line and server
speak different protocol versions, `4` if a shrinking config write is
refused.

### `absorb` — pull a host's changes back into the checkout

```
$ herdr-setup absorb
```

The reverse of `apply`, and the reason this tool is usable day to day:
change Herdr interactively on any host, `absorb`, commit, `apply` elsewhere.
Rewrites `manifest/plugins.list` and `manifest/config.toml` straight from the
host's own `plugins.json` and `config.toml`, touching only the checkout.
Refuses to run — and writes nothing — when the manifest has uncommitted
changes, or when it cannot tell whether it does (git failing for any reason
is treated as dirty, never as clean). Review with `git diff` and commit.
Exit `0` on success, `2` on error, `4` if the manifest is dirty.

### `onboard` — detect and offer agent integrations

```
$ herdr-setup onboard
$ herdr-setup onboard --yes       # install every offered integration
$ herdr-setup onboard --dry-run   # print what would be offered, do nothing
```

Detects coding agents on the host (by command and by configuration
directory), prints a table of what was found and its Herdr integration state
— absent, current, or outdated with both versions — and offers each absent or
outdated one individually. Installs only what is accepted, feeds the
integrations it just installed or refreshed to Herdr immediately afterward
(see `feed` below), and remembers nothing between runs. Exit `0` on success,
`3` under a protocol mismatch.

### `feed` — report live sessions to Herdr

```
$ herdr-setup feed
$ herdr-setup feed --yes   # take the best candidate without asking
```

For each agent pane Herdr reports, finds the matching session through the
adapter seam (below) and sends `pane.report_agent_session` — the same call
the official integration hooks make — so that a server restart resumes the
pane instead of replacing it with a bare shell. Reports without asking only
when exactly one candidate comes back at `exact` confidence; every other case
is a question, and a question nobody is there to answer is a skip, never a
guess. Exit `0` on a clean run, `1` if any pane could not be reported, `3`
under a protocol mismatch.

## The adapter seam

An adapter answers one question about one coding agent — which session is
this pane in? — and is any executable file in `adapters/`, in any language.
Adding support for a new harness means writing one such file and nothing
else; the full contract, worked examples, and a checklist are in
[`docs/adapters.md`](docs/adapters.md).

Four adapters ship today: Claude Code (exact, by process id), Codex,
opencode, and GitHub Copilot CLI (all heuristic, by directory). **The
Copilot adapter is unverified** — GitHub Copilot CLI was not installed on the
machine this tool was written on, so it is tested against fixtures only. Its
probe reports this (`"unverified": true`), `feed` warns before using it by
name, and it stops being unverified when someone runs it on a host that has
the CLI and confirms a resume.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Clean: no drift, or the command succeeded |
| 1 | Drift found (`diff`), or a pane could not be reported (`feed`) |
| 2 | Error: a missing dependency, an unparseable manifest, a git failure |
| 3 | Preflight refusal: no Herdr, no server, or a protocol version mismatch |
| 4 | A destructive write was refused: a dirty manifest (`absorb`), or a config write that would remove lines (`apply`) without `--yes` or a terminal answer |

## What this tool does not do

It does not install Herdr or uv, uninstall or disable anything, merge two
hosts' configurations, or run as a daemon or watcher. See the [design
doc](docs/superpowers/specs/2026-09-08-herdr-setup-design.md) for the full
rationale and the post-merge test plan that proves session hand-off works
end to end on a real host.
