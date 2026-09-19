# herdr-setup audit: fixes from the live-host Test Plan

**Date:** 2026-09-14
**Status:** approved (operator, after the post-merge Test Plan of #5)
**Amends:** `docs/superpowers/implemented/specs/2026-09-13-session-audit-design.md`

## Problem

The post-merge Test Plan ran `herdr-setup audit` against 30 live Herdr panes, 6 GitHub owners and about
800 MB of Claude Code history. Every read-only guarantee held (refs and status unchanged in all 13
audited repositories, a bad token exiting 2 with no table). But four defects made the report slow,
sometimes incomplete, and partly wrong.

1. **A complete run took 8 minutes.** A profile of one full run under host load (about 20 on 16 cores) found:

   | Work | Calls | Time | Share |
   |---|---|---|---|
   | git subprocesses | 1,269 | 331 s | 69% |
   | the Claude adapter's `sessions` query | 1 | 82 s | 17% |
   | `gh` calls | 37 | 57 s | 12% |
   | Python itself | — | 2.6 s | <1% |

   The git layer asks the same questions once per branch directory instead of once per repository:
   - `git rev-parse`, `git worktree list` and two `git remote` calls for each of 288 directories that
     belong to a handful of repositories;
   - one `git for-each-ref` for each of 101 local refs.

2. **The Claude adapter's `sessions` query timed out.** Under the same load it needed 82 s against a
   120 s limit, and one run exceeded it. That run came back incomplete, listing only Codex's sessions.
3. **Collaborator repositories were mislabelled and duplicated in section 3.** GitHub's
   `repositoryOwner(login:).repositories` for a user also returns repositories the user collaborates
   on: 10 of 45 for the operator's account. The audit built each slug as `<queried login>/<name>`, so
   `example-org/example-repo` also appeared as `<user>/example-repo`, and every open PR there was listed twice.
   The mislabelled copy can never match a session branch.
4. **Branch evidence was over-attributed.** A session is credited with a branch whenever its command
   text names another repository's worktree path, or a branch-creating command aimed at another
   repository. The operator's own hand-audit session, which inspected and cleaned up everyone's
   worktrees, was credited with about 20 branches across 8 repositories. That produced all three
   section 2 rows, which were false positives. Unexpanded shell variables also leaked into
   directories, as in `.../example-repo/$W/feat__example-branch`.

## Changes

### 1. Resolve each repository once (`lib/audit.py`)
- Cache directory resolution for the whole run:
  - directory → work tree toplevel;
  - toplevel → `Repo`, which covers the main checkout, the chosen remote and the GitHub slug.

  A directory is resolved at most once, and a work tree's `Repo` is built at most once, however many
  branches point at it. Each clone is its own checkout with its own local refs, so its facts are read
  once per checkout, not once for all clones of one repository. Linked worktrees share their main
  checkout's refs and are read with it. GitHub is also asked once per checkout, and the report
  reconciles clones of one repository afterwards.
- Per checkout, three git reads answer every branch, whatever the branch count:
  - ONE `git for-each-ref --format='%(refname) %(objectname)' refs/heads refs/remotes/<remote>`,
    which answers `local_ref` and the default ref, and names each branch's tip;
  - ONE `git for-each-ref --merged=<default ref> refs/heads`, which answers ancestry;
  - ONE `git cat-file --batch-check`, fed `<oid>^{commit}` for each branch the merged read left
    out. `--merged` skips a tip that names a missing object or a blob without a word, where
    `merge-base --is-ancestor` exited 128; such a branch still raises GitError, for that branch only.
- Behaviour is unchanged, with one accepted limit: a readable tip commit whose PARENT object is
  missing is no longer detected. `merge-base` failed on it; `--merged` reads it as not contained, and
  finding it means walking history, which is `git fsck`'s job.
- Every existing merge-state test still passes. The one that counted per-branch git commands now
  counts the per-checkout reads instead.
- **Proof:** a test counts git subprocesses in a scenario with many branches across two repositories
  and several clones, and asserts the count is bounded by the number of repositories, not the number
  of branches. The same scenario run through the old per-branch layer fails that bound.

### 2. A realistic `sessions` timeout (`lib/feed.py`, contract)
- `SESSIONS_TIMEOUT` goes from 120 s to 600 s. The timeout bounds a hung adapter; the amount of work is
  already bounded by `--since`.
- `docs/adapters.md` and the spec's Failing rule say 600 s and why.
- The probe (10 s) and resolve (30 s) limits are unchanged; they bound `feed`'s interactive path.
- The archived spec's 120 s (`docs/superpowers/implemented/specs/2026-09-13-session-audit-design.md`,
  its Failing rule) is superseded by this amendment. It is left as written, because an archived spec is
  the historical record of what was built then.

### 3. Owned repositories only, named by GitHub (`lib/audit.py`, fake gh)
- `HsOwnerPullRequests` passes `ownerAffiliations: [OWNER]` and selects `nameWithOwner`; each open
  PR's repository is `nameWithOwner`, never `<login>/<name>`.
- Open PRs are deduplicated by (repository, number) as a safety net across owners.
- `HsRepoOpenPullRequests` pagination uses the real owner and name.
- The fake gh models a collaborator repository returned without the affiliation filter (a
  construction, named in its header). A test lists one PR in a collaborator repository once, under
  its real name, and fails before the fix.

