# Journal: 2026-09-14-audit-live-host-findings-design

<!-- fr:journal kind=decision scope=spec id=d-fix-all-four-together created=2026-09-14T20:16:28 -->
### d-fix-all-four-together · decision · Fix the four live-host defects on one branch

Operator decision after the post-merge Test Plan of #5: slow git resolution, the tight sessions timeout, collaborator repositories in section 3, and over-attributed branch evidence were found by one live run and share its Test Plan.

<!-- fr:journal kind=decision scope=spec id=d-archive-before-fix created=2026-09-14T20:16:33 -->
### d-archive-before-fix · decision · Archive the session-audit plan before starting the fix

Operator decision. With a live plan on main, fr isolation up refuses every workspace unless scripts/validate-plans.sh is committed, and the operator declined that wrapper. #6 archived the plan so main has none.

<!-- fr:journal kind=decision scope=spec id=d-no-fr-plan created=2026-09-14T20:16:40 -->
### d-no-fr-plan · decision · The fix has a spec and journal but no fr plan

A live plan directory would re-trigger fr's validator requirement for every later workspace in this repository.

<!-- fr:journal kind=decision scope=spec id=d-evidence-own-repository created=2026-09-14T20:16:47 -->
### d-evidence-own-repository · decision · A session is credited only with branches in its own repository

Operator decision. The hand-audit session inspected and cleaned up other repositories' worktrees and was credited with about 20 branches across 8 repositories, which produced every section 2 row.

<!-- fr:journal kind=decision scope=spec id=d-same-repo-worktree-cd-counts created=2026-09-14T20:16:52 -->
### d-same-repo-worktree-cd-counts · decision · Same-repository fr worktrees reached by cd still count

fr sessions work through cd <worktree> && ... while the recorded cwd stays in the base clone; counting only cwd would drop most real fr evidence.

<!-- fr:journal kind=decision scope=spec id=d-sessions-timeout-600 created=2026-09-14T20:16:57 -->
### d-sessions-timeout-600 · decision · The sessions timeout is 600 s rather than a faster parser

Under host load the Claude adapter needed 82 s against a 120 s limit and one run went incomplete. The timeout bounds a hung adapter; --since already bounds the work. Parser speed is a separate optimisation.

<!-- fr:journal kind=decision scope=spec id=d-one-for-each-ref-per-repo created=2026-09-14T20:17:03 -->
### d-one-for-each-ref-per-repo · decision · Local refs are read with one for-each-ref per repository

A profile showed 1,269 git subprocesses, 69% of an 8-minute run, because lookups repeated per branch directory. One call per repository matches the per-repository cache.

<!-- fr:journal kind=discovery scope=spec id=red-fix3-open-prs created=2026-09-14T20:39:28 -->
### red-fix3-open-prs · discovery · RED: open PRs mislabel collaborator repositories and follow-up pages

tests/test_audit_github.py against the unfixed GitHub layer, fake gh extended with a collaborator repository: rc=1, Ran 30, failures=2. test_a_collaborator_repositorys_pull_request_is_listed_once_under_its_real_name listed PR 5 twice, as example-org/example-repo and example-user/example-repo. test_every_record_and_follow_up_page_names_the_repository_as_github_does got EXAMPLE-ORG/example-repo, the login as given, instead of nameWithOwner.

<!-- fr:journal kind=discovery scope=spec id=red-fix3-dedup created=2026-09-14T20:39:33 -->
### red-fix3-dedup · discovery · RED: run() reports an open PR twice when two owners both list it

tests/test_audit_report.py against the unfixed run(): rc=1, Ran 35, failures=1. test_an_open_pull_request_two_owners_both_list_is_reported_once found 4 unmatched_prs rows for 2 pull requests (example-org/example-repo 31 and 32, each twice).

<!-- fr:journal kind=discovery scope=spec id=red-fix1-git-calls created=2026-09-14T20:41:32 -->
### red-fix1-git-calls · discovery · RED: git processes grow with branches, not repositories

tests/test_audit_git_calls.py run against the per-branch git layer of df3a200 (lib/audit.py unchanged in the workspace when the file was imported): rc=1, Ran 3 tests in 814.282s FAILED (failures=3). Failing: test_git_processes_are_bounded_by_repositories_not_branches,test_one_ref_read_per_repository_names_its_heads_and_its_remote,test_twice_the_branches_cost_no_more_git_processes. The scenario (4 checkouts of 2 repositories plus a linked worktree, 97 branches named from checkout, subdirectory and removed-worktree directories) cost 808 git processes against a bound of 34: rev-parse 105, remote 210, worktree list 105, for-each-ref 291, merge-base 97. No refs/heads refs/remotes/origin read existed (0 of 4).

<!-- fr:journal kind=discovery scope=spec id=red-fix4-evidence-scope created=2026-09-14T20:46:00 -->
### red-fix4-evidence-scope · discovery · RED: Claude adapter credits sessions with other repositories' branches

