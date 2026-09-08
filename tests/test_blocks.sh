#!/usr/bin/env bash
# Tests for hs_strip_plugin_blocks and hs_extract_plugin_blocks in
# lib/common.sh. Neither exists yet; expect failure until P3.T1.S2 writes
# them.
#
# A plugin-written block is every line from a line matching
# `^# --- added by <id>` through the following `^# --- end <id> ---`,
# inclusive, matching the id at both ends rather than accepting any end
# marker. hs_strip_plugin_blocks prints the file with every such region
# removed; hs_extract_plugin_blocks prints only those regions (markers
# included), in order. The two outputs partition the file's lines.
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

if ! command -v hs_strip_plugin_blocks >/dev/null 2>&1; then
  fail "hs_strip_plugin_blocks is not defined yet"
  hs_test_report
fi
if ! command -v hs_extract_plugin_blocks >/dev/null 2>&1; then
  fail "hs_extract_plugin_blocks is not defined yet"
  hs_test_report
fi

# --- no markers at all: strip is a no-op, extract prints nothing ---

plain="$work/plain.toml"
cat > "$plain" <<'EOF'
[ui]
agent_panel_sort = "priority"

[misc]
foo = "bar"
EOF

out="$(hs_strip_plugin_blocks "$plain")"
status=$?
assert_status "strip on a file with no markers exits 0" 0 "$status"
assert_eq "strip on a file with no markers is a no-op" "$(cat "$plain")" "$out"

out="$(hs_extract_plugin_blocks "$plain")"
status=$?
assert_status "extract on a file with no markers exits 0" 0 "$status"
assert_eq "extract on a file with no markers prints nothing" "" "$out"

# --- an operator section plus two different plugins' blocks, with
# content preserved after the last block. The first block is the real
# example from the design doc. ---

two_blocks="$work/two_blocks.toml"
cat > "$two_blocks" <<'EOF'
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

expected_stripped="$work/expected_stripped.toml"
cat > "$expected_stripped" <<'EOF'
[ui]
agent_panel_sort = "priority"



[misc]
foo = "bar"
EOF

expected_extracted="$work/expected_extracted.toml"
cat > "$expected_extracted" <<'EOF'
# --- added by ez-corp.space-usage (removed by `status-disable`) ---
[ui.sidebar.spaces]
rows = [ ["state_icon", "workspace"], ["$usage"] ]
# --- end ez-corp.space-usage ---
# --- added by kryptamine.auto-title ---
[auto_title]
enabled = true
# --- end kryptamine.auto-title ---
EOF

out="$(hs_strip_plugin_blocks "$two_blocks")"
status=$?
assert_status "strip on two plugin blocks exits 0" 0 "$status"
assert_eq "strip removes both blocks, keeps the operator lines and trailing content" \
  "$(cat "$expected_stripped")" "$out"

out="$(hs_extract_plugin_blocks "$two_blocks")"
status=$?
assert_status "extract on two plugin blocks exits 0" 0 "$status"
assert_eq "extract prints both blocks, markers included, in order" \
  "$(cat "$expected_extracted")" "$out"

# --- a begin marker with no matching end: fatal, exit 2, names the id ---

unterminated="$work/unterminated.toml"
cat > "$unterminated" <<'EOF'
[ui]
agent_panel_sort = "priority"

# --- added by ez-corp.space-usage ---
[ui.sidebar.spaces]
rows = [ ["state_icon", "workspace"], ["$usage"] ]
EOF

err="$work/unterminated_strip.err"
out="$(hs_strip_plugin_blocks "$unterminated" 2>"$err")"
status=$?
assert_status "strip on an unterminated block exits 2" 2 "$status"
assert_contains "strip names the unterminated plugin id" "$(cat "$err")" "ez-corp.space-usage"

err="$work/unterminated_extract.err"
out="$(hs_extract_plugin_blocks "$unterminated" 2>"$err")"
status=$?
assert_status "extract on an unterminated block exits 2" 2 "$status"
assert_contains "extract names the unterminated plugin id" "$(cat "$err")" "ez-corp.space-usage"

# --- an end marker with no begin: fatal, exit 2, names the id ---

stray_end="$work/stray_end.toml"
cat > "$stray_end" <<'EOF'
[ui]
agent_panel_sort = "priority"
# --- end ez-corp.space-usage ---
[misc]
foo = "bar"
EOF

err="$work/stray_end_strip.err"
out="$(hs_strip_plugin_blocks "$stray_end" 2>"$err")"
status=$?
assert_status "strip on a stray end marker exits 2" 2 "$status"
assert_contains "strip names the stray end marker's plugin id" "$(cat "$err")" "ez-corp.space-usage"

err="$work/stray_end_extract.err"
out="$(hs_extract_plugin_blocks "$stray_end" 2>"$err")"
status=$?
assert_status "extract on a stray end marker exits 2" 2 "$status"
assert_contains "extract names the stray end marker's plugin id" "$(cat "$err")" "ez-corp.space-usage"

hs_test_report
