# herdr-setup audit and install

**Date:** 2026-09-13
**Status:** approved (batched Q&A, 2026-09-13)

## Problem

An operator who runs many coding-agent sessions across many repositories loses
track of work in three ways. A pane gets closed while its branch is still
unmerged. A session opens a pull request and the pane is reused for something
else. A pull request stays open with nobody working on it.

The facts needed to notice any of this exist, but in four places that do not
know about each other. Herdr knows which panes are open and which session each
one runs. Each agent's own session history knows which branches a session
touched. git knows which refs exist locally. GitHub knows which pull requests
exist and whether they merged.

A hand-run version of this audit on 2026-09-12 joined the four and found
stranded work. It also found every trap this spec designs around: transcripts
whose `gitBranch` field says `main` because the session started in a base clone
and worked in a worktree; squash merges that `git branch --merged` cannot see;
throwaway branch names from tool tests; and search output that carries no head
branch at all.

The tool also has no install step. The README asks the operator to edit `PATH`
by hand.

## Goals

1. `herdr-setup audit`, read-only, reports three sections:
   1. agent sessions open in Herdr, and the branches they work on;
   2. sessions not open in Herdr that left unmerged branches;
   3. open pull requests, across the configured GitHub owners, that match no
      session branch.
2. The adapter contract gains an optional, read-only `sessions` query.
   Claude Code and Codex implement it now.
3. `herdr-setup install` puts the entrypoint on `PATH` by symlink into
   `~/.local/bin`.

## Non-goals

- Acting on a finding. `audit` never closes a pull request, deletes a branch,
  resumes a session, or feeds Herdr.
- Fetching. `audit` never runs `git fetch` or any other git network command.
  Remote state comes from the GitHub API, read-only.
- A configuration file. The owner list is derived per run.
- Session history for opencode and Copilot CLI. Their panes are reported as
  "no session history (unsupported)".
- Pull request data from forges other than GitHub. A branch in such a repository
  gets a merge state from local ancestry only.
- Uninstalling. No `uninstall` subcommand, and `install` never removes a file.

## Commands

### `install`

```
herdr-setup install [--dry-run]
```

Makes `~/.local/bin/herdr-setup` a symlink to this checkout's entrypoint. The
target is the entrypoint's resolved real path (`$HS_ROOT/herdr-setup`, which
already follows symlinks), so installing from an existing link installs the
checkout it points at. It touches no Herdr state and does not run the preflight
gate. It does not need uv.

| Situation | Action | Exit |
|---|---|---|
| `~/.local/bin` missing | `mkdir -p` it, say so, continue | — |
| `~/.local/bin` exists but is not a directory | refuse, one line | 2 |
| link path absent | `ln -s <target> <link>`, print `installed: <link> -> <target>` | 0 |
| link path is a symlink whose target is this entrypoint | `already installed: <link>` and write nothing | 0 |
| link path is a symlink to anything else, dangling included | refuse: name what it points at, say to remove it by hand to switch checkouts | 4 |
| link path is a regular file or a directory | refuse, naming it | 4 |
| `mkdir` or `ln` fails | one line naming the step | 2 |

"This entrypoint" means the link's `readlink` value equals the target, or both
resolve to the same real path.

After the action (or the dry run), two warnings go to stderr. Neither changes the
exit status:

- `~/.local/bin` is not an entry of `PATH` (compared with and without a trailing
  slash): name the directory and say to add it to the shell profile.
- `command -v herdr-setup` resolves to a different file: name it as shadowing
  the link.

`--dry-run` prints `+ mkdir -p <dir>` when it applies, then `+ ln -s <target>
<link>`, and writes nothing. The refusal cases refuse under `--dry-run` too,
with the same exit status, because a dry run says what a real run would do.
Exit 4 keeps its meaning from the existing table: a write was refused.

### `audit` (read-only)

```
herdr-setup audit [--since DAYS] [--owner LOGIN]... [--include-sdk]
                  [--include-bots] [--json]
```

| Flag | Default | Meaning |
|---|---|---|
| `--since DAYS` | 30 | Session history window, passed to each adapter. A positive integer up to 3650. |
| `--owner LOGIN` | the `gh` user plus its orgs | Repeatable. Replaces the default owner list for this run. |
| `--include-sdk` | off | Include sessions an automated caller drove (see the contract). |
| `--include-bots` | off | List bot-authored pull requests in section 3. |
| `--json` | off | Print one JSON object instead of the text tables. |

