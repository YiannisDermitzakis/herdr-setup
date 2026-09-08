#!/usr/bin/env bash
# shellcheck disable=SC2015
# this suite's idiom throughout: pass()/fail()
# (tests/helpers/assert.sh) never return nonzero, so "A && pass || fail C" cannot
# silently take the wrong branch.
# Tests for the config-splice half of `apply`: hs_splice_config and
# hs_apply_config in lib/common.sh, and their wiring into cmd_apply
# (herdr-setup). Neither exists yet; expect failure until P4.T2.S2
# writes them.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"

if [ ! -f "$repo_root/lib/common.sh" ]; then
  fail "lib/common.sh does not exist yet"
  hs_test_report
fi
# shellcheck source=lib/common.sh
. "$repo_root/lib/common.sh"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# =====================================================================
# hs_splice_config
# =====================================================================

if ! command -v hs_splice_config >/dev/null 2>&1; then
  fail "hs_splice_config is not defined yet"
  hs_test_report
fi

host_with_blocks="$work/host_with_blocks.toml"
cat > "$host_with_blocks" <<'EOF'
[ui]
agent_panel_sort = "priority"

# --- added by ez-corp.space-usage (removed by `status-disable`) ---
[ui.sidebar.spaces]
rows = [ ["state_icon", "workspace"], ["$usage"] ]
# --- end ez-corp.space-usage ---

# --- added by kryptamine.auto-title ---
[auto_title]
enabled = true
# --- end kryptamine.auto-title ---

[misc]
foo = "bar"
EOF

# --- round trip: when the manifest IS the host's own stripped content
# (the steady-state case: apply already ran once, nothing drifted since),
# splicing reproduces the original host file byte for byte -- the blocks
# land back in exactly the position they came from. ---

manifest_roundtrip="$work/manifest_roundtrip.toml"
hs_strip_plugin_blocks "$host_with_blocks" > "$manifest_roundtrip"

out="$(hs_splice_config "$host_with_blocks" "$manifest_roundtrip")"
status=$?
assert_status "hs_splice_config exits 0 on a clean round trip" 0 "$status"
assert_eq "splicing manifest-equals-stripped-host reproduces the host file exactly" \
  "$(cat "$host_with_blocks")" "$out"

# --- drift with the same line shape: only a value changed, block count
# and position among operator lines is unaffected -- blocks still land at
# the same relative position, unchanged, in original order. ---

manifest_same_shape="$work/manifest_same_shape.toml"
cat > "$manifest_same_shape" <<'EOF'
[ui]
agent_panel_sort = "recency"

[misc]
foo = "bar"
EOF

out="$(hs_splice_config "$host_with_blocks" "$manifest_same_shape")"
status=$?
assert_status "hs_splice_config exits 0 on same-shape drift" 0 "$status"
assert_contains "same-shape splice uses the manifest's new value" "$out" 'agent_panel_sort = "recency"'
case "$out" in
  *'agent_panel_sort = "priority"'*) fail "same-shape splice kept the host's stale value" ;;
  *) pass ;;
esac
first_block_line=$(printf '%s\n' "$out" | grep -n '^# --- added by ez-corp.space-usage' | cut -d: -f1)
second_block_line=$(printf '%s\n' "$out" | grep -n '^# --- added by kryptamine.auto-title' | cut -d: -f1)
[ -n "$first_block_line" ] && [ -n "$second_block_line" ] && [ "$first_block_line" -lt "$second_block_line" ] \
  && pass || fail "blocks are not present and in original relative order: $out"

# --- a differently-shaped manifest (a line added): both blocks still
# appear, unchanged, markers included, and still in original relative
# order -- the contract that matters when the anchor position itself
# cannot line up exactly, per the design note in lib/common.sh. ---

manifest_reshaped="$work/manifest_reshaped.toml"
cat > "$manifest_reshaped" <<'EOF'
[ui]
agent_panel_sort = "recency"

[misc]
foo = "bar"
newkey = "baz"
EOF

