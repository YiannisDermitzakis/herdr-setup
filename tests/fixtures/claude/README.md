# Captured Claude Code state

Two different captures live in this directory, of two different stores.
**Both are captures from a running Claude Code, not constructions.** Same
rule as `tests/fixtures/herdr/`, and the same reason: a fixture written by
the same hand and in the same sitting as the code that reads it proves the
two agree with each other, not that either agrees with the real thing.

- `session.json` -- one process's own session state file,
  `~/.claude/sessions/<pid>.json`. This is what the existing, `exact`-
  confidence `feed` adapter reads, by matching a pane's foreground process id.
  See its own section below.
- `transcript.jsonl` -- line shapes out of the TRANSCRIPT store,
  `<config>/projects/*/*.jsonl`. This is what the NEW, read-only `sessions`
  query (docs/superpowers/specs/2026-09-13-session-audit-design.md) reads;
  `feed`'s adapter never touches this store. See its own section further
  below.

## `session.json`

### Provenance

Captured 2026-09-08 from `~/.claude/sessions/<pid>.json` for a live,
interactive Claude Code process on macOS (version 2.1.251). Read directly off
disk -- this file is world-readable by design, unlike the paired `.key` file
beside it, which is not captured here and is never read by this adapter.

At the same instant, `ps -o lstart= -p <pid>` on the same host printed
`Sat Aug 29 06:41:34 2026`, two hours ahead of this file's own `procStart`,
`Sat Aug 29 04:41:34 2026`. That gap is not a mistake in the capture -- it is
the whole reason `docs/adapters.md` spends a section on it. `ps` prints local
wall-clock time (this host runs at UTC+2); Claude Code records `procStart` in
UTC with no zone marker. `adapters/claude` has to parse it as UTC
(`calendar.timegm`, never `time.mktime`) or it rejects every session on this
host while working by accident on one that happens to run at UTC.

### What was masked