The global `--dry-run` and `--yes` are accepted and change nothing, since
`audit` makes no writes and asks no questions.

Order of work, each step fail-closed:

1. `hs_require_socket`, first and unconditionally. `audit` needs `herdr agent
   list`, so a mismatched, missing or unreachable server stops the run with the
   existing one-line messages, exit 3.
2. `git` and `gh` on `PATH`, and `gh auth status --hostname github.com --active`
   succeeding. The hostname matters: the unscoped form checks every configured
   host, so a stale login somewhere else fails it. `--active` matters for the
   same reason within github.com: it checks only the account `gh` would use, so
   a stale, inactive second account does not fail it. Otherwise one line naming
   which, exit 2.
3. Gather, join and resolve (see "The audit runner").
4. Print the report.

#### Text output

Shapes only. Every name below is a placeholder.

```
audit: 3 panes, 41 sessions since 2026-08-14, owners: example-user, example-org

open in Herdr
  PANE   TAB    AGENT   SESSION   REPO                      BRANCH                 STATE
  w2:p9  alpha  claude  00000000  example-org/example-repo  feat/health-endpoint   open PR #12 (draft)
  w2:p2  beta   claude  00000000  example-org/example-repo  fix/image-size         unmerged
  w2:p7  beta   codex   -         -                         -                      session not reported to Herdr

closed, with unmerged branches
  AGENT   SESSION      LAST ACTIVE  REPO                      BRANCH       STATE
  claude  00000000 +2  2026-09-10   example-org/example-repo  feat/retry   open PR #7

open PRs no session is working on
  REPO                      PR   BRANCH       AUTHOR        UPDATED     NOTE
  example-org/example-repo  #31  chore/bump   example-user  2026-08-30  draft

not listed: 12 branches gone, 2 unresolved, 4 bot PRs hidden.
```

- **Section 1** has one row per (pane, branch). A pane with no branch evidence
  gets one row whose state column says why:
  - `no branch evidence`;
  - `session not reported to Herdr`, when the pane has no `agent_session`
    value;
  - `session not found in history (outside --since, or SDK-driven)`;
  - `no session history (unsupported)`, when the agent's adapter has no
    `sessions` query;
  - `session history unavailable (adapter failed)`, when the agent's adapter
    declared `sessions` but could not answer this run, or when an adapter of
    that name failed its `probe` (the report is then incomplete);
  - `no adapter`, when no adapter declares that agent.

  Every state is shown except `gone`, which is counted. A pane whose every
  branch is `gone` keeps one row, with the state `only gone branches
  (counted)`, so no pane Herdr lists is missing. `TAB` is the tab label from
  `herdr tab list`. Section 1 is informational.
- **Section 2** has one row per (repository, branch) in state `unmerged` or
  `open-pr`. The repository is the GitHub repository (`owner/name`, compared
  ignoring case) when the branch resolved on github.com, and the main checkout
  only when it did not, so several clones of one GitHub repository are one
  repository. The session is the newest one that touched the branch in any
  clone, and `+N` counts older ones. A branch any open pane touched -- through
  its session's history or its own directory (Sources item 3), in any clone --
  is excluded, because it is not stranded.
  - Merge state is per (repository, branch), but clones can disagree on its
    local half (one holds a commit the other lacks). The report then uses the
    most actionable state among them: `unmerged`, then `open-pr`,
    `unresolved`, `contained`, `merged`, `gone`. Section 1 and the footer
    counts use that same one state, so clones never disagree in the report.
- **Section 3** lists open pull requests in non-archived repositories of the
  owner list. It leaves out any pull request whose head branch equals a
  resolved session branch in the same repository. "Same repository" means the
  base repository or the head repository, so fork pull requests match too. The
  comparison uses every session in the window, open or closed, in any state,
  and every branch an open pane's own directory names (Sources item 3).
  Bot-authored pull requests are hidden and counted unless `--include-bots`.
  Drafts are listed and labelled.
- The footer counts what was deliberately not listed:
  - branches `gone`;
  - branches `unresolved`;
  - bot pull requests hidden.
- A run that did not read every source ends with `incomplete: <reasons>`.

Session ids are shortened to 8 characters in text. JSON carries them whole.
Every heading is always printed; a section with no rows prints `(none)` under
it. Warnings and errors go to stderr, the report to stdout.

