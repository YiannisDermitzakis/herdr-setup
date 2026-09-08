# Journal: 2026-09-08-herdr-setup

<!-- fr:journal kind=discovery scope=plan id=5089dcc1b70e created=2026-09-08T09:47:36 phase=1 -->
### 5089dcc1b70e · discovery · tests/helpers/ needs a herdr symlink, not just fake-herdr (phase 1)

lib/common.sh and the entrypoint call the bare `herdr` command (never
`fake-herdr`). PATH resolution therefore needs a file literally named
`herdr` on tests/helpers. This dev machine has a real herdr installed at
/usr/local/bin/herdr, so getting this wrong does not error — it silently
falls through to the real binary and every preflight/hs_herdr_json test
would exercise the wrong command with no obvious symptom beyond wrong
output. Fixed by adding `tests/helpers/herdr` as a relative symlink
(`ln -s fake-herdr herdr`) alongside the fake-herdr script, so `command -v
herdr` on tests/helpers-prefixed PATH resolves to the symlink (whose target
is the real logic). Anything in a later phase that spawns its own PATH
(adapters, feed.py subprocess calls) must include tests/helpers on PATH the
same way run.sh does, or it will hit the real system herdr instead of the
fake one.

<!-- fr:journal kind=finding scope=plan id=998bc29faa4a created=2026-09-08T09:54:14 phase=1 state=fixed -->
### 998bc29faa4a · finding [fixed] · lib/common.sh used 'status' as a local, which is read-only in zsh (phase 1)

Verifying the phase-1 gates by sourcing lib/common.sh into an interactive zsh produced 'read-only variable: status' and hs_preflight returned an empty state, which hs_require_socket then reported as an unrecognized state. Harmless in normal use, because every script carries a bash shebang, but it is a trap for anyone sourcing the library and it would have been copied into later phases. Renamed the locals to rc. Also changed the herdr invocation from 2>/dev/null to 2>&1 so an error herdr writes to stderr still reaches the operator instead of producing an empty message. Behaviour under bash is unchanged and the suite stays green; the library now sources cleanly under both shells.

<!-- fr:journal kind=discovery scope=plan id=2b06b9db2361 created=2026-09-08T10:16:15 phase=2 -->
### 2b06b9db2361 · discovery · cmd_diff's section-accumulation contract, and where the plugin logic lives (phase 2)

cmd_diff (herdr-setup) is now a thin dispatcher over per-area section
functions that live in lib/common.sh, not inline logic. The plugin section
is hs_diff_plugins(manifest_file, plugins_json): it prints its own report
lines to stdout and returns 0 (all ok/unmanaged), 1 (something missing or
moved), or 2 (manifest or host plugins.json failed to parse).

cmd_diff's own body is exactly this shape, and phase 3 should follow it for
the config and integration sections:

    cmd_diff() {
      local rc=0
      local plugins_rc=0

      hs_diff_plugins "$HS_ROOT/manifest/plugins.list" "$(hs_plugins_json_path)" || plugins_rc=$?

      if [ "$plugins_rc" -eq 2 ]; then
        return 2
      fi
      if [ "$plugins_rc" -ne 0 ]; then
        rc=1
      fi

      return "$rc"
    }

The critical detail: a section is ALWAYS invoked as `section || section_rc=$?`,
never as a bare statement. herdr-setup runs under `set -euo pipefail`; a bare
call to a function that returns nonzero (1 for drift, 2 for a hard error)
trips errexit and kills the script before you can read $?. The `||` form is
safe because the failing command is not the last in the list. Do the same for
hs_diff_config / hs_diff_integrations (whatever you name them): call each
with `|| section_N_rc=$?`, escalate to `return 2` immediately on a hard
error from any section (matching the existing plugins_rc==2 check), otherwise
OR the non-2 failures into one `rc=1` and fall through to run every section
regardless (the design doc: "diff ... Exit 0 when the host matches, 1 when it
drifts, 2 on error" -- and P3.T2.S1 wants the integrations section to never
affect the exit status at all, only config and plugins do).

Two new lib/common.sh helpers phase 3 will likely want, both already in
place: hs_herdr_config_dir() (override: HERDR_CONFIG_DIR, default
$HOME/.config/herdr) and hs_plugins_json_path() = "$(hs_herdr_config_dir)/plugins.json".
The host's config.toml almost certainly belongs at
"$(hs_herdr_config_dir)/config.toml" -- add an hs_config_toml_path() next to
it rather than inventing a new env var.

hs_preflight_banner (P3.T2.S2) should be the FIRST thing cmd_diff prints, so
you'll need to restructure cmd_diff to print that line before running the
plugin section too, not just before your own new sections.

Testing pattern for a real end-to-end `herdr-setup diff` invocation, established
in tests/test_diff_plugins.sh: HS_ROOT is derived from $0 by fully resolving
symlinks, so it cannot be overridden by env var. To test cmd_diff without
touching this checkout's own manifest/ (which doesn't exist yet -- phase
5/10's job), copy herdr-setup and lib/common.sh into a $work/sandbox
directory, mkdir $work/sandbox/manifest, write fixtures there, and invoke
$work/sandbox/herdr-setup directly. HS_ROOT then resolves to the sandbox,
completely isolated from the real repo.

Also fixed in phase 2: hs_manifest_plugins now fails closed (exit 2, one
stderr line naming the file) on a MISSING or unreadable manifest file, not
just a malformed line -- AGENTS.md's "an unreadable manifest ... stops the
run with one clear line" names this case explicitly. This is why
tests/test_entrypoint.sh's "diff is accepted and exits 0" assertion was
changed to expect exit 2 in this checkout (no manifest/ committed yet) --
the other four (still-stub) subcommands keep exiting 0, and the flag
pass-through checks (--dry-run/--yes visible as HS_DRY_RUN/HS_YES) were moved
to use `apply` instead of `diff`, since diff no longer echoes the flags it saw.

Acceptance matrix: diff-detects-plugin-drift and diff-runs-under-protocol-mismatch
are implemented and unit-tested (tests/test_diff_plugins.sh,
tests/test_manifest.sh, tests/test_hostplugins.sh) but were deliberately left
at status: not-implemented in docs/acceptance/matrix.yaml, per this plan's own
established convention (phase 1 did the same for socket-commands-refuse-under-mismatch):
phase 10 (P10.T2.S2) does the bulk flip to `ci` with level references, once
.github/workflows/ci.yml actually runs tests/run.sh. Notes on both rows explain
the phase-2 coverage and point at phase 10 as the flip point -- don't flip them
in phase 3 either; just add a similar note when phase 3's config/integration
tests land.

<!-- fr:journal kind=decision scope=plan id=0e3bba1e3da0 created=2026-09-08T10:32:34 phase=2 -->
### 0e3bba1e3da0 · decision · Python comes from uv, pinned per script, not from the host (phase 2)

Operator decision, taken after phase 2. The portability floor was system bash 3.2 plus whatever python3 the host had, at least 3.9, stdlib only. It is now: bash 3.2 for the shell, and for Python a uv-resolved interpreter named in each file's PEP 723 header, with a '#!/usr/bin/env -S uv run --script' shebang. uv joins Herdr and git as a prerequisite and also pins the dev tools (pytest, ruff) through a dependency group in pyproject.toml. Rationale: the tool's purpose is making hosts identical, so depending on whichever interpreter a host happens to carry works against it; and it matches how the operator already installs fr and browser-harness. Measured cost on this host: a uv start is about 265ms against about 84ms for a bare system python3, so the interpreter must stay off hot paths. hs_herdr_json runs once per Herdr call and now parses the error line in shell with sed; all structured work is batched behind subcommands of lib/hs.py, reached only through hs_py, which is also where the uv-missing check lives. tomllib is now available but config parsing stays line-based, because plugin-written blocks and the operator's formatting must survive byte for byte and a TOML round trip would discard both. Phases 6, 8 and 10 were rewritten to match; phase 2's own step text was corrected to describe what now exists.

<!-- fr:journal kind=discovery scope=plan id=06856d9d6197 created=2026-09-08T10:50:15 phase=3 -->
### 06856d9d6197 · discovery · Block splitter contract, cmd_diff's config/integration sections, and what apply (phase 4) needs (phase 3)

Two new lib/common.sh primitives, both bash-3.2 loops (no awk, no associative
arrays), and both fail the same way: fatal, exit 2, one stderr line naming the
file, line number and plugin id, on an unterminated block (begin with no
matching end) or a stray/mismatched end marker (no open block, or an id that
does not match the currently open one).

hs_strip_plugin_blocks <file>: prints every line OUTSIDE a plugin-written
block. hs_extract_plugin_blocks <file>: prints every line INSIDE one,
**markers included** (both the `# --- added by <id> ...` and the
`# --- end <id> ---` line come out with the block). This was a deliberate
choice, not the only reading of "prints only those regions": apply (phase 4)
needs the markers back verbatim to re-splice a block as a still-valid,
still-identifiable plugin block, not just bare content. Both share one
internal engine, `_hs_plugin_block_walk <file> <mode>` (mode=strip|extract) --
don't duplicate the marker-matching logic if you touch this again, extend the
walker.