out="$(hs_splice_config "$host_with_blocks" "$manifest_reshaped")"
status=$?
assert_status "hs_splice_config exits 0 on a reshaped manifest" 0 "$status"
assert_contains "reshaped splice carries the manifest's new line" "$out" 'newkey = "baz"'
# shellcheck disable=SC2016
# single-quoted on purpose: the backtick is
# literal text being asserted against, not a command substitution to expand.
assert_contains "reshaped splice still carries block 1 verbatim" "$out" \
  '# --- added by ez-corp.space-usage (removed by `status-disable`) ---'
assert_contains "reshaped splice still carries block 1's end marker" "$out" \
  '# --- end ez-corp.space-usage ---'
assert_contains "reshaped splice still carries block 2 verbatim" "$out" \
  '# --- added by kryptamine.auto-title ---'
first_block_line=$(printf '%s\n' "$out" | grep -n '^# --- added by ez-corp.space-usage' | cut -d: -f1)
second_block_line=$(printf '%s\n' "$out" | grep -n '^# --- added by kryptamine.auto-title' | cut -d: -f1)
[ -n "$first_block_line" ] && [ -n "$second_block_line" ] && [ "$first_block_line" -lt "$second_block_line" ] \
  && pass || fail "blocks are not in original relative order on a reshaped manifest: $out"

# --- a host with no config.toml yet: no blocks to preserve, the manifest
# content passes through unchanged ---

no_host="$work/does-not-exist.toml"
out="$(hs_splice_config "$no_host" "$manifest_same_shape")"
status=$?
assert_status "hs_splice_config exits 0 with no host file" 0 "$status"
assert_eq "with no host file, splice output is the manifest content unchanged" \
  "$(cat "$manifest_same_shape")" "$out"

# --- a manifest line carrying a character Python calls a line break but
# the shell does not. hs.py used str.splitlines(), which splits on vertical
# tab, form feed, \x85, U+2028 and U+2029; lib/common.sh's `while read`
# splits on \n alone. Rejoined with "\n", each of those turned into a REAL
# newline in the operator's own config, and the two sides then disagreed on
# how many lines the file had, which is what every block anchor is counted
# in. ---

manifest_odd="$work/manifest_odd.toml"
printf '[ui]\nagent_panel_sort = "a\014b"\n\n[misc]\nfoo = "bar"\n' > "$manifest_odd"

out="$(hs_splice_config "$host_with_blocks" "$manifest_odd")"
status=$?
assert_status "hs_splice_config exits 0 on a manifest with a form feed in a value" 0 "$status"
assert_contains "the form feed stays inside its own line, not turned into a newline" \
  "$out" "$(printf 'agent_panel_sort = "a\014b"')"
manifest_odd_lines="$(wc -l < "$manifest_odd" | tr -d ' ')"
block_lines="$(hs_extract_plugin_blocks "$host_with_blocks" | wc -l | tr -d ' ')"
assert_eq "the spliced file has the manifest's lines plus the blocks', and no more" \
  "$((manifest_odd_lines + block_lines))" \
  "$(printf '%s\n' "$out" | wc -l | tr -d ' ')"

# and the blocks still land where the anchors say, because both sides now
# count the same lines
# shellcheck disable=SC2016
# single-quoted on purpose: the backtick is
# literal text being asserted against, not a command substitution to expand.
assert_contains "the form-feed manifest still gets its blocks back" "$out" \
  '# --- added by ez-corp.space-usage (removed by `status-disable`) ---'

# --- a missing/unreadable manifest config is fail-closed: exit 2, one
# stderr line naming the file ---

manifest_missing="$work/no-manifest-config.toml"
err="$work/splice_missing_manifest.err"
out="$(hs_splice_config "$host_with_blocks" "$manifest_missing" 2>"$err")"
status=$?
assert_status "hs_splice_config exits 2 when the manifest config is missing" 2 "$status"
assert_contains "hs_splice_config names the missing manifest file" "$(cat "$err")" "$manifest_missing"

# =====================================================================
# hs_apply_config
# =====================================================================

if ! command -v hs_apply_config >/dev/null 2>&1; then
  fail "hs_apply_config is not defined yet"
  hs_test_report
