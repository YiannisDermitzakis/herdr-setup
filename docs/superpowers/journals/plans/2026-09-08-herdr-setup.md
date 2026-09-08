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