What apply likely needs to do the reverse: hs_strip_plugin_blocks(host_config)
gives the operator-line skeleton to update from manifest/config.toml;
hs_extract_plugin_blocks(host_config) gives the ordered list of blocks
(markers included) to preserve untouched. The two outputs partition the
host file's lines exactly -- concatenating strip's output back together
with extract's blocks reinserted at their original relative position
(blocks stay in their original order and appear after all the operator
lines that preceded them in the source, since strip and extract both walk
top to bottom) reconstructs the host file. Neither function currently
tags a block with a line number or anchor for re-insertion elsewhere in a
*different* file (e.g. spliced into an updated manifest skeleton) -- if
apply needs "insert this block after this operator line", that positional
bookkeeping doesn't exist yet and would need its own pass, possibly a third
mode on the same walker.

cmd_diff (herdr-setup) is now: hs_preflight_banner (always first, one line,
never fails) -> plugin section (hs_diff_plugins, hard-error escalates to
`return 2` immediately, skipping config/integrations) -> config section
(hs_diff_config, same hard-error contract) -> integration section
(hs_diff_integrations, invoked as `hs_diff_integrations || true`, return
value never consulted, never touches $rc). Every section call uses the
established `section || section_rc=$?` shape -- never bare -- because of
`set -e`.

hs_diff_config <host-config> <manifest-config>: strips the host file, diffs
it against the manifest file with `diff -u -L host -L manifest` (BSD diff on
macOS supports -L same as GNU's --label; verified on this host). "config
match" / rc=0 on identity, "config drift:\n<unified diff>" / rc=1 otherwise.
A missing host file is treated as empty (a host that never ran `apply`), not
an error. A missing/unreadable manifest file is the fail-closed case, exit 2,
matching hs_manifest_plugins' contract. New hs_config_toml_path() sits next
to hs_plugins_json_path() in common.sh, same HERDR_CONFIG_DIR override.

hs_diff_integrations: calls `herdr integration status` directly (never
through hs_herdr_json -- this is plain text, not the JSON-with-error-object
shape, and it does not cross the socket, so it must keep working under a
mismatch or with no server). Filters out `<agent>: not installed (...)`
lines (an integration section reports on *installed* integrations only, per
the design doc's "Lists each installed integration"); everything else is
echoed back prefixed `integration: `. Always returns 0, same contract as
hs_preflight -- cmd_diff never lets its return value move $rc. No herdr on
PATH at all degrades to one informational line rather than failing the
whole `diff` run.

Test gotcha for anyone adding another end-to-end cmd_diff sandbox test
(tests/test_diff_config.sh): the plugin section's hs_resolve_ref shells out
to `git ls-remote`, so a sandbox invocation needs its OWN fake `git` on PATH
(copy the inline fake-git-in-a-tempdir technique from
tests/test_diff_plugins.sh) -- forgetting it means the sandbox silently hits
the real GitHub repo instead of a fixture, which doesn't error, it just
returns a real, wrong sha and reports a false "moved" drift. Also: any
sandbox that exercises cmd_diff now needs a manifest/config.toml to exist
(even empty) or the new config section fails closed with exit 2 before
reaching the plugin/integration output at all -- had to add an empty one to
tests/test_diff_plugins.sh's existing sandbox for this reason, since that
test predates the config section and only ever built manifest/plugins.list.

Acceptance matrix gotcha: `fr acceptance check` parses notes as plain YAML
scalars -- a literal ": " (colon then space) inside a multi-line unquoted
notes value breaks the parse ("mapping values are not allowed here") even
though the file elsewhere reads fine. Use " -- " instead of ": " when hand-
editing an existing row's notes field.

diff-runs-under-protocol-mismatch and config-splice-preserves-plugin-blocks
rows both got their notes updated to describe phase 3's coverage; both stay
`not-implemented` on purpose, per the same phase-10 bulk-flip convention
phase 2 established for diff-detects-plugin-drift.

<!-- fr:journal kind=finding scope=plan id=216be8bfed47 created=2026-09-08T10:54:40 phase=3 state=fixed -->
### 216be8bfed47 · finding [fixed] · hs_herdr_json decided 'is this an error' by substring, and misread good responses (phase 3)

Introduced by me under the since-withdrawn startup-cost rule. A Herdr response is an error when the top-level object has an error key, but the code tested whether the raw text contained the token "error", which a perfectly good response can carry in a value: a plugin list containing "status":"error" was reported as a failed call and its result discarded. Replaced with an authoritative parse, hs_py herdr-error, which reads the response and exits 0 with the message only when there really is an error key. The cheap substring test survives as a filter for whether there is anything to parse at all, which cannot miss a real error because an error key always puts the token in the text. Two regression tests added in tests/test_preflight.sh, one for each direction, plus a check that a multi-line message is folded onto one line.

<!-- fr:journal kind=decision scope=plan id=8f3a0c6f882c created=2026-09-08T11:12:47 phase=4 -->
### 8f3a0c6f882c · decision · Block re-insertion anchor: a third walk mode, exact in the steady state, order-preserving otherwise (phase 4)

Phase 3 flagged that neither hs_strip_plugin_blocks nor hs_extract_plugin_blocks
records WHERE a block sat, so apply's config splice has no anchor to reinsert a
block into a DIFFERENT file (the manifest-derived skeleton) than the host file
it came from. It suggested either a third mode on the walker or a wrapper
pairing extract's output with a marker search on the target.

A marker search on the target is not available here: by definition the
manifest never holds plugin blocks (they are per-host, config-splice-preserves-
plugin-blocks / the design doc's "Layout" section), so there is nothing to
search for in the manifest to anchor against.

Chosen: a third mode on _hs_plugin_block_walk, `splice`, extending the same
shared engine rather than duplicating marker-matching (as phase 3's own note
warned against). It emits the same thing `extract` does, except each block is
preceded by one sentinel line, `<HS_SPLICE_SENTINEL><n>`, where `<n>` is the
number of lines `strip` would have printed before that block began -- the
block's anchor, counted in the HOST file's own stripped-line terms.
hs_splice_config feeds this stream to a new hs.py subcommand,
`splice-config`, which reads the manifest's lines as the new skeleton and
reinserts each block at position `anchor + offset`, offset accumulating the
length of every block already inserted (anchors are non-decreasing since
blocks are walked top to bottom, so this is a straightforward left-to-right
insertion, not a general interval-merge problem).

Why this is the right level of correctness rather than a heuristic reach for
more: after every successful apply, the host's own stripped content IS the
manifest content (that is what apply just wrote) -- so on the very next round,
the anchor computed against the host lines up exactly with the manifest's own
line count, and the block lands back in its exact original position. That is
the steady-state case that matters in practice (nothing but plugin blocks
changed since the last apply, which is the common day-to-day case this tool
optimizes for). When the manifest's operator-line shape genuinely changed
since the last apply (an absorb captured a hand edit, or a fresh host's first
apply against a manifest shaped like no host it has ever run against), the
anchor is best-effort: every block still comes back unchanged, markers
included, and still in original relative order (the actual acceptance-matrix
wording), just not necessarily at the byte-identical original line. Tests:
tests/test_apply_config.sh covers both the byte-exact round-trip case (host's
own stripped content used as the manifest) and same-shape/reshaped-manifest
drift (values changed / lines added), asserting presence, verbatim content,
and relative order in the latter two, byte-identical reconstruction in the
former.

Phase 5 (absorb) needs to know: hs_splice_config takes (host_config,
manifest_config) and prints the new host content on stdout; it is read-only
(never writes), so absorb's own writer (rewriting manifest/config.toml FROM
the host with blocks stripped) is unaffected and does not need this function
at all -- absorb just needs hs_strip_plugin_blocks, already built in phase 3.
Phase 8/10's absorb-apply-roundtrip-is-noop test should absorb a host, then
apply back to the SAME host: that is exactly the byte-exact case above (the
freshly-absorbed manifest equals the host's current stripped content), so the
round trip is a true no-op by this design, not by coincidence.

<!-- fr:journal kind=finding scope=plan id=caa0453a638a created=2026-09-08T11:13:03 phase=4 state=fixed -->
### caa0453a638a · finding [fixed] · Capturing $? right after a negated (!) condition captures the wrong status (phase 4)

Introduced and caught by me in this phase. The pattern used correctly
elsewhere in this file (hs_diff_plugins, cmd_diff) is
`cmd || var=$?` -- `$?` inside the `||` right-hand side is cmd's own exit
status, since `||` only runs its right side when cmd genuinely failed and
`$?` has not been overwritten yet.

`if ! cmd; then rc=$?; ...; fi` is a different, broken shape: `!` negates
cmd's exit status for the if-test BEFORE `$?` is read, so when cmd fails
(exit 2), `! cmd` succeeds (exit 0) and `rc=$?` captures 0, not 2. I wrote
this exact shape twice while drafting hs_splice_config's internal call to
_hs_plugin_block_walk and hs_apply_config's call to hs_splice_config, and
both silently returned 0 on a hard error instead of propagating the real
exit code -- caught by tests/test_apply_config.sh's "manifest config
missing" case (expected exit 2, got 0) during the initial full-suite run,
not by the unit-level assertions I wrote for it, which happened to only
check the message went to stderr, not the exit status of the outer function
in that specific path.

Fixed by dropping the `!` and hardcoding `return 2` in both call sites,
since both callees (_hs_plugin_block_walk and hs_splice_config) only ever
fail with exit 2, never any other nonzero code -- matching how
hs_diff_plugins already treats hs_manifest_plugins' failure (also hardcoded
to 2, also a binary success/2 contract). If a later phase adds a call
site where the callee's nonzero code needs to be preserved verbatim (not
just detected), do not reach for `if ! cmd; then rc=$?`; use
`cmd; rc=$?; if [ "$rc" -ne 0 ]; then ...` instead, or the `cmd || var=$?`
form already used by cmd_diff/cmd_apply's own section-accumulation.

<!-- fr:journal kind=finding scope=plan id=ceb7e3e0a86d created=2026-09-08T11:13:21 phase=4 state=fixed -->
### ceb7e3e0a86d · finding [fixed] · A host that itself runs Herdr exports HERDR_SOCKET_PATH into every test subprocess (phase 4)

The machine this phase was built on runs Herdr itself as an agent-pane tool
and exports HERDR_SOCKET_PATH (and HERDR_BIN_PATH, HERDR_ENV,
HERDR_PANE_ID, ...) into every shell it spawns, tests/run.sh's included.
hs_socket_path() reads `${HERDR_SOCKET_PATH:-$HS_DEFAULT_SOCKET_PATH}`, so
on such a host this ambient value wins over the default derived from the
sandboxed $HOME tests/run.sh sets up -- a test that expects "no server" by
relying on a fresh $HOME having no ~/.config/herdr/herdr.sock silently sees
the OPERATOR'S REAL socket instead, and hs_preflight reports whatever that
real Herdr's actual state happens to be (matched, on this host), not
no-server.

This bit tests/test_entrypoint.sh's new apply-fails-closed-on-no-server
assertion in this phase: it expected exit 3 and got exit 2 (apply got past
hs_require_socket and failed on the missing manifest instead). Every
EXISTING test that exercises preflight (tests/test_preflight.sh, and the
sandbox sections of tests/test_diff_plugins.sh / tests/test_diff_config.sh
/ tests/test_apply_plugins.sh) was already immune, because all of them
explicitly pass HERDR_SOCKET_PATH (pointing at a real fixture socket or a
guaranteed-missing path) rather than relying on the default -- this phase's
new entrypoint-level test was the first to lean on the default.

Fixed by explicitly setting HERDR_SOCKET_PATH to a guaranteed-nonexistent
path in that test, matching the established convention. Left unfixed at
the harness level: tests/run.sh does not scrub HERDR_* environment
variables before running a test file. Any later phase (6: feed, or
onboard/absorb if they gain their own preflight-dependent tests) that adds
a new test relying on the DEFAULT socket/config-dir path, on a host that
itself runs Herdr, will hit this same trap. The robust fix would be
tests/run.sh unsetting HERDR_SOCKET_PATH, HERDR_CONFIG_DIR, and
HERDR_BIN_PATH before each test file runs (alongside the fresh $HOME it
already sets up) rather than every test file remembering to override them
individually -- worth doing in phase 6 or whichever phase next touches
tests/run.sh, since the trap will keep recurring test-file by test-file
otherwise.

<!-- fr:journal kind=finding scope=plan id=900abc826c49 created=2026-09-08T11:18:40 phase=4 state=fixed -->
### 900abc826c49 · finding [fixed] · The test runner inherited the operator's live Herdr environment (phase 4)

Phase 4 hit this and flagged it for a later phase; fixing it at the harness level now, because it silently weakens every test written from here on. Herdr exports HERDR_ENV, HERDR_SOCKET_PATH, HERDR_BIN_PATH, HERDR_PANE_ID, HERDR_TAB_ID and HERDR_WORKSPACE_ID into every process it starts. The suite is normally run from inside a Herdr pane, so all six reached every test, and hs_socket_path resolved to the operator's real socket. A test meaning to exercise the no-server path found a real server and proved nothing. tests/run.sh now clears all of them plus HERDR_CONFIG_DIR with env -u before each file; tests that need one set it themselves. tests/test_env_isolation.sh is a permanent guard: it asserts the six are unset, that HOME is a throwaway directory, and that herdr resolves inside tests/helpers rather than to the real binary. Verified it fails when a variable is leaked back in.

<!-- fr:journal kind=decision scope=plan id=c7ec9445b528 created=2026-09-08T11:40:25 phase=5 -->
### c7ec9445b528 · decision · manifest/config.toml carries no header -- it is spliced verbatim into a live host (phase 5)

hs_absorb_plugins writes a header comment atop manifest/plugins.list (a herdr-setup-only
format whose `#` lines hs_manifest_plugins already treats as comments, per tests/fixtures/manifest_valid.list),
but hs_absorb_config deliberately writes NO header at all into manifest/config.toml.

Tried adding one first, since the task read "a short header comment naming the tool" as applying to
both files. It broke the round trip immediately: manifest/config.toml is not free-form documentation,
it is the exact skeleton hs_splice_config (phase 4) reinserts plugin blocks into and hs_apply_config
then writes byte-for-byte onto a real host's live config.toml. A header line there would (a) get
permanently baked into the operator's actual Herdr config on the very first apply, re-appearing (and
potentially duplicating, since it is not a plugin-marked block hs_strip_plugin_blocks would ever remove)
on every absorb/apply cycle after, and (b) break the byte-exact steady-state case journal 8f3a0c6f882c
(phase 4) documents: the manifest would then differ from the host's own stripped content by exactly
those header lines, forcing hs_apply_config to see `changed=1` and write + reload-config even when
nothing meaningful drifted, and forcing every plugin block's anchor to be recomputed against a
reshaped (not identical) skeleton instead of landing back at its exact original position.

