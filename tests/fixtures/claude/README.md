# Captured Claude Code session state

**This is a capture from a running Claude Code, not a construction.** Same
rule as `tests/fixtures/herdr/`, and the same reason: a fixture written by
the same hand and in the same sitting as the code that reads it proves the
two agree with each other, not that either agrees with the real thing.

## Provenance

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

## What was masked

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

## What this settles

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
