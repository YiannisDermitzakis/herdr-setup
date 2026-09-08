#!/usr/bin/env bash
# Tests for hs_manifest_plugins in lib/common.sh. It does not exist yet;
# expect failure until P2.T1.S2 writes it.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
. "$test_dir/helpers/assert.sh"

if [ ! -f "$repo_root/lib/common.sh" ]; then
  fail "lib/common.sh does not exist yet"
  hs_test_report
fi
. "$repo_root/lib/common.sh"

if ! command -v hs_manifest_plugins >/dev/null 2>&1; then
  fail "hs_manifest_plugins is not defined yet"
  hs_test_report
fi

fixtures="$test_dir/fixtures"

# --- a well-formed manifest: comments and blank lines skipped, whitespace
# trimmed, a source with a subdir keeps the subdir in the source field ---

out="$(hs_manifest_plugins "$fixtures/manifest_valid.list")"
status=$?
assert_status "valid manifest exits 0" 0 "$status"

expected="$(printf '%s\n%s\n%s' \
  'kryptamine/herdr-auto-title	v0.3.3' \
  'ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay	main' \
  'some-org/some-repo/tools/subdir	v1.2')"
assert_eq "valid manifest prints one <source>TAB<ref> line per entry, subdir kept" \
  "$expected" "$out"

# --- one field (missing ref): fatal, exit 2, names file and line number ---

status="$(hs_manifest_plugins "$fixtures/manifest_one_field.list" >/dev/null 2>/dev/null; echo $?)"
assert_status "one-field line exits 2" 2 "$status"

err="$(hs_manifest_plugins "$fixtures/manifest_one_field.list" 2>&1 1>/dev/null)"
assert_contains "one-field error names the file" "$err" "manifest_one_field.list"
assert_contains "one-field error names the line number" "$err" ":2:"

# --- three fields (extra token after ref): fatal, exit 2, names file/line ---

status="$(hs_manifest_plugins "$fixtures/manifest_three_field.list" >/dev/null 2>/dev/null; echo $?)"
assert_status "three-field line exits 2" 2 "$status"

err="$(hs_manifest_plugins "$fixtures/manifest_three_field.list" 2>&1 1>/dev/null)"
assert_contains "three-field error names the file" "$err" "manifest_three_field.list"
assert_contains "three-field error names the line number" "$err" ":2:"

# --- missing manifest: fail closed with one clear line, exit 2 (AGENTS.md:
# "an unreadable manifest ... stops the run with one clear line") ---

missing="$fixtures/does-not-exist.list"
status="$(hs_manifest_plugins "$missing" >/dev/null 2>/dev/null; echo $?)"
assert_status "missing manifest exits 2" 2 "$status"

err="$(hs_manifest_plugins "$missing" 2>&1 1>/dev/null)"
assert_contains "missing-manifest error names the file" "$err" "does-not-exist.list"
assert_eq "missing-manifest error is exactly one line" "1" "$(printf '%s\n' "$err" | wc -l | tr -d ' ')"

hs_test_report