### 4. Credit a session only for its own repository (`adapters/claude`, contract)
- **The session's repository.** The adapter has no git, so it names the session's repository from
  the line's `cwd`:
  - a `cwd` inside an fr worktree `.../.cache/fr/worktrees/<repo>/<slug>` belongs to that `<repo>`,
    and to nothing else on its path;
  - any other `cwd` belongs to one of its path components, leaving out fr's own `.cache`, `fr` and
    `worktrees` components and everything below them.

  "The same repository" below means an fr worktree whose `<repo>` is the session's repository.
- **Worktree-path evidence** counts only when:
  - the path is the line's own `cwd` (or contains it); or
  - the path is reached by a `cd` or `-C` in the session's commands, AND it is an fr worktree of the
    same repository.

  fr sessions work through `cd <worktree> && ...` while their recorded `cwd` stays in the base
  clone, so the second case keeps real fr work.
- **Command evidence** (checkout or switch `-b`, worktree add `-b`, push `-u`, `gh pr create --head`,
  `fr isolation up|attach --branch`) counts only when the command's effective directory is:
  - **inside an fr worktree:** an fr worktree of the same repository. Being beneath the `cwd` is not
    enough, because a home-directory session has every fr worktree beneath it;
  - **anywhere else:** the line's `cwd` or beneath it. A branch created in an ordinary checkout beneath
    a non-repository `cwd` still counts (`cwd=/work`, `cd other-repo && git checkout -b p`): it is
    that session's work, and without git the adapter cannot see where one repository ends.

  `gh pr create --repo|-R [HOST/]OWNER/NAME` acts on the repository it names, so it counts only when
  `NAME` is the session's repository; otherwise its evidence is dropped.
- **A directory containing an unexpanded variable** (`$NAME` or `${NAME}`) is not a directory. The
  evidence falls back to the line's `cwd` when that is its own repository, and is dropped otherwise.
  Only a leading `$HOME` or `${HOME}` component is expanded, like `~`; `$HOMEX` and `${HOME_DIR}` are
  other variables.
- **`git-branch-field` evidence** is unchanged; it is always the session's own `cwd`.
- **Known limits:**
  - a subdirectory `cwd` cannot reach its checkout's other directories upward. With `cwd=/work/a/sub`,
    `cd .. && git checkout -b x` is dropped, because `/work/a` is not beneath the `cwd`;
  - a generic path component still matches. A repository named `work` is taken for the session's
    repository when `cwd=/work/a`.
- **Tests:**
  - a session in repository A that inspects, `cd`s into, or creates branches in repository B's
    worktrees gets no B branches;
  - a home-directory session gets no branches from fr worktrees beneath it;
  - the same session working in A's fr worktrees keeps them, by `cd` and by `-C` alone;
  - a branch created in a checkout beneath a non-repository `cwd` counts;
  - a repository name must equal a component, not be part of one, and fr's own path components never
    match;
  - `gh pr create --repo` counts for its own repository only;
  - a `$W` directory is dropped, and so are `$HOMEX` and `${HOME_DIR}`.

  The contract documents the rule.

  **Amended 2026-09-19.** A later fix round widened what the adapter READS, leaving
  untouched which repository a session owns: a shape behind a prefix that merely runs it
  (`timeout`, `env`, `nice`, or an assignment such as `FR_ISOLATION_TARGET=worktree`); a
  repository name whose case differs between GitHub and the checkout folder it was cloned
  into; and a path bound to a shell variable earlier in the same command and reached by
  `cd $W`, which the `$W` limit above had dropped. `docs/adapters.md` carries the current
  rule; the investigation is in
  `docs/superpowers/journals/debug/2026-09-19-audit-session-evidence-extraction.md`.

## Non-goals

- Speeding up the Claude adapter's parsing. The timeout change removes the incompleteness; a faster
  parser is a separate optimisation.
- Parallelising `gh` calls.
- Codex evidence changes: Codex records only its own `git.branch` and `cwd`, so it already follows
  the rule.

## Testing

- The lean loop:
  - targeted test files during red and green;
  - the full gates once;
  - `uv run --group dev shellcheck` for shell;
  - `fr acceptance check`.
- Every fix is proven by a test that fails before it.
- No timing assertions: speed is proven by counting git subprocesses.

## Test Plan

Post-merge, operator-driven, on the same Mac:

1. `herdr-setup audit --json` completes, and `incomplete` is empty.
2. Wall time is well under the previous 8 minutes. Measure it once with `/usr/bin/time -p`, and note
   the host load.
3. The same profile reports git subprocesses in the tens, not about 1,270.
4. Section 3 lists each open PR once, under its real repository.
5. Section 2 no longer lists the hand-audit session's branches from other repositories.
6. Refs and status of every audited repository are unchanged.

## Decisions

| Decision | Reasoning |
|---|---|
| Fix all four together on one branch | Operator decision. They were found by one live run and share its Test Plan. |
| Archive the session-audit plan first (#6) | Operator decision. With no live plan on `main`, `fr isolation up` needs no validator wrapper. |
| No fr plan for this fix; spec plus journal only | A live plan would re-trigger fr's validator requirement for every later workspace. |
| A session's evidence is limited to its own repository | Operator decision. Inspection and dispatch sessions otherwise own everyone's branches. |
| Same-repository fr worktrees reached by `cd` still count | fr work happens through `cd <worktree>` while the recorded `cwd` stays in the base clone; dropping it would lose most real evidence. |
| 600 s `sessions` timeout rather than a faster parser | It fixes incompleteness with a one-line, documented change. Optimisation is a separate job. |
| One `for-each-ref` per repository | It answers every local-ref question at once, and matches the git layer's per-repository cache. |
