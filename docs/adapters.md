# The adapter contract

An adapter answers one question about one coding agent: **which session is
this pane in?**

It is any executable file in `adapters/`, in any language. The runner
(`lib/feed.py`) finds it, asks it two questions, and decides what to do with
the answers. Adding support for a new harness means writing one such file and
nothing else.

Every example in this document is extracted and executed by
`tests/test_contract.py`, including the worked adapter at the end. If
something here is wrong, that test fails.

## The one rule that is not negotiable

**The runner decides. The adapter never reports.**

An adapter prints candidates. It does not open the Herdr socket, does not
send `pane.report_agent_session`, and does not act on its own conclusion. One
place decides, and that place is the runner.

The reason is narrow and it is the reason this seam exists at all: feeding
Herdr the wrong session id makes the pane resume **the wrong conversation**
after a server restart. That failure is silent, and it is worse than the
problem the tool solves. So the runner reports without asking in exactly one
case:

> one candidate, at `exact` confidence.

Everything else is a question put to the operator. With no terminal and no
`--yes`, the pane is skipped and the summary says so. A pane left unfed is
fed by the next run; a pane fed the wrong id is not undone by anything.

## Discovery

The runner takes every file in `adapters/` that

- is a regular file, and
- is executable by the running user, and
- does not begin with `.`, and
- does not end in `.md`,

sorted by name. A README, an editor backup and a subdirectory are ignored.

An adapter whose probe fails in any way is **skipped with a warning naming
it**, and the rest of the run continues. A broken adapter is a broken
adapter, not a broken run — but it is never silent, because it means an
agent's panes will go unfed.

## `adapters/<name> probe`

Called with one argument, `probe`. Prints one JSON object on stdout and exits
0. It must not write to anything and must not need a running Herdr.

<!-- contract: probe -->
```json
{
  "agent": "claude",
  "source": "herdr:claude",
  "available": true,
  "confidence": "exact",
  "unverified": false
}
```

| Key | Required | Meaning |
|---|---|---|
| `agent` | yes | Herdr's own agent name. Matched against the `agent` field of each entry in `herdr agent list`. |
| `source` | yes | Herdr's report source, sent back as `params.source`. It is not always `herdr:<agent>`, so it is declared rather than derived. |
| `available` | yes | `false` when this agent leaves no readable session state on this host. The runner then skips the adapter **silently** — that is the adapter working correctly. |
| `confidence` | yes | `exact` or `heuristic`. Any other value is a skip. See below. |
| `unverified` | no | `true` if the adapter has never been confirmed against a real installation. Defaults to `false`. The runner uses it and warns, naming the adapter. |
| `command` | no | The argv0 the pane's foreground process is matched against, when it is not the agent name. Defaults to `agent`. |

### `exact` versus `heuristic`

This is a claim about **what the adapter can tie a session to**, not about how
confident it feels.

- **`exact`** — the adapter can tie a session to a *specific process*. Claude
  Code qualifies because it writes a session file named after the process id,
  so the pane's own pid identifies the session and no other pane can share
  it.
- **`heuristic`** — the adapter can only match on a *directory*. Two panes
  open in the same repository look identical to it, and one of them is
  certainly wrong. Codex, opencode and Copilot CLI are all in this class.

`confidence` appears in two places and means the same thing in both: on the
probe it describes what the adapter can do at all; on each candidate it
describes that particular match. The runner reads **both** when deciding, and
reports unasked only when they agree on `exact` — so a `heuristic` adapter
may not promote a match to `exact` just because it found only one. The probe
is the ceiling; a candidate can be less certain than the adapter, never more.

A candidate with no `session_id`, or with none of the two documented
confidences, is dropped. It cannot be reported, so as a prompt option it does
nothing and as a lone `exact` candidate it would be reported unasked.

## `adapters/<name> resolve`

Called with one argument, `resolve`. Reads one JSON object on stdin, prints
one JSON object on stdout, exits 0.

Every pane the runner found for this agent arrives at once:

<!-- contract: resolve-request -->
```json
{
  "panes": [
    {
      "pane_id": "w2:p2",
      "cwd": "/path/to/checkout",
      "pid": 38080,
      "pid_start_epoch": 1788500000
    }
  ]
}
```

