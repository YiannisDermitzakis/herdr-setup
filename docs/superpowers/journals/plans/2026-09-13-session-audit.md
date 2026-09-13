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