fi

log="$work/herdr.log"

# hs_backup_files <dir>: the timestamped backups hs_apply_config has taken
# in <dir>, newest last.
hs_backup_files() {
  find "$1" -maxdepth 1 -name 'config.toml.bak.*' 2>/dev/null | sort
}

# =====================================================================
# The shrinking-write gate. `apply` overwrites the operator's live Herdr
# config, and it used to do that in total silence: the reviewer's case was
# operator lines plus one plugin block, an empty manifest config, and an
# apply that left only the block behind, exit 0, nothing printed at all.
# "Last write wins" is the design; saying nothing while doing it is not.
# So every real write and every dry run prints the diff, and a write that
# ends with FEWER lines than it started with needs --yes or an answer at
# the terminal. There is no terminal here, so the default path refuses.
# =====================================================================

host_dir="$work/host_drift"
mkdir -p "$host_dir"
host_file="$host_dir/config.toml"
cp "$host_with_blocks" "$host_file"
original_content="$(cat "$host_file")"

rm -f "$log"
out="$work/shrink_refused.out"; err="$work/shrink_refused.err"
HS_DRY_RUN=0 HS_YES=0 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file" "$manifest_same_shape" >"$out" 2>"$err"
status=$?
assert_status "a shrinking write with no --yes and no terminal is refused with 4" 4 "$status"
assert_eq "a refused write leaves the host file exactly as it was" \
  "$original_content" "$(cat "$host_file")"
[ -z "$(hs_backup_files "$host_dir")" ] && pass || fail "a refused write still took a backup"
[ ! -f "$log" ] && pass || fail "a refused write still called herdr: $(cat "$log" 2>/dev/null)"
assert_contains "the refusal says how many lines would go" "$(cat "$err")" "removes 2 line"
assert_contains "the refusal names --yes as the way through" "$(cat "$err")" "--yes"

# --- and the diff of that write is shown, not just announced. `+ write
# <path>` told the operator nothing about what was about to happen to
# their config. ---
assert_contains "the change is shown as a diff" "$(cat "$out")" "config change:"
assert_contains "the diff shows the line being removed" "$(cat "$out")" '-agent_panel_sort = "priority"'
assert_contains "the diff shows the line being added" "$(cat "$out")" '+agent_panel_sort = "recency"'
assert_contains "the diff labels the sides" "$(cat "$out")" "current"

# --- accepted with --yes: writes the new content, backs the old content
# up first, writes via a temp file in the same directory then renames (no
# leftover temp file afterwards), and calls reload-config exactly once. ---

rm -f "$log"
out="$work/shrink_yes.out"
HS_DRY_RUN=0 HS_YES=1 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file" "$manifest_same_shape" >"$out" 2>/dev/null
status=$?
assert_status "hs_apply_config exits 0 after a successful write" 0 "$status"
assert_contains "hs_apply_config's new content uses the manifest's value" \
  "$(cat "$host_file")" 'agent_panel_sort = "recency"'
assert_contains "an accepted write still shows its diff" "$(cat "$out")" "config change:"
assert_eq "hs_apply_config backed up the previous content" \
  "$original_content" "$(cat "$(hs_backup_files "$host_dir" | head -n1)" 2>/dev/null)"
leftover="$(find "$host_dir" -maxdepth 1 -name '.herdr-setup-config.*' 2>/dev/null)"
[ -z "$leftover" ] && pass || fail "hs_apply_config left a temp file behind: $leftover"
assert_eq "hs_apply_config calls reload-config exactly once" "1" \
  "$(grep -c '^server reload-config$' "$log")"

# --- a GROWING write needs no confirmation: the gate is about losing
# content, not about writing at all. ---

manifest_grown="$work/manifest_grown.toml"
cat > "$manifest_grown" <<'EOF'
[ui]
agent_panel_sort = "recency"

[misc]
foo = "bar"
added_by_the_manifest = 1
another_added_line = 2
yet_another_added_line = 3
EOF