tests/test_roundtrip.sh caught this directly: with the header in config.toml, "the round trip makes no
reload-config call" failed (got 1 call) and the host's config.toml came back reordered (blocks moved
relative to operator lines) rather than byte-identical. Removed the header from hs_absorb_config and the
round trip passed clean on the first try after that.

manifest/plugins.list has no such constraint -- absorb OVERWRITES it wholesale every time (never spliced
into anything, never merged), and hs_manifest_plugins already skips `#`-prefixed lines, so a header
there is free: it never reaches a host, and re-adding it every absorb is idempotent by construction.

Anything later touching manifest/config.toml (documentation, a future `--annotate` flag, etc.) should
keep this asymmetry in mind: config.toml is host-file-shaped and gets spliced verbatim; plugins.list is
herdr-setup-shaped and is safe to decorate.

<!-- fr:journal kind=finding scope=plan id=4be56ee4550b created=2026-09-08T11:40:43 phase=5 state=fixed -->
### 4be56ee4550b · finding [fixed] · absorb does not gate on hs_require_socket, contrary to the socket-commands-refuse-under-mismatch row's wording (phase 5)

The acceptance-matrix row socket-commands-refuse-under-mismatch says "apply, absorb, onboard and
feed stop with one line naming the protocol mismatch". The design doc's own Commands > absorb section and
Errors and safety > Preflight section do NOT list absorb among the commands that stop under a mismatch or
no-server host -- only apply, onboard and feed are named there, and absorb's own description ("It touches
only the checkout") never mentions calling herdr at all.

Checked absorb's actual data path: it reads plugins.json and config.toml straight from disk via
hs_absorb_plugins/hs_absorb_config, exactly like the plugin/config sections of `diff` (which also does not
gate on hs_require_socket, by design, so it stays useful under a mismatch). Neither absorb function makes a
single herdr call. There is nothing for a protocol mismatch to block.

Decision this phase: followed the design doc's dedicated absorb/Preflight sections over the acceptance
row's summary wording. cmd_absorb (herdr-setup) does NOT call hs_require_socket; it runs the same whether
the host is matched, mismatched, or has no server at all -- consistent with `diff`.