`cwd` is the agent process's own working directory. `pid` is the pane's
foreground agent process, picked out of the pane's foreground processes by
`argv0` — a pane usually holds more than one, since a child of the agent is
foreground too.

### `pid_start_epoch` is epoch seconds, UTC

**This is the one thing in this document most likely to cost you an
afternoon.** Read it before comparing a start time to anything.

`pid_start_epoch` is when the agent process started, as **integer seconds
since the Unix epoch** — an absolute instant, the number `time.time()`
returns. It exists for one job: to stop a **recycled pid** from matching a
stale session file. An `exact` adapter identifies a session by process id,
and a process id that has been handed out again points at somebody else's
session.

Herdr does not report a start time at all, so the runner reads it from the
operating system with `ps -o lstart=`, which prints **local** wall-clock time,
and converts it to epoch seconds in the local frame.

The trap is on your side. An agent that records its own process start
normally does so as a **wall-clock string with no zone marker**, and the zone
is not necessarily local. Claude Code's `~/.claude/sessions/<pid>.json`
records `procStart` in **UTC**. On a host at UTC+2 the two describe the same
instant two hours apart:

```
ps -o lstart=            Fri Sep  4 10:59:46 2026     (local)
Claude Code procStart    Fri Sep  4 08:59:46 2026     (UTC)
```

An adapter that parses `procStart` as local time and compares it to
`pid_start_epoch` rejects **every** session on such a host, and accepts
everything on a host that happens to run in UTC — which is the worst possible
combination, because it works on the machine you test it on. Parse a UTC
string with `calendar.timegm`, not `time.mktime`, and compare with a second
or two of slack, since the sources round differently.

`pid_start_epoch` may be `null` when the process has already exited or the
start time could not be read. Treat `null` as "cannot rule out pid reuse" —
do not treat it as a match and do not treat it as a mismatch. `pid` may be
`null` too.

The answer is one entry per pane, candidates best first:

<!-- contract: resolve-response -->
```json
{
  "results": [
    {
      "pane_id": "w2:p2",
      "candidates": [
        {
          "session_id": "0260f4e1-1c9a-4a77-9f0e-3a7c2d5b8e10",
          "label": "frank",
          "updated": "2026-09-04T09:12:00Z",
          "confidence": "exact",
          "session_path": "/path/to/transcript.jsonl"
        }
      ]
    }
  ]
}
```

| Key | Required | Meaning |
|---|---|---|
| `session_id` | yes | What Herdr resumes with. A candidate without one is dropped. |
| `confidence` | yes | `exact` or `heuristic`, for **this** match. |
| `label` | no | A short human name for the prompt — a repository, a title. Falls back to the session id. |
| `updated` | no | When the session was last touched, shown at the prompt to help the operator choose. |
| `session_path` | no | The transcript file, if there is one. Sent on as `params.agent_session_path`, and omitted entirely when the adapter does not supply it. |

**Order matters.** `--yes` takes the first candidate, so best first is not a
stylistic preference. Newest-first is the usual ordering for a directory
match.

Answering about fewer panes than were asked about is fine: a pane the adapter
said nothing about is treated the same as one it had no candidates for. An
empty `candidates` list is a perfectly good answer, and the pane is skipped
with a note.

## Failing

An adapter that cannot answer should **exit non-zero**, or print nothing.
Both are read as "this adapter contributed nothing", warned about, and the
run carries on with the others. Do not print an empty `results` list to
paper over an error: that is indistinguishable from "there are no sessions
here", and the difference is the whole reason the runner is careful.

Neither subcommand may block waiting for input. `probe` is given 10 seconds
and `resolve` 30; past that the adapter is skipped.

## The `sessions` query

A third, **optional** question, unrelated to `resolve`: not "which session is
this pane in", but "what has this agent worked on recently, and on what
branches". `herdr-setup audit` is the only caller. An adapter that has
nothing to say about it simply does not declare it.

### Opt-in

An adapter declares `"sessions": true` in its `probe` output:

<!-- contract: sessions-probe -->
```json
{
  "agent": "claude",
  "source": "herdr:claude",
  "available": true,
  "confidence": "exact",
  "sessions": true
}
```