tests/test_adapter_claude_sessions.py against the unfixed adapter: rc=1, Ran 110, failures=4, all in TestEvidenceIsLimitedToTheSessionsOwnRepository: test_another_repositorys_worktrees_and_checkout_credit_nothing, test_a_home_directory_session_is_credited_with_no_ones_worktrees, test_its_own_fr_worktrees_reached_by_cd_or_dash_cap_c_still_count (the other repository's worktree, cd'd into, was credited too), test_a_directory_holding_an_unexpanded_variable_is_not_a_directory. test_commands_in_its_own_checkout_and_beneath_it_still_count passed, as it should before and after.

<!-- fr:journal kind=discovery scope=spec id=red-fix2-sessions-timeout created=2026-09-14T20:50:47 -->
### red-fix2-sessions-timeout · discovery · RED: the documented and runner sessions timeout is 120 s, not 600 s

tests/test_contract.py with the new test_it_states_the_runners_own_sessions_timeout_of_600_seconds: rc=1, Ran 29, failures=1. docs/adapters.md and lib/feed.py agreed on 120, and feed.SESSIONS_TIMEOUT == 600.0 failed with 120.0 != 600.0.

<!-- fr:journal kind=decision scope=spec id=d-ancestry-one-merged-read created=2026-09-14T20:51:07 -->
### d-ancestry-one-merged-read · decision · Ancestry is answered with one for-each-ref --merged per repository

The spec names one for-each-ref for refs. Keeping merge-base --is-ancestor per undecided local branch would still make the git process count grow with branches (the RED scenario ran 97 merge-base calls), which the spec's own proof forbids. for-each-ref --format=%(refname) --merged=<default ref> refs/heads lists exactly the branches merge-base --is-ancestor calls contained, once per (main checkout, default ref). A missing or broken branch ref still raises GitError naming the repository, as merge-base did.

<!-- fr:journal kind=discovery scope=spec id=disc-broken-ref-still-refused-only-when-asked created=2026-09-14T20:51:27 -->
### disc-broken-ref-still-refused-only-when-asked · discovery · One ref read keeps a broken ref an error only for the branch that names it

for-each-ref exits 0 for a ref it cannot read and prints warning: ignoring broken ref <ref> on stderr (checked with git under LC_ALL=C). Reading refs/heads whole would otherwise turn one broken ref into a GitError for every branch of the repository. GitCache records each such warning as broken and raises only when local_ref, default_ref or is_ancestor asks about that ref; any other stderr is still an error. tests/test_audit_git.py test_a_ref_git_cannot_read_is_an_error_not_an_absence and tests/test_audit_merge_state.py test_a_git_failure_inside_the_repository_raises stay green unmodified.

<!-- fr:journal kind=discovery scope=spec id=disc-merge-state-lookup-count-test-changed created=2026-09-14T20:51:36 -->
### disc-merge-state-lookup-count-test-changed · discovery · Changed test: test_one_resolution_per_repository_and_branch_however_many_name_it

tests/test_audit_merge_state.py asserted exactly one 'for-each-ref --format=%(refname) refs/heads/feat/shared' line and one merge-base line for four entries. Those per-branch commands no longer exist, so the assertions could not stay unmodified whatever the implementation. The test now asserts exactly one 'for-each-ref --format=%(refname) refs/heads refs/remotes/origin' read, exactly one --merged ancestry read, and that no git command names refs/heads/feat/shared at all: the same intent (the work is done once), stated for the new layer and stricter. Every other merge-state test is unmodified.

<!-- fr:journal kind=discovery scope=spec id=green-fix1-no-refactor created=2026-09-14T20:51:43 -->
### green-fix1-no-refactor · discovery · GREEN fix 1; no-refactor-because: GitCache is already the refactor

tests/test_audit_git_calls.py rc=0 (Ran 3, OK), tests/test_audit_git.py rc=0 (Ran 20, OK, unmodified), tests/test_audit_merge_state.py rc=0 (Ran 26, OK). no-refactor-because: fix 1 moved the per-question functions into GitCache and left repo_for, local_ref, default_ref, is_ancestor and local_default as thin delegates with their contracts and docstrings; nothing further to clean.

<!-- fr:journal kind=discovery scope=spec id=disc-owner-query-shape-test-changed created=2026-09-14T20:58:13 -->
### disc-owner-query-shape-test-changed · discovery · Changed test: test_hs_owner_pull_requests strips nameWithOwner before the capture comparison

tests/test_audit_github.py compares the answer to the module's HsOwnerPullRequests with the key shape of tests/fixtures/gh/owner-pull-requests.json. The capture selected only name; the module now also selects nameWithOwner (spec change 3), so the shapes cannot be equal. The new helper without_repository_name_with_owner removes that one key after checking it is an owner/name string, exactly as without_nested_page_info already did for the nested pageInfo construction. No live capture was taken (no live runs); nameWithOwner's leaf shape is the captured headRepository.nameWithOwner string.