#### JSON output

```json
{
  "generated_at": "2026-09-13T12:00:00Z",
  "since_days": 30,
  "owners": ["example-user", "example-org"],
  "open": [{"pane_id": "w2:p9", "tab": "alpha", "agent": "claude",
            "session_id": "...", "note": null,
            "branches": [{"repo": "example-org/example-repo", "name": "feat/x",
                          "state": "open-pr", "pr": {"number": 12, "draft": true, "url": "..."},
                          "evidence": ["command", "worktree-path"], "reason": null}]}],
  "closed_unmerged": [{"repo": "...", "name": "...", "state": "unmerged", "pr": null,
                       "session": {"agent": "claude", "id": "...", "cwd": "...",
                                   "last_active": "...", "title": "..."},
                       "older_sessions": 2}],
  "unmatched_prs": [{"repo": "...", "number": 31, "title": "...", "head": "chore/bump",
                     "author": "...", "bot": false, "draft": true, "updated_at": "...",
                     "url": "..."}],
  "counts": {"gone": 12, "unresolved": 2, "bots_hidden": 4},
  "incomplete": []
}
```

A branch's `reason` is null except for an `unresolved` branch, where it says
why ("counted, with the reason in JSON", under Resolving a branch).

#### Exit codes

| Code | Meaning |
|---|---|
| 0 | Sections 2 and 3 are empty: nothing to act on |
| 1 | Section 2 or section 3 has at least one row |
| 2 | Error, or an incomplete report. An incomplete report is printed and still exits 2. An error the runner did not anticipate is 2 too, with its traceback on stderr: Python's own exit status for it, 1, would read as findings. |
| 3 | Preflight refusal |

2 takes precedence over 1. A report missing a source can overstate section 3,
so it is never read as a clean or ordinary result.

## The `sessions` query

An addition to `docs/adapters.md`. The two existing questions are unchanged.

### Opt-in

An adapter declares `"sessions": true` in its `probe` output. Absent means
false, and the runner never calls `sessions` on it. `validate_probe` rejects a
non-boolean value.

### `adapters/<name> sessions --since <days> [--include-sdk]`

Prints one JSON object on stdout and exits 0:

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
  writes. Existence and merge state are the runner's job, so an adapter reports
  every plausible name and does not try to be clever about which ones are real.
- **Bounded.** Only history touched within `--since` days, by file modification
  time.
- **Tolerant.** A truncated or in-progress file, an unparseable line, or an
  unreadable file is skipped. It is never fatal to the query.
- **`--include-sdk`** asks for sessions that an automated caller drove rather
  than a person. An adapter must accept the flag, and one that cannot tell the
  difference ignores it.
- **Timestamps.** An adapter converts its own recorded time into the exact
  shape the key table names -- second-precision UTC, `Z` -- itself, before
  printing it: a numeric offset is converted to UTC and a fractional-second
  suffix is dropped. A recorded time with NO zone marker at all is read as
  UTC. A date with no time component is not a timestamp at all and is
  treated the same as anything else unusable (see Tolerant). The runner
  drops anything that does not already match exactly.