Absent means false, and the runner never calls `sessions` on that adapter.
`validate_probe` rejects a non-boolean value.

### `adapters/<name> sessions --since <days> [--include-sdk]`

Prints one JSON object on stdout and exits 0:

<!-- contract: sessions-response -->
```json
{
  "sessions": [
    {
      "id": "00000000-0000-4000-8000-000000000001",
      "cwd": "/work/alpha",
      "last_active": "2026-09-12T18:04:11Z",
      "title": "add the health endpoint",
      "branches": [
        {"name": "feat/health-endpoint", "dir": "/work/alpha",
         "evidence": "command", "seen_at": "2026-09-12T17:58:02Z"}
      ]
    }
  ]
}
```

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | The session id Herdr knows the session by, the value `agent_session.value` carries. |
| `cwd` | yes | The session's working directory, the latest one known. |
| `last_active` | yes | ISO 8601 UTC, second precision, `Z`. |
| `title` | no | A short human name. |
| `branches` | yes | May be empty. At most one entry per (`name`, `dir`), keeping the latest `seen_at`. |
| `branches[].name` | yes | A branch name. |
| `branches[].dir` | yes | Where the branch lives when known (a worktree path), otherwise the session `cwd`. |
| `branches[].evidence` | yes | `session-meta`, `git-branch-field`, `command` or `worktree-path`. |
| `branches[].seen_at` | yes | ISO 8601 UTC, second precision, `Z`: when that evidence was recorded. |

Rules:

- **Read-only, and local.** No network, no git, no `gh`, no Herdr socket, no
  writes. Existence and merge state are the runner's job, so an adapter
  reports every plausible name and does not try to be clever about which
  ones are real.
- **Bounded.** Only history touched within `--since` days, by file
  modification time.
- **Tolerant.** A truncated or in-progress file, an unparseable line, or an
  unreadable file is skipped. It is never fatal to the query.
- **`--include-sdk`** asks for sessions that an automated caller drove rather
  than a person. An adapter must accept the flag, and one that cannot tell
  the difference ignores it.