<!-- fr:journal kind=discovery scope=spec id=disc-adapter-tests-retargeted-to-own-repository created=2026-09-14T20:58:27 -->
### disc-adapter-tests-retargeted-to-own-repository · discovery · Changed tests: adapter evidence tests that relied on cross-repository attribution

tests/test_adapter_claude_sessions.py. Every change keeps the test's own subject and moves its directories inside the line's own repository, never loosening an assertion. (1) Directory tests whose target sat outside the line's cwd /work/alpha now target beneath it -- test_cd_then_and_and_sets_dir_for_the_later_segment, test_git_dash_cap_c_sets_dir_for_its_own_segment_only, test_a_cd_line_does_not_swallow_the_next_lines_command, test_a_single_ampersand_is_a_segment_boundary, test_git_work_tree_equals_sets_dir use /work/alpha/other, /work/alpha/third, /work/alpha/wt. (2) Relative-resolution tests keep their distinguishable bases with a parent cwd -- test_a_second_relative_cd_resolves_against_the_first_cds_result, test_git_dash_cap_c_relative_dir_resolves_against_a_prior_cd, test_fr_isolation_repo_relative_path_resolves_against_a_prior_cd and test_fr_isolation_attach_with_repo_sets_dir use cwd /work; test_worktree_add_relative_path_resolves_against_a_prior_cd uses cwd /work/deep, where ../wt still resolves differently against the cd (/work/deep/wt) and the cwd (/work/wt). (3) Tilde tests test_cd_tilde_expands_to_home and test_git_dash_cap_c_tilde_expands_to_home run with cwd set to HOME. (4) Worktree-path tests read from command text now reach the worktree the way the rule counts, with cd, from a cwd in the same repository /work/example-repo -- test_a_worktree_path_inside_a_command_yields_its_branch is renamed test_a_worktree_path_reached_by_cd_in_a_command_yields_its_branch (cd .../feat__q/src instead of cat), and test_a_slug_followed_by_a_semicolon_does_not_swallow_it, test_a_slug_followed_by_and_and_does_not_swallow_it, test_a_slug_followed_by_a_redirect_does_not_swallow_it (cd .../feat__q>out instead of cat), test_dollar_home_worktree_path_in_a_command_is_expanded and test_dollar_brace_home_worktree_path_in_a_command_is_expanded (cd instead of cat). The old shapes -- naming another repository's worktree with cat, or creating a branch in another directory -- are now asserted to credit nothing, in TestEvidenceIsLimitedToTheSessionsOwnRepository.

<!-- fr:journal kind=discovery scope=spec id=green-fix2-no-refactor created=2026-09-14T21:00:30 -->
### green-fix2-no-refactor · discovery · GREEN fix 2; no-refactor-because: a constant and its documented value

tests/test_contract.py rc=0 (Ran 29, OK) and tests/test_feed_sessions.py rc=0 (Ran 41, OK, its timeout-mechanism test still injects 0.2 s). README.md states no sessions timeout, so it needed no change; the archived 2026-09-13 spec is untouched. no-refactor-because: fix 2 changes one constant, its comment and one sentence of docs/adapters.md.

<!-- fr:journal kind=discovery scope=spec id=green-fix3-no-refactor created=2026-09-14T21:00:46 -->
### green-fix3-no-refactor · discovery · GREEN fix 3; no-refactor-because: the dedup is one small helper beside open_prs

tests/test_audit_github.py rc=0 (Ran 30, OK), tests/test_fake_gh.py rc=0 (Ran 30, OK), tests/test_audit_report.py rc=0 (Ran 35, OK). open_prs reads nameWithOwner for the record and the HsRepoOpenPullRequests owner and name; open_prs_for dedupes by (repo lowercased, number) and run() calls it, so TestRun's staged open_prs is still called once per owner. no-refactor-because: nothing duplicated remains to extract.

<!-- fr:journal kind=discovery scope=spec id=green-fix4-refactor created=2026-09-14T21:02:23 -->
### green-fix4-refactor · discovery · GREEN fix 4; refactor: the scope rule is three helpers, worktree-path reads cwd only

tests/test_adapter_claude_sessions.py rc=0 (Ran 110, OK), TestBranchNameFilterAgreesWithTheRunner included, so the adapter's name-filter copy still agrees with lib/feed.py's. The rule lives in _within, _same_repository_fr_worktree and _in_own_repository; _process_bash_command applies it to every shape and records a cd or git -C target as worktree-path evidence, so extract_worktree_path_evidence no longer scans command text and the shape matchers' return values are unchanged. _resolve_dir now expands a leading $HOME or ${HOME} like ~, matching the prefixes WORKTREE_PATH_RE already accepted; any other variable leaves the directory unknown. Refactor after green: the WORKTREE_PATH_RE comment and docs/adapters.md (Where it is read, Scope, Directory) now state the rule.
