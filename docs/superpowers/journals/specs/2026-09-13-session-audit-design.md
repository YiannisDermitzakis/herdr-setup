# Journal: 2026-09-13-session-audit-design

<!-- fr:journal kind=decision scope=spec id=d-install-shape created=2026-09-13T22:17:52 -->
### d-install-shape · decision · Install is a symlink plus a subcommand

Operator decision (pre-brainstorm): `herdr-setup install` symlinks the entrypoint into ~/.local/bin; the audit is `herdr-setup audit`. Install only adds: refuses to overwrite a different file, never uninstalls, warns when ~/.local/bin is not on PATH.

<!-- fr:journal kind=decision scope=spec id=d-agents-approach-a created=2026-09-13T22:17:55 -->
### d-agents-approach-a · decision · Adapters gain an optional read-only sessions query

Operator decision (pre-brainstorm, approach A): extend the adapter contract with `sessions --since <days>`, opt-in via `"sessions": true` in probe. Implemented for claude and codex; copilot and opencode report 'no session history (unsupported)'.

<!-- fr:journal kind=decision scope=spec id=d-owners created=2026-09-13T22:17:58 -->
### d-owners · decision · GitHub owners default to the gh user plus its orgs

Operator decision (pre-brainstorm): default owners are the authenticated gh user plus `gh api user/orgs`; a repeatable --owner replaces the list for one run. No config file.

<!-- fr:journal kind=decision scope=spec id=d-exit-codes created=2026-09-13T22:18:01 -->
### d-exit-codes · decision · audit exit codes mirror diff

Q&A answer: 0 nothing to act on, 1 when section 2 (closed sessions with unmerged branches) or section 3 (unmatched open PRs) has rows, 2 on error. Section 1 is informational and never sets 1. Preflight refusal stays 3 like every socket command.

<!-- fr:journal kind=decision scope=spec id=d-pr-scope created=2026-09-13T22:18:04 -->
### d-pr-scope · decision · Section 3 hides bot PRs, keeps drafts

Q&A answer: bot-authored PRs (author __typename Bot or login ending [bot]) are hidden and counted, --include-bots shows them. Drafts are listed and labelled, because fr-goal opens every PR as a draft.

<!-- fr:journal kind=decision scope=spec id=d-test-plan-host created=2026-09-13T22:18:06 -->
### d-test-plan-host · decision · Post-merge Test Plan runs on the operator's Herdr Mac only

Q&A answer: install, then audit against live sessions, compared with a hand audit, on the one Mac that runs Herdr with live claude and codex panes. No Linux host in the Test Plan.

<!-- fr:journal kind=decision scope=spec id=d-install-mkdir created=2026-09-13T22:18:09 -->
### d-install-mkdir · decision · install creates ~/.local/bin when missing

Q&A answer: mkdir -p counts as adding, so it is allowed; install says it created the directory and still warns when it is not on PATH. --dry-run prints the mkdir and the ln.

<!-- fr:journal kind=review scope=spec id=r-spec-review-1 created=2026-09-13T22:24:55 -->
### r-spec-review-1 · review · Spec review against Q&A answers and codebase reality

Checked every named helper and shape: hs_resolve_root (entrypoint), feed.herdr_json/_agent_entries/validate_probe, feedlib.LEAKY_VARS, tests/run.sh env clearing, test_public_hygiene.sh, herdr tab list (result.tabs[].label/tab_id), fr isolation status --format json (list of {repo, branch, worktree, sessions[].session_id}), GraphQL repositoryOwner.repositories(isArchived:false), pullRequests(headRefName, states), Ref.compare (partial data + NOT_FOUND + gh exit 1 on a missing ref, captured). All four Q&A answers are encoded. Fixed: (1) gh auth status now scoped to --hostname github.com, since a stale login on another host fails the unscoped form; (2) a PR whose headRepository is null (deleted fork) is not evidence of a merge for this repo; (3) added a Documentation section -- README install/commands/exit table, and AGENTS.md's 'hs_py is the only sanctioned door' sentence, already contradicted by hs_feed, now names the doors; (4) section 1's missing-session note also covers SDK-skipped sessions; (5) testing names the contract wrong-type test for the new sessions key. Acceptance rows added: audit-reports-stranded-work, audit-merge-state-sees-squash-merges, audit-fails-closed, adapter-sessions-query, install-adds-never-overwrites.

