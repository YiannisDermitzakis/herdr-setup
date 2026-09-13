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