Left open rather than resolved unilaterally: I updated this row's notes (docs/acceptance/matrix.yaml) to
flag the mismatch, but did not edit the acceptance wording itself or add a socket gate to absorb. Phase 6
(which the row's existing notes already point at as the flip point, for cmd_feed) should either drop
"absorb" from this row's acceptance text to match the design doc, or make a deliberate call that absorb
should gate on the socket too and wire hs_require_socket into cmd_absorb at that point -- whichever it
picks, phase 5 deliberately left cmd_absorb ungated rather than guess.

<!-- fr:journal kind=finding scope=plan id=24e52049085f created=2026-09-08T11:44:42 phase=5 state=fixed -->
### 24e52049085f · finding [fixed] · Acceptance row listed absorb among the commands a mismatch blocks; it is not one (phase 5)

Raised as an open finding by phase 5, and the mistake was mine when seeding the matrix. The design doc's Preflight section names apply, onboard and feed as the commands that stop under a protocol mismatch, and absorb is deliberately absent because it reads the host's plugins.json and config.toml from disk and never calls herdr at all -- the same reason diff keeps working in that state. Phase 5 followed the design doc rather than the row, which was the right call, and flagged the discrepancy instead of quietly resolving it. The row now reads apply, onboard and feed. No code change was needed.

<!-- fr:journal kind=finding scope=plan id=1348cd0f37a1 created=2026-09-08T12:02:42 phase=5 state=fixed -->
### 1348cd0f37a1 · finding [fixed] · The tool could not start on bash 3.2, and the suite could not see it (phase 5)

Milestone review, verified. Two defects, one masking the other. (1) The entrypoint's final dispatch expanded an empty array under set -u, which bash 3.2 and up to 4.3 treat as an unbound variable, so every command except --help died at line 254. bash 3.2 is the declared floor precisely because it is what macOS ships, so the tool was dead on its primary target. Fixed with the portable form. (2) tests/run.sh invoked each test file with a bare bash, which resolves on PATH to the newest installed bash, 5.3 on this machine. So running the runner under /bin/bash tested the runner on 3.2 and every test on 5.3, and the orchestrator repeatedly claimed bash 3.2 verification that never happened. Fixed by invoking each file under the same bash running the runner, so /bin/bash tests/run.sh now exercises the floor end to end. With (1) fixed the suite passes 13/13 on both 3.2 and 5.3; before the fix it was 6 of 13 files failing on 3.2 while showing green through five phases.

<!-- fr:journal kind=finding scope=plan id=ac30ffbfb635 created=2026-09-08T12:35:56 state=fixed -->
### ac30ffbfb635 · finding [fixed] · C2 -- apply swallowed Herdr's trust preview and would have hung at a real terminal

Milestone review, verified. The DEFAULT install path -- no --yes, the one an
operator takes -- ran `herdr plugin install` through hs_herdr_json, which is a
command substitution capturing both streams while stdin is still the terminal.
Herdr shows a trust preview and waits for an answer. The preview went into a
variable, the operator saw a stopped terminal with no output at all, and herdr sat
blocked on a read behind a prompt nobody had been shown.

Fixed with hs_herdr_interactive, a second wrapper next to hs_herdr_json: it runs
herdr in the foreground with stdin, stdout and stderr inherited and reads only the
exit status. hs_apply_plugins uses it for an install without --yes, and keeps
hs_herdr_json for --yes (no prompt, and the error object is still caught by
parsing). It is a reusable helper, not a special case: phase 9's onboard offers an
integration and waits for an answer the same way and must call it too.

Writing the test found a SECOND half of the same bug that the review had not
reached. hs_apply_plugins iterated the manifest with `done < "$manifest_tmp"`, so
the manifest file was the stdin of everything inside the loop. Even with the
capture removed, the interactive install would have answered Herdr's trust prompt
with the next line of plugins.list and never let the operator speak. The loop now
reads on fd 3 (`read ... <&3` / `done 3< "$manifest_tmp"`), leaving stdin alone.
Any later phase that runs an interactive command inside a `while read` loop has the
same trap waiting; feed and onboard both will.

Harness: the fake herdr never prompted and never read, so this whole path was
unexpressible. FAKE_HERDR_PROMPT=1 makes it print a trust preview, read one line,
log the answer, and exit 1 on anything but yes. Three tests at the function level
(accept, decline, --yes asks nothing) and one through the real entrypoint, all
feeding answers on stdin. Against the pre-fix implementation the preview never
reaches stdout and zero answers reach herdr.

<!-- fr:journal kind=finding scope=plan id=e699fae3077a created=2026-09-08T12:36:22 state=fixed -->
### e699fae3077a · finding [fixed] · C3 -- absorb's dirty-manifest guard failed open and ate a hand edit

Milestone review, verified. hs_require_clean_manifest captured
`git status --porcelain -- manifest/` with stderr discarded and never looked at
git's exit status. Every way git can fail -- the checkout is not a git repository,
.git is unreadable, a bad config -- produced an empty string, and an empty string
is exactly what a clean tree produces. The guard said clean, absorb overwrote a
hand-edited manifest, exit 0, not one word of warning. That is precisely the
outcome the guard exists to prevent, produced by the guard itself.

Fixed by capturing the exit status separately (`|| git_rc=$?`, never `if ! cmd`) and
treating any non-zero status as a hard refusal, exit 2, naming what git said. The
dirty-tree refusal keeps its own exit 4, so "dirty" and "cannot tell" stay
distinguishable.

Harness: the old fake git shifted past `-C <dir>`, ignored every argument, printed
$FAKE_GIT_STATUS_OUTPUT and always exited 0 -- it could express "clean" and "dirty"
and nothing else, and the sandbox it ran against was not a git repository at all.
The passing test proved only that a canned string was parsed. It is gone.
test_absorb.sh now builds real repositories with `git init` and covers clean,
dirty (a tracked file edited by hand plus an untracked one), reverted-back-to-clean,
and not-a-repository, plus two end-to-end runs through the entrypoint: a committed
manifest that is then hand-edited (refused, edit intact), and a checkout that is not
a git repository (refused, both hand-written files byte-identical afterwards).
Against the pre-fix implementation that last one fails with the hand edit already
replaced by absorbed content, which is the reviewer's finding reproduced as a test.

Two knock-on harness fixes went with it. The sandbox and the leaky-config sandbox
are now real repositories, so absorb gets past the guard and the home-path check is
actually the thing under test; and the "touches nothing outside manifest/" listing
excludes .git.

<!-- fr:journal kind=finding scope=plan id=163fccbfef8f created=2026-09-08T12:36:23 state=fixed -->
### 163fccbfef8f · finding [fixed] · C4 -- the preflight gate concluded 'matched' from a call it watched fail, and could not see stderr

Milestone review, verified, both halves.

(a) hs_preflight only reported `mismatched` when the probe exited non-zero AND the
output carried "protocol_mismatch". Any other failure fell through to `matched`, so
a server that had just refused the probe -- socket_closed, a permission error, an
answer that could not be read -- let `apply` write to it. The gate is the whole
fail-closed rule, and it was reading a failure as health.

(b) The probe ran `herdr plugin list 2>/dev/null`. A herdr that reports its error
object on stderr left the gate with an empty string to reason about, so even the
mismatch it was looking for was invisible. hs_herdr_json had always merged the two
streams for exactly this reason; the probe was the inconsistent one.

Fixed by adding a fourth state rather than folding into mismatched, because the two
call for different words to the operator: `unreachable` means the probe failed and
herdr did not say it was a protocol problem. hs_preflight now reports `matched`
only for a probe that SUCCEEDED. The probe moved into its own function,
hs_preflight_probe, with 2>&1, so hs_preflight and hs_require_socket agree on what
is asked and how the answer is read; hs_require_socket re-probes on the unreachable
path to quote what herdr actually said (hs_preflight runs in a command substitution,
so a global set there is lost, and this is an error path on a tool run by hand).
The message is folded to one line by hs_flatten, matching the one-line contract the
other three states are tested for.

Spec updated: the Preflight section names four states, says matched means the probe
succeeded and nothing else, and says the probe reads stderr.

Harness: tests/helpers/fake-herdr hard-coded the protocol_mismatch object on stdout.
It now takes FAKE_HERDR_ERROR_CODE=<code> for an arbitrary error and
FAKE_HERDR_ERROR_STREAM=stderr to emit on the other stream;
FAKE_HERDR_PROTOCOL_MISMATCH=1 is kept as shorthand. Against the pre-fix
implementation both new preflight states come back `matched`.

<!-- fr:journal kind=finding scope=plan id=2ca963cb90a4 created=2026-09-08T12:36:45 state=fixed -->
### 2ca963cb90a4 · finding [fixed] · I1 -- an annotated tag resolved to the tag object, so a plugin pinned to one never converged

Milestone review, verified against a real annotated tag. An annotated tag is an
object of its own pointing at a commit, so `git ls-remote <url> <tag>` answers with
two lines -- the tag object, then the commit under a `^{}` suffix. hs_resolve_ref
took the first with `head -n1`. Herdr records the COMMIT, so the two could never
match: a plugin pinned to an annotated tag reported `moved` on every diff and was
reinstalled on every apply, for ever, without converging, and diff never returned 0.
The design doc's own example manifest pins v0.3.3.

Fixed by asking for both patterns, `"$ref"` and `"${ref}^{}"`, and preferring the
peeled line (`awk '$2 ~ /\^\{\}$/'`), falling back to the first line when there is
none -- a branch or a lightweight tag has only the one, and it is the commit.
Confirmed against github.com/git/git that the peeled line is returned only when the
`^{}` pattern is asked for; the bare pattern alone does not produce it.

Harness: the fake git read `$3` and printed one line, so no fixture could be an
annotated tag. It now loops over every pattern given and honours a `.tagobj`
fixture beside the `.sha` one -- with both present it answers the bare pattern with
the tag object and the `^{}` pattern with the commit, exactly as real git does.
Tested at both levels: hs_resolve_ref returns the commit, and hs_diff_plugins
reports a plugin sitting at that commit as `ok`. Pre-fix it returns the tag object
and reports `moved`.

<!-- fr:journal kind=finding scope=plan id=45b9e48217d2 created=2026-09-08T12:36:46 state=fixed -->
### 45b9e48217d2 · finding [fixed] · I2 -- hs_herdr_json merged stderr into the JSON it returned

Milestone review, verified. `output="$(herdr "$@" 2>&1)"` glued anything herdr said
on stderr to the front of the answer it returned as a parseable result. One
deprecation notice and the caller's parse fails on a response that was fine.
Harmless to date only because both current callers discard stdout -- phase 6's feed
parses `agent list` and `pane process-info` through this wrapper, so it would have
broken the first time it mattered.

Fixed by capturing the two separately (stderr to a temp file). On success stdout is
returned clean and herdr's stderr is passed through to ours, so a warning stays
visible; on failure stderr is folded into the error message, which is usually where
it says why. C4's requirement is unaffected: the preflight probe is its own
function, hs_preflight_probe, and it still merges deliberately.

Harness: FAKE_HERDR_STDERR_NOTE makes the fake write to stderr and still succeed.
The test asserts the returned stdout is byte-identical to the fixture and that the
note appears on our stderr; pre-fix the returned value carries the note glued on.

<!-- fr:journal kind=finding scope=plan id=df9771277b58 created=2026-09-08T12:37:11 state=fixed -->
### df9771277b58 · finding [fixed] · I3 -- apply could gut a host config without showing or asking anything

Milestone review, verified. Operator lines plus one plugin block, an empty manifest
config, apply -- and the host was left with only the block, exit 0, nothing
printed. "Last write wins" is the documented design and stays the design; what was
wrong is that the most destructive write the tool makes was the one it said least
about, and --dry-run printed `+ write <path>` with no diff at all.

Fixed on two axes:

1. Every real write and every dry run prints the unified diff of the change first,
   labelled current/new. A host with no config yet is diffed against empty, so the
   creation case shows its content too.
2. A write that leaves the file with FEWER lines than it had is gated: it needs
   --yes, or a yes answer at the terminal (hs_confirm). Refused, it writes nothing
   and returns 4, which cmd_apply passes through unchanged -- the same "refused,
   nothing happened" status absorb's dirty-manifest guard already uses. With no
   terminal there is nobody to ask, so it refuses; silence is not consent.

Note on the reviewer's "reusing hs_diff_config": hs_diff_config compares the host's
STRIPPED content against the manifest, which is a different question from "what is
about to change on disk". The shared piece is hs_unified_diff, a four-argument
helper both now call, so the two render a change the same way; the operands stay
different on purpose.

The line-count rule is deliberately blunt and does fire on ordinary drift -- an
operator who deleted two blank lines from the manifest gets asked. That is the
trade: nothing can distinguish "tidied the manifest" from "manifest is empty" by
inspection, but the diff plus one question lets the operator distinguish it in a
second, and --yes silences it for good. The existing "successful write" test was
exactly such a case and now runs with HS_YES=1; a matching refusal test and a
growing-write test (which is NOT gated) sit beside it.

<!-- fr:journal kind=finding scope=plan id=fbc18f8218f0 created=2026-09-08T12:37:12 state=fixed -->
### fbc18f8218f0 · finding [fixed] · I4 -- the single .bak slot was destroyed by the next apply

Milestone review, verified. hs_apply_config copied the host config to
`<host>.bak`, so the safety net survived exactly one mistake: the second apply
overwrote the backup taken by the first, and an operator who noticed on the second
run had already lost what they wanted back.

Fixed with hs_backup_path -- `<host>.bak.<UTC timestamp>`, plus a counter if a file
is already there (two applies inside one second) -- and it never overwrites an
existing file. `cp -p` rather than `cp`, so the backup carries the original's mode
too. Tested: two successive writes leave two distinct backups and the first still
holds the original content untouched.

<!-- fr:journal kind=finding scope=plan id=3c7e55789110 created=2026-09-08T12:37:14 state=fixed -->
### 3c7e55789110 · finding [fixed] · I5 -- apply changed the host config's file mode

Milestone review, verified 0644 to 0600. hs_apply_config wrote through
`mktemp`, which creates 0600, and the rename carried that mode onto the target --
dropping group access and any ACL on a file the operator, not this tool, owns.

Fixed by `cp -p "$host_file" "$write_tmp"` before writing the content: cp -p sets
the temp file's mode (and ownership where permitted) from the original, and the
content write then truncates it without touching the mode. Portable, unlike
`chmod --reference`, which is GNU-only and this tool targets macOS as well. A
config that did not exist before gets 0644. Tested with a 0640 host config, and the
0644 default for a freshly created one.

<!-- fr:journal kind=finding scope=plan id=2ce1f7105469 created=2026-09-08T12:37:43 state=fixed -->
### 2ce1f7105469 · finding [fixed] · The nine cheap minors, and what each was actually hiding

1. hs_py failing inside hs_herdr_json degraded silently. `if message="$(... | hs_py
   herdr-error)"` conflated "not an error object" (exit 1) with "could not run the
   parser at all" (exit 2, no uv), so an error response with a zero exit status
   passed as a success. The three cases are now distinguished; a parse that could
   not be made is reported and treated as a failure. Tested by running with uv off
   PATH.
2. The failed-install path was untested because the fake always exited 0 for
   `plugin install`. FAKE_HERDR_FAIL=<prefix> exits 1 with a plain non-JSON message
   for a matching call. hs_apply_plugins' "returns 1 if any install failed" clause
   is checked for the first time, including that it attempts every drifted plugin
   rather than stopping at the first.
3. hs.py used str.splitlines(), which splits on vertical tab, form feed, \x85,
   U+2028 and U+2029, while lib/common.sh's `while read` splits on \n alone.
   Rejoined with "\n", each of those became a REAL newline in the operator's config,
   and the two sides then disagreed on the line count every block anchor is measured
   in. Replaced with a split_lines() helper that splits on "\n" and drops a single
   trailing empty field, matching both splitlines' trailing-newline behaviour and
   the shell's. Tested with a form feed inside a config value.
4. A CRLF host config gave a misleading `unterminated plugin block` error: an end
   marker reads as `--- end <id> ---\r` and matched nothing. _hs_plugin_block_walk
   now strips a trailing CR before MATCHING and still prints the line exactly as
   read, so a CRLF file survives a strip/extract round trip byte for byte -- the
   test counts the carriage returns on both sides.
5. Duplicate manifest lines were processed twice, producing two installs. Rejected
   rather than deduped: two lines for one source are either redundant or
   contradictory (two pinned refs, and nothing says which the host converges on),
   and AGENTS.md says never guess and continue. Membership is a space-delimited
   string test, since bash 3.2 has no associative arrays and a source is by
   construction whitespace-free.
6. .python-version said 3.14 while PEP 723 and pyproject say >=3.13 and ruff targets
   py313. Set to 3.13.
7. Stale header comments in herdr-setup and lib/common.sh still claimed a Python 3.9
   floor and "no tomllib", contradicting AGENTS.md -- Python comes from uv here, not
   from the host. Rewritten to say so.
8. hs_manifest_plugins ran `set +f` unconditionally, handing a caller that had
   deliberately turned globbing OFF a shell with it back on. `$-` is inspected first
   and the option restored only if we changed it. Tested from both directions.
9. HS_DRY_RUN and HS_YES were read unguarded, so sourcing lib/common.sh under
   `set -u` without them died. Every read is `${VAR:-0}` now.

<!-- fr:journal kind=finding scope=plan id=85aeab2b149b created=2026-09-08T12:37:44 state=refuted -->
### 85aeab2b149b · finding [refuted] · Minor 10 -- a host config with no trailing newline still causes one extra write; deliberately not fixed

Reviewer's minor 10, left open with reasoning rather than fixed.

hs_py splice-config always ends its output with a newline, so a host config whose
last line has none differs from the spliced content on the first apply, gets one
write and one reload-config, and converges after that.

Not fixed because I do not think it is a defect. The content apply writes is
derived from the manifest, and the manifest is the source of truth; a host file
missing its final newline is a difference the manifest legitimately corrects, in
the same way any other drift is. Fixing it would mean carrying "did the HOST end
without a newline" through the splice as a separate signal and letting the host's
shape override the manifest's on that one byte -- a special case with no principle
behind it, in the function that has to stay byte-exact for the steady-state anchor
guarantee (journal 8f3a0c6f882c).

**Orchestrator, settling this:** agreed, and refuted rather than deferred. The
behaviour is real but it is not a defect: it is the manifest correcting drift, which
is the tool's whole job. It is now written into the design doc under "One deliberate
normalisation", so it is specified behaviour rather than an accident, and a later
phase that wants it changed is changing the spec, not fixing a bug.

If a later phase disagrees, the place to change it is splice_config in lib/hs.py,
where the trailing newline is added, and the test to write first is one that
asserts a second apply against an unchanged host makes no call -- which
tests/test_roundtrip.sh already asserts for the normal case.

<!-- fr:journal kind=decision scope=plan id=9bf94ba2cc4f created=2026-09-08T12:38:04 -->
### 9bf94ba2cc4f · decision · A guard is only as good as the fake that can make it fail

The four critical findings were four instances of one pattern, and the pattern
matters more than any of them.

Each guard failed OPEN -- read a failure as health -- and each had a test that
passed while proving something weaker than its name claimed, because the fake it
exercised could not produce the failure. The fake herdr never prompted, so the
interactive install path did not exist as far as the suite was concerned. It only
ever emitted one error code, on one stream, so the preflight gate's two fail-open
branches were unreachable. It always exited 0 for an install, so the failed-install
branch was unreachable. The fake git ignored its arguments and always exited 0
against a sandbox that was not a git repository, so a guard whose entire job is
reading git's exit status was tested by parsing a canned string. The suite was
thirteen green files over four load-bearing mechanisms that did not work.

So the rule for the phases still to come: when a guard's job is to REFUSE, the fake
must be able to make it refuse, and the test must be one that fails before the fix.
Every fix here was verified that way -- the new tests were run against HEAD's
implementation with the new harness, and every one of them failed, each naming its
own defect (six of thirteen files passed; the two that did pass, diff_config and
roundtrip, target no fail-open).

Two practical rules fell out of it:

- Prefer a REAL dependency to a fake when the thing under test is that dependency's
  failure mode. `git init` in a temp directory costs nothing and cannot lie about
  its own exit status. Fakes are for things that are expensive or absent (herdr,
  the network).
- A switch on a fake is cheap and permanent. FAKE_HERDR_PROMPT, FAKE_HERDR_FAIL,
  FAKE_HERDR_ERROR_CODE, FAKE_HERDR_ERROR_STREAM and FAKE_HERDR_STDERR_NOTE each
  exist because a real failure got past a test that looked like it covered it.
  Phases 6 and 9 should add to that list rather than working around it, and
  tests/run.sh now clears all of them per file so one left set in a developer's
  shell cannot reconfigure the fake under every test.

<!-- fr:journal kind=discovery scope=plan id=e6bbea77b29e created=2026-09-08T13:02:04 phase=6 -->
### e6bbea77b29e · discovery · The feed runner's herdr shapes, and the fake switch that makes the exit-zero error expressible (phase 6)

lib/feed.py speaks to herdr through its own herdr_json(), the Python
counterpart of lib/common.sh's hs_herdr_json, and it fails in the same
three-part order: a non-zero exit is a failure whatever was printed; a
top-level `error` key is a failure EVEN WITH EXIT 0; anything unparseable
(no output, not JSON, not an object) is a failure. stdout and stderr are
captured separately and never merged, per finding I2 -- feed is the caller
that made that fix necessary, and it now parses two commands through it.

Harness extension, one switch: FAKE_HERDR_ERROR_EXIT=<n>. The fake could
emit an error object on either stream, but only ever with exit 1, so the
nastiest shape of all -- a well-formed error object returned alongside a
SUCCESSFUL exit status -- was unexpressible. hs_herdr_json has guarded that
case since phase 3 and no test could construct it. Now
FAKE_HERDR_ERROR_CODE=socket_closed + FAKE_HERDR_ERROR_EXIT=0 does.
Verified by weakening feed.py: dropping the error-key check makes exactly
that test fail, and nothing else.

Three more fail-open shapes were verified the same way, each weakening
caught by exactly one test:
- `{"result":{"panes":[]}}` (valid JSON, exit 0, no `agents` key) must
  RAISE, not read as zero panes. This is the one that most looks like a
  healthy idle host.
- process-info with no `processes` list must raise, not drop the pane.
- A failure on the SECOND round of calls (agent list fine, `pane
  process-info` refused -- FAKE_HERDR_FAIL="pane process-info") must raise.
  Swallowing it per pane leaves a run that reports success over an empty
  set, which is the original bug with one extra step.

The JSON shapes feed parses are NOT observed from a live herdr: this host
sits in the protocol_mismatch state the spec's test plan describes, so
`agent list` cannot answer. They are the shapes the plan's own step text
names, taken as the contract:
  agent list          -> result.agents[] each {pane_id|id, agent, ...}
  pane process-info   -> result.{cwd, processes[]}, each process
                         {pid, argv0, foreground, pid_start_epoch|start_epoch}
argv0 is matched whole AND by basename, so /usr/local/bin/claude counts.
An adapter may declare `command` in its probe when its argv0 is not its
agent name; it defaults to the agent name. Phase 10's live test plan is
where these shapes get confirmed against a restarted server -- if one is
wrong, the failure is loud (HerdrError naming the missing key), never a
silent empty run, which is the property that matters.

<!-- fr:journal kind=discovery scope=plan id=a7c2486e97d0 created=2026-09-08T13:23:24 phase=6 -->
### a7c2486e97d0 · discovery · The adapter interface phases 7 and 8 write against (phase 6)

The whole contract is written, with worked examples, in docs/adapters.md, and
tests/test_contract.py EXTRACTS every example from that document by an HTML
marker (`<!-- contract: probe -->` and friends) and runs it through the real
code -- validate_probe for the probe example, and the minimal shell adapter as
an actual executable through probe(), usable_adapters() and resolve(). So the
document cannot drift from the runner: tighten a rule without updating the doc
and test_contract.py fails, add an example that does not work and it fails.
Phases 7/8 should read the document, not this entry, but the interface in
short:

    adapters/<name> probe      -> one JSON object on stdout, exit 0
      required: agent, source, available (bool), confidence (exact|heuristic)
      optional: unverified (bool, default false), command (string)

    adapters/<name> resolve    -> reads {"panes":[...]} on stdin,
                                  prints {"results":[{pane_id, candidates}]}
      pane:      {pane_id, cwd, pid, pid_start_epoch}   (pid/start may be null)
      candidate: {session_id, confidence} required;
                 label, updated, session_path optional

Details phases 7/8 will actually trip over:

- `command` is new and is NOT in the plan's step text. It is what the pane's
  foreground argv0 is matched against, and it DEFAULTS to the agent name. All
  four adapters in phases 7/8 want the default (claude/codex/opencode/copilot),
  so none of them declares it. It exists so an agent whose binary is not named
  after it does not need a change to the runner. argv0 is matched whole AND by
  basename, so /usr/local/bin/claude counts.
- `confidence` appears on the probe AND on each candidate and means the same
  thing in both, but the RUNNER READS THE CANDIDATE'S. A heuristic adapter must
  not promote a match to `exact` just because it found only one -- that is
  precisely the case the runner would then report unasked.
- A candidate with no `session_id`, or with neither documented confidence, is
  dropped by candidates_by_pane() before the decision. It cannot be reported,
  so as a prompt option it does nothing and as a lone `exact` candidate it
  would be a silent wrong report.
- ORDER IS LOAD-BEARING. `--yes` takes candidates[0]. Newest first.
- Answering about fewer panes than were given is fine and tested.
- To fail, exit non-zero or print nothing. Do NOT print an empty results list
  to paper over an error -- that is indistinguishable from "no sessions here",
  and the difference is the whole reason the runner is careful. An adapter
  failure warns, counts, and makes the run exit 1; it never stops the others.
- probe gets 10s, resolve 30s, then the adapter is skipped.
- `available: false` is skipped SILENTLY (the adapter working correctly);
  every other probe failure is skipped WITH A WARNING naming the adapter.
  `unverified: true` is used, with a warning -- that is copilot's path.

Testing an adapter: tests/helpers/feedlib.py has load_feed(), write_adapter(),
probe_adapter() and RecordingServer, and isolate_environment(), which each
test_*.py calls at import. Phase 8's P8.T3.S1 conformance pass extends
tests/test_contract.py, which already has the shape it needs (probe every
executable in a directory, assert validate_probe accepts it, assert
available:false on a host where the agent is absent) applied to the
documented example adapter.

<!-- fr:journal kind=decision scope=plan id=64897182d4e6 created=2026-09-08T13:23:51 phase=6 -->
### 64897182d4e6 · decision · The suite now runs Python test files, and how the report stage is proved (phase 6)

Harness changes phases 7-10 inherit.

**tests/run.sh runs test_*.py as well as test_*.sh.** The plan asks for Python
unittest files, and run.sh only globbed .sh. A .py file is run under
`uv run --quiet --script`, which is the same door lib/hs.py and lib/feed.py go
through -- Python comes from uv, not from the host (AGENTS.md), so the suite
must not reach for a host interpreter either. Both kinds get the identical
env -u treatment, fresh HOME and tests/helpers-prefixed PATH. FAKE_HERDR_ERROR_EXIT
was added to the cleared list alongside it. test_harness.sh's nested throwaway
suite is unaffected -- it writes only .sh files and the .py glob matches
nothing there, which the `[ -e ]` guard already handled.

**The .py files also isolate themselves.** pyproject collects `tests/test_*.py`
under pytest, so `uv run --group dev pytest` now picks these up too -- and
OUTSIDE run.sh nothing prefixes PATH, so `herdr` would resolve to the REAL one
installed on the machine and a test would make live calls to the operator's own
server. feedlib.isolate_environment(), called at import by every .py test file,
prepends tests/helpers to PATH and clears the same variables run.sh does.
Verified both ways: 18/18 files under run.sh on bash 3.2 AND 5.3, and
91 passed under pytest.

**tests/helpers/feedlib.py** is the shared plumbing (not a test file, and out of
the glob's way in helpers/): load_feed() imports lib/feed.py by path --
registering it in sys.modules BEFORE exec_module, because @dataclass resolves
its field annotations through sys.modules[cls.__module__] and fails at class
definition time otherwise; write_adapter()/probe_adapter() build inline shell
adapters; RecordingServer is a real AF_UNIX server that records the JSON lines
it receives.

Two deliberate choices about how the report stage is proved, both following the
remediation rule that a guard is only as good as the fake that can make it fail:

1. **A real socket, not a stub.** "--dry-run opened no socket" is only evidence
   if a real server was sitting there ready to receive. RecordingServer is
   listening throughout that test and records nothing.
2. **A real pty, not an injected flag.** Every other test passes `interactive`
   into run(). tests/test_feed_report.py's TestAtARealTerminal does not: it
   spawns lib/feed.py as a program with os.openpty() on stdin and lets it work
   out for itself that somebody is there, then writes the answer. "There is
   nobody to ask" is a decision the tool makes from its environment, and a test
   that hands it the answer proves nothing about it. The paired test runs the
   same program with stdin on /dev/null and asserts the pane is skipped and the
   reason names --yes or the terminal.

Phase 9's onboard will want the same pty technique for its own y/n offers.

One TDD note, recorded rather than glossed: validate_probe was written during
task 1, because probe() needs it, so its task 4 test could not fail for want of
the function. tests/test_contract.py failed for a different real reason --
docs/adapters.md did not exist and every one of its 17 assertions raised -- and
it is a stronger test for it, since it reads the contract out of the document
rather than restating it.

<!-- fr:journal kind=decision scope=plan id=3c6f2f90bbe0 created=2026-09-08T13:24:16 phase=6 -->
### 3c6f2f90bbe0 · decision · What --yes means to feed, and the three exit statuses (phase 6)

The plan's step text says an uncertain pane "in a non-interactive run without
--yes is skipped with a note rather than guessed", which fixes the no-terminal
case but leaves --yes itself undefined. Settled here, consistent with the rest
of the tool:

**--yes takes the adapter's best candidate, candidates[0].** It is the operator
waiving the question in advance, exactly as it waives apply's shrinking-write
gate and Herdr's own trust preview. It wins over a terminal too: with --yes at
a tty, feed never prompts. Two consequences worth stating plainly. Candidate
ORDER becomes load-bearing, so docs/adapters.md says best-first is not a
stylistic preference. And --yes on a host full of heuristic adapters WILL
sometimes report a wrong session -- that is what the operator asked for, and the
default (skip, and say why) is the safe one.

The decision table, in the order it is evaluated:

    no candidates                       -> skip, "no session found"
    one candidate, confidence `exact`   -> REPORT, no prompt
    --yes                               -> report candidates[0]
    a terminal                          -> ask; a blank, out-of-range or
                                           non-numeric answer is a skip
    neither                             -> skip, naming --yes or a terminal

Only the second line reports unasked. The prompt goes to STDERR so the report
lines and the summary stay pipeable.

**Three exit statuses, and skips are not failures.**

    0  everything the run was sure about was reported
    1  something did not get through -- a send failed, or an adapter could not
       answer. Its panes are named on stderr.
    2  Herdr could not be read. The run does not know what is out there.

Deliberately, a pane the run SKIPPED does not make the exit status non-zero: a
skip is a decision the run made and told the operator about, and the next run
can still feed that pane. Exit 2 exists so that "Herdr refused" can never be
confused with "nothing to do" by a caller reading only the status -- cmd_feed
passes it through unchanged.

**send() does not swallow failures, and that is a deliberate divergence from
Herdr's own integration hooks.** The claude hook wraps its whole socket block
in `except Exception: pass`, which is right for a hook that must never disturb
the agent it is attached to and wrong here: a pane that was not fed is the
exact thing feed exists to prevent. A failed send warns, counts, and changes
the exit status.

The wire format is Herdr's own, read off the installed claude integration hook
(~/.claude/hooks/herdr-agent-state.sh, HERDR_INTEGRATION_VERSION=8) rather than
invented: {"id","method":"pane.report_agent_session","params":{pane_id, source,
agent, seq, agent_session_id[, agent_session_path]}}, one JSON line, id shaped
`<source>:<ms>:<6 random digits>`, seq from time.time_ns(). next_seq() adds one
guarantee the hook does not need -- strictly increasing within a run -- since
feed reports several panes in a row and two inside one nanosecond tick would
otherwise tie.

<!-- fr:journal kind=finding scope=plan id=d2af1a991c1f created=2026-09-08T13:50:07 phase=6 state=fixed -->
### d2af1a991c1f · finding [fixed] · panes_for read three fields Herdr does not return, and the fixtures agreed with it (phase 6)

Raised by the orchestrator against phase 6 as first delivered, verified, and
fixed. The most important entry in this journal, because the code bug is
downstream of the process bug.

**What was wrong.** panes_for/_foreground_match read `result.processes`, a
`foreground` boolean on each entry, and a `pid_start_epoch` field. Herdr
returns none of the three. Against a real host every pane raised
`answer carries no 'processes' list` -- fail-closed, so loud rather than
silent, but completely non-functional. 91 tests were green.

**Why the tests did not catch it.** I wrote the fixtures and the parser in the
same hour from the same guess. They agreed with each other and neither agreed
with Herdr. This is the milestone review's own pattern (journal 9bf94ba2cc4f,
"a guard is only as good as the fake that can make it fail") arriving one
level up: it is not enough for the fake to be able to express failure if the
fake's idea of SUCCESS is fiction. I even recorded the guess honestly in
journal entry "The feed runner's herdr shapes" -- "NOT observed from a live
herdr ... they are the shapes the plan's own step text names" -- and then
tested against the guess anyway. Writing down that you are guessing is not a
substitute for checking.

**The captures.** The CLI on this host is protocol 22 against a protocol 20
server and refuses every call, which is what made the guess feel unavoidable.
It was not: the SERVER answers fine, and a JSON-RPC line written straight to
$HERDR_SOCKET_PATH returns real `agent.list` and `pane.process_info` payloads.
Both are now in tests/fixtures/herdr/ with provenance and masking recorded in
its README, and that directory carries the standing rule: **a fixture for a
Herdr call is a capture, not a construction.** Trimming a captured list or
editing a value is fine; adding, renaming or removing a KEY is not, and
feedlib's agent_entry()/process_entry() raise KeyError on an unknown key so
the rule is enforced rather than merely stated. Every hand-built Herdr payload
in test_feed_resolve.py, test_feed_report.py and test_feed_entrypoint.sh is
gone; TestAgainstTheCapturedShape replays a capture byte for byte.

**The three corrections.**
1. The list is `result.process_info.foreground_processes`.
2. There is NO `foreground` flag. Everything in that list IS foreground; the
   flag test matched nothing, which is why every pane dropped out even before
   the missing-key error. A test now asserts the capture has no such key, so
   nobody re-adds the check.
3. Herdr reports no start time anywhere.

Two more the captures gave for free, both traps:
- `name` for the Claude process is `"2.1.260"` -- its VERSION. Matching a
  pane's agent on `name` fails silently. `argv0` is correct, and there is a
  test asserting the capture's `name` values to say so.
- A pane commonly has SEVERAL foreground processes (the agent plus a `node`
  child). `foreground_process_group_id` names the job the pane is really
  running, so it is now the tie-break when two entries match argv0, rather
  than list order.

`cwd` now comes from the matched agent process's own `cwd`, falling back to
the agent-list entry's `foreground_cwd` then `cwd` -- all three are in the
captures.

Also noticed, not acted on: `agent.list` carries `agent_session`
{source, agent, kind, value} for a pane Herdr already has a record for. feed
does not read it -- its job is to top up, and re-reporting an id a pane
already has is harmless. A later phase that wants to skip known panes, or
warn before overwriting a DIFFERENT id, has the field. Recorded in the
fixtures README.

<!-- fr:journal kind=decision scope=plan id=e5a59fdee2c5 created=2026-09-08T13:50:29 phase=6 -->
### e5a59fdee2c5 · decision · pid_start_epoch is kept, read from ps, and its time frame is now specified (phase 6)

Herdr reports no process start time, so the choice was to supply it or drop it
from the contract. **Kept, and supplied from the OS**, because it is the only
thing standing between an `exact` adapter and a recycled pid: Claude Code
names its session file after a process id, and an id handed out again points
at somebody else's session. Phase 7's whole `exact` claim leans on it. A field
adapters cannot rely on would be worse than none, so it is now real.

`feed.pid_start_epoch(pid)` runs `ps -o lstart= -p <pid>` -- not /proc, which
macOS does not have. Two details are load-bearing and both are commented at
the call site:

- **LC_ALL=C is forced.** `ps` localises month and day names. A host with a
  German LC_TIME prints "Fr Sep  4 ..." and every parse fails, silently losing
  the guard on every pane. (The same C-locale trap this operator has hit
  before in other tools.) Tested by running the call with a de_DE locale in
  the environment.
- **`lstart` is LOCAL wall-clock**, parsed with time.mktime, which reads a
  struct_time as local and returns an absolute epoch. The day is space-padded
  ("Sep  4"), so the output is whitespace-normalised first.

Failure is None, not an exception. That is NOT a hole in the fail-closed rule:
that rule is about never reading a failed HERDR call as an empty answer. This
is an enrichment the runner adds itself, and a pane is perfectly feedable
without it.

**The time frame is now specified in docs/adapters.md, at length, because it
is the trap phases 7 and 8 would otherwise walk into.** `pid_start_epoch` is
integer epoch seconds -- an absolute instant. But an agent that records its
own process start does so as a wall-clock string with no zone marker, and the
zone is not necessarily local. Verified on this host, same process, same
instant:

    ps -o lstart=            Fri Sep  4 10:59:46 2026     (local, CEST)
    Claude Code procStart    Fri Sep  4 08:59:46 2026     (UTC)

An adapter that parses `procStart` with time.mktime rejects EVERY session
here, and accepts everything on a host that happens to run in UTC -- which is
the worst combination, because it works on the machine you test on. Use
calendar.timegm for a UTC string, and compare with a second or two of slack.
tests/test_contract.py now asserts the document states the frame, names
procStart, and says "epoch seconds", so this cannot be quietly dropped.

Null means "cannot rule out pid reuse" -- not a match and not a mismatch.
Documented, because either wrong reading loses sessions.

<!-- fr:journal kind=finding scope=plan id=0ea0f83b128e created=2026-09-08T13:56:58 phase=6 state=fixed -->
### 0ea0f83b128e · finding [fixed] · Live Herdr captures reached the tree still carrying the operator's project names (phase 6)

Phase 6 fixed the right root cause by replacing invented fixtures with real captures taken from the live server, and masked home paths and session ids correctly. What survived was a second class of identifier the mask did not cover: two of the operator's repository names in cwd fields, and three real work-in-progress branch titles in terminal_title, one of which named the very task in progress. Masked at the orchestrator, values only, shapes untouched, and the suite confirms the captures still parse: 18/18 files on bash 3.2 and 5.3, 105 under pytest. The durable fix is in the plan: phase 10's public-hygiene test excluded tests/fixtures, which is backwards, because captures are the highest-risk files in a public repository rather than the lowest. That step now covers them and checks for unmasked session ids as well as home paths.

<!-- fr:journal kind=discovery scope=plan id=df0e96cb9b2a created=2026-09-08T14:20:53 phase=7 -->
### df0e96cb9b2a · discovery · Claude adapter: exact by pid, UTC time-frame handled, transcript keyed on the session file's own cwd (phase 7)

adapters/claude implements the exact-by-pid contract. Config dir is
$CLAUDE_CONFIG_DIR if set, else $HOME/.claude, mirrored by adapters/codex's
own $CODEX_HOME (not specified in the phase brief; assumed by analogy with
Claude's env var and with real Codex CLI's own resolution of $CODEX_HOME,
and needed for the adapter to be testable without touching a real home
directory -- docs/adapters.md checklist item 6).

Time frame: procStart is a UTC wall-clock string with no zone marker;
pid_start_epoch is a true epoch integer. parse_proc_start_utc uses
calendar.timegm, never time.mktime. Tested against tests/fixtures/claude/,
a real capture whose own procStart differs from this machine's `ps -o
lstart=` by two hours -- the same offset the phase 6 journal recorded
independently on a different pid, so it is a property of the host, not a
fluke of one process. A 5-second slack absorbs the two clocks' own
rounding. Guard: a pid whose file's own start time disagrees with the
pane's pid_start_epoch by more than the slack is treated as a recycled pid
and yields no candidate; a null on either side cannot rule reuse in or out
and is not treated as either, per docs/adapters.md.

Design decision beyond the phase brief's literal text: once a pid has
matched a session file, the transcript path is slugified from the SESSION
FILE's own cwd, not the pane's cwd from the resolve request. They should
agree in the ordinary case; test_the_transcript_path_uses_the_sessions_own_cwd_not_the_panes
pins the case where they do not, so a later "simplification" cannot quietly
switch this to the pane's cwd.

Fixture: tests/fixtures/claude/session.json, one real ~/.claude/sessions/<pid>.json
capture, masked (cwd, sessionId, bridgeSessionId, messagingSocketPath's home
path, and the "name" field, which is a human nickname that can name the
actual work in progress -- the same leak class phase 6 found in Herdr's
terminal_title). Provenance and full masking rationale in that directory's
README.

<!-- fr:journal kind=discovery scope=plan id=660690531511 created=2026-09-08T14:21:14 phase=7 -->
### 660690531511 · discovery · Codex adapter: heuristic by directory, first-line-only, 30-day-dir bound (phase 7)

adapters/codex matches on directory only, so every candidate stays
heuristic and is never promoted to exact even when it is the only match --
tested explicitly, since that promotion is exactly the failure mode
docs/adapters.md warns a heuristic adapter against.

Only the first line of each rollout file is ever opened (readline() once,
never read() or iterate the file), verified by a test that appends a
malformed, oversized second line and asserts resolution still succeeds. A
rollout whose first line is missing, not JSON, or JSON but not
"session_meta" is skipped, not fatal -- one broken or in-progress rollout
must not hide the others.

The scan is bounded to the newest 30 day-directories under
<config>/sessions/. Day directories are zero-padded (yyyy/mm/dd), so a
plain string sort is already chronological order -- no date parsing needed
to find the newest ones. Tested with 31 day directories, each holding a
matching session: the answer contains exactly 30 candidates and excludes
the oldest.

Fixture: tests/fixtures/codex/session-meta.json, the first line of a real
rollout, masked (cwd, id/session_id, git.repository_url, git.commit_hash;
base_instructions.text shortened to a placeholder since it is several KB
of Codex's own public boilerplate this adapter never reads -- an edited
value, not a removed key, matching the discipline in
tests/fixtures/herdr/README.md).

$CODEX_HOME as the override env var (default ~/.codex) is a design
decision, not a phase-brief requirement -- see the paired claude-adapter
journal entry for the reasoning. Phase 8's opencode and Copilot adapters
should each look for whatever their own real CLI actually honours ($XDG
paths, $COPILOT_HOME per the design doc) rather than assuming this same
pattern applies without checking.

<!-- fr:journal kind=finding scope=plan id=6877d5ce3da6 created=2026-09-08T14:21:51 phase=7 state=fixed -->
### 6877d5ce3da6 · finding [fixed] · ruff over a bare adapters/ directory silently lints nothing -- fixed via extend-include (phase 7)

Found while verifying this phase's own work, and relevant to phase 10's
CI step, which the plan already writes as \"uv run --group dev ruff
check\" ... over lib/ and adapters/.

ruff's file discovery matches by extension. Passing an adapter file by
name (ruff check adapters/claude) lints it fine and caught a real SIM102
finding, now fixed. Passing the DIRECTORY (ruff check adapters/, or the
combined invocation phase 10 plans) prints \"warning: No Python files
found under the given path(s)\" and exits 0 -- the adapters are silently
excluded from lint coverage entirely, while the command still reports
success. The top-level \"ruff check .\" this repo's own CI would
plausibly run has the identical blind spot, invisibly, since it also
exits 0.

Fixed at the config level rather than left for phase 10 to discover the
same way: pyproject.toml's [tool.ruff] now carries extend-include =
[\"adapters/*\"] (with adapters/*.md carved back out via extend-exclude,
matching lib/feed.py's own discover() rule, in case a README lands there
later). Verified: ruff check adapters/ and ruff format --check adapters/
lib/ now both see all four Python files (hs.py, feed.py, claude, codex)
instead of two.

Phase 8's opencode and Copilot adapters need no equivalent fix -- the glob
already covers the whole adapters/ directory -- but should re-run
`ruff check adapters/` (the directory form, not just the two new files by
name) to confirm coverage rather than assuming it from this entry.
