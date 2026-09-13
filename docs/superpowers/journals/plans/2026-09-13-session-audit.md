# Journal: 2026-09-13-session-audit

<!-- fr:journal kind=decision scope=plan id=d-tier-models created=2026-09-13T22:33:48 -->
### d-tier-models · decision · Phase tiers map to models per dispatch, not via a global binding

fr models resolve --harness claude binds standard (claude-sonnet-5) but not hard; the operator's models.yaml uses the legacy cheap/standard/premium keys. Rather than write the operator's global ~/.config/fr/models.yaml unasked, each fr-phase-executor dispatch passes the model directly: standard -> sonnet (phases 1, 2, 4), hard -> opus (phase 3), mirroring the operator's own premium=claude-opus-5 binding.

<!-- fr:journal kind=discovery scope=plan id=b80778f12afd created=2026-09-13T23:11:09 phase=1 -->
### b80778f12afd · discovery · no-refactor-because: P1.T2 (phase 1)

cmd_install/cmd_install_warn and hs_resolve_path read clearly at GREEN (each refusal is its own guarded return, one stderr line, matching the design table row for row); shellcheck is clean and ruff needs no changes. No extraction was warranted, so P1.T2.S3 is this note rather than a diff.

<!-- fr:journal kind=discovery scope=plan id=d1cfa5206b74 created=2026-09-14T00:15:29 phase=1 -->
### d1cfa5206b74 · discovery · RED: tests/test_fixture_owner_hygiene.sh (P1.T3 fixup, item A.7) (phase 1)

Ran against the pre-fix fixtures (664e228): rc=1, 2 passed, 2 failed. FAIL 1: the matrix's own owner ('YiannisDermitzakis') appears in tests/fixtures/gh/README.md:13. FAIL 2: tests/fixtures/gh/owner-pull-requests.json's endCursor ('[a real, since-rotated cursor value]') decodes to more than the sanctioned cursor:v2:placeholder payload -- it embeds a real repository id. The guard-on-the-guard nested cases (2 passed) already proved the checks fire on a poisoned copy. Fixes 1-3 (owner-string prose rewrite, category-only masking prose, synthetic cursor) are what turns this green.

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
