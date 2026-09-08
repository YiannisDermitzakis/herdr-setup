#!/usr/bin/env bash
# Tests for hs_host_plugins in lib/common.sh. It does not exist yet; expect
# failure until P2.T2.S2 writes it.
#
# hs_host_plugins reads a plugins.json file directly and never invokes
# herdr -- FAKE_HERDR_PROTOCOL_MISMATCH=1 is set throughout this file to
# prove that.
set -u
export FAKE_HERDR_PROTOCOL_MISMATCH=1

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

if ! command -v hs_host_plugins >/dev/null 2>&1; then
  fail "hs_host_plugins is not defined yet"
  hs_test_report
fi

fixtures="$test_dir/fixtures"

# --- a well-formed plugins.json: one line per entry, sorted by plugin_id ---

out="$(hs_host_plugins "$fixtures/plugins_valid.json")"
status=$?
assert_status "valid plugins.json exits 0" 0 "$status"

expected="$(printf '%s\n%s' \
  'ez-corp.space-usage	ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay	main	94a2ea3bf21ec35c6da51b9657c97167e68034ce' \
  'kryptamine.auto-title	kryptamine/herdr-auto-title	v0.3.3	aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"
assert_eq "prints one <plugin_id>TAB<source>TAB<ref>TAB<commit> line per entry, sorted by plugin_id" \
  "$expected" "$out"

# --- a missing file means a host with no plugins yet: no output, exit 0 ---

missing="$test_dir/fixtures/does-not-exist.json"
out="$(hs_host_plugins "$missing")"
status=$?
assert_status "missing plugins.json exits 0" 0 "$status"
assert_eq "missing plugins.json prints nothing" "" "$out"

# --- a malformed file is a fatal error, exit 2 ---

status="$(hs_host_plugins "$fixtures/plugins_malformed.json" >/dev/null 2>/dev/null; echo $?)"
assert_status "malformed plugins.json exits 2" 2 "$status"

err="$(hs_host_plugins "$fixtures/plugins_malformed.json" 2>&1 1>/dev/null)"
assert_contains "malformed-file error names the file" "$err" "plugins_malformed.json"

hs_test_report
