# Constructed GitHub Copilot CLI session-event shape

**This is a construction, not a capture, and that is the one permitted
exception to the rule the other three fixture directories under
`tests/fixtures/` follow.** GitHub Copilot CLI is not installed on this
machine (`copilot` is not on `$PATH`) and there is no real
`session-state/` store anywhere on it to capture from, so `event.json` in
this directory is written by hand from GitHub's own documentation and from
Herdr's own `copilot` integration hook, not lifted off a real installation.
`adapters/copilot` ships with `unverified: true` because of this, and says so
in its own `probe` output; that flag is the honest version of what this
README says in prose.

## What is CONFIRMED, and where from

- **`$COPILOT_HOME`, falling back to `~/.copilot`.** Documented directly:
  GitHub's own reference page,
  <https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference>,
  states the env var overrides the config directory and the default is
  `~/.copilot`. Independently corroborated inside the `herdr` binary itself
  (`strings /usr/local/bin/herdr`), whose bundled `copilot` integration hook
  template resolves the same pair -- `.copilot` / `COPILOT_HOME` -- next to
  the equivalent pairs it carries for every other agent it integrates
  (`.claude`/`CLAUDE_CONFIG_DIR`, `.hermes`/`HERMES_HOME`, ...). Two
  independent sources agreeing is stronger than the phase brief asked for.
- **Session data lives under `session-state/`, organised by session id, one
  subdirectory per session, each holding an event log named `events.jsonl`.**
  Also from the same GitHub reference page, in those words.
- **Herdr's own live integration reports a session by its `session_id` (or
  `sessionId`) field**, read from the JSON payload Copilot CLI's own
  `SessionStart` hook writes to stdin -- confirmed by reading the actual
  hook script template embedded in the `herdr` binary
  (`# HERDR_INTEGRATION_ID=copilot`). That is Herdr's PUSH path (the hook
  reports a session the moment Copilot starts one) and this adapter's PULL
  path (`herdr-setup feed` asking, after the fact, which session a pane is
  in) are different mechanisms reading different data, but the hook script
  is still the closest thing to primary evidence of how Copilot CLI and
  Herdr talk about a session id on this exact integration, so it is recorded
  here.

## What is a GUESS, named as one

GitHub's documentation says an event line in `events.jsonl` carries `type`,
`data`, `id`, `timestamp` and `parentId` (per a third-party session viewer
built against real output,
<https://github.com/mitsha-microsoft/copilot-session-explorer>, which is
consistent with, but not the same source as, the two items above). **It does
not say what is inside `data`, and in particular it does not confirm a
`cwd` field.** `event.json`'s `data.cwd` is this adapter's own guess at where
a session's working directory would live, chosen because every other
adapter in this tool that reads a per-session working directory names the
field exactly that -- Claude Code's session file (`cwd`), Codex's
`session_meta.payload.cwd`, Herdr's own `pane.process_info` (`cwd`,
`foreground_cwd`) -- so `cwd` is the path of least surprise if Copilot CLI
follows the same convention, not a confirmed fact about it.

Two guesses this fixture and `adapters/copilot` deliberately do NOT make,
because there was nothing to hang them on:

- **`type`'s real value.** `event.json` uses `"session-meta"`, chosen only
  to echo Codex's own first-line envelope for readability; `adapters/copilot`
  does not filter on it, reading `data.cwd` out of whatever the first line's
  `type` says, so a wrong guess here cannot cause a false negative.
- **A title or label field.** Nothing in either documentation source names
  one, so `adapters/copilot`'s candidates carry no `label` and rely on the
  contract's own documented fallback (the session id, taken from the
  `session-state/<id>/` directory name itself -- which IS confirmed, not
  guessed).

## What GitHub explicitly warns is unstable

The same reference page separately documents `session-store.db`, a SQLite
index the CLI keeps for cross-session search, but calls its own schema an
"internal implementation detail" that "can change between Copilot
releases." `adapters/copilot` does not touch it, for the same reason
`adapters/codex` never reads past a rollout's first line: the documented,
per-session `events.jsonl` is the more stable surface, and the phase brief's
instruction to say plainly what is unverified would be defeated by quietly
reverse-engineering an internal database GitHub itself says not to depend on.

## If this adapter is ever confirmed against a real installation

Replace this README's "GUESS" section with what was actually found, capture
a REAL `event.json` first line the way `tests/fixtures/claude/` and
`tests/fixtures/codex/` capture theirs (masking `cwd`, the session id, and
any embedded system-prompt-shaped text per `tests/test_public_hygiene.sh`),
and drop `unverified` from `adapters/copilot`'s probe output. Until then,
both stay.
