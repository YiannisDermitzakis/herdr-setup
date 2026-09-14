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
