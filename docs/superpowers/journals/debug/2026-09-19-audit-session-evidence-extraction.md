# Journal: 2026-09-19-audit-session-evidence-extraction

<!-- fr:journal kind=repro scope=debug id=repro-26-credited-to-nobody created=2026-09-19T13:00:51 -->
### repro-26-credited-to-nobody · repro · Open PR example-org/example-repo#NN is credited to no session, though its worktree and session both exist

audit --json listed feat/example-branch in section 3 (no session working on it). The fr worktree ~/.cache/fr/worktrees/Example-Repo/feat__example-branch exists, the Example-Repo session is live, and the component rule matches (fr records <repo> as Example-Repo, the cwd's own last component). Probing the shipped adapter: no session credits that branch.

<!-- fr:journal kind=ruled-out scope=debug id=ruled-out-scope-rule created=2026-09-19T13:00:54 -->
### ruled-out-scope-rule · ruled-out · Not the own-repository scope rule from the 2026-09-14 fixes

_same_repository_fr_worktree(~/.cache/fr/worktrees/Example-Repo/feat__example-branch, <projects>/Example-Repo) returns a match. The scope rule admits this branch; something upstream never produces the evidence.

<!-- fr:journal kind=ruled-out scope=debug id=ruled-out-push-flag-order created=2026-09-19T13:00:57 -->
### ruled-out-push-flag-order · ruled-out · Not git push flag order

Probed: 'git push -u origin feat/f' and 'git push -q -u origin feat/g' both credit the branch. -q between push and -u is handled.

<!-- fr:journal kind=root-cause scope=debug id=rc-prefix-hides-shape created=2026-09-19T13:17:49 -->
### rc-prefix-hides-shape · root-cause · A command prefix hides the shape behind it, because the shape checks read the segment's FIRST token

_process_bash_command dispatches on segment[0] being cd, git, fr or gh. A real transcript rarely puts the command first: 'timeout 600 fr isolation up --branch x', 'FR_ISOLATION_TARGET=worktree fr ...', 'env FOO=1 git checkout -b x' and 'nice -n 10 git switch -c x' all match nothing. Probed against the shipped adapter: the unprefixed form is credited, all four prefixed forms are dropped.

<!-- fr:journal kind=root-cause scope=debug id=rc-gh-repo-case created=2026-09-19T13:17:51 -->
### rc-gh-repo-case · root-cause · gh --repo is compared case-sensitively against a cwd path component

_names_own_repository compared the NAME from 'gh pr create --repo OWNER/NAME' against _session_repositories(cwd) with ==. GitHub normalises NAME to lower case; the folder the repository was cloned into keeps the operator's spelling. Probed: --repo example-org/example-repo is dropped for a session in a Example-Repo checkout, --repo example-org/Example-Repo is credited. The same fold was missing for an fr worktree's own <repo>.

<!-- fr:journal kind=root-cause scope=debug id=rc-bound-worktree-path created=2026-09-19T13:17:52 -->
### rc-bound-worktree-path · root-cause · A worktree path bound to a shell variable is dropped though the literal path is in the same command

The operator's sessions bind the long path once and reach it afterwards: 'W=<path>; cd $W && ...'. _resolve_dir expands a leading $HOME only, so cd $W stayed a non-directory and the worktree was never reached. Probed: 'cd <literal path>' is credited, 'W=<same literal path>; cd $W' is dropped.

<!-- fr:journal kind=finding scope=debug id=fx-prefix created=2026-09-19T13:18:53 state=fixed -->
### fx-prefix · finding [fixed] · The shape checks now read past a prefix that merely runs the command

_strip_command_prefixes drops assignment prefixes and the runner words env, timeout and nice (with timeout's options and its one duration) before the cd/git/fr/gh dispatch. The word list is deliberately short: a word that does not simply run its argument would hand that argument's directory to the wrong shape. Pinned by test_a_command_prefix_does_not_hide_the_shape_behind_it, and guarded by test_a_prefix_does_not_widen_the_repository_rule (a prefixed command in another repository still counts for nothing) and test_a_prefix_is_not_mistaken_for_the_command (timeoutctl, envsubst, and a prefix with no command after it).

<!-- fr:journal kind=finding scope=debug id=fx-case-fold created=2026-09-19T13:18:55 state=fixed -->
### fx-case-fold · finding [fixed] · Repository names are compared with case folded, on both sides

_session_repositories returns lower-cased names and both probes fold: the gh --repo NAME and an fr worktree's own <repo>. Pinned by test_gh_repo_names_the_own_repository_whatever_the_case and test_the_repository_matches_whichever_side_carries_the_capitals; test_gh_repo_still_drops_a_repository_that_is_not_its_own keeps the rule from widening.

<!-- fr:journal kind=finding scope=debug id=fx-bindings created=2026-09-19T13:18:56 state=fixed -->
### fx-bindings · finding [fixed] · A path bound to a shell variable in the same command is now reached

A segment that is nothing but assignments binds them, and _resolve_dir expands a leading $VAR or ${VAR} from those bindings for cd, git -C, --repo and the worktree add path. One level only, and only from a binding the command itself made. Pinned by test_a_worktree_path_bound_to_a_variable_is_reached_by_cd, test_a_bound_worktree_is_reached_by_dash_cap_c_alone and test_a_binding_that_starts_with_home_is_still_expanded; guarded by test_a_bound_variable_does_not_escape_the_repository_rule, test_a_variable_that_was_never_bound_is_still_not_a_directory and test_a_binding_whose_value_holds_another_variable_reaches_nothing.

<!-- fr:journal kind=finding scope=debug id=fx-binding-guard-simplified created=2026-09-19T13:18:58 state=fixed -->
### fx-binding-guard-simplified · finding [fixed] · Refusing to bind a value that holds a variable was inert, and wrong for $HOME

Mutation testing showed the guard survived every test: a bound value carrying an unknown variable is dropped downstream anyway, by the same _VARIABLE_RE check that drops any non-directory. Its one live effect was to refuse W=$HOME/..., which _resolve_dir can resolve. The guard is gone and the $HOME case is now pinned by a test; the reverse mutation (refusing such values again) is killed.
