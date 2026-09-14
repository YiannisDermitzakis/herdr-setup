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
| `tab-list.json` | `tab.list` | Captured 2026-09-13 from the same server (see below), trimmed to the three tabs whose `tab_id` matches `agent-list.json`'s three panes (`w2:t9`, `w2:t2`, `w2:t7`), keeping every key. |

### `tab-list.json`, captured 2026-09-13

The real `herdr tab list` on this host names every tab across every
workspace — real project names, real branch-derived titles, several dozen
entries. All of that is masked or dropped: only the three tabs `audit`'s own
read (`herdr tab list`, joined against `agent list`'s `tab_id`) needs are
kept. Because this fixture exists to be JOINED against `agent-list.json`
(the same three panes, described by two different calls), every value that
the join depends on agreeing was checked against `agent-list.json` and
corrected where the two captures -- taken on different days, of a host whose
tabs keep moving -- no longer lined up:

| Field | What it is here | Why |
|---|---|---|
| `tab_id` | `w2:t9`, `w2:t2`, `w2:t7`, unchanged from the real 2026-09-13 capture | These are the join key; they already matched `agent-list.json`'s three panes' own `tab_id` values, captured 2026-09-08. |
| `label`'s agent segment | `claude` on all three | The real 2026-09-13 capture had `codex` on the `w2:t7` entry (a different tab occupies that id today; tabs are reused). `agent-list.json`'s own `agent` field for the pane at `w2:t7` is `claude`, and this fixture describes THAT pane, so the label was corrected to agree -- inventing a `codex` join here would test a case this fixture set does not otherwise support (no `agent-list.json` entry is `codex`). |
| `label`'s project-name segment | `alpha` for `w2:t9`, `beta` for `w2:t2` and `w2:t7` | The real capture named a real project here. Replaced to agree with `agent-list.json`'s own `cwd`/`foreground_cwd` for these same three panes (`/work/alpha`, `/work/beta`, `/work/beta`), the placeholder names that fixture already uses -- a plain `example-repo` on all three (an earlier draft of this fixture) disagreed with `agent-list.json` naming two DIFFERENT working directories under one shared label text, which a join test could misread as evidence the two captures describe different repositories. |
| `label`'s topic text | `add the health endpoint`, `fix-image-size-check`, `check-engine-version` | `agent-list.json`'s own `terminal_title` fields for these same three panes, reused rather than inventing new placeholder text, so the same event reads as the same event across both captures. |
| `label`'s leading number, and the `number` field | Both `9` for `w2:t9`, both `2` for `w2:t2`, both `7` for `w2:t7` | The real 2026-09-13 capture's three matched entries did NOT have a leading label digit equal to `number` -- on a live host with tabs opened and closed over time, a tab's position-derived `number` and the digit its own label happened to be given at creation drift apart. Nothing in this fixture set exercises label-number parsing, so the mismatch was pure noise here; it was set consistent (leading digit == `number`) on all three rather than leaving two unrelated-looking numbers sitting next to each other in committed test data, which is more likely to be copied into a wrong assumption than to be useful. |
| `pane_count`, `agent_status`, `focused`, `workspace_id` | Kept as captured | Opaque per-run counters and flags, not identity, and not part of the join. |

**Not seen, and therefore a construction in a later phase:** a tab with
`pane_count` greater than 1, a workspace other than `w2`, and a label whose
leading number genuinely disagrees with its own `number` field (the real,
ordinary case this fixture deliberately does not preserve -- see the table
above).

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