rm -f "$log"
HS_DRY_RUN=0 HS_YES=0 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file" "$manifest_grown" >/dev/null 2>&1
status=$?
assert_status "a growing write goes through with no --yes and no terminal" 0 "$status"
assert_contains "the grown content landed" "$(cat "$host_file")" "yet_another_added_line = 3"

# --- the backup slot survives more than one mistake. A single `.bak` was
# destroyed by the very next apply, so the safety net was gone by the time
# an operator noticed they wanted it. ---

backups="$(hs_backup_files "$host_dir")"
assert_eq "each write took its own backup" "2" "$(printf '%s\n' "$backups" | wc -l | tr -d ' ')"
assert_eq "the first backup still holds the original content, untouched" \
  "$original_content" "$(cat "$(printf '%s\n' "$backups" | head -n1)")"

# --- the host file keeps its own mode. mktemp makes 0600, and renaming
# that over the target silently took a group-readable config private.
#
# `ls -l | cut -c1-10` reads the mode string portably below. `stat`'s format
# flags differ between BSD (macOS, `-f`) and GNU (Linux, `-c`) -- the same
# BSD/GNU split lib/common.sh's own hs_apply_config avoids with `cp -p`
# instead of the GNU-only `chmod --reference` -- and every filename here is
# a fixed literal with nothing exotic in it, so shellcheck's find-based
# suggestion buys nothing this suite needs. ---
host_dir_mode="$work/host_mode"
mkdir -p "$host_dir_mode"
host_file_mode="$host_dir_mode/config.toml"
cp "$host_with_blocks" "$host_file_mode"
chmod 640 "$host_file_mode"
# shellcheck disable=SC2012
mode_before="$(ls -l "$host_file_mode" | cut -c1-10)"

rm -f "$log"
HS_DRY_RUN=0 HS_YES=1 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file_mode" "$manifest_same_shape" >/dev/null 2>&1
# shellcheck disable=SC2012
assert_eq "the written config keeps the original's mode" \
  "$mode_before" "$(ls -l "$host_file_mode" | cut -c1-10)"
# shellcheck disable=SC2012
assert_eq "the backup keeps the original's mode too" \
  "$mode_before" "$(ls -l "$(hs_backup_files "$host_dir_mode" | head -n1)" | cut -c1-10)"

# --- calling it again with the same manifest is a no-op: content
# unchanged, no new backup write, no reload-config call ---

cp "$host_file" "$host_file.before_noop"
before_backups="$(hs_backup_files "$host_dir")"
rm -f "$log"
HS_DRY_RUN=0 HS_YES=0 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file" "$manifest_grown"
status=$?
assert_status "a no-op apply still exits 0" 0 "$status"
assert_eq "a no-op apply leaves the host file untouched" \
  "$(cat "$host_file.before_noop")" "$(cat "$host_file")"
assert_eq "a no-op apply takes no backup" "$before_backups" "$(hs_backup_files "$host_dir")"
[ ! -f "$log" ] && pass || fail "a no-op apply still called herdr: $(cat "$log" 2>/dev/null)"

# --- --dry-run: no write, no backup, no herdr call, on a host that
# WOULD otherwise change ---

host_dir_dry="$work/host_dryrun"
mkdir -p "$host_dir_dry"
host_file_dry="$host_dir_dry/config.toml"
cp "$host_with_blocks" "$host_file_dry"
before="$(cat "$host_file_dry")"

rm -f "$log"
out="$work/dryrun.out"
HS_DRY_RUN=1 HS_YES=1 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file_dry" "$manifest_same_shape" >"$out" 2>/dev/null
status=$?
assert_status "dry-run hs_apply_config exits 0" 0 "$status"
assert_eq "dry-run makes no change to the host file" "$before" "$(cat "$host_file_dry")"
[ -z "$(hs_backup_files "$host_dir_dry")" ] && pass || fail "dry-run created a backup file"
[ ! -f "$log" ] && pass || fail "dry-run called herdr: $(cat "$log" 2>/dev/null)"
assert_contains "dry-run shows the diff it would write" "$(cat "$out")" '+agent_panel_sort = "recency"'
assert_contains "dry-run still names the write it would make" "$(cat "$out")" "+ write"