This repository is public, and `tests/test_public_hygiene.sh` enforces the
rule below rather than merely stating it: it scans the whole tree for a real
home directory path or email address, and holds `tests/fixtures/` to a
stricter bar on top of that (real session ids, git remotes, commit hashes,
embedded system prompts). **Every key from the real file is kept; only
values were changed.** The field-by-field mask table, so the next capture
(phase 8's opencode and Copilot fixtures) does not have to re-derive it:

| field | why it matters | replaced with |
|---|---|---|
| `sessionId` | identifies a real conversation | the all-zero placeholder UUID, `00000000-0000-4000-8000-000000000001` |
| `bridgeSessionId` | same, an internal pairing id this adapter never reads | a placeholder in the same shape, `session_<24 zeros>` |
| `cwd` | a real path naming a real private repository | `/work/alpha`, matching `tests/fixtures/herdr/`'s placeholder style |
| `messagingSocketPath` | the real value carries the operator's home directory and username | `/tmp/placeholder/cc-socks/<pid>.sock` |
| `name` | Claude Code derives this from context, and it can name the actual work in progress in that pane -- the same leak class phase 6 found and fixed in Herdr's `terminal_title` | a neutral topic, `example-session` (`nameSource` stays `"user"`: it describes the mechanism, not the content) |
| `pid`, `startedAt`, `procStart`, `version`, `peerFeatures`, `kind`, `entrypoint`, `pidDomain`, `nameSince`, `updatedAt`, `status`, `statusUpdatedAt` | shape and timing only, no identity | kept as captured |

One coupling in that last row is load-bearing, not incidental: `procStart`
and `startedAt` are kept as a matched PAIR from the same real instant, because
their disagreement (see below) is the whole fixture this adapter's timezone
test depends on. Substituting either one alone, or inventing new values for
both, would erase that offset and stop the test from testing anything.

### What this settles

- The session file's own `cwd` is what `adapters/claude` slugifies to find
  the transcript, not the pane's `cwd` from the `resolve` request. The two
  should normally agree, but the session file is the adapter's single,
  self-contained source once it has matched a pid to a file, and a session
  written for a directory that has since been renamed or moved should not
  point at whatever the pane happens to be sitting in now.
- `procStart` is a UTC string with no zone marker (see above). Do not add a
  timezone-aware `datetime` here; `calendar.timegm(time.strptime(...))` reads
  the naive string as UTC directly, which is what it is.
- `name` is a human nickname, not a command or a version -- unrelated to the
  Herdr-side `name` trap (`tests/fixtures/herdr/README.md`), where the
  process's own `name` field is its version string. Different capture,
  different field, same word.

## `transcript.jsonl`

### Provenance

Captured 2026-09-13 by reading real `*.jsonl` transcripts under this
machine's own `~/.claude/projects/`, produced by Claude Code 2.1.270. Four
lines, from two different real sessions, assembled into one file as if they
were one session's timeline (`sessionId` unified to the placeholder below) --
each line's OWN shape is the capture; which session it originally came from
is not:

| Line | Original source | `type` |
|---|---|---|
| 1 | This plan's own fr-goal session, the `/super-fr:fr-goal` slash command that started it | `user`, carrying `cwd`, `gitBranch`, `entrypoint`, `timestamp`, `sessionId` |
| 2 | The same session, its first `Bash` tool call | `assistant`, a `tool_use` block named `Bash` |
| 3 | An unrelated session in a different repository | `custom-title` |
| 4 | An unrelated session in a different repository | `ai-title` |

Line 1 is a deliberately unremarkable choice: its `gitBranch` reads `"main"`
even though the session went on to work in an `fr` worktree on a feature
branch, which is exactly the trap the design doc's Problem section names
("transcripts whose `gitBranch` field says `main` because the session
started in a base clone and worked in a worktree"). Its `evidence:
git-branch-field` is filtered out by the "cannot be a real branch" rule
(`main` is one of the excluded names) -- kept here anyway, unedited, because
that IS the shape a real capture has: mostly `main`, filtered, and kept for
the sessions that did start on a branch.

### What was masked

Every key from each real line is kept; only values were changed:

| Field(s) | Real value | Replaced with |
|---|---|---|
| `sessionId`, `session_id` (all lines) | two different real session ids | the placeholder UUID used throughout `tests/fixtures/`, `00000000-0000-4000-8000-000000000001`, unified across all four lines |
| `parentUuid`, `promptId`, `uuid` (per-message ids) | real, distinct per-message ids | distinct placeholder UUIDs (`...002` through `...006`) -- distinct because the real capture has them distinct (a child's `uuid` is never its own `parentUuid`), unlike the session id above, which really was the same value everywhere |
| `message.id`, `content[].id` (`msg_…`, `toolu_…`), `wireToolInputs` key, `requestId` | real per-call ids from the Anthropic API | short placeholder ids, kept structurally consistent (the `toolu_…` id inside `content` matches the `wireToolInputs` key, as it does in the real capture) |
| `cwd` (both lines) | a real path naming this checkout's own worktree, and a real path naming the base clone | `/work/example-repo` |
| `input.command`, `wireToolInputs.….command` | referenced the same real worktree path and this plan's real branch name | `/work/example-repo` and `feat/example-branch` |
| `message.content` (line 1's prompt text) | several sentences naming this repository, this plan and a real handover file path | the short placeholder `"do the example task"` |
| `customTitle` | a real session title | `"renaming the config module"` |
| `aiTitle` | a real session title | `"bump the pinned dependency version"` |

**Kept as captured**, because it carries no identity: `type`, `version`,
`entrypoint`, `userType`, `origin.kind`, `effort`, `perTurnEffort`,
`isSidechain`, `apiBlockIndex`, `attributionSkill`, `attributionPlugin`,
`stop_reason` and friends, every `usage` token count, and both timestamps.

### What was NOT seen on this host, and is therefore a construction later

- A subagent transcript (`<session-id>/subagents/*.jsonl`) and a line with
  `isSidechain: true` -- this host's subagent transcripts did not happen to
  fall within a quick capture pass; a later phase's tests construct one, the
  same way `tests/test_feed_probe.py` constructs a broken adapter rather than
  capturing brokenness.
- An `entrypoint` starting with `sdk-` (an SDK-driven session) on the same
  shape of line captured here. This host's actual `sdk-cli`/`sdk-py`
  transcripts are the bulk of its history (the design doc's own Problem
  section: "about three quarters of the newest transcripts"), but none was
  hand-picked into this fixture; a later phase constructs one to test the
  `--include-sdk` filter both ways.
- Every other `command` evidence shape the design doc lists (`git
  checkout -b`, `git worktree add`, `git push -u`, `gh pr create --head`) --
  the one real command captured here (`fr run start fr-goal --branch …`)
  matches none of them, on purpose: this fixture's job is the two outer line
  SHAPES (`user`, `assistant` with a `Bash` tool_use), not a worked evidence
  example. A later phase constructs one Bash command per shape.
- A truncated or unparseable line, and a file outside the `--since` window --
  both are constructions, by their nature (a fixture cannot capture "this
  file finished writing badly").
