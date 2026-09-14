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

Clone the repository, then let the entrypoint put itself on `PATH`:

```
git clone https://github.com/YiannisDermitzakis/herdr-setup.git
cd herdr-setup
./herdr-setup install
```

`install` makes `~/.local/bin/herdr-setup` a symlink to this checkout's
entrypoint, creating `~/.local/bin` when it is missing. It only ever adds.
When anything other than a link to this entrypoint is already at that path —
a file, a directory, or a link to another checkout — it refuses with exit `4`
and leaves it alone; remove it yourself to switch checkouts. Run it again and
it says the link is already there. It warns, without failing, when
`~/.local/bin` is not on your `PATH`, or when another `herdr-setup` earlier on
`PATH` shadows the link. `install --dry-run` prints the commands it would run
and writes nothing.

The entrypoint finds its own `lib/` and `adapters/` next to itself, following
symlinks, so the link is all it needs.

### Prerequisites

- **[Herdr](https://herdr.dev)** itself, already installed and configured on
  the host. This tool never installs, upgrades, or reconfigures it.
- **git**, for `absorb`'s manifest-cleanliness check and for resolving plugin
  refs in `diff`.
- **[uv](https://docs.astral.sh/uv/)**, which pins and runs this tool's own
  Python. Every Python file here carries its own PEP 723 interpreter
  requirement, so two hosts run the exact same interpreter regardless of
  what they happen to have installed system-wide.
- **[gh](https://cli.github.com/)**, the GitHub CLI, logged in to github.com.
  Only `audit` needs it: it reads pull requests through `gh api`.

The shell floor is bash 3.2, which is what macOS ships.

## Commands

### `install` — put `herdr-setup` on `PATH`

```
$ ./herdr-setup install
$ ./herdr-setup install --dry-run   # print the mkdir and ln it would run, do nothing
```

Symlinks `~/.local/bin/herdr-setup` to this checkout's entrypoint (see
[Install](#install)). It touches no Herdr state and needs no Herdr server. It
never overwrites or removes a file. Exit `0` when the link is installed or was
already there, `4` when something else is at the link path, `2` when
`~/.local/bin` exists but is not a directory, or creating it or the link fails.

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

### First, on your reference host: `absorb`

The manifest describes one particular Herdr setup, and until you have run
`absorb` it does not describe yours. So the first command on the machine
whose configuration you want to keep is:

```
$ herdr-setup absorb          # write manifest/ from THIS host
$ git diff                    # read what it captured
$ git commit -am "my Herdr setup"
```

`apply` is what you then run on the *other* machines. Do not reach for
`apply --yes` against a manifest somebody else absorbed: `--yes` waives the
gate that stops a write from silently removing lines from your live config,
and a manifest that describes a different host is exactly the case that gate
exists for. Run `apply --dry-run` first and read the diff.

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

A line may name a ref or omit it. `owner/repo v1.2.3` pins a branch or tag.
`owner/repo` alone means the repository's default branch, which is what Herdr
installs from when given no `--ref`, and what it records for such a plugin.

Refuses to run — and writes nothing — when the manifest has uncommitted
changes, or when it cannot tell whether it does: git failing for any reason
is treated as dirty, never as clean, and so is a `manifest/` git is ignoring,
which it cannot vouch for either. Review with `git diff` and commit.
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

`onboard --yes` accepts the installs it offers, and **stops there**. It is not
carried into the feed step that follows. They are different consents: one
suppresses an install prompt, the other waives "which session is this pane
in?", whose wrong answer brings a live pane back running somebody else's
conversation. An uncertain pane is skipped and said so; run `herdr-setup feed
--yes` when you mean to waive that question.

### `feed` — report live sessions to Herdr

```
$ herdr-setup feed
$ herdr-setup feed --dry-run   # print the calls it would make, make none
$ herdr-setup feed --yes       # take the best candidate without asking
```

For each agent pane Herdr reports, finds the matching session through the
adapter seam (below) and sends `pane.report_agent_session` — the same call
the official integration hooks make — so that a server restart resumes the
pane instead of replacing it with a bare shell. Reports without asking only
when exactly one candidate comes back at `exact` confidence *from an adapter
that declared `exact`*; every other case is a question, and a question nobody
is there to answer is a skip, never a guess.

A skip is not a failure. Exit `1` means something did not get through — a
send that failed, or an adapter that could not answer — and the run says which
pane. A pane deliberately skipped is a decision the run made, told you about,
and exited `0` on; the next run can still feed it. Exit `3` under a protocol
mismatch.

### `audit` — find stranded work (read-only)

```
$ herdr-setup audit
$ herdr-setup audit --since 7             # session history window in days (default 30)
$ herdr-setup audit --owner example-org   # replace the owner list; repeatable
$ herdr-setup audit --include-sdk         # include sessions an automated caller drove
$ herdr-setup audit --include-bots        # list bot-authored pull requests too
$ herdr-setup audit --json                # one JSON object instead of the tables
```

Reports three sections:

1. **open in Herdr**: every agent pane Herdr lists, with the branches its
   session worked on and their state (`open PR #n`, `merged PR #n`,
   `contained`, `unmerged`, `unresolved`), or why it has none: `no adapter`,
   `no session history (unsupported)`, `session history unavailable (adapter
   failed)`, `session not reported to Herdr`, `session not found in history`
   (outside `--since`, or SDK-driven), or `no branch evidence`. A pane whose
   every branch is gone shows `only gone branches (counted)`;
2. **closed, with unmerged branches**: branches with work not in the default
   branch, or with an open pull request, that no open pane touched, with the
   newest session that did and a `+N` count of older ones. Clones of one
   GitHub repository count as that one repository;
3. **open PRs no session is working on**: open pull requests in your GitHub
   owners' non-archived repositories (the `gh` user and its organisations, or
   the `--owner` list) whose head branch no session in the window, and no open
   pane's own directory, touched. Drafts are labelled; bot pull requests are
   hidden and counted.

It reads Herdr's agent and tab lists, the session history of every adapter
that offers it (Claude Code and Codex today), the local git repositories those
sessions worked in, and GitHub through `gh api`. Merge state asks GitHub for a
merged pull request before it asks git about ancestry, because a squash merge
is never an ancestor of the default branch. A branch with no ref and no pull
request left is counted as gone, not listed.

It never writes anything: no file, no git ref, no `git fetch`, no GitHub
mutation, and no Herdr call beyond the two lists. It never closes a pull
request, deletes a branch, or resumes a session. `--dry-run` and `--yes` are
accepted and change nothing.

Exit `0` when sections 2 and 3 are empty, `1` when either has a row. Exit `2`
on an error — `git` or `gh` missing, `gh` not logged in, a `gh` or `git` call
failing — with one line and no report; and also when the report is incomplete
because an adapter's probe failed or its session history could not be read,
in which case the report is still printed
and ends with an `incomplete:` line. `2` wins over `1`: a report missing a
source could overstate section 3. Exit `3` under a protocol mismatch or with no
Herdr server.

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
| 0 | Clean: no drift, nothing to act on, or the command succeeded |
| 1 | Drift found (`diff`), a pane could not be reported (`feed`), or stranded work or an unmatched pull request found (`audit`) |
| 2 | Error: a missing dependency, an unparseable manifest, a git or gh failure; or an incomplete `audit` report, which is still printed |
| 3 | Preflight refusal: no Herdr, no server, or a protocol version mismatch |
| 4 | A write was refused: a dirty manifest (`absorb`), a config write that would remove lines (`apply`) without `--yes` or a terminal answer, or something other than a link to this checkout already at `~/.local/bin/herdr-setup` (`install`) |

## What this tool does not do

It does not install Herdr or uv, uninstall or disable anything, merge two
hosts' configurations, or run as a daemon or watcher. See the [design
doc](docs/superpowers/specs/2026-09-08-herdr-setup-design.md) for the full
rationale and the post-merge test plan that proves session hand-off works
end to end on a real host.
