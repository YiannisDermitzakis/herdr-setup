#!/usr/bin/env bash
# Tests for the herdr-setup entrypoint: usage, subcommand dispatch, and
# global flags. The entrypoint does not exist yet; expect failure until
# P1.T2.S2 writes it.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
. "$test_dir/helpers/assert.sh"

entry="$repo_root/herdr-setup"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# run_entry <out-file> <err-file> [args...] -- runs the entrypoint, prints
# its exit status on stdout (the caller captures it via command substitution).
run_entry() {
  local out_file="$1" err_file="$2"
  shift 2
  "$entry" "$@" >"$out_file" 2>"$err_file"
  echo $?
}

if [ ! -x "$entry" ]; then
  fail "herdr-setup does not exist yet (or is not executable) at $entry"
  hs_test_report
fi

# --- no arguments: usage to stderr, exit 2 ---
out="$work/out_noargs"; err="$work/err_noargs"
status="$(run_entry "$out" "$err")"
assert_status "no args exits 2" 2 "$status"
[ -s "$err" ] && pass || fail "no-args usage goes to stderr"
[ ! -s "$out" ] && pass || fail "no-args prints nothing to stdout"
assert_contains "no-args usage names the commands" "$(cat "$err")" "diff"

# --- unknown subcommand: exit 2, naming the subcommand ---
out="$work/out_unknown"; err="$work/err_unknown"
status="$(run_entry "$out" "$err" frobnicate)"
assert_status "unknown subcommand exits 2" 2 "$status"
assert_contains "unknown subcommand names itself" "$(cat "$err")" "frobnicate"

# --- --help: usage to stdout, exit 0 ---
out="$work/out_help"; err="$work/err_help"
status="$(run_entry "$out" "$err" --help)"
assert_status "--help exits 0" 0 "$status"
assert_contains "--help prints usage" "$(cat "$out")" "usage:"

# --- each of the five subcommands is accepted with a stub that exits 0 ---
for sub in diff apply absorb onboard feed; do
  out="$work/out_$sub"; err="$work/err_$sub"
  status="$(run_entry "$out" "$err" "$sub")"
  assert_status "$sub is accepted and exits 0" 0 "$status"
done

# --- --dry-run / --yes are visible to the subcommand as HS_DRY_RUN / HS_YES,
# regardless of whether they come before or after the subcommand ---
out="$work/out_flags_before"; err="$work/err_flags_before"
status="$(run_entry "$out" "$err" --dry-run --yes diff)"
assert_status "flags before the subcommand still exit 0" 0 "$status"
assert_contains "HS_DRY_RUN=1 visible (flags before)" "$(cat "$out")" "dry-run=1"
assert_contains "HS_YES=1 visible (flags before)" "$(cat "$out")" "yes=1"

out="$work/out_flags_after"; err="$work/err_flags_after"
status="$(run_entry "$out" "$err" diff --dry-run --yes)"
assert_status "flags after the subcommand still exit 0" 0 "$status"
assert_contains "HS_DRY_RUN=1 visible (flags after)" "$(cat "$out")" "dry-run=1"
assert_contains "HS_YES=1 visible (flags after)" "$(cat "$out")" "yes=1"

out="$work/out_noflags"; err="$work/err_noflags"
status="$(run_entry "$out" "$err" diff)"
assert_status "no flags still exits 0" 0 "$status"
assert_contains "HS_DRY_RUN defaults to 0" "$(cat "$out")" "dry-run=0"
assert_contains "HS_YES defaults to 0" "$(cat "$out")" "yes=0"

hs_test_report
