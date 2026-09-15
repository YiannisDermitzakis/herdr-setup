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

The spec names one for-each-ref for refs. Keeping merge-base --is-ancestor per undecided local branch would still make the git process count grow with branches (the RED scenario ran 97 merge-base calls), which the spec's own proof forbids. for-each-ref --format=%(refname) --merged=<default ref> refs/heads lists the branches merge-base --is-ancestor calls contained, once per (main checkout, default ref). A missing or broken branch ref still raises GitError naming the repository. A tip naming a missing object or a blob does NOT fail through --merged, which skips it silently; merge-base exited 128 on it. So the branches the merged read leaves out have their tips checked with one cat-file --batch-check per checkout, and such a branch raises GitError for that branch only. A readable tip whose parent object is missing is not detected, an accepted limit (corrected in review round 2, see r2-disc-ancestry-decision-corrected).

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

<!-- fr:journal kind=discovery scope=spec id=r2-red-home-cwd-credits-fr-worktrees created=2026-09-14T23:02:36 -->
### r2-red-home-cwd-credits-fr-worktrees · discovery · RED (review Important 1): a home-directory cwd credits fr worktrees beneath it

tests/test_adapter_claude_sessions.py TestEvidenceIsLimitedToTheSessionsOwnRepository against HEAD 5bfd51e: rc=1, Ran 12, failures=4. test_a_home_directory_session_is_credited_with_no_ones_worktrees, extended with branch-creating commands in ~/.cache/fr/worktrees/example-repo/feat__z from cwd HOME, credited feat/z-checkout (and the push and switch): a directory beneath the cwd was admitted before the repository component rule was consulted.

<!-- fr:journal kind=discovery scope=spec id=r2-red-git-corrupt-tip-not-refused created=2026-09-14T23:02:39 -->
### r2-red-git-corrupt-tip-not-refused · discovery · RED (review Important 2): a corrupt branch tip is read as unmerged

tests/test_audit_git.py TestCorruptBranchTips against HEAD 5bfd51e: rc=1, Ran 2, failures=2. test_a_tip_naming_a_missing_object_raises_for_that_branch_only and test_a_tip_naming_a_blob_raises_for_that_branch_only both got 'GitError not raised': for-each-ref --merged silently leaves such a tip out, where merge-base --is-ancestor exited 128.

<!-- fr:journal kind=discovery scope=spec id=r2-red-generic-components-match created=2026-09-14T23:02:41 -->
### r2-red-generic-components-match · discovery · RED (review Minor 5): fr path components and a worktree cwd's prefix match as repositories

Same run (rc=1, Ran 12, failures=4). test_a_cwd_inside_an_fr_worktree_belongs_to_that_worktrees_repository credited feat/named-box, feat/named-fr and feat/w beside feat/other, from a cwd inside example-repo's fr worktree. test_a_repository_named_like_an_fr_path_component_is_not_credited credited feat/named-fr and feat/named-work: a repository named fr matched the .cache/fr component of the cwd.

<!-- fr:journal kind=discovery scope=spec id=r2-red-gh-repo-flag-escapes created=2026-09-14T23:02:46 -->
### r2-red-gh-repo-flag-escapes · discovery · RED (review Minor 7): gh pr create --repo naming another repository still counts

Same run (rc=1, Ran 12, failures=4). test_gh_pr_create_with_a_repo_flag_counts_only_for_its_own_repository credited feat/other-repo (-R example-org/example-repo-2) and feat/other-repo-equals (--repo=example-org/example-repo-2) from cwd /work/example-repo.

<!-- fr:journal kind=discovery scope=spec id=r2-disc-ancestry-decision-corrected created=2026-09-14T23:06:33 -->
### r2-disc-ancestry-decision-corrected · discovery · Correction: d-ancestry-one-merged-read overstated what --merged refuses