# --- a dry run of a shrinking write is refused too, so --dry-run answers
# the same question a real run would ---
rm -f "$log"
HS_DRY_RUN=1 HS_YES=0 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file_dry" "$manifest_same_shape" >/dev/null 2>&1
status=$?
assert_status "a dry run of a shrinking write is refused with 4 as well" 4 "$status"

# --- a host config that does not exist yet: apply creates it, no
# backup (nothing to back up), reload-config still called once ---

host_dir_new="$work/host_new"
mkdir -p "$host_dir_new"
host_file_new="$host_dir_new/config.toml"

rm -f "$log"
HS_DRY_RUN=0 HS_YES=0 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file_new" "$manifest_same_shape" >/dev/null
status=$?
assert_status "hs_apply_config exits 0 creating a fresh host config" 0 "$status"
[ -f "$host_file_new" ] && pass || fail "hs_apply_config did not create the host config"
assert_contains "the freshly created config carries the manifest content" \
  "$(cat "$host_file_new")" 'agent_panel_sort = "recency"'
[ -z "$(hs_backup_files "$host_dir_new")" ] && pass || fail "hs_apply_config backed up a file that never existed"
assert_eq "hs_apply_config still reloads once for a freshly created config" "1" \
  "$(grep -c '^server reload-config$' "$log")"
# shellcheck disable=SC2012
assert_eq "a config created from nothing gets 0644" "-rw-r--r--" \
  "$(ls -l "$host_file_new" | cut -c1-10)"

# --- a missing/unreadable manifest config is fail-closed: exit 2, one
# stderr line naming the file, and no side effects ---

err="$work/apply_config_missing.err"
rm -f "$log"
out="$(HS_DRY_RUN=0 HS_YES=0 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file" "$manifest_missing" 2>"$err")"
status=$?
assert_status "hs_apply_config exits 2 when the manifest config is missing" 2 "$status"
assert_contains "hs_apply_config names the missing manifest file" "$(cat "$err")" "$manifest_missing"
[ ! -f "$log" ] && pass || fail "hs_apply_config called herdr despite a hard error"

# =====================================================================
# The shrinking-write gate on a host config with NO TRAILING NEWLINE.
#
# The gate counted with `wc -l`, which counts newlines, not lines: a file
# whose last line has no newline is one short. A five-line host config
# written without a trailing newline counted as four, the four-line manifest
# counted as four, and "fewer lines than before" was false -- so a line was
# removed from a live config silently, with exit 0, on the default
# non-interactive path the gate exists to hold.
#
# Hosts like this are not exotic. The design doc's own "One deliberate
# normalisation" section is entirely about configs that arrive without a
# trailing newline.
# =====================================================================

host_dir_nonl="$work/host_no_trailing_newline"
mkdir -p "$host_dir_nonl"
host_file_nonl="$host_dir_nonl/config.toml"
printf '[ui]\nagent_panel_sort = "priority"\ntheme = "dark"\nfont_size = 13\nscrollback = 10000' \
  > "$host_file_nonl"
assert_eq "the fixture really has no trailing newline" "" \
  "$(tail -c 1 "$host_file_nonl" | tr -d 'a-zA-Z0-9"={} ' | tr '\n' 'N')"
nonl_before="$(cat "$host_file_nonl")"

manifest_nonl="$work/manifest_four_lines.toml"
cat > "$manifest_nonl" <<'EOF'
[ui]
agent_panel_sort = "priority"
theme = "dark"
font_size = 13
EOF

rm -f "$log"
err="$work/nonl.err"
HS_DRY_RUN=0 HS_YES=0 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file_nonl" "$manifest_nonl" >/dev/null 2>"$err"
status=$?
assert_status "a shrinking write is refused on a host config with no trailing newline" \
  4 "$status"
assert_eq "the refused write left the host config exactly as it was" \
  "$nonl_before" "$(cat "$host_file_nonl")"
[ -z "$(hs_backup_files "$host_dir_nonl")" ] && pass \
  || fail "a refused write on a no-trailing-newline config still took a backup"