- **Branch names that cannot be real work branches are dropped:**
  - the empty name, `main`, `master`, `HEAD`, and `worktree-agent-*`;
  - anything containing `$`;
  - anything starting with `-`;
  - anything git's ref-name rules reject: whitespace or control characters, any
    of `~^:?*[\`, `..`, `@{`, `//`, a leading or trailing `/`, a trailing `.`,
    a component ending in `.lock`, a component starting with `.`, or the single
    character `@`;
  - template text: anything containing `<>{}|;&()'"` or a backquote.

  The runner applies the same filter again, so an adapter that forgets cannot
  inject noise.
- **Failing** is as for `resolve`: exit non-zero, or print nothing. An empty
  `sessions` list means "no sessions in the window", never "could not read".
  Timeout: 120 seconds. A malformed SESSION or a malformed BRANCH within an
  otherwise-good answer is tolerated -- each is dropped and counted
  SEPARATELY, never fatal on its own -- but neither count is silently
  swallowed: the runner warns by the adapter's name and both counts
  whenever either is non-zero, and raises rather than returning an empty
  list when EVERY session was malformed. A session that survives whole but
  loses every branch to a malformed `seen_at` is NOT this case -- "no
  branches" is itself a valid answer for a session, so a non-zero
  dropped-BRANCH count alone never raises, only warns.

### Claude Code

- **Store.** `<config>/projects/*/*.jsonl`, where `<config>` is
  `$CLAUDE_CONFIG_DIR`, else `~/.claude`. That glob covers top-level transcripts
  only: subagent transcripts live one level deeper, in `<session-id>/subagents/`.
  A line with `isSidechain: true` is skipped as well.
- **Identity.** The id is the file stem, which is what `claude --resume` takes.
  `cwd` is the last `cwd` field in the file. `last_active` is the last
  `timestamp`, falling back to the file's modification time.
- **Title.** The latest `customTitle` (a `custom-title` record), else the latest
  `aiTitle` (an `ai-title` record), else omitted.
- **SDK sessions.** A transcript whose `entrypoint` starts with `sdk-` (`sdk-cli`,
  `sdk-py`) is skipped unless `--include-sdk`. On the host this was designed on,
  those are about three quarters of the newest transcripts. The decision uses
  the FIRST line that carries an `entrypoint` at all, not the last -- it
  records how the session started, and a later line changing it (a resumed
  or forked session) does not retroactively include or exclude it.
- **Evidence `git-branch-field`.** Each line's `gitBranch`, with that line's
  `cwd` as `dir`. It mostly reads `main` and is filtered out. It is kept for the
  sessions that did start on a branch.
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
  - **Where it is read.** Only in `cwd` fields and in Bash command text, never
    in tool output. `fr isolation status` output lists every worktree on the
    host and would attribute all of them to whichever session ran it.

### Codex

- **Store.** `<config>/sessions/*/*/*/rollout-*.jsonl`, where `<config>` is
  `$CODEX_HOME`, else `~/.codex`. Only files modified within the window are
  read, and only their first line.
- **The first line** must be a `session_meta` record. The id is
  `payload.id`, `cwd` is `payload.cwd`, and `last_active` is the file's
  modification time.
- **Evidence `session-meta`.** `payload.git.branch`, with `seen_at` set to
  `payload.timestamp`. A rollout with no `git` block, or a null or empty branch
  (a detached HEAD), yields a session with no branches.
- **Sub-threads.** A rollout whose `payload.parent_thread_id` is set (subagent
  and guardian threads) is skipped. It is Codex's counterpart of a Claude
  subagent transcript.
- **Version 1 limits.** No command parsing. `--include-sdk` is accepted and
  ignored.

### opencode and Copilot CLI

No `sessions` key in `probe`. Unchanged otherwise.

## The audit runner

`lib/audit.py`, a PEP 723 script reached through a new door, `hs_audit` in
`lib/common.sh`, beside `hs_feed`. Like `feed.py` it is its own program: it
spawns adapters, `git` and `gh`, which none of `hs.py`'s batched helpers
do. It imports `lib/feed.py` from its own directory, so discovery, probing,
probe validation, `herdr_json` and the agent-list reader have one
implementation.

### Sources

1. **Panes.** `herdr agent list` gives, per entry, `pane_id`, `agent`,
   `agent_session.value` (possibly absent), `foreground_cwd`, `cwd` and
   `tab_id`. A pane's directory is `foreground_cwd`, the agent process's own,
   falling back to `cwd` -- `lib/feed.py`'s order. `herdr tab
   list` gives tab labels. Both go through `feed.herdr_json`, so a failure after
   the preflight is exit 2.
2. **Session history.** Every usable adapter declaring `sessions: true` is
   asked once. A failing adapter is warned about by name, and its agent's
   sessions are missing from this run. The report is then marked incomplete.
   An adapter whose `probe` fails is warned about by name the same way and
   also marks the report incomplete: nothing says whether it would have
   declared `sessions`, and its panes would otherwise read `no adapter`.
3. **Pane directories.** A pane whose own `cwd` is an fr worktree path gets that
   branch, by the same `worktree-path` rule. This is how an unsupported agent's
   pane still shows what it works on.

The audit never runs `fr`. Worktree-path evidence is text matching on a path,
not a call to the tool that made it.

### Resolving a branch

For each distinct (`dir`, `name`):

1. **Find the repository.**
   - `dir` if `git -C <dir> rev-parse --show-toplevel` succeeds, else the
     session's `cwd`.
   - If neither is a work tree, the branch is `unresolved`: counted, with the
     reason in JSON.
   - A directory that no longer exists counts as "not a work tree". That is
     the ordinary fate of a removed worktree. So does one where git says it is
     not a git repository, or is a bare one. Any other git failure in a
     directory that exists -- a `safe.directory` refusal, a repository this git
     cannot read -- is an error: exit 2, naming the directory.
2. **Name it.** The chosen remote is `origin`, else the only remote. Its URL
   names `owner/name` when the host is `github.com`, in any letter case:
   https (with or without credentials), `ssh://` (with or without a user and
   a port), or scp-like `[user@]github.com:`, each with or without `.git` and
   a trailing slash. With no such remote the branch still resolves, by
   ancestry only, and has no pull request data. Known limit: an SSH host alias
   such as `git@github-work:owner/name`, which only `~/.ssh/config` maps to
   `github.com`, is not recognised, so such a remote resolves by ancestry only. Every remote-tracking ref below is the
   chosen remote's, `refs/remotes/<remote>/...`, so a clone whose only remote
   is `upstream` is read the same way as one with `origin`.
