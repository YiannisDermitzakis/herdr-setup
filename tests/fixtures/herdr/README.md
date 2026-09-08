# Captured Herdr responses

**Every file in this directory is a capture from a running Herdr. None of it
is constructed, and none of it may be.**

That rule exists because it was broken once, in the worst place. Phase 6's
first cut of `panes_for` invented three things the API does not return — a
`result.processes` list, a `foreground` boolean on each entry, and a
`pid_start_epoch` field — and its fixtures invented the same three, so ninety
green tests agreed with the code and neither agreed with Herdr. It failed
against the first real host it met. A test whose fixture and whose code were
written by the same hand in the same hour proves they match each other; it
proves nothing about the thing they are both supposed to describe.

So: **a fixture for a Herdr call is a capture, and its provenance is recorded
here.** Trimming a captured list to fewer entries is fine. Editing a value is
fine, and masking one is required. Adding, renaming or removing a *key* is
not — at that point it stops being evidence.

## Provenance

Captured 2026-09-08 from a live Herdr **server protocol 20** (Herdr 0.8.2) on
macOS, by writing a JSON-RPC line to `$HERDR_SOCKET_PATH` and reading the
answer. The command line on that host was newer (protocol 22) and refused
every call, which is the state the design document's test plan describes; the
socket answered normally, so the captures are the server's own output.

| File | Call | Notes |
|---|---|---|
| `agent-list.json` | `agent.list` | Trimmed from 20 entries to 3 — one `working` and two `idle` — keeping every key. |
| `pane-process-info.json` | `pane.process_info` with `pane_id` | Verbatim, one pane. |

### Envelope

The socket answers `{"id", "result":{"type", …}}`. The command line renders
the same payload with `type` lifted to the top level. Both carry the same
`result`, and the runner reads only `result.agents` and
`result.process_info.foreground_processes`, which are identical either way.

### What was masked

This repository is public (AGENTS.md: no real home directory path, username,
hostname or email). Only values were changed, never a key or a shape:

- Working directories → `/work/<name>`.
- A plugin path under the home directory → `/home/placeholder-user/…`.
- `agent_session.value`, a real session id → a placeholder UUID.

Pane, tab, workspace and terminal ids are opaque per-run identifiers and are
kept as captured.

## Three things these captures settle

1. **The process list is at `result.process_info.foreground_processes`**, not
   `result.processes`.
2. **There is no `foreground` flag on an entry.** Everything in
   `foreground_processes` is foreground, by construction. A test for such a
   flag matches nothing.
3. **Herdr reports no process start time at all.** `pid_start_epoch` is
   therefore the runner's own work, read from the operating system with
   `ps -o lstart=`. See `lib/feed.py` and the note on time frames in
   `docs/adapters.md`.

One more, which the captures make plain and which is easy to get wrong:
`name` for a Claude process is `"2.1.260"` — its **version**, not its command.
Matching a pane's agent on `name` fails silently. `argv0` is the field to
match.

`agent.list` also carries `agent_session` for a pane Herdr already has a
record for. `feed` does not read it today: its job is to top up, and
re-reporting an id a pane already has is harmless. A later phase that wants
to skip already-known panes, or to warn before overwriting a *different* id,
has the field to hand.