[ ! -f "$log" ] && pass \
  || fail "a refused write on a no-trailing-newline config still called herdr: $(cat "$log" 2>/dev/null)"
assert_contains "the refusal counts the missing last line too" "$(cat "$err")" "removes 1 line"

# =====================================================================
# A write that FAILS must fail loudly. Every step of the write ran under
# `|| rc=$?` with `set -e` suppressed, and none of the three had its status
# checked: the backup copy, the content write, and the rename. So a full
# disk -- the realistic trigger -- produced an unchanged config, a zero exit
# status, and a `reload-config` call telling Herdr to re-read a file that had
# not changed. The operator is told the apply worked.
#
# Each step is stubbed here by defining a shell function that shadows the
# command, which is enough because lib/common.sh is sourced into this test's
# own shell. Each stub is unset immediately afterwards.
# =====================================================================

fail_dir="$work/host_write_fails"
mkdir -p "$fail_dir"
fail_file="$fail_dir/config.toml"

# --- the rename fails: nothing was written, so nothing may be reported as
# written, and Herdr must not be told to reload ---
cp "$host_with_blocks" "$fail_file"
fail_before="$(cat "$fail_file")"
rm -f "$log"
err="$work/mv_fail.err"
# shellcheck disable=SC2329
# invoked by name from inside hs_apply_config, which shellcheck cannot see.
mv() { return 1; }
HS_DRY_RUN=0 HS_YES=1 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$fail_file" "$manifest_same_shape" >/dev/null 2>"$err"
status=$?
unset -f mv
assert_status "hs_apply_config returns 2 when the rename fails" 2 "$status"
assert_eq "a failed rename leaves the host config exactly as it was" \
  "$fail_before" "$(cat "$fail_file")"
[ ! -f "$log" ] && pass \
  || fail "a failed rename still called reload-config: $(cat "$log" 2>/dev/null)"
[ -s "$err" ] && pass || fail "a failed rename was refused silently"

# --- the content write fails part-way, the way it does on a full disk: some
# bytes land in the temp file and cat exits non-zero. A truncated config must
# never reach the host, and the status must say so. ---
cp "$host_with_blocks" "$fail_file"
fail_before="$(cat "$fail_file")"
rm -f "$log"
err="$work/cat_fail.err"
# shellcheck disable=SC2329
# invoked by name from inside hs_apply_config, as above.
cat() { command head -n 2 "$1"; return 1; }
HS_DRY_RUN=0 HS_YES=1 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$fail_file" "$manifest_same_shape" >/dev/null 2>"$err"
status=$?
unset -f cat
assert_status "hs_apply_config returns 2 when the content write fails" 2 "$status"
assert_eq "a truncated write never reaches the host config" \
  "$fail_before" "$(cat "$fail_file")"
[ ! -f "$log" ] && pass \
  || fail "a truncated write still called reload-config: $(cat "$log" 2>/dev/null)"
leftover="$(find "$fail_dir" -maxdepth 1 -name '.herdr-setup-config.*' 2>/dev/null)"
[ -z "$leftover" ] && pass || fail "a failed write left its temp file behind: $leftover"

# --- the backup copy fails: the previous content cannot be saved, so the
# write must not happen at all. The backup is the operator's only way back. ---
cp "$host_with_blocks" "$fail_file"
fail_before="$(cat "$fail_file")"
rm -f "$log"
err="$work/cp_fail.err"
# shellcheck disable=SC2329
# invoked by name from inside hs_apply_config, as above.
cp() { return 1; }
HS_DRY_RUN=0 HS_YES=1 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$fail_file" "$manifest_same_shape" >/dev/null 2>"$err"
status=$?
unset -f cp
assert_status "hs_apply_config returns 2 when the backup cannot be taken" 2 "$status"
assert_eq "no backup means no write" "$fail_before" "$(cat "$fail_file")"
[ ! -f "$log" ] && pass \
  || fail "a config that could not be backed up was still reloaded: $(cat "$log" 2>/dev/null)"

hs_test_report
