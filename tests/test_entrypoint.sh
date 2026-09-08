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

# --- each of absorb/onboard/feed is accepted with a stub that exits 0.
# diff (phase 2) and apply (phase 4) got real implementations and are
# checked separately below: this checkout has no manifest/plugins.list
# yet (that lands in phase 5/10), so they fail closed per AGENTS.md ("an
# unreadable manifest ... stops the run") rather than reporting a stub
# success. ---
for sub in absorb onboard feed; do
  out="$work/out_$sub"; err="$work/err_$sub"
  status="$(run_entry "$out" "$err" "$sub")"
  assert_status "$sub is accepted and exits 0" 0 "$status"
done

out="$work/out_diff"; err="$work/err_diff"
status="$(run_entry "$out" "$err" diff)"
assert_status "diff is accepted; fails closed on a missing manifest" 2 "$status"
assert_contains "diff names the missing manifest on stderr" "$(cat "$err")" "manifest"

# --- apply calls hs_require_socket first, unconditionally, and fails
# closed there before it ever gets to a missing manifest -- proving the
# gate runs before anything else, exactly as AGENTS.md's fail-closed rule
# and the design doc's preflight section require. HERDR_SOCKET_PATH is
# forced to a path that cannot exist rather than relying on the default
# derived from $HOME: a host that itself runs Herdr (as this one does)
# exports HERDR_SOCKET_PATH into every child process, which would
# otherwise silently point this test at the operator's own real socket
# regardless of the sandboxed $HOME tests/run.sh sets up. ---
out="$work/out_apply"; err="$work/err_apply"
status="$(HERDR_SOCKET_PATH="$work/no-such-herdr.sock" run_entry "$out" "$err" apply)"
assert_status "apply is accepted; fails closed with no Herdr server reachable" 3 "$status"
assert_contains "apply names the missing server on stderr" "$(cat "$err")" "server"

# --- --dry-run / --yes are visible to the subcommand as HS_DRY_RUN / HS_YES,
# regardless of whether they come before or after the subcommand. Uses
# `absorb`, still a phase-1 stub, since neither diff nor apply echoes the
# flags it saw any more. ---
out="$work/out_flags_before"; err="$work/err_flags_before"
status="$(run_entry "$out" "$err" --dry-run --yes absorb)"
assert_status "flags before the subcommand still exit 0" 0 "$status"
assert_contains "HS_DRY_RUN=1 visible (flags before)" "$(cat "$out")" "dry-run=1"
assert_contains "HS_YES=1 visible (flags before)" "$(cat "$out")" "yes=1"

out="$work/out_flags_after"; err="$work/err_flags_after"
status="$(run_entry "$out" "$err" absorb --dry-run --yes)"
assert_status "flags after the subcommand still exit 0" 0 "$status"
assert_contains "HS_DRY_RUN=1 visible (flags after)" "$(cat "$out")" "dry-run=1"
assert_contains "HS_YES=1 visible (flags after)" "$(cat "$out")" "yes=1"

out="$work/out_noflags"; err="$work/err_noflags"
status="$(run_entry "$out" "$err" absorb)"
assert_status "no flags still exits 0" 0 "$status"
assert_contains "HS_DRY_RUN defaults to 0" "$(cat "$out")" "dry-run=0"
assert_contains "HS_YES defaults to 0" "$(cat "$out")" "yes=0"

hs_test_report