<!-- fr:journal kind=decision scope=spec id=d-spec-relative-paths-current-dir created=2026-09-14T06:38:53 -->
### d-spec-relative-paths-current-dir · decision · Relative paths in command evidence resolve against the current directory

Phase 2 review: the spec said relative paths resolve against the line's cwd, which gives the wrong directory after a cd earlier in the same command. The spec now says the latest cd in the command, else the line's cwd.

<!-- fr:journal kind=decision scope=spec id=d-spec-timestamps-second-precision created=2026-09-14T06:38:55 -->
### d-spec-timestamps-second-precision · decision · Session timestamps are second-precision UTC with Z

Phase 2 review: Claude Code writes milliseconds and offsets can appear, while Codex timestamps come from mtimes. Normalising in the adapter and validating in the runner makes cross-adapter comparisons exact.

<!-- fr:journal kind=decision scope=spec id=d-spec-filter-git-ref-rules created=2026-09-14T06:38:56 -->
### d-spec-filter-git-ref-rules · decision · The branch-name filter follows git's ref rules for @ and .lock

Phase 2 review: the filter accepted the single character @ and a component ending in .lock, both of which git rejects. The spec wording is tightened to match.

<!-- fr:journal kind=decision scope=spec id=d-spec-naive-timestamps-utc created=2026-09-14T06:39:11 -->
### d-spec-naive-timestamps-utc · decision · A timestamp with no zone is read as UTC; a date alone is not a timestamp

Phase 2 re-review: both agents write Z, so a zoneless value is most likely UTC; a bare date carries no time and is rejected rather than invented.

<!-- fr:journal kind=decision scope=spec id=d-spec-dropped-branches-counted created=2026-09-14T06:39:13 -->
### d-spec-dropped-branches-counted · decision · Dropped branches are counted and warned, like dropped sessions

Phase 2 re-review: once seen_at became strict, an uncounted drop would turn a format slip into a false clean audit.

<!-- fr:journal kind=decision scope=spec id=d-spec-entrypoint-first-wins created=2026-09-14T06:39:15 -->
### d-spec-entrypoint-first-wins · decision · A transcript's first entrypoint decides the SDK skip

Phase 2 re-review: a resumed session can carry a later, different entrypoint; the first records how the session began, and reading only to it keeps the skip cheap.

<!-- fr:journal kind=decision scope=spec id=d-drop-fr-enrichment created=2026-09-14T11:13:08 -->
### d-drop-fr-enrichment · decision · The audit does not call fr; the fake fr is removed

Operator decision before phase 4, replacing an earlier choice to keep a fake fr. The optional fr isolation status source and its fr-binding evidence are out of scope, so the fake fr, its test and its switches were removed and the test guard covers herdr and gh only. Worktree-path evidence stays: it is text matching on worktree paths, with no fr command involved. herdr-setup is a public Herdr tool, and a runtime dependency on the operator's own workflow CLI did not belong in it.

<!-- fr:journal kind=decision scope=spec id=d-keep-json-output created=2026-09-14T11:13:45 -->
### d-keep-json-output · decision · Phase 4 keeps --json output

Operator decision before phase 4, when --json and the fr enrichment were offered as the optional parts of its scope: --json stays, the fr enrichment goes.

<!-- fr:journal kind=decision scope=spec id=d-spec-repository-identity created=2026-09-14T15:12:34 -->
### d-spec-repository-identity · decision · A repository in the report is its GitHub slug, not a local checkout

Phase 4 review: grouping by checkout listed a branch once per clone and let an open pane in one clone miss closed sessions in another. The slug identifies the repository; the checkout path is the fallback only without a GitHub remote.

<!-- fr:journal kind=decision scope=spec id=d-spec-pane-directory-branches-count created=2026-09-14T15:12:35 -->
### d-spec-pane-directory-branches-count · decision · Branches found from a pane's own directory count as session branches

Phase 4 review: they already fed section 2's exclusion and section 3's matching; the spec now states it, since a pane an unsupported agent runs in is still work in progress.
