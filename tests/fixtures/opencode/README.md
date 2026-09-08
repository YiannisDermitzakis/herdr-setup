# Captured opencode `session` table schema

Same rule as `tests/fixtures/herdr/`, `tests/fixtures/claude/` and
`tests/fixtures/codex/`, adapted for a SQLite store rather than a JSON file:
**the schema is a capture, the rows are not.** A fixture written by the same
hand and in the same sitting as the code that reads it proves the two agree
with each other, not that either agrees with the real thing -- phase 6's own
postmortem (`tests/fixtures/herdr/README.md`) is what this indirection exists
not to repeat.

## Provenance

`schema.sql` is `sqlite3 ~/.local/share/opencode/opencode.db ".schema
session"`, copied byte for byte, run against a real opencode installation
(version 1.18.5) on this machine on 2026-09-08. **No row from that database is
in this repository anywhere** -- every row `tests/test_adapter_opencode.py`
and `tests/helpers/feedlib.py`'s `write_opencode_db()` insert is synthesised,
using the placeholder style already established by `tests/fixtures/`
(`/work/alpha`, `/work/beta`, ...) and made-up session ids that were never
real (`test-session-*`), not the all-zero UUID placeholder those other
fixtures use, because opencode's own `id` column is not a UUID -- a real one
is a 30-character opaque string, confirmed by `SELECT length(id) FROM
session LIMIT 1` against the live database (content not read, length only).
A made-up non-UUID string in that same shape cannot be confused with a
placeholder for a UUID that was never there.

Also confirmed directly against the live database, content-free (`SELECT
typeof(time_updated), time_updated FROM session LIMIT 3` -- magnitudes only,
never the rows' actual directory or title values): `time_updated` and
`time_created` are **epoch milliseconds** (13-digit values, e.g.
`1774731087364`), not epoch seconds. `adapters/opencode` divides by 1000
before formatting the `updated` field. Getting this wrong does not fail
loudly -- a millisecond value read as seconds is a date in the year 58281,
which sorts fine (still newest-first) but displays wrong at the prompt, so a
test pins the conversion rather than only the ordering.

## What the adapter actually reads

Six columns: `id`, `parent_id`, `directory`, `title`, `time_updated`,
`time_archived`. The rest of the schema is carried here anyway, verbatim,
because trimming a captured schema to what today's caller needs is exactly
the drift `tests/fixtures/herdr/README.md` warns against -- a later reader
of this file should see the same table the real opencode server sees, not a
hand-picked subset.

`resolve` matches on `directory = <pane cwd>` (exact string equality, the
same discipline `adapters/codex` uses for its own `payload.cwd` match) **AND
`parent_id IS NULL`** (a child/sub-session of another session is never
reported on its own) **AND `time_archived IS NULL`** (an archived session is
gone as far as this adapter is concerned), ordered by `time_updated`
descending. `label` comes from `title`.

## Why read-only, and why a URI

`adapters/opencode` opens the database via a `file:<path>?mode=ro` URI
(`sqlite3.connect(uri, uri=True)`) rather than a plain path. A plain-path
connection can implicitly create the file if it does not exist and, more to
the point, opens read-write -- against a database a running opencode server
may be writing to (the live one on this machine carries `-wal` and `-shm`
sidecar files, i.e. WAL mode, which is exactly what makes a concurrent
read-only reader safe **as long as it never asks to write**). `mode=ro`
makes "never asks to write" a property SQLite itself enforces, not a
convention the adapter's own code has to uphold by never calling the wrong
method.
