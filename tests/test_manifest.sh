#!/usr/bin/env bash
# Tests for hs_manifest_plugins in lib/common.sh. It does not exist yet;
# expect failure until P2.T1.S2 writes it.
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

# --- one field: VALID, and means the repository's default branch ---
#
# Herdr records no `requested_ref` for a plugin installed without `--ref`; it
# simply took the default branch. A manifest that could not express that could
# not describe such a host, and `diff` died with exit 2 on the first Linux
# machine this was run on. One field is therefore a plugin with no pinned ref,
# and the ref field comes back empty.

out="$(hs_manifest_plugins "$fixtures/manifest_no_ref.list")"
status=$?
assert_status "a ref-less line is accepted" 0 "$status"
assert_eq "a ref-less line yields an empty ref" \
  "kryptamine/herdr-auto-title$(printf '\t')" \
  "$(printf '%s' "$out" | sed -n 1p)"
assert_eq "a pinned line still yields its ref" \
  "ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay$(printf '\t')main" \
  "$(printf '%s' "$out" | sed -n 2p)"

# The old fixture repeats one source, so it is a DUPLICATE test, not a
# field-count test. Keeping it under its real name, because it passed under the
# wrong one while the field-count rule was being changed out from under it.

status="$(hs_manifest_plugins "$fixtures/manifest_one_field.list" >/dev/null 2>/dev/null; echo $?)"
assert_status "a repeated source exits 2" 2 "$status"
err="$(hs_manifest_plugins "$fixtures/manifest_one_field.list" 2>&1 1>/dev/null)"
assert_contains "the duplicate error names the file" "$err" "manifest_one_field.list"

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

# --- the same source named twice: fatal, exit 2. Two lines for one plugin
# are either redundant (apply ran the same install twice) or contradictory
# (two pinned refs, and nothing says which the host converges on). Both were
# silently processed in order. ---

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

dupes="$work/duplicate.list"
cat > "$dupes" <<'EOF'
kryptamine/herdr-auto-title   v0.3.3
ezcorp-org/overlay            main
kryptamine/herdr-auto-title   v0.4.0
EOF

status="$(hs_manifest_plugins "$dupes" >/dev/null 2>/dev/null; echo $?)"
assert_status "a duplicated source exits 2" 2 "$status"
err="$(hs_manifest_plugins "$dupes" 2>&1 1>/dev/null)"
assert_contains "the duplicate error names the source" "$err" "kryptamine/herdr-auto-title"
assert_contains "the duplicate error names the second line" "$err" ":3:"
assert_contains "the duplicate error says what is wrong" "$err" "duplicate"

# an identical repeat is refused too -- it is still two installs
same_twice="$work/same_twice.list"
cat > "$same_twice" <<'EOF'
kryptamine/herdr-auto-title   v0.3.3
kryptamine/herdr-auto-title   v0.3.3
EOF
status="$(hs_manifest_plugins "$same_twice" >/dev/null 2>/dev/null; echo $?)"
assert_status "an identical repeated line exits 2 as well" 2 "$status"

# --- hs_manifest_plugins borrows `set -f` to count a line's fields, and it
# used to hand the shell back with globbing ON regardless of how it found
# it. A caller that had deliberately turned globbing off got it back. ---

set -f
hs_manifest_plugins "$fixtures/manifest_valid.list" >/dev/null 2>&1
case "$-" in
  *f*) pass ;;
  *) fail "hs_manifest_plugins turned the caller's noglob back off" ;;
esac
set +f

hs_manifest_plugins "$fixtures/manifest_valid.list" >/dev/null 2>&1
case "$-" in
  *f*) fail "hs_manifest_plugins left globbing disabled for a caller that had it on" ;;
  *) pass ;;
esac

hs_test_report
