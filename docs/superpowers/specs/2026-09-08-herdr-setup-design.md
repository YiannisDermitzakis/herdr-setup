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

- Installing Herdr or uv. Both are one-time, host-specific acts. The tool checks
  for each and stops with an install hint when one is missing.
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
plugin-written blocks in place, and runs `herdr server reload-config`. It never
removes a plugin and never touches integrations.

Herdr's own trust preview is shown for each install unless `--yes` is passed. That
means the install runs in the foreground with the operator's terminal attached, and
only its exit status is read: an install whose output the tool captured would show
the operator nothing and leave Herdr waiting for an answer behind an invisible
prompt.

The config write is the most destructive thing the tool does, so it is also the
loudest. Before writing, `apply` prints the unified diff of the change, under
`--dry-run` as well. Last write wins, but a write that leaves the file with fewer
lines than it had needs `--yes` or a yes answer at the terminal; refused, it writes
nothing and exits 4. The previous content is backed up first, to a timestamped
`config.toml.bak.<UTC timestamp>` — one fixed `.bak` slot was destroyed by the next
apply, so the safety net survived only a single mistake. The written file keeps the
mode the host config had.

### `absorb`

The reverse of `apply`, and the reason the tool is usable day to day: change Herdr
interactively on any host, absorb, commit, apply elsewhere.

It rewrites `manifest/plugins.list` from the host's `plugins.json`, taking
`source.owner`, `source.repo`, and `source.requested_ref` from each entry and
dropping `source.resolved_commit` and `source.managed_path`, and rewrites
`manifest/config.toml` from the host config with plugin-written blocks stripped. It
touches only the checkout. Review with `git diff` and commit.

It refuses to run when the manifest has uncommitted changes, so it can never
silently overwrite an edit made by hand. It refuses just as firmly when it cannot
find out: git failing for any reason — the checkout is not a git repository, `.git`
is unreadable — is a refusal, never a clean tree. Reading git's output and ignoring
its exit status made every one of those failures look like "nothing is modified",
which is the one answer that lets absorb overwrite.

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
- `apply` backs up the host config before writing, to a timestamped file that no
  later run overwrites, and writes by temporary file and rename so the config is
  never half-written. The written file keeps the original's mode.
- `apply` prints the diff of the config change before making it, and a change that
  removes lines needs `--yes` or an answer at the terminal.
- A guard that cannot reach its evidence refuses. A failed check is never read as a
  passing one, whether the check is a Herdr probe or a `git status`.
- `--dry-run` on `apply`, `absorb`, `onboard`, and `feed` prints every command and
  every socket call it would make, and makes none.
- No secrets and no personal paths in the repository. Paths are derived at runtime.

### One deliberate normalisation

The manifest is the source of truth for the operator's own configuration lines,
and what `apply` writes always ends with a newline. A host file whose last line
has none therefore differs from the spliced content on the first `apply`, takes
one write and one `reload-config`, and matches from then on. That is drift the
manifest corrects, like any other. The alternative is to carry "did the host end
without a newline" through the splice as a separate signal and let the host's
shape override the manifest's on that one byte, which is a special case with no
principle behind it, inside the one function that has to stay byte-exact for the
steady-state anchor guarantee.

## Preflight: the Herdr version gate

Upgrading the Herdr command line without restarting the server leaves the two
speaking different protocol versions. The command line then refuses most calls with
`protocol_mismatch` and exits 1. This is not a rare corner: a package manager
upgrade produces it, and the machine this was written on sat in that state.

The tool therefore runs a preflight before any command and reports which of four
states the host is in:

- **Matched.** The preflight's probe call succeeded. Everything works.
- **Mismatched.** The probe failed with a `protocol_mismatch` error. `diff` still
  runs in full, because it reads `plugins.json` and `config.toml` from disk and
  reads integration state through `herdr integration status`, none of which cross
  the socket. `apply`, `onboard`, and `feed` stop with one line naming the mismatch
  and the restart needed, rather than proceeding to a confusing partial result.
- **No server.** `diff` still runs. The rest stop.
- **Unreachable.** The probe failed for some other reason: the socket closed, a
  permission error, an answer that could not be read. `diff` still runs; the rest
  stop, quoting what Herdr actually said.

Matched means the probe SUCCEEDED, and nothing else. Reading only the error code
and calling every other failure healthy is the same fail-open bug one level up: a
server that had just refused the probe would be written to.