Review round 1 found the decision's claim wrong. It said a missing or broken branch ref still raises GitError as merge-base did, but a tip naming a missing object or a blob is silently left out by for-each-ref --merged, so the branch read as unmerged, where merge-base --is-ancestor exited 128 (reviewer probe probe_ancestry.out, cases missing-object and points-at-blob). fr journal add cannot rewrite an entry with the same id, so the decision's text was corrected in place in the journal file, and this entry records why. The fix: the per-checkout refs read now carries %(objectname), and the branches --merged left out have their tips checked with one cat-file --batch-check per checkout; only a branch whose tip does not peel to a commit raises. A missing PARENT of a readable tip is an accepted, documented limit (git fsck's job).

<!-- fr:journal kind=discovery scope=spec id=r2-disc-archived-spec-left-as-written created=2026-09-14T23:06:35 -->
### r2-disc-archived-spec-left-as-written · discovery · The archived spec's 120 s is superseded, not edited

Review round 1, Minor 8. docs/superpowers/implemented/specs/2026-09-13-session-audit-design.md still states a 120 s sessions timeout in its Failing rule. It is left as written because an archived spec is the historical record of what was built then; this spec's Change 2 now says the 120 s is superseded by this amendment, and docs/adapters.md and lib/feed.py carry the live 600 s.

<!-- fr:journal kind=discovery scope=spec id=r2-green-corrupt-tip created=2026-09-14T23:08:30 -->
### r2-green-corrupt-tip · discovery · GREEN (review Important 2): one cat-file batch per checkout refuses a non-commit tip

tests/test_audit_git.py rc=0 (Ran 22, OK: TestCorruptBranchTips both pass, every earlier git-layer test unmodified and green); tests/test_audit_git_calls.py rc=0 (Ran 3, OK); tests/test_audit_merge_state.py rc=0 (Ran 26, OK). The refs read now uses --format=%(refname) %(objectname); GitCache.not_commits feeds <oid>^{commit} for the branches the --merged read left out to ONE git cat-file --batch-check per (main checkout, default ref), and a stdout line that is not '<oid> commit <size>' marks that branch only. cat-file joins READ_ONLY_SUBCOMMANDS (it reports type and size only).

<!-- fr:journal kind=discovery scope=spec id=r2-disc-tests-changed-for-tip-check created=2026-09-14T23:08:34 -->
### r2-disc-tests-changed-for-tip-check · discovery · Changed tests for the tip check: one format string and one per-work-tree bound

tests/test_audit_merge_state.py test_one_resolution_per_repository_and_branch_however_many_name_it matched the refs read by its exact argv, which now carries %(objectname): the expected line is updated, its counts (one refs read, one --merged read, nothing naming the branch) are unchanged. tests/test_audit_git_calls.py CALLS_PER_TOPLEVEL goes from 5 to 6 for the tip check, still a constant per work tree, and test_one_ref_read_per_repository_names_its_heads_and_its_remote now also asserts exactly one cat-file per checkout, so a per-branch tip check fails it.

<!-- fr:journal kind=discovery scope=spec id=r2-green-scope-rules created=2026-09-14T23:08:37 -->
### r2-green-scope-rules · discovery · GREEN (review Important 1, Minors 5 and 7): the scope rules in adapters/claude

tests/test_adapter_claude_sessions.py rc=0 (Ran 117, OK). _in_own_repository sends any directory inside an fr worktree through the same-repository rule even when it is beneath the cwd, and admits any other directory beneath the cwd (the decided /work + cd other-repo case, test_a_branch_created_in_a_checkout_beneath_a_non_repository_cwd_still_counts). _session_repositories names the session's repository: a cwd inside an fr worktree belongs to that worktree's repo only, and otherwise fr's .cache/fr/worktrees components and everything below them are left out. gh pr create --repo|-R OWNER/NAME counts only when NAME is the session's repository (_names_own_repository).

<!-- fr:journal kind=discovery scope=spec id=r2-mut-important-1 created=2026-09-14T23:09:30 -->
### r2-mut-important-1 · discovery · Mutation proof (review Important 1): beneath-cwd admission of fr worktrees

rv/mutate.py on a copy of the fixed tree, mutation r2-i1-fr-worktree-beneath-cwd-admitted (the fr-worktree check in _in_own_repository also admits a directory beneath the cwd, restoring the old rule): TestEvidenceIsLimitedToTheSessionsOwnRepository rc=1, Ran 12, failures=1, test_a_home_directory_session_is_credited_with_no_ones_worktrees. KILLED.

<!-- fr:journal kind=discovery scope=spec id=r2-red-mut-minor-1 created=2026-09-14T23:09:33 -->
### r2-red-mut-minor-1 · discovery · RED by mutation (review Minor 1): -C reach alone

The guard passed before any code change, so its RED is a mutation. Reviewer mutation m4-reached-C-off (reached(...) for git -C replaced by pass) on the fixed tree: rc=1, Ran 12, failures=1, test_its_own_fr_worktree_reached_only_by_dash_cap_c_counts. KILLED; in review round 1 the same mutation SURVIVED.

<!-- fr:journal kind=discovery scope=spec id=r2-red-mut-minor-2 created=2026-09-14T23:09:35 -->
### r2-red-mut-minor-2 · discovery · RED by mutation (review Minor 2): exact component match

The guard passed before any code change. Mutation r2-m2-substring (repo checked with 'in own_cwd', a substring match, instead of against _session_repositories): rc=1, Ran 12, failures=3, test_the_repository_must_equal_a_cwd_component_not_be_part_of_one, test_a_cwd_inside_an_fr_worktree_belongs_to_that_worktrees_repository, test_a_repository_named_like_an_fr_path_component_is_not_credited. KILLED.

<!-- fr:journal kind=discovery scope=spec id=r2-red-mut-minor-3 created=2026-09-14T23:09:38 -->
### r2-red-mut-minor-3 · discovery · RED by mutation (review Minor 3): whole-component HOME expansion

The guard passed before any code change. Reviewer mutation m4-home-loose (raw.startswith(prefix) instead of raw == prefix or raw.startswith(prefix + '/')): rc=1, Ran 12, failures=1, test_only_a_whole_home_variable_is_expanded (cwd is HOME's parent, so a loosely expanded $HOMEX/sub lands beneath it). KILLED; in review round 1 it SURVIVED.

<!-- fr:journal kind=discovery scope=spec id=r2-red-mut-minor-4 created=2026-09-14T23:09:41 -->
### r2-red-mut-minor-4 · discovery · RED by mutation (review Minor 4): case-insensitive PR dedup

The guard passed before any code change. Reviewer mutation m3-dedup-case (dedup key record['repo'] without .lower()): tests/test_audit_report.py TestRun rc=1, Ran 8, failures=1, test_one_pull_request_is_reported_once_whatever_the_case_of_its_repository. KILLED; in review round 1 the report tests let it survive.

<!-- fr:journal kind=discovery scope=spec id=r2-mut-minors-5-7-important-2 created=2026-09-14T23:09:44 -->
### r2-mut-minors-5-7-important-2 · discovery · Mutation proofs (review Minors 5 and 7, Important 2)

r2-m5-fr-components-kept (_session_repositories ignores the fr path, fr_path = None): rc=1, Ran 12, failures=2, test_a_cwd_inside_an_fr_worktree_belongs_to_that_worktrees_repository and test_a_repository_named_like_an_fr_path_component_is_not_credited. r2-m7-gh-repo-ignored (the gh --repo check replaced by if False): rc=1, Ran 12, failures=1, test_gh_pr_create_with_a_repo_flag_counts_only_for_its_own_repository. r2-i2-tip-check-off (the not_commits raise replaced by if False): tests/test_audit_git.py TestCorruptBranchTips rc=1, Ran 2, failures=2, both tip tests. All KILLED. Summary in the reviewer scratchpad, rv/mut/r2-summary.txt.

<!-- fr:journal kind=discovery scope=spec id=r2-disc-spec-drift-and-limits created=2026-09-14T23:09:47 -->
### r2-disc-spec-drift-and-limits · discovery · Spec corrected (review Minors 6 and 8) and one wording kept to the code

Change 1 now states the three per-checkout reads (refs with object names, one --merged, one cat-file batch check), the missing-parent limit, and that each clone is its own checkout read once, not once for all clones. The review asked to say GitHub facts are shared per repository; resolve_branches groups by main checkout, so repo_branches and compare run once per checkout and the report reconciles clones afterwards, and the spec says that instead. Change 2 says the archived 120 s is superseded and left as written. Change 4 names the session's repository, both command rules, gh --repo, the HOME expansion, and two known limits: cd .. from a subdirectory cwd is dropped, and a generic component such as work still matches.

<!-- fr:journal kind=finding scope=spec id=fx-nonrepo-cwd-credits-fr-worktrees created=2026-09-14T23:25:26 state=fixed -->
### fx-nonrepo-cwd-credits-fr-worktrees · finding [fixed] · A non-repository cwd still credited fr worktrees beneath it

A session started in the home directory or a projects folder was credited with branches created in other repositories' fr worktrees, because they were beneath its cwd. Any directory inside an fr worktree now has to pass the same-repository component rule even when it is beneath cwd. A branch-creating command in an ordinary checkout beneath a non-repository cwd still counts, as real work by that session; the adapter cannot see repository boundaries without git, and the spec says so.

<!-- fr:journal kind=finding scope=spec id=fx-corrupt-tip-reads-unmerged created=2026-09-14T23:25:28 state=fixed -->
### fx-corrupt-tip-reads-unmerged · finding [fixed] · A corrupt branch tip was classified unmerged instead of failing closed

Batching ancestry into one --merged read made a branch ref naming a missing object or a blob silently unmerged, where merge-base used to exit 128 and stop the run. Tips the --merged read excluded are now checked with one cat-file batch check per repository, and a broken tip raises for that branch only. A commit whose parent object is missing is not detected (git fsck territory); the docstring and spec state the limit, and the journal entry that claimed broken refs still raised is corrected.

<!-- fr:journal kind=finding scope=spec id=fx-c-reach-untested created=2026-09-14T23:25:30 state=fixed -->
### fx-c-reach-untested · finding [fixed] · git -C reach into the session's own fr worktree was never tested on its own

The only -C test reached the same worktree as a preceding cd, so the two hits deduplicated. A test whose only reference is git -C into the own worktree now fails when -C reach is removed.

<!-- fr:journal kind=finding scope=spec id=fx-component-substring-untested created=2026-09-14T23:25:32 state=fixed -->
### fx-component-substring-untested · finding [fixed] · The exact path-component match was unguarded

A substring match survived mutation. A cwd named like the repository plus a suffix now credits nothing for that repository's worktrees.

<!-- fr:journal kind=finding scope=spec id=fx-home-prefix-untested created=2026-09-14T23:25:34 state=fixed -->
### fx-home-prefix-untested · finding [fixed] · The HOME expansion prefix rule was unguarded

A loose startswith survived mutation. Directories beginning $HOMEX or ${HOME_DIR} are now tested to stay dropped.

<!-- fr:journal kind=finding scope=spec id=fx-case-insensitive-dedup-untested created=2026-09-14T23:25:36 state=fixed -->
### fx-case-insensitive-dedup-untested · finding [fixed] · Case-insensitive open-PR deduplication was unguarded

A case-sensitive mutation survived. Two owners' copies of one PR differing only in letter case are now reported once.

<!-- fr:journal kind=finding scope=spec id=fx-component-rule-fr-prefix-names created=2026-09-14T23:25:38 state=fixed -->
### fx-component-rule-fr-prefix-names · finding [fixed] · The component rule matched the fr worktree path's own components

A repository named like a component of an fr worktree path (fr, for example) matched any cwd inside an fr worktree. Those path components are now ignored, and a cwd inside an fr worktree takes that worktree's repository as its own.

<!-- fr:journal kind=finding scope=spec id=fx-component-rule-ancestor-folder-names created=2026-09-14T23:25:41 state=refuted -->
### fx-component-rule-ancestor-folder-names · finding [refuted] · A repository named like an ancestor folder of cwd still matches

Verified on the fixed code: an fr worktree of a repository named work is credited to a session whose cwd is /work/a. Closing it means matching only cwd's last component, which drops real fr-worktree evidence for every session started in a subdirectory of its repository. Missing real work is worse for an audit than this over-credit, which needs a repository named exactly like an ancestor folder of the session. Kept as a documented limit in the spec and docs/adapters.md.

<!-- fr:journal kind=finding scope=spec id=fx-subdir-cwd-parent-cd created=2026-09-14T23:25:43 state=refuted -->
### fx-subdir-cwd-parent-cd · finding [refuted] · A subdirectory cwd loses a branch created after cd ..

Conforms to the spec's rule (the command's directory must be cwd or beneath it) and is rare, since sessions start at a repository root. Widening it would need repository boundaries the git-free adapter cannot see. Documented in the spec as a known limit rather than changed.

<!-- fr:journal kind=finding scope=spec id=fx-gh-pr-create-repo-escape created=2026-09-14T23:25:45 state=fixed -->
### fx-gh-pr-create-repo-escape · finding [fixed] · gh pr create --repo other/repo escaped the own-repository rule

gh's --repo was ignored, so a PR opened for another repository still credited its head branch. --repo and -R must now name the session's own repository by the component rule, or the evidence is dropped.

<!-- fr:journal kind=finding scope=spec id=fx-spec-drift created=2026-09-14T23:25:47 state=fixed -->
### fx-spec-drift · finding [fixed] · The spec understated the per-checkout reads and misdescribed clones and the timeout

It now names one refs read with object names, one --merged read and one cat-file batch check per checkout; says each clone is its own checkout, resolved and asked of GitHub once per checkout, with the report reconciling clones by repository afterwards (the review suggested sharing GitHub facts per repository, but the spec describes what the code does, and changing that is a separate optimisation); and says the archived spec's 120 s is superseded by this amendment, left as written because archived specs are a historical record.