3. **Deduplicate.** The resolution key is (repository, branch). Several sessions
   and directories naming the same branch in the same repository resolve once.

### Merge state

First match wins:

| State | Condition | Listed |
|---|---|---|
| `merged` | A MERGED pull request in the repository whose head is this branch and whose head repository is the repository | §1 only |
| `open-pr` | An OPEN pull request with the same head rule | §1, §2 |
| `contained` | At least one ref exists, and every ref that exists is reachable from the default branch | §1 only |
| `unmerged` | A local or remote ref exists that is not contained | §1, §2 |
| `gone` | No local ref, no remote ref, and no open or merged pull request | counted |

The pull request check comes first because squash merges defeat ancestry. The
head repository check stops a fork's same-named branch counting as this
repository's merge. A pull request whose `headRepository` is null (its fork was
deleted) cannot show that, so it is not used for `merged` or `open-pr`.

- **The refs examined:**
  - the local `refs/heads/<b>`, if it exists;
  - the GitHub branch `refs/heads/<b>`, if it exists.

  `refs/remotes/<remote>/<b>` is not consulted: it is as stale as the last
  fetch, and GitHub answers directly.
- **The default branch** is GitHub's `defaultBranchRef.name`. For a repository
  with no GitHub remote it is the target of the chosen remote's
  `refs/remotes/<remote>/HEAD`. With neither, the branch is `unresolved`, never
  guessed.
- **Local ancestry** is `git merge-base --is-ancestor refs/heads/<b> <default>`.
  `<default>` is `refs/remotes/<remote>/<default>` (the chosen remote) when that
  exists, else `refs/heads/<default>`. When neither exists locally -- a clone
  from before a default-branch rename, a `--single-branch` clone, a fork clone
  -- a branch that needs local ancestry is `unresolved` with the reason
  `default branch <name> not present locally`, never an error. Otherwise exit 0
  is contained, exit 1 is not, and any other exit is an error: exit 2 for the
  run, naming the repository.
- **Remote ancestry** is GitHub's compare of the default branch against
  `<b>`. `IDENTICAL` or `BEHIND` is contained.
- **A branch cut from the default branch** with no commits of its own is
  `contained`. There is no work on it to strand.

### GitHub queries

Every call is `gh api --hostname github.com` and read-only, so the host
queried is the host `gh auth status --hostname github.com` checked, whatever
`GH_HOST` says. Every GraphQL request has a fixed operation name, so the fake
`gh` can answer by name. Branch names are always
passed as GraphQL variables with `-f` (a raw string), never interpolated into
the query and never with `-F`, which type-infers: a branch named `123` or
`true` would reach GitHub as a number or a boolean.

1. **Owners.** `gh api user --jq .login`, then `gh api --paginate user/orgs`.
   Skipped when `--owner` is given.
