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

- `payload.cwd` -> `/work/alpha`, the same placeholder used throughout this
  fixture set.
- `payload.id` and `payload.session_id` -> the same placeholder UUID
  (`00000000-0000-4000-8000-000000000001`) used in `tests/fixtures/claude/`.
  The real capture has session_id == id, so the fixture keeps that equality
  rather than inventing a difference that was never observed.
- `payload.git.repository_url` -> a placeholder `owner/repo`. The real value
  named one of the operator's own repositories.
- `payload.git.commit_hash` -> forty zeros. The real value was a real commit
  in that repository.
- `payload.base_instructions.text` -> elided to a short note. The real value
  is several kilobytes of Codex's own standard system prompt: public,
  generic, and not something `adapters/codex` reads at all (only `cwd`,
  `originator`, `cli_version`, `timestamp`, `git`, and `source` are read, per
  the phase brief). Shortening a string value is the same move as trimming
  `agent-list.json`'s entry list to three -- an edit to a value the fixture
  rule explicitly allows, not the removal of a key.

`timestamp` (both the envelope's and the payload's own), `type`,
`originator`, `cli_version`, `source`, `thread_source`, `model_provider`, and
`git.branch` are kept as captured: none of them is personally identifying,
and `branch: "main"` was already the least specific value it could have been.

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
