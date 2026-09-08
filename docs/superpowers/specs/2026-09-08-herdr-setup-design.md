# herdr-setup: reproduce and sync a Herdr setup across machines

**Date:** 2026-09-08
**Status:** approved

## Problem

Herdr keeps its configuration on the host and offers no export, no sync, and no
way to ask "is this machine current?". Setting up a second machine means
remembering which plugins were installed, at which refs, and which agent
integrations were turned on.

The agent integrations are the part that bites. Herdr resumes a coding agent
after a server restart only if that agent reported its session id, and an agent
reports only through an integration hook that it loaded at startup. A session
that began before the hook was installed never reports, so a restart replaces it
with a bare shell. On the machine this was written for, a herdr upgrade left 21
Claude Code sessions in exactly that state.

## Goals

1. Capture a Herdr configuration into a git checkout, and apply it to another host.
2. Report how a host differs from the checkout, without writing anything.
3. Offer the coding agents found on a host their Herdr integration, and refresh
   an integration that a Herdr upgrade left stale.
4. Report live agent sessions to Herdr, so a restart resumes them. Support Claude
   Code, Codex, opencode, and GitHub Copilot CLI, through a seam that accepts more.

## Non-goals

- Installing Herdr. That is a one-time, host-specific, often privileged act. The
  tool checks for Herdr and stops with an install hint if it is absent.
- Uninstalling or disabling anything. The tool adds and updates only.
- A daemon, a watcher, or any background process.
- Merging two hosts' configurations. Last write wins, and git shows what changed.

## Architecture

### The checkout is external

The repository is cloned anywhere. Nothing in Herdr points at it, and Herdr does
not know it exists. Commands copy files between the checkout and the host's real
Herdr configuration.

Copying, not symlinking, is deliberate. Plugins rewrite `config.toml` by writing a
temporary file and renaming it over the original, which replaces a symlink with a
regular file. A symlinked configuration would break the first time a plugin
toggled a setting.

The consequence: a host is only as current as its last `apply`. `diff` is how you
ask whether it has fallen behind.

### Layout

```
herdr-setup              bash entrypoint
lib/
  common.sh              socket call, plugins.json reader, config block splitter
  feed.py                adapter runner: probe, resolve, confirm, report
adapters/
  claude                 executable, one per harness
  codex
  opencode
  copilot
manifest/
  plugins.list           owner/repo[/subdir] <ref>, one per line
  config.toml            the operator's own Herdr config lines
docs/
  adapters.md            the adapter contract, for adding a harness
tests/
```

### What the manifest holds

Only what is identical on every host. Plugins with their pinned refs, and the
operator's own `config.toml` lines. Integrations, harness choices, resolved commit
hashes, and any absolute path stay out: they are per-host by nature.

`manifest/plugins.list` is a line per plugin, `#` for comments:

```
kryptamine/herdr-auto-title              v0.3.3
ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay   main
```

The ref is a branch or a tag. Herdr's `--ref` rejects a bare commit, and records
the commit it resolved to in `plugins.json`, which is what `diff` compares against.

## Commands

### `diff` (read-only)

Reports, and writes nothing.

- **Plugins.** For each manifest line: missing, present at the pinned ref, or ref
  moved. "Moved" is decided by resolving the ref with `git ls-remote` and comparing
  it to the commit Herdr recorded. A branch-pinned plugin therefore shows an
  available update rather than drifting in silence. Plugins on the host that the
  manifest does not name are listed as unmanaged, not as errors.
- **Config.** The host's `config.toml` with every plugin-written block removed,
  diffed against `manifest/config.toml`. A plugin-written block is the region
  between `# --- added by <id> ...` and `# --- end <id> ---`.
- **Integrations.** Informational only, since they are not in the manifest. Lists
  each installed integration and whether Herdr calls it current or outdated.

Exit 0 when the host matches, 1 when it drifts, 2 on error.

### `apply`

Installs missing plugins with `herdr plugin install --ref`, reinstalls those whose
ref moved, splices `manifest/config.toml` into the host file while leaving
plugin-written blocks in place, and runs `herdr server reload-config`. It backs the
host config up first. Herdr's own trust preview is shown for each install unless
`--yes` is passed. It never removes a plugin and never touches integrations.

### `absorb`

The reverse of `apply`, and the reason the tool is usable day to day: change Herdr
interactively on any host, absorb, commit, apply elsewhere.

It rewrites `manifest/plugins.list` from the host's `plugins.json`, keeping the
source and the requested ref and dropping the resolved commit, and rewrites
`manifest/config.toml` from the host config with plugin-written blocks stripped. It
touches only the checkout. Review with `git diff` and commit.

It refuses to run when the manifest has uncommitted changes, so it can never
silently overwrite an edit made by hand.

### `onboard`

Per-host and interactive, because different machines run different agents.

Detects agents two ways: the command on `PATH`, and the agent's configuration
directory being present. Prints a table of agent, how it was detected, and
integration state (absent, current, or outdated with both versions). Offers each
one individually. Installs only what is accepted, and nothing is remembered
between runs.

It then runs `feed` for every agent whose integration was just installed or
refreshed. That is the moment live sessions are guaranteed to be unreported.

### `feed`

For each agent pane Herdr reports, find the session and send one
`pane.report_agent_session` call: the same call the official hooks make. Prints the
count Herdr has persisted at the end.

## The adapter seam

An adapter is any executable in `adapters/`. The protocol is JSON on stdout, so an
adapter can be written in any language.

**`adapters/<name> probe`** prints one object and exits 0:

