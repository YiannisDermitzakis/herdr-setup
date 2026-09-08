#!/usr/bin/env bash
# Tests for the config-splice half of `apply`: hs_splice_config and
# hs_apply_config in lib/common.sh, and their wiring into cmd_apply
# (herdr-setup). Neither exists yet; expect failure until P4.T2.S2
# writes them.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
. "$test_dir/helpers/assert.sh"

if [ ! -f "$repo_root/lib/common.sh" ]; then
  fail "lib/common.sh does not exist yet"
  hs_test_report
fi
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

# --- a host that drifts from the manifest: writes the new content,
# backs the old content up first, writes via a temp file in the same
# directory then renames (no leftover temp file afterwards), and calls
# reload-config exactly once. ---

host_dir="$work/host_drift"
mkdir -p "$host_dir"
host_file="$host_dir/config.toml"
cp "$host_with_blocks" "$host_file"
original_content="$(cat "$host_file")"

rm -f "$log"
HS_DRY_RUN=0 HS_YES=0 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file" "$manifest_same_shape"
status=$?
assert_status "hs_apply_config exits 0 after a successful write" 0 "$status"
assert_contains "hs_apply_config's new content uses the manifest's value" \
  "$(cat "$host_file")" 'agent_panel_sort = "recency"'
assert_eq "hs_apply_config backed up the previous content" \
  "$original_content" "$(cat "${host_file}.bak" 2>/dev/null)"
leftover="$(find "$host_dir" -maxdepth 1 -name '.herdr-setup-config.*' 2>/dev/null)"
[ -z "$leftover" ] && pass || fail "hs_apply_config left a temp file behind: $leftover"
assert_eq "hs_apply_config calls reload-config exactly once" "1" \
  "$(grep -c '^server reload-config$' "$log")"

# --- calling it again with the same manifest is a no-op: content
# unchanged, no new backup write, no reload-config call ---

cp "$host_file" "$host_file.before_noop"
rm -f "$log"
HS_DRY_RUN=0 HS_YES=0 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file" "$manifest_same_shape"
status=$?
assert_status "a no-op apply still exits 0" 0 "$status"
assert_eq "a no-op apply leaves the host file untouched" \
  "$(cat "$host_file.before_noop")" "$(cat "$host_file")"
[ ! -f "$log" ] && pass || fail "a no-op apply still called herdr: $(cat "$log" 2>/dev/null)"

# --- --dry-run: no write, no backup, no herdr call, on a host that
# WOULD otherwise change ---

host_dir_dry="$work/host_dryrun"
mkdir -p "$host_dir_dry"
host_file_dry="$host_dir_dry/config.toml"
cp "$host_with_blocks" "$host_file_dry"
before="$(cat "$host_file_dry")"

rm -f "$log"
HS_DRY_RUN=1 HS_YES=0 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file_dry" "$manifest_same_shape" >/dev/null
status=$?
assert_status "dry-run hs_apply_config exits 0" 0 "$status"
assert_eq "dry-run makes no change to the host file" "$before" "$(cat "$host_file_dry")"
[ ! -f "${host_file_dry}.bak" ] && pass || fail "dry-run created a backup file"
[ ! -f "$log" ] && pass || fail "dry-run called herdr: $(cat "$log" 2>/dev/null)"

# --- a host config that does not exist yet: apply creates it, no
# backup (nothing to back up), reload-config still called once ---

host_dir_new="$work/host_new"
mkdir -p "$host_dir_new"
host_file_new="$host_dir_new/config.toml"

rm -f "$log"
HS_DRY_RUN=0 HS_YES=0 FAKE_HERDR_LOG="$log" \
  hs_apply_config "$host_file_new" "$manifest_same_shape"
status=$?
assert_status "hs_apply_config exits 0 creating a fresh host config" 0 "$status"
[ -f "$host_file_new" ] && pass || fail "hs_apply_config did not create the host config"
assert_contains "the freshly created config carries the manifest content" \
  "$(cat "$host_file_new")" 'agent_panel_sort = "recency"'
[ ! -f "${host_file_new}.bak" ] && pass || fail "hs_apply_config backed up a file that never existed"
assert_eq "hs_apply_config still reloads once for a freshly created config" "1" \
  "$(grep -c '^server reload-config$' "$log")"

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

hs_test_report
