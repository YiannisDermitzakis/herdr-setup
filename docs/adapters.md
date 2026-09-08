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
describes that particular match. The runner reads the **candidate's** value
when deciding, so a `heuristic` adapter may not promote a match to `exact`
just because it found only one.

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

`pid` is the pane's foreground process — the agent itself — and
`pid_start_epoch` is when it started, which is what stops a recycled pid from
matching a stale session file. Either may be `null` if Herdr did not report
it.

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
