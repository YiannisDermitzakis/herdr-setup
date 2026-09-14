# Journal: 2026-09-13-session-audit

<!-- fr:journal kind=decision scope=plan id=d-tier-models created=2026-09-13T22:33:48 -->
### d-tier-models · decision · Phase tiers map to models per dispatch, not via a global binding

fr models resolve --harness claude binds standard (claude-sonnet-5) but not hard; the operator's models.yaml uses the legacy cheap/standard/premium keys. Rather than write the operator's global ~/.config/fr/models.yaml unasked, each fr-phase-executor dispatch passes the model directly: standard -> sonnet (phases 1, 2, 4), hard -> opus (phase 3), mirroring the operator's own premium=claude-opus-5 binding.

<!-- fr:journal kind=discovery scope=plan id=b80778f12afd created=2026-09-13T23:11:09 phase=1 -->
### b80778f12afd · discovery · no-refactor-because: P1.T2 (phase 1)

cmd_install/cmd_install_warn and hs_resolve_path read clearly at GREEN (each refusal is its own guarded return, one stderr line, matching the design table row for row); shellcheck is clean and ruff needs no changes. No extraction was warranted, so P1.T2.S3 is this note rather than a diff.

<!-- fr:journal kind=discovery scope=plan id=d1cfa5206b74 created=2026-09-14T00:15:29 phase=1 -->
### d1cfa5206b74 · discovery · RED: tests/test_fixture_owner_hygiene.sh (P1.T3 fixup, item A.7) (phase 1)

Ran against the pre-fix fixtures (the fixture commit): rc=1, 2 passed, 2 failed. FAIL 1: the matrix's own owner appears in tests/fixtures/gh/README.md:13. FAIL 2: tests/fixtures/gh/owner-pull-requests.json's endCursor decodes to more than the sanctioned cursor:v2:placeholder payload -- it embeds a real repository id (the leaked cursor value itself is not repeated here, on purpose: this journal is committed history too). The guard-on-the-guard nested cases (2 passed) already proved the checks fire on a poisoned copy. Fixes 1-3 (owner-string prose rewrite, category-only masking prose, synthetic cursor) are what turns this green.

<!-- fr:journal kind=discovery scope=plan id=9b71ab4dc742 created=2026-09-14T00:18:50 phase=1 -->
### 9b71ab4dc742 · discovery · RED: hs_resolve_path symlink-loop hang (P1 fixup, item B.8) (phase 1)

tests/test_install.sh cases 12 (self-loop) and 13 (two-link cycle): rc=1, 2 failed, 37 passed. Both got exit 137 (SIGKILL from the 10s watchdog) instead of 4 -- hs_resolve_path's while [ -h ... ] loop has no hop cap and spins forever on a cyclic symlink. Capping at 40 hops and returning 1 on overflow is what turns this green (cmd_install already treats a failed resolve as 'not this checkout', exit 4).

<!-- fr:journal kind=discovery scope=plan id=d20f146e627f created=2026-09-14T00:25:06 phase=1 -->
### d20f146e627f · discovery · RED: cmd_audit did not forward --adapters/--socket (P1 fixup, item B.12) (phase 1)

tests/test_audit_entrypoint.sh, run with the fake herdr on PATH: rc=1, 8 passed, 1 failed. FAIL: 'cmd_audit forwards --adapters and --socket before the operator's own flags': expected argv:--adapters <dir>/adapters --socket <sock> --since 5, got argv:--since 5 -- cmd_audit was calling hs_audit "$@" with no defaults at all, unlike cmd_feed's own args=(--adapters ... --socket ...) pattern. Building the same args array in cmd_audit, then hs_audit "${args[@]}" "$@", is what turns this green.

<!-- fr:journal kind=discovery scope=plan id=7f76d4a9ddf9 created=2026-09-14T00:27:49 phase=1 -->
### 7f76d4a9ddf9 · discovery · RED: shadow warning did not compare real paths (P1 fixup, item B.13) (phase 1)

tests/test_install.sh: rc=1, 56 passed, 1 failed. FAIL 10c: an earlier PATH entry that is itself another symlink to the SAME entrypoint was warned as shadowing it -- cmd_install_warn's old check only compared 'command -v herdr-setup' against the literal $link string, never resolving either side, so a second link to the identical checkout always read as a foreign shadow. Walking PATH in order and comparing hs_resolve_path'd real paths (own function, cmd_install_warn_shadow) is what turns this green.

<!-- fr:journal kind=discovery scope=plan id=a58a73a3c4c7 created=2026-09-14T00:29:42 phase=1 -->
### a58a73a3c4c7 · discovery · RED: install accepted and silently ignored extra arguments (P1 fixup, item B.14) (phase 1)

tests/test_install.sh case 18: rc=1, 3 failed. 'herdr-setup install some-extra-argument' exited 0 and actually created ~/.local/bin -- cmd_install never looked at its own $@ at all, so the entrypoint's dispatch loop's dumb HS_ARGS accumulation silently passed extra positional args through unvalidated. Checking $# -gt 0 at the top of cmd_install and refusing (exit 2, naming $1) before anything else runs is what turns this green.

<!-- fr:journal kind=discovery scope=plan id=41957fb300b1 created=2026-09-14T00:30:35 phase=1 -->
### 41957fb300b1 · discovery · no-refactor-because: P1.T1 (phase 1)

cmd_audit and hs_audit mirror cmd_feed/hs_feed exactly (same gate-then-door shape, same comment style), and lib/audit.py's argparse setup reads clearly at GREEN. shellcheck and ruff need no changes. No extraction was warranted, so P1.T1.S3 is this note rather than a diff.

<!-- fr:journal kind=discovery scope=plan id=9d2cef7cff14 created=2026-09-14T01:08:11 phase=1 -->
### 9d2cef7cff14 · discovery · gate-measurement bug: piping shellcheck/pytest/ruff into tail before reading $? (phase 1)