2. **`HsOwnerPullRequests($login, $after)`**, per owner.
   - It pages `repositoryOwner.repositories(first: 50, isArchived: false)`.
   - For each repository it asks for `pullRequests(states: OPEN, first: 100)`:
     `number title url headRefName isDraft updatedAt author { login __typename }
     headRepository { nameWithOwner }`, and that connection's `pageInfo {
     hasNextPage endCursor }` -- without it, a repository with more than 100
     open pull requests cannot be told from one with exactly 100.
   - Open pull requests are queried directly, not searched: the hand audit
     showed a recency window misses old open pull requests.
   - A repository with more than 100 open pull requests is paged with
     **`HsRepoOpenPullRequests`**.
3. **`HsRepoBranches($owner, $name, $h0..$hN, $q0..$qN)`**, per repository, in
   chunks of 20 branches.
   - It asks for `defaultBranchRef { name }`.
   - For each branch it asks for `ref(qualifiedName: $qI) { target { oid } }`,
     and for `pullRequests(headRefName: $hI, states: [OPEN, MERGED], first: 20)
     { pageInfo { hasNextPage endCursor } nodes { number state isDraft url
     headRepository { nameWithOwner } } }`.
   - A branch whose pull requests run past that page -- a name like `patch-1`
     shared by many forks' pull requests -- is paged to the end with
     **`HsBranchPullRequests($owner, $name, $head, $after)`**, the same
     selection for that one branch, so its merge state is never decided from a
     truncated list.
4. **`HsCompare($owner, $name, $h0..$hN)`**, per repository, in chunks.
   - It asks `defaultBranchRef { cI: compare(headRef: $hI) { status } }`.
   - Only branches whose GitHub ref exists and whose state no pull request
     decided are asked.

GitHub answers a compare against a missing ref with partial data, a
`NOT_FOUND` entry in `errors`, and `gh` exit 1 (captured 2026-09-13). Because
queries 3 and 4 never ask about a ref that is not known to exist, any `errors`
entry and any non-zero exit is a failure. A failed `gh` call stops the run
before any table is printed: exit 2, one line quoting what `gh` said. The one
exception is an owner login that does not resolve under `--owner`, which is
the same exit 2 with the login named.

### Bots

A pull request is bot-authored when `author.__typename` is `Bot`, or the login
ends in `[bot]`. A deleted author (`author: null`) is not a bot.

## Errors and safety

- **`audit` writes nothing.**
  - No file anywhere, no git ref, no fetch, no Herdr call beyond `agent list`
    and `tab list`, and no GitHub mutation.
  - A test asserts that the fake `gh` saw only `api user`, `api user/orgs`,
    `auth status` and GraphQL queries (never a `mutation`). It also asserts
    that the fixture repositories' refs and status are byte-identical before
    and after.
- **Fail closed:**
  - Herdr unreachable: exit 3.
  - `gh` or `git` missing, `gh` unauthenticated, or any `gh` call failing:
    exit 2, and no report.
  - A git command failing inside a repository that exists: exit 2.
  - An adapter that declared `sessions` but could not answer: the report is
    printed, marked incomplete, and exits 2.

  Nothing unreadable becomes an empty section.
- **`install` only adds.** It never overwrites, never removes, and refuses with
  exit 4 whenever the link path holds anything other than a link to this
  entrypoint.
- **Public repository.**
  - Fixtures follow `tests/test_public_hygiene.sh`: placeholder session ids,
    `/work/...` paths, and the `example-org` and `example-user` names.
  - Captured `gh` responses are sanitised the same way. Their shape is kept
    and their values are replaced.

## Portability

Bash 3.2 for `cmd_install` and `cmd_audit`. Python only through uv, as PEP 723
scripts. `readlink` without `-f`, which BSD lacks: comparing link targets uses
`hs_resolve_root`'s own loop, or `cd -P`. `shlex`, `json`, `subprocess` and
`os.stat` are all standard library. `gh` joins git and uv as a prerequisite,
and only `audit` needs it.

## Documentation

- **README.**
  - The Install section becomes `git clone` then `./herdr-setup install`.
  - Commands gains `install` and `audit`.
  - The exit-code table gains `audit`'s 1 (findings) and `install`'s 4
    (refused).
  - Prerequisites gains `gh`, needed by `audit` only.
- **`docs/adapters.md`** gains the `sessions` section above, with its marked
  examples.
- **AGENTS.md** says `hs_py` is the only sanctioned door to the interpreter, but
  `hs_feed` already exists beside it. The sentence names the three doors
  (`hs_py`, `hs_feed`, `hs_audit`) and the rule that decides between them: batched
  helpers go behind `hs_py`, and a program that spawns other programs gets a
  door of its own.
- **The entrypoint's usage text** lists both new commands.

## Testing

- **The fake `herdr`** gains `tab list` fixtures. No new switches are needed:
  its existing failure switches already cover `agent list`.
- **A fake `gh`** lives at `tests/helpers/fake-gh`, with a `gh` symlink beside
  the `herdr` one, so no test can reach GitHub. It is a PEP 723 script.
  - **State.** It answers from a state file named by `FAKE_GH_STATE`: owners,
    orgs, repositories, refs, compare results, and pull requests.
  - **Answering.** It answers by operation name and pages for real, with the
    page size set by `FAKE_GH_PAGE_SIZE`. It type-infers `-F` values the way the
    real `gh` does, so a regression to `-F` fails on a branch named `123`.
  - **Missing refs.** For a compare against a missing ref it reproduces the
    captured shape: partial data, a `NOT_FOUND` error, exit 1.
  - **Failure switches:**
    - `FAKE_GH_FAIL=<argv prefix>`: exit 1 with a plain stderr message;
    - `FAKE_GH_UNAUTH=1`: `auth status` fails;
    - `FAKE_GH_GRAPHQL_ERRORS=1`: data plus an `errors` array, exiting 0, the
      shape a caller that checks only the exit status gets wrong;
    - `FAKE_GH_LOG`: logs argv.
  - **Capture checks.** Its response shapes are checked against sanitised
    captures in `tests/fixtures/gh/`.
- **Environment.** `tests/run.sh` and `feedlib.LEAKY_VARS` clear the new
  `FAKE_GH_*` variables.
- **Adapter `sessions`** is tested against fixture stores built in temporary
  directories, from sanitised captured line shapes
  (`tests/fixtures/claude/transcript.jsonl`, the Codex `session-meta` files).
  Shapes not yet seen on a host are named and covered as constructions:
  - **Claude:**
    - a truncated last line, and an unparseable line;
    - a session whose only evidence is a worktree path;
    - each command shape, including `cd` and `git -C` directories and a
      `shlex` failure;
    - a subagent directory, and a sidechain line;
    - an `sdk-cli` entrypoint, with and without `--include-sdk`;
    - a file outside the window;
    - every filtered name form.
  - **Codex:**
    - a rollout with no `git` block, and a null branch (detached HEAD);
    - a sub-thread;
    - an empty or truncated first line;
    - a rollout outside the window.
- **Merge state** uses real git repositories in temporary directories, with
  remotes set to `example-org` URLs and git identity set by environment. The
  cases, each tested against the fake `gh`:
  - squash-merged (merged pull request, no ancestry);
  - merge-commit merged without a pull request (ancestry);
  - a fresh branch;
  - a local-only unpushed branch;
  - a remote-only branch, ahead and behind;
  - a deleted branch (`gone`);
  - a fork pull request with the same head name;
  - a branch named `123`;
  - a missing directory falling back to the session `cwd`;
  - no work tree at all (`unresolved`).
- **Runner:**
  - the join, and every section 1 note;
  - section 2 exclusions and `+N`;
  - section 3 matching (base and head repository), with bots hidden and shown,
    and drafts labelled;
  - JSON shape;
  - each exit status: incomplete, `gh` failure, git failure, Herdr failure
    after the preflight;
  - nothing written.
- **Contract.** `validate_probe`'s wrong-type test gains `sessions`.
  `docs/adapters.md` gains marked examples `sessions-probe` and
  `sessions-response`. They are extracted and validated through the runner's
  own parser. A conformance pass checks that every adapter declaring `sessions`
  answers `{"sessions": []}` on an empty home, and on a present but empty
  store.
- **Entrypoint.**
  - `tests/test_audit_entrypoint.sh` covers dispatch, arguments reaching
    `audit.py`, the preflight gate (exit 3 under a mismatch, before any `gh`
    call), and `--dry-run` being accepted.
  - `tests/test_install.sh` covers every row of the `install` table, the `PATH`
    and shadowing warnings, and `--dry-run`, under the bash 3.2 floor in CI.
- **Proof first.** New tests are seen to fail before the code that makes them
  pass. A guard is proven by a fake that can fail.

## Test Plan

Post-merge, operator-driven, on the one Mac that runs Herdr 0.9.0 with live
Claude Code and Codex panes, where the 2026-09-12 hand audit was done.

1. `herdr-setup install --dry-run` prints the `ln -s` it would run and changes
   nothing. `herdr-setup install` creates the link. A second run says it is
   already installed and exits 0, and `command -v herdr-setup` resolves to the
   link. `HOME="$(mktemp -d)"`, with a decoy file at
   `$HOME/.local/bin/herdr-setup`, refuses with exit 4 and leaves the decoy
   byte-identical.
2. Record `git -C <repo> for-each-ref` and `git status --porcelain` for three
   audited repositories. Run `herdr-setup audit`.
3. Section 1 has a row for every Claude Code and Codex pane that `herdr agent
   list` reports, and each pane's branches are the ones the operator knows it
   works on.
4. Redo the hand audit the same day by the 2026-09-12 method. Every stranded
   branch and every unmatched open pull request it finds appears in section 2
   or 3. No squash-merged branch is listed. Section 3 shows no bot pull
   requests, and its drafts are labelled.
5. `herdr-setup audit --json` parses, and its counts equal the text run's. The
   exit status is 1 when section 2 or 3 has rows, else 0.
6. `herdr-setup audit --since 1` lists no more in section 2 than step 2 did.
   `--include-sdk` reports more sessions in its header line.
7. `GH_TOKEN=invalid herdr-setup audit` exits 2 with one line and prints no
   table.
8. The refs and status recorded in step 2 are unchanged.

## Implementation Plans

| Plan | Repo | File | Depends on |
|------|------|------|------------|
| 2026-09-13-session-audit | `YiannisDermitzakis/herdr-setup` | `2026-09-13-session-audit` | — |

## Decisions

| Decision | Reasoning |
|---|---|
| Install is a symlink plus a subcommand | Operator decision. The entrypoint already resolves its root through symlinks, so a link needs nothing else. |
| `install` creates `~/.local/bin` when missing | Operator decision (Q&A). Creating a directory is adding, which the hard rule allows. |
| A symlink pointing elsewhere is refused, exit 4 | It is "a different file". Replacing it would silently switch which checkout runs, which is removal by another name. |
| Sessions come from an optional adapter query (approach A) | Operator decision. Session history is per agent, and the seam already exists for exactly that. |
| Adapters report names; the runner decides existence and merge state | The same split as `feed`, where one place decides. It keeps adapters free of git and network, so they stay fast and testable. |
| Owners default to the `gh` user plus its orgs, and `--owner` replaces them | Operator decision. No configuration file to leak or drift. |
| Exit codes mirror `diff`: 0, 1, 2, with 3 for the preflight | Operator decision (Q&A). Section 1 is informational. An incomplete report is 2, because a missing source can overstate section 3. |
| Bot pull requests are hidden and counted; drafts are kept | Operator decision (Q&A). Bots never have a session. fr-goal opens every pull request as a draft. |
| Merge state checks pull requests by head first, then ancestry | Squash merges defeat ancestry, a fact the hand audit established. |
| No `git fetch`; remote refs come from GitHub | A read-only command must not update refs. GitHub answers without a stale remote-tracking copy. |
| Compare is asked only for refs known to exist | GitHub answers a missing ref with partial data and an error. Avoiding that makes "any error is a failure" hold without exceptions. |
| Branch names go in GraphQL variables with `-f` | Interpolation is injection-shaped. `-F` type-infers, and would turn a branch named `123` into a number. |
| `audit` is gated on the preflight like `feed` | It needs `herdr agent list`. The hard rule stops on an unreachable socket. |
| A `gh` failure stops the report; an adapter failure marks it incomplete | Without `gh`, no merge state exists at all. One broken adapter should not hide the others, which is `feed`'s own policy, but the exit status still says the report is partial. |
| The audit does not call `fr` | Operator decision before phase 4. herdr-setup is a public Herdr tool, so the optional `fr isolation status` source and its fake were removed. Worktree-path evidence stays, because it matches text in a path rather than calling `fr`. |
| `worktree-path` evidence is read only from `cwd` fields and command text | Tool output such as `fr isolation status` lists every worktree on the host, and would attribute all of them to one session. |
| SDK-driven Claude sessions are skipped by default | Most transcripts on the design host are automated observer and review runs. |
| Codex sub-threads are skipped | They are Codex's subagent transcripts, which the Claude side skips by layout. |
| `hs_audit` is its own door beside `hs_feed`, importing `feed.py` | `audit.py` spawns adapters, `git` and `gh`, much as `feed.py` spawns adapters. One discovery and probe implementation, not two. |
| `contained` covers a branch with no commits of its own | There is nothing on it to strand, so listing it would be noise. |