```json
{"agent": "claude", "source": "herdr:claude", "available": true,
 "confidence": "exact", "unverified": false}
```

`agent` is Herdr's own agent name and `source` its report source; the two differ
for some agents, so both are declared rather than derived. `available` is false
when the agent leaves no readable session state on this host, and the runner then
skips it. `confidence` is `exact` when the adapter can tie a session to a specific
process, and `heuristic` when it can only match on a directory.

**`adapters/<name> resolve`** reads one object on stdin:

```json
{"panes": [{"pane_id": "w2:p2", "cwd": "/path", "pid": 38080,
            "pid_start_epoch": 1788500000}]}
```

and prints candidates, best first:

```json
{"results": [{"pane_id": "w2:p2", "candidates": [
  {"session_id": "0260...", "label": "frank", "updated": "2026-09-04T09:12:00Z",
   "confidence": "exact", "session_path": "/optional/transcript"}]}]}
```

**The runner owns every decision the adapter does not.** It reports without asking
when a single candidate comes back at `exact` confidence. In every other case it
shows the candidates and asks, because feeding Herdr a wrong session id makes a
pane resume the wrong conversation. An adapter never talks to the Herdr socket.

## The four adapters

| Agent | Session source | Match | Herdr resumes with |
|---|---|---|---|
| Claude Code | `~/.claude/sessions/<pid>.json` | exact, by process id | `claude --resume <id>` |
| Codex | `~/.codex/sessions/<y>/<m>/<d>/rollout-*.jsonl`, first line `session_meta` carries `cwd` and `id` | heuristic, by directory | `codex resume <id>` |
| opencode | `opencode.db`, `session` table, root rows only (no parent, not archived) | heuristic, by directory | `opencode --session <id>` |
| Copilot CLI | `$COPILOT_HOME` or `~/.copilot` session state | heuristic, by directory | `copilot --resume=<id>` |

Resume commands are Herdr's, read from its own resume logic, not invented here.

Claude is the only exact adapter, because Claude Code writes a file named after the
process id. The other three can only ask which sessions were open in this
directory, so they always confirm.

The Copilot adapter ships **unverified**. GitHub Copilot CLI was not installed on
the machine this was written on, so it is tested against fixtures only. Its `probe`
returns `"unverified": true`, the runner prints a warning before using it, and the
README says so. It stops being unverified when someone runs it on a host that has
the CLI and confirms a resume.

## Errors and safety

- Fails closed. No Herdr on `PATH`, no socket, or an unparseable manifest stops the
  run with one line saying which.
- Read-only commands never write. `diff` and `probe` touch nothing.
- `apply` backs up the host config before writing, and writes by temporary file and
  rename so the config is never half-written.
- `--dry-run` on `apply`, `absorb`, `onboard`, and `feed` prints every command and
  every socket call it would make, and makes none.
- No secrets and no personal paths in the repository. Paths are derived at runtime.

## Portability

bash 3.2 and Python 3.9 are the floor, which is what macOS ships. No `tomllib`, no
associative arrays, no `mapfile`. Config parsing is line-based, which is enough for
splicing marked blocks and never needs a TOML parser. macOS and Linux only, matching
the plugins in the manifest.

## Testing

- A fake `herdr` on `PATH` records the calls made to it and serves canned
  `plugins.json`, `agent list`, and `pane process-info` output. No test touches a
  real Herdr, a real socket, or the operator's home directory.
- Adapters are tested against fixture session stores in a temporary directory,
  including the Copilot one.
- Round-trip test: `absorb` then `apply` against a fake host is a no-op.
- Block-splicing tests: a config with plugin blocks survives `apply` with the blocks
  in place and the operator's lines updated.

## Test Plan

Post-merge, operator-driven, on the machine that prompted this. It currently runs
Herdr 0.9.0 on the command line against a 0.8.2 server, with a Claude integration at
v8 where 0.9.0 wants v9, and 21 live Claude sessions.

1. `herdr-setup diff` reports the host's two plugins as matching the manifest, and
   reports the Claude integration as outdated.
2. `herdr-setup onboard` lists the agents present, offers the Claude refresh from v8
   to v9, installs it on acceptance, and offers nothing for agents that are absent.
3. `herdr-setup feed` reports every live session and prints a persisted count equal
   to the number of live agent panes.
4. `herdr server stop`, then `herdr`. Every pane that held a session comes back
   running its agent's resume command, and no pane comes back as a bare shell.
5. `herdr-setup diff` on a second machine reports every manifest plugin as missing;
   `apply` installs them; `diff` then reports a match.

## Decisions

| Decision | Reasoning |
|---|---|
| Public repository under the operator's own account | A personal tool, not org work. The manifest holds nothing sensitive. |
| Copy, never symlink | Plugins rewrite the config by rename, which destroys a symlink. |
| Integrations excluded from the manifest | Different hosts run different agents. The choice belongs to the host. |
| Detect and offer, rather than install every integration | Installing an integration for an absent agent writes files into a directory that would not otherwise exist. |
| Adapters as executables with a JSON protocol | Adding a harness needs no change to the runner and no particular language. |
| The runner confirms; the adapter never reports | A wrong session id resumes the wrong conversation. One place decides. |
| Refs pinned to a branch or tag, never a commit | Herdr's `--ref` rejects a bare commit. The recorded resolved commit gives drift detection anyway. |
| `absorb` refuses a dirty manifest | It overwrites rather than merges, so it must never eat an uncommitted hand edit. |
| Ship Copilot unverified rather than omit it | The seam and the contract are the deliverable. An untested adapter that says so is more useful than an absent one. |