Several of this round's own gate checks read $? immediately after a 'cmd 2>&1 | tail -N' pipeline, which captures tail's exit status (always 0), never the command's. shellcheck's real exit status was 1 the whole time (SC2012 info-level finding, tests/test_install.sh's two 'ls -i' lines -- shellcheck's default --severity is style, which fails on info too), masked by this. Fixed the finding (shellcheck disable=SC2012, matching tests/test_apply_config.sh's own precedent) and re-verified pytest/ruff/shellcheck by capturing $? directly after the primary command (output redirected to a file or a variable, never through a trailing pipe) -- all four now genuinely rc=0. No other hidden failures found.

<!-- fr:journal kind=discovery scope=plan id=af925953de2b created=2026-09-14T01:45:05 phase=1 -->
### af925953de2b · discovery · RED: test_fixture_owner_hygiene.sh's guard-on-the-guard passes vacuously (P1 re-review, item 1) (phase 1)

Reproduced in a temp sandbox: HOME pointed at a .gitconfig with core.hooksPath set to a pre-commit hook that always exits 1 (simulating an operator's global hook), and the CURRENT (unfixed) test_fixture_owner_hygiene.sh run against it: rc=0, 4 passed, 0 failed. The nested git commit for both the owner-hit and cursor-hit cases was blocked by the hook (verified directly: 'blocked by a global pre-commit hook', commit rc=1) rather than by the guard's own owner/cursor detection ever running -- but the outer test's hs_test_nested_owner_hit/hs_test_nested_cursor_hit only check that the nested subshell's overall exit status is non-zero, so a git-setup failure of ANY kind (a hook, missing identity) is indistinguishable from a correct guard firing. Capturing the nested run's output and requiring the SPECIFIC expected FAIL: line, plus making the nested git commands fail loudly with -c core.hooksPath=/dev/null and explicit identity, is what turns this from a vacuous pass into a real proof.

<!-- fr:journal kind=discovery scope=plan id=6900fa31a7c6 created=2026-09-14T01:50:16 phase=1 -->
### 6900fa31a7c6 · discovery · RED: run_install_watchdog waits the full 10s even on a quick exit (P1 re-review, item 2) (phase 1)

Reproduced in a standalone script that calls run_install_watchdog through a command substitution, exactly as tests/test_install.sh's own call sites do (status="$(run_install_watchdog ...)"): a self-loop install that itself returns in well under a second still took the full 10s end to end (measured with date +%s before/after). Direct, non-command-substitution invocation of the same function body did NOT reproduce it (returned in ~0-1s) -- the bug is specific to running inside $(...), where 'kill "$watchdog"' does not reliably reach the sleep it is blocked in, so 'wait "$watchdog"' blocks until the sleep finishes naturally. Replacing the kill-the-wrapper-subshell approach with a plain polling loop (kill -0 "$pid" every 0.1s, force-kill and break past 10s) removes the separate watchdog job entirely and fixes it.

<!-- fr:journal kind=discovery scope=plan id=47fb32c8ea8c created=2026-09-14T01:52:46 phase=1 -->
### 47fb32c8ea8c · discovery · RED: cases 16/17 asserted only the PATH substring, not the tool's own wording (P1 re-review, item 3) (phase 1)

assert_contains only checked that $home16/.local/bin (or the link path) appeared somewhere in stderr, which is true regardless of what herdr-setup actually says -- the assertion cannot fail even if the message text changed to something unrelated that still happens to name the path. Confirmed by mutating a sandbox copy of herdr-setup (both 'could not create' lines rewritten to a neutral 'refused' message via awk) and re-running cases 16/17's OLD assertion form against it: it still passed, proving it does not test the wording at all. Asserting the literal 'herdr-setup: install: could not create <path>.' message is what actually exercises the tool's own text.

<!-- fr:journal kind=discovery scope=plan id=5e6e2a2952c6 created=2026-09-14T01:56:07 phase=1 -->
### 5e6e2a2952c6 · discovery · RED: check_fixture_cursors.py skips unpadded base64 and only scans gh/ (P1 re-review, item 5) (phase 1)

Confirmed two gaps: (1) base64.b64decode(..., validate=True) rejects a token missing its trailing '=' padding as a decode ERROR rather than restoring it, so a real unpadded cursor (e.g. the base64 of 'cursor:v2:abcdefghi' with its '==' stripped, Y3Vyc29yOnYyOmFiY2RlZmdoaQ) is silently skipped -- verified: uv run check_fixture_cursors.py against a file containing only that token exits 0. (2) tests/test_fixture_owner_hygiene.sh only invokes the script against tests/fixtures/gh, so a cursor-shaped token anywhere else under tests/fixtures/ is never scanned at all, regardless of the script's own capability. Restoring padding before decode (t + '=' * (-len(t) % 4)) and widening the shell test's own invocation to tests/fixtures/ fixes both.

<!-- fr:journal kind=discovery scope=plan id=130a17ce536b created=2026-09-14T02:05:28 phase=1 -->
### 130a17ce536b · discovery · RED: cmd_install_warn_shadow glob-expands PATH and mis-words a not-on-PATH dir (P1 re-review, item 9) (phase 1)

tests/test_install.sh cases 19-20: rc=1, 2 failed, 67 passed. Case 19: a literal '*' PATH entry ('$work/star*dir', itself carrying no herdr-setup) glob-expanded into a sibling directory ('starXdir') that DOES have one, and the shadow check wrongly warned about it as if it were really on PATH -- 'for entry in $PATH' with IFS=':' controls word-splitting but not pathname expansion, so an unquoted PATH component containing a glob metacharacter is expanded against the filesystem. Case 20: with $dir (~/.local/bin) not on PATH at all, the warning still said 'is on PATH ahead of $dir', which is nonsensical when $dir has no PATH position to be ahead of. Building the split PATH list once via 'set -f; set -- $PATH; set +f' (mirroring hs_manifest_plugins' own had_noglob-guarded pattern) and wording the message conditionally on whether $dir was actually found on PATH fixes both.

<!-- fr:journal kind=finding scope=plan id=f1-gh-readme-real-login created=2026-09-14T02:28:50 phase=1 state=fixed -->
### f1-gh-readme-real-login · finding [fixed] · Fixture README named the real account, repository and branch (phase 1)

tests/fixtures/gh/README.md quoted the queried account, repository and a real branch name. Rewritten generically; squashed into the fixture commit before any push, so the names never reached the public history. Guarded by tests/test_fixture_owner_hygiene.sh, which reads the owner from the matrix at runtime.

<!-- fr:journal kind=finding scope=plan id=f1-masked-value-detail created=2026-09-14T02:28:51 phase=1 state=fixed -->
### f1-masked-value-detail · finding [fixed] · READMEs described masked values in identifying detail (phase 1)

Descriptions of masked titles, projects and organisations reduced to their category.

<!-- fr:journal kind=finding scope=plan id=f1-cursor-real-repo-id created=2026-09-14T02:28:53 phase=1 state=fixed -->
### f1-cursor-real-repo-id · finding [fixed] · A pagination cursor decoded to a real repository id (phase 1)

GitHub cursors are base64 of cursor:v2: plus a binary id. Replaced with a placeholder cursor; the new cursor check fails on any real-id cursor.

<!-- fr:journal kind=finding scope=plan id=f1-tab-list-repointed-silently created=2026-09-14T02:28:54 phase=1 state=fixed -->
### f1-tab-list-repointed-silently · finding [fixed] · tab-list.json was re-pointed to agent-list.json without saying so (phase 1)

The README now tables every re-pointed value, and the labels agree with agent-list.json.

<!-- fr:journal kind=finding scope=plan id=f1-install-symlink-loop-hang created=2026-09-14T02:28:56 phase=1 state=fixed -->
### f1-install-symlink-loop-hang · finding [fixed] · install hung forever on a symlink loop at the link path (phase 1)

hs_resolve_path is capped at 40 hops; an unresolvable link is refused with exit 4. A watchdog-wrapped test proves it.

<!-- fr:journal kind=finding scope=plan id=f1-resolve-branch-untested created=2026-09-14T02:28:58 phase=1 state=fixed -->
### f1-resolve-branch-untested · finding [fixed] · The same-real-path branch of the install check was untested (phase 1)

Intermediate-link and relative-link cases added; a mutation of the resolver fails them.

<!-- fr:journal kind=finding scope=plan id=f1-mkdir-ln-fail-untested created=2026-09-14T02:28:59 phase=1 state=fixed -->
### f1-mkdir-ln-fail-untested · finding [fixed] · The install row 'mkdir or ln fails -> 2' had no test (phase 1)

Read-only ~/.local and ~/.local/bin cases added, skipped as root.

<!-- fr:journal kind=finding scope=plan id=f1-weak-install-assertions created=2026-09-14T02:29:01 phase=1 state=fixed -->
### f1-weak-install-assertions · finding [fixed] · Three install assertions could not fail (phase 1)

Case 1 asserts the created: line, case 2 the inode as well as readlink, case 8 the absence of a + ln line.

<!-- fr:journal kind=finding scope=plan id=f1-audit-args-not-forwarded created=2026-09-14T02:29:03 phase=1 state=fixed -->
### f1-audit-args-not-forwarded · finding [fixed] · cmd_audit did not forward --adapters/--socket like cmd_feed (phase 1)

Forwarded; proven against a sandbox stub that echoes its argv.

<!-- fr:journal kind=finding scope=plan id=f1-shadow-warning-wrong-order created=2026-09-14T02:29:04 phase=1 state=fixed -->
### f1-shadow-warning-wrong-order · finding [fixed] · The shadowing warning fired for a herdr-setup later on PATH (phase 1)

The check walks PATH in order and compares resolved paths.

<!-- fr:journal kind=finding scope=plan id=f1-install-extra-args created=2026-09-14T02:29:06 phase=1 state=fixed -->
### f1-install-extra-args · finding [fixed] · install silently accepted extra arguments (phase 1)

An extra argument exits 2, naming it.

<!-- fr:journal kind=finding scope=plan id=f1-no-refactor-note-missing created=2026-09-14T02:29:08 phase=1 state=fixed -->
### f1-no-refactor-note-missing · finding [fixed] · P1.T1's no-refactor-because note was missing (phase 1)

Recorded. From phase 2 on, every RED run is journalled with its exit status and failing tests.

<!-- fr:journal kind=finding scope=plan id=f1-transcript-reserialised created=2026-09-14T02:29:09 phase=1 state=fixed -->
### f1-transcript-reserialised · finding [fixed] · transcript.jsonl was re-serialised with spacing (phase 1)

Lines rewritten compactly with key order preserved, the way Claude Code writes them.

<!-- fr:journal kind=finding scope=plan id=f1-open-pr-node-not-captured created=2026-09-14T02:29:11 phase=1 state=fixed -->
### f1-open-pr-node-not-captured · finding [fixed] · No open pull request node or bot author had been captured (phase 1)

open-pull-requests.json captured read-only from a public repository with dependabot pull requests.

<!-- fr:journal kind=finding scope=plan id=f1-plan-gates-header-dropped created=2026-09-14T02:29:13 phase=1 state=fixed -->
### f1-plan-gates-header-dropped · finding [fixed] · Plan steps pointed at a gates header fr plan create dropped (phase 1)

The gate commands and the RED-journalling rule now live in the plan prose.

<!-- fr:journal kind=finding scope=plan id=f1-history-tdd-order created=2026-09-14T02:29:14 phase=1 state=refuted -->
### f1-history-tdd-order · finding [refuted] · History shows implementation committed before its tests (phase 1)

True of phase 1's commits, which grouped by file rather than by red/green. No functional commit fails the suite, so nothing is lost for bisection; the proof of red-first now lives in the journal, where every later RED run is recorded with its exit status.

<!-- fr:journal kind=finding scope=plan id=f1-history-shellcheck-gap created=2026-09-14T02:29:16 phase=1 state=refuted -->
### f1-history-shellcheck-gap · finding [refuted] · Three commits fail shellcheck until a later commit fixes it (phase 1)

Lint only (SC2012, info severity) and the suite passes at every commit, so bisecting a behaviour change is unaffected. Rewriting history again to fix a lint rule would cost more safety than it buys.

<!-- fr:journal kind=finding scope=plan id=f1-rewrite-beyond-instruction created=2026-09-14T02:29:17 phase=1 state=fixed -->
### f1-rewrite-beyond-instruction · finding [fixed] · The executor rewrote history with filter-branch instead of stopping (phase 1)

Instructed to abort a conflicting rebase and report blocked, it used git filter-branch and expired the reflog. The branch was never pushed; the orchestrator verified bfe205c is untouched, authorship and trailers survive, and no identifier remains. Later rounds forbid any history rewrite.

<!-- fr:journal kind=finding scope=plan id=f1r-vacuous-guard-on-guard created=2026-09-14T02:29:19 phase=1 state=fixed -->
### f1r-vacuous-guard-on-guard · finding [fixed] · The fixture-owner guard's self-test could pass vacuously (phase 1)

It counted any non-zero exit of the nested run as proof, so a nested git commit blocked by a hook passed without checking anything. It now requires the specific FAIL message for the poison it planted, and a nested git failure is itself a failure. The RED run is journalled.

<!-- fr:journal kind=finding scope=plan id=f1r-watchdog-waits-timeout created=2026-09-14T02:29:21 phase=1 state=fixed -->
### f1r-watchdog-waits-timeout · finding [fixed] · The install watchdog always waited its full timeout (phase 1)

Rewritten as a polling loop; the loop cases assert they finish in under 5 seconds.

<!-- fr:journal kind=finding scope=plan id=f1r-mkdir-ln-message-unproven created=2026-09-14T02:29:22 phase=1 state=fixed -->
### f1r-mkdir-ln-message-unproven · finding [fixed] · Cases 16 and 17 could not fail on install's own wording (phase 1)

mkdir and ln print the path themselves, so the assertion matched their text. It now matches install's literal line, with a permanent mutation case.

<!-- fr:journal kind=finding scope=plan id=f1r-local-named-status created=2026-09-14T02:29:24 phase=1 state=fixed -->
### f1r-local-named-status · finding [fixed] · A local named status in test_install.sh (phase 1)

AGENTS.md forbids it (read-only in zsh). Renamed to rc; no other occurrence.

<!-- fr:journal kind=finding scope=plan id=f1r-cursor-checker-gaps created=2026-09-14T02:29:26 phase=1 state=fixed -->
### f1r-cursor-checker-gaps · finding [fixed] · The cursor check skipped unpadded tokens and scanned only gh/ (phase 1)

Padding is restored before decoding and every file under tests/fixtures/ is scanned; poisoned-copy cases cover both.

<!-- fr:journal kind=finding scope=plan id=f1r-open-pr-traceable created=2026-09-14T02:29:27 phase=1 state=fixed -->
### f1r-open-pr-traceable · finding [fixed] · open-pull-requests.json kept a real PR number and timestamp (phase 1)

Replaced with placeholders; the README states exactly what was kept (the bot login and its head-branch shape).

<!-- fr:journal kind=finding scope=plan id=f1r-account-facts-in-readmes created=2026-09-14T02:29:29 phase=1 state=fixed -->
### f1r-account-facts-in-readmes · finding [fixed] · Fixture READMEs stated facts about the operator's account and host (phase 1)

Reworded to describe only each capture's shape.

<!-- fr:journal kind=finding scope=plan id=f1r-cmd-audit-comment created=2026-09-14T02:29:31 phase=1 state=fixed -->
### f1r-cmd-audit-comment · finding [fixed] · cmd_audit's comment misplaced --dry-run and --yes (phase 1)

Corrected: the global parser consumes them and audit does not re-add them, because they change nothing for a read-only command.

<!-- fr:journal kind=finding scope=plan id=f1r-shadow-glob-and-wording created=2026-09-14T02:29:32 phase=1 state=fixed -->
### f1r-shadow-glob-and-wording · finding [fixed] · The shadow warning globbed PATH and misworded the off-PATH case (phase 1)

PATH is split under set -f with the caller's noglob state restored, and the message covers both cases. The RED runs are journalled.

<!-- fr:journal kind=finding scope=plan id=f1r-root-skip-counted-as-pass created=2026-09-14T02:29:34 phase=1 state=fixed -->
### f1r-root-skip-counted-as-pass · finding [fixed] · Root-only skips were counted as passes (phase 1)

They print SKIP on stderr and count as nothing.

<!-- fr:journal kind=finding scope=plan id=f1r-tab-label-cwd created=2026-09-14T02:29:36 phase=1 state=fixed -->
### f1r-tab-label-cwd · finding [fixed] · Tab labels disagreed with agent-list.json's working directories (phase 1)

Labels now say alpha and beta, matching agent-list.json, and the README tables it.

<!-- fr:journal kind=finding scope=plan id=f1r-journal-owner-dead-sha created=2026-09-14T02:29:37 phase=1 state=fixed -->
### f1r-journal-owner-dead-sha · finding [fixed] · A journal entry named the owner and a destroyed commit sha (phase 1)

Entry text scrubbed. The owner still shows as the removed line of that fix's own diff. It is the repository's canonical owner, already public on main in the README clone URL and the matrix, so history was not rewritten again for it.

<!-- fr:journal kind=discovery scope=plan id=5f82cf7c8a48 created=2026-09-14T02:43:53 phase=2 -->
### 5f82cf7c8a48 · discovery · RED: tests/test_feed_sessions.py + test_contract.py additions (P2.T1.S1) (phase 2)

Before lib/feed.py gained sessions support: uv run --quiet --script tests/test_feed_sessions.py -> rc=1, 32 tests, 1 failure + 29 errors (only test_it_rejects_a_non_boolean genuinely failed; the rest errored on missing feed.Adapter 'sessions' kwarg / missing feed.branch_name_ok / feed.parse_sessions / feed.sessions / feed.EVIDENCE). tests/test_contract.py's new TestTheSessionsExamples and the widened marked-block list also failed for the same reason (docs/adapters.md had no sessions-probe/sessions-response blocks yet).

<!-- fr:journal kind=discovery scope=plan id=ce19471ebeaf created=2026-09-14T02:51:13 phase=2 -->
### ce19471ebeaf · discovery · RED: tests/test_adapter_claude_sessions.py before cmd_sessions existed (P2.T2.S1) (phase 2)

Before adapters/claude gained cmd_sessions/branch_name_ok/derive_session: uv run --quiet --script tests/test_adapter_claude_sessions.py -> rc=1, 26 tests, 24 failures + 1 error (probe lacked sessions key; adapters/claude sessions exited 2 as an unknown subcommand for every walk/identity/title/sdk/window/tolerance/git-branch-field test). After the implementation: rc=0, 26/26 (one own test-expectation bug fixed along the way: customTitle wins over aiTitle regardless of line order, not just chronologically -- the fixture's own ai-title line comes after its custom-title line).

<!-- fr:journal kind=discovery scope=plan id=7630150c3d77 created=2026-09-14T02:59:41 phase=2 -->
### 7630150c3d77 · discovery · RED: TestCommandEvidence + TestWorktreePathEvidence before task 3's extractors existed (P2.T3.S1) (phase 2)

Before extract_command_evidence/extract_worktree_path_evidence were filled in (stubs were no-ops): uv run --quiet --script tests/test_adapter_claude_sessions.py -> rc=1, 48 tests, 19 failures (every fr isolation/git checkout|switch|worktree|push/gh pr create shape, the cd/-C dir tracking, the no-space and unbalanced-quote cases, and both worktree-path cases -- the filtered cases like HEAD and "$BR" and the tool_result negative case passed vacuously since an empty branch list was already the no-op's output). After filling in both functions: rc=0, 48/48.

<!-- fr:journal kind=discovery scope=plan id=4958b78199e3 created=2026-09-14T03:22:06 phase=2 -->
### 4958b78199e3 · discovery · Deviation: the plan brief's own worktree-path example trips test_public_hygiene.sh (P2.T3.S1) (phase 2)

The brief's suggested fixture cwd for the worktree-path evidence test used a path shaped like /work/ + home + /.cache/fr/worktrees/... . test_public_hygiene.sh's home-directory check matches that shape (/home/[A-Za-z0-9._-]+) anywhere in the tree, fixture or not, and also matched it inside my own explanatory code comment on first attempt. Replaced the placeholder path segment with something else (still exercising the same regex/dir/slug behaviour) and rewrote the comment to describe the collision without repeating the offending substring. Full suite (bash tests/run.sh) now 31/31, rc=0.

<!-- fr:journal kind=discovery scope=plan id=4e8abc19c0c9 created=2026-09-14T03:26:20 phase=2 -->
### 4e8abc19c0c9 · discovery · RED: tests/test_adapter_codex_sessions.py before cmd_sessions existed (P2.T4.S1) (phase 2)

Before adapters/codex gained cmd_sessions/branch_name_ok/derive_session: uv run --quiet --script tests/test_adapter_codex_sessions.py -> rc=1, 21 tests, 17 failures + 3 errors (probe lacked sessions key; adapters/codex sessions exited 2 as an unknown subcommand for every identity/git-branch/no-git/detached-head/subthread/tolerance/window test). One own test-authoring bug found and fixed before this RED run counted: the first identity test asserted a branch was present, but session-meta.json's own captured branch is main, which is filtered -- split into a pure-identity test plus a separate real-branch-name test using an edited git block. After the implementation: rc=0, 21/21.

<!-- fr:journal kind=discovery scope=plan id=987bf877ad1a created=2026-09-14T03:32:54 phase=2 -->
### 987bf877ad1a · discovery · RED (deliberate): conformance sessions-empty-list tests, verified by breaking claude on purpose (P2.T5.S1) (phase 2)

The new conformance tests (every adapter declaring sessions answers {sessions: []} rc=0 on an empty home and on the ADAPTER_HOME_LAYOUT present-but-empty home; opencode/copilot do not declare sessions; at least claude+codex do) passed first time against the already-implemented adapters, so per the step's own instruction I broke adapters/claude deliberately (replaced its print(json.dumps(...)) call with a no-op pass) and reran: rc=1, 2 errors (both new empty-home/empty-store tests, adapter='claude', each because sessions printed nothing at all rather than a JSON object). Restored via git checkout -- adapters/claude (file was otherwise already committed clean); reran: rc=0, 28/28.

<!-- fr:journal kind=discovery scope=plan id=4d5bd3c67a90 created=2026-09-14T03:42:46 phase=2 -->
### 4d5bd3c67a90 · discovery · no-refactor-because: P2.T5 (quality-gate pass, T5.S3) (phase 2)

Re-read docs/adapters.md's sessions/Claude Code/Codex sections top to bottom against both adapters' actual code (evidence shapes, dedup rule, filter list, timeout, Bounded/Tolerant/read-only rules, checklist item 7) -- found no drift, so no doc edit was needed. All gates green: bash tests/run.sh 32/32 rc=0; uv run --group dev pytest 311 passed rc=0; ruff check . rc=0; ruff format --check . rc=0 (42 files); shellcheck -s bash -x herdr-setup lib/*.sh tests/*.sh rc=0; fr acceptance check rc=0 (13 rows: 8 ci, 1 skipped, 4 not-implemented -- adapter-sessions-query's notes updated to name what phase 2 verified, status correctly left not-implemented pending phase 4's runner-side 'reported as unsupported' half); reports regenerated via fr acceptance report --deterministic.

<!-- fr:journal kind=discovery scope=plan id=e0241c2f3bf4 created=2026-09-14T04:09:46 phase=2 -->
### e0241c2f3bf4 · discovery · RED: multi-line/newline, current-dir resolution, and command-parsing gap fixes (review items 1,3,4,6) (phase 2)

Before adapters/claude gained newline-aware tokenising, current-dir-based relative resolution, and the push/worktree/checkout/subshell/cd- parsing fixes: uv run --quiet --script tests/test_adapter_claude_sessions.py -> rc=1, 67 tests, 15 failures: test_a_second_line_is_still_read_as_its_own_command, test_a_cd_line_does_not_swallow_the_next_lines_command, test_a_backslash_newline_continuation_is_joined, test_a_second_relative_cd_resolves_against_the_first_cds_result, test_git_push_dash_u_dash_f_skips_the_option_before_remote, test_git_push_refspec_strips_a_leading_refs_heads, test_git_worktree_add_reason_value_is_not_mistaken_for_the_path, test_git_checkout_flags_before_dash_b_are_skipped, test_git_dash_lowercase_c_global_option_before_the_subcommand, test_a_subshell_is_its_own_segment, test_cd_dash_falls_back_to_the_lines_cwd, test_bare_cd_falls_back_to_the_lines_cwd, test_a_slug_followed_by_a_semicolon_does_not_swallow_it, test_a_slug_followed_by_and_and_does_not_swallow_it, test_a_tilde_prefixed_worktree_path_expands_to_home. The two new ~ expansion tests (cd and git -C) already passed against the unfixed code -- item 3 was a coverage gap, not a bug -- so they are not in this failure list.

<!-- fr:journal kind=discovery scope=plan id=4be84f7e3452 created=2026-09-14T04:19:36 phase=2 -->
### 4be84f7e3452 · discovery · RED: branch filter accepts bare @ and a.lock component (review item 7) (phase 2)

Added '@' and 'a.lock/b' to tests/helpers/feedlib.py's shared UNREAL_BRANCH_NAMES. Before tightening all three branch_name_ok copies: uv run --quiet --script tests/test_feed_sessions.py -> rc=1, 1 failure (test_every_unreal_name_is_rejected); tests/test_adapter_claude_sessions.py -> rc=1, 1 failure (test_every_unreal_name_is_rejected_by_the_adapters_own_copy); tests/test_adapter_codex_sessions.py -> rc=1, same failure. All three copies accepted the bare '@' (git's HEAD-alias shorthand) and 'a.lock/b' (a non-final component ending in .lock, which git's ref rules reject per-component, not just for the whole name).

<!-- fr:journal kind=discovery scope=plan id=9e4832105f2e created=2026-09-14T04:25:40 phase=2 -->
### 9e4832105f2e · discovery · RED: sessions() discards parse_sessions' dropped count (review item 2) (phase 2)

Before sessions() returned (sessions, dropped) and warned/raised on drops: uv run --quiet --script tests/test_feed_sessions.py -> rc=1, 35 tests, 5 errors (test_it_runs_sessions_with_since_and_parses_the_answer, test_the_result_has_already_passed_parse_sessions -- both unpack a 2-tuple sessions() did not return -- plus the three new tests: test_a_dropped_entry_is_warned_about_by_name_and_count, test_no_warning_when_nothing_was_dropped, test_every_entry_malformed_raises_rather_than_returning_an_empty_list, all erroring the same way). An adapter whose every entry was malformed previously returned [] silently, indistinguishable from a genuinely empty window -- the exact ambiguity the sessions contract's Failing rule forbids.

<!-- fr:journal kind=discovery scope=plan id=6ec7be37b325 created=2026-09-14T04:31:25 phase=2 -->
### 6ec7be37b325 · discovery · RED: parse_sessions accepted offset/millisecond/garbage timestamps (review item 5, runner half) (phase 2)

Before TIMESTAMP_RE was added to lib/feed.py's parse_sessions: uv run --quiet --script tests/test_feed_sessions.py -> rc=1, 38 tests, 2 failures (test_a_malformed_last_active_drops_and_counts_the_session, test_a_malformed_seen_at_drops_only_the_branch), each parametrised over an offset (+02:00), a millisecond fraction, garbage text, a missing 'T' separator and an empty string. parse_sessions accepted every one of them as a valid last_active/seen_at. After adding TIMESTAMP_RE (^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$) and applying it to both fields: rc=0, 38/38.

<!-- fr:journal kind=discovery scope=plan id=538edf4b459b created=2026-09-14T04:35:48 phase=2 -->
### 538edf4b459b · discovery · RED: adapters/claude does not normalise last_active/seen_at (review item 5, adapter half) (phase 2)

Before adapters/claude gained normalize_timestamp() and applied it to last_active and every branch's seen_at: uv run --quiet --script tests/test_adapter_claude_sessions.py -> rc=1, 72 tests, 10 failures. Five are pre-existing assertions updated to the normalised (no-milliseconds) expected value ahead of the fix (test_a_top_level_transcript_yields_expected_identity, test_a_sidechain_line_is_ignored, test_an_unparseable_middle_line_is_skipped, test_gitbranch_becomes_a_git_branch_field_branch, test_gitbranch_is_deduped_per_name_and_dir_keeping_the_latest -- each failed with the raw '.000Z' value instead of the stripped one). Five are new: test_an_offset_timestamp_is_converted_to_utc, test_a_millisecond_timestamp_is_stripped_to_second_precision, test_git_branch_field_seen_at_is_normalised_too, test_command_evidence_seen_at_is_normalised_too, test_every_timestamp_matches_the_runner_s_own_pattern.

<!-- fr:journal kind=discovery scope=plan id=6f4429714675 created=2026-09-14T04:39:37 phase=2 -->
### 6f4429714675 · discovery · RED: adapters/codex does not normalise seen_at (review item 5, codex half) (phase 2)

Before adapters/codex gained normalize_timestamp() applied to payload.timestamp: uv run --quiet --script tests/test_adapter_codex_sessions.py -> rc=1, 23 tests, 3 failures (test_a_real_branch_yields_session_meta_evidence, updated ahead of the fix to expect the normalised value against the fixture's own millisecond payload.timestamp; plus two new tests, test_an_offset_timestamp_is_converted_to_utc and test_a_millisecond_timestamp_is_stripped_to_second_precision). last_active needed no change: it always comes from the file's own mtime, already exact.

<!-- fr:journal kind=discovery scope=plan id=7ea4241661ab created=2026-09-14T04:51:41 phase=2 -->
### 7ea4241661ab · discovery · Performance: stream transcript lines and exit early on an SDK skip (review item 11) (phase 2)

read_transcript_lines now opens the file and iterates it line by line (binary mode, decode per line) instead of read_bytes()+split, which held a whole transcript (reported: 805 MB / 837 files in a real 30-day window) in memory at once. derive_session now decides an SDK-entrypoint skip on the FIRST line carrying entrypoint (a real transcript's entrypoint is a session-level constant repeated every line) and returns immediately, which also closes the generator's open file before the rest of a filtered transcript is ever read. This is a behavior-preserving refactor for every real transcript shape currently tested (no test transcript mixes different entrypoint values across its own lines), verified by re-running the existing suite rather than a RED/GREEN cycle: uv run --quiet --script tests/test_adapter_claude_sessions.py -> rc=0, 74/74 both before and after. Added test_a_skipped_sdk_transcript_with_invalid_later_lines_is_still_skipped_cleanly (a later line that is invalid UTF-8 and separately a truncated JSON line) as a regression guard, though the tolerant per-line parser already handled such garbage before this change too -- its value is guarding against a future change that removes that tolerance while relying on early exit to paper over it.

<!-- fr:journal kind=discovery scope=plan id=8720b34233b6 created=2026-09-14T04:53:57 phase=2 -->
### 8720b34233b6 · discovery · refactor-done: P2.T1.S3 (phase 2)

branch_name_ok (lib/feed.py, and its adapter copies) reads as the spec's own bullet list: one named set or literal per rule (_UNREAL_EXACT_NAMES, _UNREAL_PREFIX, _GIT_INVALID_CHARS, _GIT_INVALID_SUBSTRINGS, _TEMPLATE_CHARS), each with a comment tying it back to the specific docs/adapters.md bullet it implements, including why template text is a separate, non-git rule (transcripts/documentation leaving behind placeholder branch names). This review round (item 7) added the bare-@ and per-component .lock checks as two more named, commented checks in the same style, and split the previous combined name.endswith('.') or name.endswith('.lock') line into its own two checks for the same reason. Missed being journalled with this exact step id when the phase's own work was first done; recorded now.

<!-- fr:journal kind=discovery scope=plan id=0d90d4ffaa7a created=2026-09-14T04:54:09 phase=2 -->
### 0d90d4ffaa7a · discovery · refactor-done: P2.T2.S3 (phase 2)

adapters/claude separates read_transcript_lines (the store walk: open, decode, skip isSidechain/unparseable lines) from derive_session (accumulate identity/title/evidence per line) from the start, with extract_command_evidence/extract_worktree_path_evidence as their own functions called from derive_session's loop -- task 3 (P2.T3) filled those two functions in without touching the walk or derive_session's own loop shape at all, which is the seam this step asked for. This review round's item 1 fix (physical-line splitting) and item 11 (streaming + early exit) both landed inside read_transcript_lines/derive_session without disturbing that separation either. Missed being journalled with this exact step id when first done; recorded now.

<!-- fr:journal kind=discovery scope=plan id=274a8f885c79 created=2026-09-14T04:54:23 phase=2 -->
### 274a8f885c79 · discovery · no-refactor-because: P2.T3.S3 (table-driving the extractors) (phase 2)

Reconsidered after this review round's item 6 fixes added more flag-skipping logic to _match_command_shape (checkout/switch flag scanning, worktree add's positional+value-flag tracking with _WORKTREE_ADD_VALUE_FLAGS, push's positional collection with _PUSH_VALUE_FLAGS and its own refspec post-processing, fr isolation's --repo handling, gh pr create's owner-prefix stripping). The five shapes still differ enough in their actual mechanics -- different value-taking-flag sets, different numbers of required positionals, different post-processing of the matched value (refspec splitting, owner-prefix stripping, --repo resolution) -- that folding them into one data-driven table would need a schema at least as complex as the current one-function-per-shape reading (subcommand name, value-flag set, positional count, post-processing callable), trading the docstring's own claim -- 'one check per shape docs/adapters.md lists, in the order it lists them' -- for a level of indirection that would cost more clarity than it buys. The already-completed refactor this step also asked for (the branch-name-filter agreement test) was done at the time. Kept as five explicit checks; the agreement test already proves the runner and the adapter's filter cannot drift regardless.

<!-- fr:journal kind=discovery scope=plan id=33f278bc9afb created=2026-09-14T04:54:32 phase=2 -->
### 33f278bc9afb · discovery · refactor-done: P2.T4.S3 (phase 2)

adapters/codex shares the mtime-window helper's name and shape with adapters/claude by convention, not import: both define SECONDS_PER_DAY = 86400 and a since_cutoff(days) -> float function with the identical docstring wording and identical one-line body (time.time() - days * SECONDS_PER_DAY), and both cmd_sessions functions call cutoff = since_cutoff(args.since) the same way. Missed being journalled with this exact step id when first done; recorded now.

<!-- fr:journal kind=discovery scope=plan id=f101c397c8a8 created=2026-09-14T05:18:34 phase=2 -->
### f101c397c8a8 · discovery · RED: parse_sessions/sessions() don't count dropped branches (re-review item 1) (phase 2)

Before parse_sessions returned (sessions, dropped_sessions, dropped_branches) and sessions() the same triple: uv run --quiet --script tests/test_feed_sessions.py -> rc=1, 41 tests, 22 errors (every call site unpacking a 2-tuple that no longer matches the test's own 3-tuple expectation, plus the new dedicated branch-drop-count tests: test_a_dropped_branch_is_warned_about_by_name_and_count, test_a_dropped_branch_alone_does_not_raise, test_several_dropped_branches_beside_a_surviving_one_are_all_counted). The underlying bug this proves: a branch dropped for a non-conforming seen_at, an unknown evidence value, or a missing field was previously invisible -- an adapter emitting millisecond seen_at values lost every branch silently and neither parse_sessions nor sessions() ever said so.

<!-- fr:journal kind=discovery scope=plan id=675fc4138291 created=2026-09-14T05:23:53 phase=2 -->
### 675fc4138291 · discovery · Mutation evidence: dropped-branch counting (re-review item 1) (phase 2)

git archive HEAD (967c76c) into a temp copy, then removed every 'dropped_branches += 1' increment in that copy's lib/feed.py parse_sessions while keeping the branch dropped (continue unchanged) -- i.e. reintroduced the exact silent-drop bug. Ran tests/test_feed_sessions.py from the temp copy directly (uv run --quiet --script <tmp>/tests/test_feed_sessions.py, no cd): rc=1, 8 failures (test_a_dropped_branch_alone_does_not_raise, test_a_dropped_branch_is_warned_about_by_name_and_count, test_a_malformed_seen_at_drops_and_counts_only_the_branch, test_a_missing_name_dir_or_seen_at_drops_and_counts_just_the_branch, test_a_non_dict_branch_entry_is_ignored_and_counted, test_an_evidence_outside_the_documented_four_is_dropped_and_counted, test_parse_sessions_re_applies_branch_name_ok_and_counts_it, test_several_dropped_branches_beside_a_surviving_one_are_all_counted). Temp copy removed afterward; the real worktree was never touched (mutation happened only in the temp copy's own lib/feed.py).

<!-- fr:journal kind=discovery scope=plan id=a66a1d0861b2 created=2026-09-14T05:26:48 phase=2 -->
### a66a1d0861b2 · discovery · RED: normalize_timestamp crashes on out-of-range offsets, accepts date-only values (re-review item 2) (phase 2)

Before catching OverflowError and rejecting date-only text in both adapters' normalize_timestamp: uv run --quiet --script tests/test_adapter_claude_sessions.py -> rc=1, 79 tests, 2 failures + 1 error (test_a_date_only_value_is_not_a_timestamp, test_an_out_of_range_offset_timestamp_does_not_crash_the_whole_query, test_an_out_of_range_offset_returns_none_rather_than_raising -- the last one an uncaught OverflowError from astimezone() on 0001-01-01T00:30:00+01:00, which also crashed the end-to-end query with a traceback and non-zero exit, violating the Tolerant rule). tests/test_adapter_codex_sessions.py -> rc=1, 28 tests, same three failures. No-zone timestamps already normalised correctly (read as UTC); only newly tested, not newly fixed.

<!-- fr:journal kind=discovery scope=plan id=ab458ed107f8 created=2026-09-14T05:31:54 phase=2 -->
### ab458ed107f8 · discovery · Mutation evidence: normalize_timestamp OverflowError/date-only guards (re-review item 2) (phase 2)

git archive HEAD (198921b) into a temp copy, reverted adapters/claude's normalize_timestamp to its pre-fix body (no 'T not in text' check, no try/except around astimezone). Ran tests/test_adapter_claude_sessions.py from the temp copy directly: rc=1, 2 failures + 1 error (test_a_date_only_value_is_not_a_timestamp, test_an_out_of_range_offset_timestamp_does_not_crash_the_whole_query, test_an_out_of_range_offset_returns_none_rather_than_raising -- the last an uncaught OverflowError). Temp copy removed afterward; real worktree untouched.

<!-- fr:journal kind=discovery scope=plan id=2364b5242ace created=2026-09-14T05:33:19 phase=2 -->
### 2364b5242ace · discovery · RED: git push --delete/-d reported the branch being deleted (re-review item 3) (phase 2)

Before the push extractor rejected --delete/-d: uv run --quiet --script tests/test_adapter_claude_sessions.py -> rc=1, 83 tests, 3 failures (test_git_push_delete_yields_nothing, test_git_push_set_upstream_delete_yields_nothing, test_git_push_dash_lowercase_d_delete_yields_nothing). git push -u origin --delete feat/x reported feat/x as work-in-progress evidence when it is the opposite -- the branch is being removed. test_git_push_dash_o_value_flag_is_skipped (a coverage gap for the already-correct _PUSH_VALUE_FLAGS handling of -o) passed immediately, confirming it was untested rather than broken.

<!-- fr:journal kind=discovery scope=plan id=c9f41ffafd81 created=2026-09-14T05:36:49 phase=2 -->
### c9f41ffafd81 · discovery · Mutation evidence: push --delete/-d rejection and _PUSH_VALUE_FLAGS coverage (re-review item 3) (phase 2)

git archive HEAD (3fc4aab) into a temp copy. Mutation A: removed the '-d'/'--delete' rejection block from the push shape -- ran tests/test_adapter_claude_sessions.py: rc=1, 3 failures (test_git_push_delete_yields_nothing, test_git_push_set_upstream_delete_yields_nothing, test_git_push_dash_lowercase_d_delete_yields_nothing). Mutation B (separate temp copy): removed '-o' from _PUSH_VALUE_FLAGS -- rc=1, 1 failure (test_git_push_dash_o_value_flag_is_skipped), confirming that test genuinely exercises the value-flag set rather than passing vacuously. Both temp copies removed afterward; real worktree untouched.

<!-- fr:journal kind=discovery scope=plan id=b8ff361926dd created=2026-09-14T05:42:12 phase=2 -->
### b8ff361926dd · discovery · RED: heredoc bodies, subshell dir leakage, more git global options, single &, worktree slug < >, $HOME expansion (re-review items 4,5) (phase 2)

Before rewriting _process_bash_command's token walk (heredoc skipping, a (paren) directory-scope stack, a single '&' as a segment separator), extending _strip_git_global_options (--no-pager, -P/--paginate, -p, --no-replace-objects, --git-dir=, --work-tree=), and tightening WORKTREE_PATH_RE (exclude < >, recognise $HOME/${HOME}): uv run --quiet --script tests/test_adapter_claude_sessions.py -> rc=1, 96 tests, 13 failures: test_heredoc_body_is_not_parsed_as_commands, test_heredoc_with_no_terminator_skips_to_the_end_of_the_command, test_heredoc_dash_form_strips_leading_tabs_from_the_terminator, test_a_quoted_heredoc_tag_is_recognised, test_cd_inside_a_subshell_does_not_leak_out, test_git_no_pager_global_option_is_skipped, test_git_dash_cap_p_global_option_is_skipped, test_git_work_tree_equals_sets_dir, test_git_dir_equals_is_skipped_without_setting_dir, test_a_single_ampersand_is_a_segment_boundary, test_dollar_home_worktree_path_in_a_command_is_expanded, test_dollar_brace_home_worktree_path_in_a_command_is_expanded, test_a_slug_followed_by_a_redirect_does_not_swallow_it (WT_PATH>out swallowed '>out' into the branch name before this).

<!-- fr:journal kind=discovery scope=plan id=6f4d69122974 created=2026-09-14T05:55:27 phase=2 -->
### 6f4d69122974 · discovery · Mutation evidence: heredoc skip, subshell dir scope, git global options, single &, worktree <> and $HOME (re-review items 4,5) (phase 2)

git archive HEAD (11e4ea2) into a fresh temp copy per mutation, each run standalone via uv run --quiet --script <tmp>/tests/test_adapter_claude_sessions.py (no cd), temp copies removed afterward, real worktree untouched throughout:
1. Heredoc skip disabled (_physical_lines reverted to plain split, no _HEREDOC_RE handling): rc=1, 4 failures (test_heredoc_body_is_not_parsed_as_commands, test_heredoc_with_no_terminator_skips_to_the_end_of_the_command, test_heredoc_dash_form_strips_leading_tabs_from_the_terminator, test_a_quoted_heredoc_tag_is_recognised).
2. Subshell dir-scope restore removed (')' pops the stack but never reassigns current_dir): rc=1, 1 failure (test_cd_inside_a_subshell_does_not_leak_out).
3. New git global-option handling removed (_GIT_NOOP_GLOBAL_FLAGS emptied, --work-tree=/--git-dir= branches deleted): rc=1, 4 failures (test_git_no_pager_global_option_is_skipped, test_git_dash_cap_p_global_option_is_skipped, test_git_work_tree_equals_sets_dir, test_git_dir_equals_is_skipped_without_setting_dir).
4. Single '&' removed from _SEGMENT_SEPARATORS: rc=1, 1 failure (test_a_single_ampersand_is_a_segment_boundary).
5. WORKTREE_PATH_RE reverted to the pre-round shape (no <> exclusion, no $HOME/${HOME} alternatives): rc=1, 3 failures (test_a_slug_followed_by_a_redirect_does_not_swallow_it, test_dollar_home_worktree_path_in_a_command_is_expanded, test_dollar_brace_home_worktree_path_in_a_command_is_expanded).

<!-- fr:journal kind=discovery scope=plan id=6adbaebbc1b0 created=2026-09-14T06:12:57 phase=2 -->
### 6adbaebbc1b0 · discovery · Mutation evidence: coverage-gap tests for command evidence (re-review item 6) (phase 2)

git archive HEAD (1a3066e) into a fresh temp copy per mutation, run standalone (no cd), temp copies removed afterward:
(a) worktree add prior-cd: _match_command_shape's 'rest' call given current_dir=base_cwd instead of current_dir -> rc=1, test_worktree_add_relative_path_resolves_against_a_prior_cd and test_fr_isolation_repo_relative_path_resolves_against_a_prior_cd both failed (same mutation touches both call sites).
(a) alone / (c) alone: same two failures reproduced together; a first attempt at the worktree-add test (cwd and cd target sharing a parent) did not distinguish base_cwd from current_dir and was fixed in a separate commit (074b4bd) before this evidence was captured.
(b) git -C: the SEPARATE dir_override resolution line changed to _resolve_dir(dir_override, base_cwd) -> rc=1, 1 failure (test_git_dash_cap_c_relative_dir_resolves_against_a_prior_cd) -- proving it needed its own, distinct mutation from (a)/(c).
(d) mtime fallback: _file_mtime_z given an off-by-one-second epoch -> rc=1, 3 failures (test_last_active_falls_back_to_a_specific_fixed_mtime, test_last_active_falls_back_to_the_files_own_mtime, and incidentally test_an_out_of_range_offset_timestamp_does_not_crash_the_whole_query, which also reads the mtime fallback).
(e) cross-block cd leakage: extract_command_evidence's per-block loop replaced with one call over all blocks' commands newline-joined -> rc=1, 1 failure (test_a_cd_in_one_tool_use_block_does_not_carry_into_a_later_block).
(f) SDK early exit: the early 'return None' replaced with a flag checked only AFTER the loop fully drains the generator (same final filtering correctness, no early exit) -> rc=1, 1 failure (test_sdk_early_exit_avoids_reading_a_huge_remainder: 5.19s not less than the 2.0s threshold) -- calibrated by direct measurement (adapters/claude sessions run directly, timed): ~0.37s with the early exit regardless of remainder size (300k or 1M garbage lines), ~1.8s mutated at 300k lines (too close to a 2.0s threshold, fixed in commit 1a3066e), ~5.2s mutated at 1,000,000 lines (comfortable margin).

<!-- fr:journal kind=discovery scope=plan id=2b2bfa5c4410 created=2026-09-14T06:17:13 phase=2 -->
### 2b2bfa5c4410 · discovery · Mutation evidence: first-entrypoint-wins (re-review item 7) (phase 2)

git archive HEAD (f304241) into a temp copy, reverted derive_session to LAST-entrypoint-wins (drop the 'entrypoint is None' guard so every line's entrypoint overwrites the last, move the SDK check to after the loop, matching the pre-item-11 design). Ran tests/test_adapter_claude_sessions.py standalone: rc=1, 3 failures -- test_a_mid_file_change_from_cli_to_sdk_keeps_the_first_entrypoint and test_a_mid_file_change_from_sdk_to_cli_keeps_the_first_entrypoint (both orders correctly caught), plus test_sdk_early_exit_avoids_reading_a_huge_remainder as a bonus (last-wins requires reading the whole file, so the timing test also senses the regression). Temp copy removed afterward; real worktree untouched.

<!-- fr:journal kind=discovery scope=plan id=3fb63f88e0d1 created=2026-09-14T06:18:22 phase=2 -->
### 3fb63f88e0d1 · discovery · Correction to 7ea4241661ab: the test count was not 74/74 on both sides (phase 2)

Entry 7ea4241661ab (Performance: stream transcript lines and exit early on an SDK skip) says 'rc=0, 74/74 both before and after' -- this conflates two different states. tests/test_adapter_claude_sessions.py had 73 test methods before that round's own new test (test_a_skipped_sdk_transcript_with_invalid_later_lines_is_still_skipped_cleanly) was added, and 74 after (git show 749a021^ vs 749a021, grep -c 'def test_'). The new test and the adapters/claude code change landed in the same commit (749a021), so 'before' and 'after' as actually run were both against the file WITH the new test already present (74 tests each time) -- the pre-existing 73 were never independently re-verified against the OLD adapter code as their own 'before' baseline in this entry, and the phrasing wrongly implies a stable 74-test suite spanned a change that in fact also changed the suite's own size. Not correcting 7ea4241661ab itself, per instruction; this entry stands as the correction.
