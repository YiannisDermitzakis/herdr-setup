# Captured Codex rollout metadata

Same rule as `tests/fixtures/herdr/` and `tests/fixtures/claude/`: **this is a
capture, not a construction.** `adapters/codex` reads exactly the first line
of a rollout file, so that is exactly what this fixture holds.

## Provenance

Captured 2026-09-08 by reading the first line of a real
`~/.codex/sessions/<yyyy>/<mm>/<dd>/rollout-<timestamp>-<id>.jsonl` file on
this machine, produced by Codex CLI 0.142.5 (`originator: codex-tui`). The
file's later lines (the actual turn-by-turn transcript) are not captured,
because `adapters/codex` never reads past the first line -- reading further
is the resource-cost trap the phase's own bound on scan depth (newest 30 day
directories) exists to avoid, and it applies to a single file's own length
too.

## What was masked

`tests/test_public_hygiene.sh` enforces this rather than merely stating it
(whole-tree: home paths, real emails; `tests/fixtures/` additionally: real
session ids, git remotes, commit hashes, embedded system prompts). **Every
key from the real capture is kept; only values were changed.** The
field-by-field table, so the next capture (phase 8's opencode and Copilot
fixtures) does not have to re-derive it:

| field | why it matters | replaced with |
|---|---|---|
| `payload.session_id`, `payload.id` | real ids -- `id` is also embedded in the real rollout's own FILENAME | the same placeholder UUID used throughout this fixture set, `00000000-0000-4000-8000-000000000001` (the real capture has `session_id == id`, kept equal rather than inventing a difference never observed) |
| `payload.cwd` | a real path naming a real private repository | `/work/alpha`, the placeholder used throughout `tests/fixtures/` |
| `payload.git.repository_url` | `git@github.com:<real-org>/<real-repo>.git` for a private organisation | `https://github.com/example-org/example-repo.git` -- an https form rather than the real capture's `git@` SSH form, because `tests/test_public_hygiene.sh`'s email-address check reads `git@host:` as a `local@domain` address; the https form still satisfies that same test's git-remote check and its `example` allowlist |
| `payload.git.commit_hash` | a real commit in a real, named repository | forty zeros |
| `payload.base_instructions.text` | several KB of Codex's own standard system prompt: public and generic, but not this adapter's to redistribute, and not something it reads at all (only `cwd`, `originator`, `cli_version`, `timestamp`, `git`, `source` are read, per the phase brief) | a one-line placeholder string naming what was removed and why -- shortening a string value, the same move as trimming `tests/fixtures/herdr/agent-list.json`'s entry list, not the removal of a key |
| `timestamp` (envelope and payload), `type`, `originator`, `cli_version`, `source`, `thread_source`, `model_provider`, `git.branch` | shape only, no identity | kept as captured (`branch: "main"` was already the least specific value it could have been) |

**On the filename.** A real rollout embeds its own session id in its name
(`rollout-<timestamp>-<id>.jsonl`), which would leak the real id even with
the content masked if this fixture reused that name verbatim. It does not:
this file is named for what it holds (`session-meta.json`, the first line of
one), never for the session it came from, so there is nothing in the
filename to rename away. `tests/helpers/write_codex_rollout()` is what
constructs an actual `rollout-*.jsonl`-shaped path for a test, and every
caller supplies its own (masked or synthetic) timestamp and id there.

## What this settles

- The envelope is `{"timestamp", "type", "payload": {...}}` and `type` is
  `"session_meta"` only for a rollout's first line -- every later line in the
  same file carries a different `type` and is never read.
- `payload.id` is what `adapters/codex` reports as `session_id`; the
  duplicate `payload.session_id` field is not used, kept here only because
  the real capture has it and removing a key is not this fixture's call to
  make.
- A rollout file whose first line is not JSON, or is JSON but not
  `session_meta`, is skipped by the adapter rather than treated as fatal --
  this fixture is the well-formed case; the malformed ones in
  `tests/test_adapter_codex.py` are written inline, the way
  `tests/test_feed_probe.py` writes its own broken adapters rather than
  capturing brokenness.