Every Herdr call checks its exit status, and no result is parsed from a failed call.
Herdr answers a blocked call with a JSON error object rather than an empty result,
so a parser that ignores the exit status sees zero agents and reports success having
done nothing. That failure mode is the reason the gate exists. The probe reads
Herdr's stderr as well as its stdout, because an error object written to stderr and
discarded looks exactly like a healthy silent success.

## Portability

The shell floor is bash 3.2, because that is what macOS ships: no associative
arrays, no `mapfile`, no `local -n`.

Python does not come from the host. Every Python file carries PEP 723 inline
metadata naming the interpreter it needs, and uv resolves and if necessary
downloads it. Two hosts therefore run the same interpreter whatever they happen
to have installed, which is the same guarantee the tool already offers for
plugins and configuration. uv joins Herdr and git as a prerequisite, and it also
pins the development tools.

uv runs the tool's own scripts and nothing else. The host's Herdr is invoked
exactly as installed, and herdr-setup never replaces, upgrades or reconfigures
it.

An interpreter start through uv costs roughly 265 milliseconds against about 84
for a bare system python3. That is not a design constraint here: the tool is run
by hand when a host is set up or has drifted, and is idle the rest of the time.
Robustness wins over startup cost. Structured work is batched behind subcommands
of `lib/hs.py` because one entry point is easier to reason about, not because
starts are expensive.

Config parsing stays line-based even though a modern interpreter brings
`tomllib`, because the tool must preserve plugin-written blocks and the
operator's own formatting byte for byte, and a TOML round trip would discard
both. macOS and Linux only, matching the plugins in the manifest.

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

Post-merge, operator-driven, on the machine that prompted this. It runs Herdr 0.9.0
on the command line against a 0.8.2 server, with a Claude integration at v8 where
0.9.0 wants v9, and 21 live Claude sessions whose ids are already recorded in
Herdr's session file.

The order matters: a protocol mismatch blocks the socket-dependent commands, so the
restart sits in the middle rather than at the end.

1. `herdr-setup diff` runs in full despite the mismatch, reports the two plugins as
   matching the manifest, reports the Claude integration as outdated at v8, and
   names the protocol mismatch.
2. `herdr-setup feed` refuses, naming the mismatch and the restart. This is the
   check that the preflight gate works.
3. `herdr-setup onboard` refuses, naming the mismatch and the restart, and prints
   nothing else — not even the detection table. `onboard` calls the preflight gate
   first and unconditionally, because both `integration install` and its feed
   hand-off cross the socket; that is what the Preflight section above and the
   `socket-commands-refuse-under-mismatch` acceptance row both require. (An earlier
   draft of this step had onboard offering the refresh under a mismatch. It never
   did, and it must not: it would install against a server that had refused the
   call.)
4. `herdr server stop`, then `herdr`. Every pane that held a session comes back
   running its agent's resume command and no pane comes back as a bare shell. This
   also proves that session records written by the older server survive the upgrade.
5. `herdr-setup onboard` now runs: it offers the Claude integration refresh from v8
   to v9 and installs it on acceptance, then feeds exactly that agent.
   `herdr-setup feed` afterwards is a no-op or tops up any pane that came back
   without a record.
6. `herdr-setup diff` on a second machine reports every manifest plugin as missing;
   `apply` installs them; `diff` then reports a match.

## Implementation Plans

| Plan | Repo | File | Depends on |
|------|------|------|------------|
| 2026-09-08-herdr-setup | `YiannisDermitzakis/herdr-setup` | `2026-09-08-herdr-setup` | — |

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
| A preflight gate on the Herdr protocol version | An upgraded command line against an old server fails in a way that reads as "nothing to do" rather than as an error. |
| `diff` reads configuration from disk, not through the command line | It stays useful in exactly the broken state an operator most wants to inspect. |
| Python is pinned by uv rather than taken from the host | The tool's purpose is making hosts identical; depending on whichever interpreter a host happens to have is at odds with that. It also matches how the operator already installs their other tools. |
| Robustness over startup cost | The tool is run by hand when a host is set up or has drifted, not in a loop, so an interpreter start is not worth designing around. |
| Ship Copilot unverified rather than omit it | The seam and the contract are the deliverable. An untested adapter that says so is more useful than an absent one. |