- **Timestamps.** An adapter converts its own recorded time into the exact
  shape the key table names -- second-precision UTC, `Z` -- itself, before
  printing it: a numeric offset (`+02:00`) is converted to UTC and a
  fractional-second suffix is dropped. A recorded time with NO zone marker
  at all is read as UTC. A date with no time component is not a timestamp
  at all and is treated the same as anything else unusable (see Tolerant).
  The runner (`lib/feed.py`'s `TIMESTAMP_RE`) drops anything that does not
  already match exactly, rather than doing this conversion on an adapter's
  behalf.
- **Branch names that cannot be real work branches are dropped:**
  - the empty name, `main`, `master`, `HEAD`, and `worktree-agent-*`;
  - anything containing `$`;
  - anything starting with `-`;
  - anything git's ref-name rules reject: whitespace or control characters,
    any of `~^:?*[\`, `..`, `@{`, `//`, a leading or trailing `/`, a trailing
    `.`, a component ending in `.lock`, a component starting with `.`, or the
    single character `@`;
  - template text: anything containing `<>{}|;&()'"` or a backquote.

  The runner applies the same filter again (`lib/feed.py`'s `branch_name_ok`),
  so an adapter that forgets cannot inject noise.
- **Failing** is as for `resolve`: exit non-zero, or print nothing. An empty
  `sessions` list means "no sessions in the window", never "could not read".
  Timeout: 120 seconds. A malformed SESSION or a malformed BRANCH within an
  otherwise-good answer is tolerated -- each is dropped and counted
  SEPARATELY, never fatal on its own -- but neither count is silently
  swallowed: the runner (`lib/feed.py`'s `sessions()`) warns by the
  adapter's name and both counts whenever either is non-zero, and raises
  rather than returning an empty list when EVERY session was malformed: an
  empty list has to mean "no sessions", and an adapter that answered with
  nothing but garbage must not be indistinguishable from one that genuinely
  had nothing to say. A session that survives whole but loses every branch
  to a malformed `seen_at` is NOT this case -- "no branches" is itself a
  valid answer for a session, so a non-zero dropped-BRANCH count alone never
  raises, only warns.

### Claude Code

- **Store.** `<config>/projects/*/*.jsonl`, where `<config>` is
  `$CLAUDE_CONFIG_DIR`, else `~/.claude`. That glob covers top-level
  transcripts only: subagent transcripts live one level deeper, in
  `<session-id>/subagents/`. A line with `isSidechain: true` is skipped as
  well.
- **Identity.** The id is the file stem, which is what `claude --resume`
  takes. `cwd` is the last `cwd` field in the file. `last_active` is the
  last `timestamp`, falling back to the file's modification time.
- **Title.** The latest `customTitle` (a `custom-title` record), else the
  latest `aiTitle` (an `ai-title` record), else omitted.
- **SDK sessions.** A transcript whose `entrypoint` starts with `sdk-`
  (`sdk-cli`, `sdk-py`) is skipped unless `--include-sdk`.
- **Evidence `git-branch-field`.** Each line's `gitBranch`, with that line's
  `cwd` as `dir`. It mostly reads `main` and is filtered out. It is kept for
  the sessions that did start on a branch.
- **Evidence `command`.** Every `tool_use` block named `Bash`, from its
  `input.command`.
  - **Parsing.** Backslash-newline continuations are joined first; the
    command is then split into physical lines, since `shlex` treats a bare
    newline as ordinary whitespace and would otherwise fuse two independent
    lines into one nonsensical segment. A heredoc's BODY is skipped
    entirely: every line after a `<<TAG`, `<<-TAG`, `<<'TAG'` or `<<"TAG"`
    marker, up to and including the line matching `TAG` exactly (leading
    tabs stripped first for the `<<-` form), is data, not commands. A
    marker whose terminator never arrives skips to the end of the command.
    Each remaining physical line is tokenised with `shlex` in POSIX mode
    with punctuation characters, then split into segments at `&&`, `||`,
    `;`, `|` and `&` (a background job runs in the same directory, so it is
    a plain separator too). `(` and `)` are not simple separators: they
    give the subshell between them its own directory SCOPE, so a `cd`
    inside `(...)` never leaks past the matching `)`. A line `shlex` cannot
    parse falls back to whitespace splitting.
  - **Directory.** `cd <dir>` sets the CURRENT directory for the segments
    after it, carried across physical lines within the same command (but
    never past a `)` that closed the subshell it happened inside); `git -C
    <dir>` and `git --work-tree=<dir>` set it for their own segment only. A
    relative path -- for `cd`, `-C`, `--work-tree=`, `--repo`, or a
    `worktree add` path -- resolves against the CURRENT directory (the
    latest `cd` already seen in this command, else the line's own `cwd`),
    and `~` resolves against `$HOME`, as text only. `cd -` and a bare `cd`
    leave the current directory unknown rather than inventing a path,
    falling back to the line's own `cwd`.
  - **Shapes read:**
    - `fr isolation up|attach ... --branch <b>` (or `--branch=<b>`), with
      `--repo <path>` as `dir` when present;
    - `git checkout|switch -b|-c|-B <b>`, with other flags before it
      skipped, and `git`'s own global options before the subcommand skipped
      -- `-c <k>=<v>`, `-C <dir>` (sets `dir`), `--work-tree=<dir>` (sets
      `dir`), `--git-dir=<dir>` (does not -- it names the metadata store,
      not a working tree), `--no-pager`, `-P`/`--paginate`, `-p`,
      `--no-replace-objects`;
    - `git worktree add <path> ... -b|-B <b>`, flags -- including
      `--reason <string>`, whose value is skipped too -- in any order, with
      `<path>` as `dir`;
    - `git push ... -u|--set-upstream <remote> <refspec>`, with other option
      tokens (and the value of one that takes one) skipped before
      `<remote>`/`<refspec>`: the source side of the refspec, minus a
      leading `+` and a leading `refs/heads/`. A push carrying `-d` or
      `--delete` yields nothing -- deleting a branch is not evidence of
      work on it;
    - `gh pr create ... --head|-H <b>`, minus an `owner:` prefix.
- **Evidence `worktree-path`.** An fr worktree path,
  `.../.cache/fr/worktrees/<repo>/<slug>`, or the same shape under `~/`,
  `$HOME/` or `${HOME}/` (all three expanded against `$HOME`). The branch
  is the slug with `__` turned back into `/`, and `dir` is the worktree
  path up to and including the slug; a slug immediately followed by `;`,
  `&`, `|`, `(`, `)`, `<` or `>` does not swallow it into the branch name.
  - **Where it is read.** Only in `cwd` fields and in Bash command text,
    never in tool output. `fr isolation status` output lists every worktree
    on the host and would attribute all of them to whichever session ran it.

### Codex

- **Store.** `<config>/sessions/*/*/*/rollout-*.jsonl`, where `<config>` is
  `$CODEX_HOME`, else `~/.codex`. Only files modified within the window are
  read, and only their first line.
- **The first line** must be a `session_meta` record. The id is
  `payload.id`, `cwd` is `payload.cwd`, and `last_active` is the file's
  modification time.
- **Evidence `session-meta`.** `payload.git.branch`, with `seen_at` set to
  `payload.timestamp`. A rollout with no `git` block, or a null or empty
  branch (a detached HEAD), yields a session with no branches.
- **Sub-threads.** A rollout whose `payload.parent_thread_id` is set
  (subagent and guardian threads) is skipped. It is Codex's counterpart of a
  Claude subagent transcript.
- **Version 1 limits.** No command parsing. `--include-sdk` is accepted and
  ignored.

### opencode and Copilot CLI

No `sessions` key in `probe`. Unchanged otherwise.

## A worked minimal adapter

A complete, working adapter in POSIX shell. It matches sessions by directory,
which is why it declares `heuristic`. Its session store is a directory of
`<session-id>.txt` files whose first line is the working directory the
session belongs to — a stand-in for whatever real store the agent keeps.

<!-- contract: minimal-adapter -->
```sh
#!/bin/sh
# adapters/example -- a minimal, working adapter.
#
# Two subcommands, JSON on stdout, and no side effects of any kind. It never
# opens the Herdr socket: the runner decides what to report.
set -eu

store="${EXAMPLE_SESSION_DIR:-$HOME/.example/sessions}"

case "${1:-}" in
  probe)
    available=false
    [ -d "$store" ] && available=true
    printf '{"agent":"example","source":"herdr:example",'
    printf '"available":%s,"confidence":"heuristic"}\n' "$available"
    ;;

  resolve)
    # Read the pane payload. This example matches on cwd alone, so it pulls
    # the pane ids and working directories out with sed rather than taking a
    # dependency on a JSON parser. A real adapter in any other language
    # should parse properly.
    payload="$(cat)"
    printf '{"results":['
    first=1
    echo "$payload" \
      | tr '{' '\n' \
      | sed -n 's/.*"pane_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1 \2/p' \
      | while read -r pane_id cwd; do
          [ "$first" -eq 1 ] || printf ','
          first=0
          printf '{"pane_id":"%s","candidates":[' "$pane_id"
          sep=""
          for file in "$store"/*.txt; do
            [ -f "$file" ] || continue
            [ "$(head -n1 "$file")" = "$cwd" ] || continue
            id="$(basename "$file" .txt)"
            printf '%s{"session_id":"%s","label":"%s","confidence":"heuristic"}' \
              "$sep" "$id" "$id"
            sep=","
          done
          printf ']}'
        done
    printf ']}\n'
    ;;

  *)
    echo "usage: $0 probe|resolve" >&2
    exit 2
    ;;
esac
```

Drop that in `adapters/`, `chmod +x` it, and `herdr-setup feed` picks it up.

## Checklist for a new adapter

1. `probe` prints all four required keys and exits 0 on a host where the
   agent is **absent**, with `available: false`.
2. `confidence` is honest: `exact` only if a session is tied to a process.
3. `resolve` orders candidates best first.
4. Nothing is written, and the Herdr socket is never touched.
5. Neither subcommand prompts or blocks.
6. Add a test beside the others in `tests/`, driving it against a fixture
   session store in a temporary directory. No test may touch a real home
   directory.
7. If the adapter opts into `sessions`, its own branch-name filter agrees
   with `lib/feed.py`'s `branch_name_ok` (the runner re-applies it
   regardless), and a test proves the two never drift apart.
