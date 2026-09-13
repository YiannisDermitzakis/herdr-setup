#!/usr/bin/env bash
# `herdr-setup audit` through the real entrypoint -- the skeleton (phase 1).
#
# lib/audit.py is a stub in this phase: it accepts its real flags through
# argparse, prints one line, and exits 2 -- the skeleton FAILS CLOSED rather
# than printing an empty, clean-looking report (AGENTS.md: "fail closed").
# Later phases give it a real body; this file only proves the entrypoint's
# OWN plumbing, which later phases build on unchanged:
#
#   1. audit gates on hs_require_socket, FIRST -- exactly like feed's own
#      gate (tests/test_feed_entrypoint.sh), because audit needs `herdr
#      agent list` (and, once implemented, `herdr tab list`) too.
#   2. A matched host reaches lib/audit.py through the new hs_audit door.
#   3. Arguments (here, --since) actually reach lib/audit.py's own argparse.
#
# The sandbox trick is the established one (tests/test_feed_entrypoint.sh):
# HS_ROOT follows $0, so a copy of the entrypoint in $work gets its own root,
# and nothing is written to this checkout.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

sandbox="$work/sandbox"
mkdir -p "$sandbox/lib"
cp "$repo_root/herdr-setup" "$sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$sandbox/lib/hs.py"
cp "$repo_root/lib/feed.py" "$sandbox/lib/feed.py"
cp "$repo_root/lib/audit.py" "$sandbox/lib/audit.py"
chmod +x "$sandbox/herdr-setup" "$sandbox/lib/audit.py"

# hs_preflight requires the socket path to EXIST before it probes. The probe
# itself is the fake herdr, and -- with no fixture for `plugin list` -- it
# answers with nothing at all, which hs_response_error reads as success.
socket="$work/herdr.sock"
: > "$socket"

# --- (1) a mismatched Herdr refuses audit, exit 3, before lib/audit.py ever
# runs -- the same one-line refusal feed's own gate prints ---
out="$work/out_mismatch"; err="$work/err_mismatch"
HERDR_SOCKET_PATH="$socket" FAKE_HERDR_PROTOCOL_MISMATCH=1 \
  "$sandbox/herdr-setup" audit >"$out" 2>"$err"
status=$?
assert_status "audit refuses under a protocol mismatch" 3 "$status"
assert_contains "the refusal names the restart" "$(cat "$err")" "restart"
assert_eq "a refused audit prints nothing to stdout" "" "$(cat "$out")"

# --- (2) a matched host reaches lib/audit.py through hs_audit, and the
# skeleton fails closed: it never prints a clean, empty report ---
out="$work/out_matched"; err="$work/err_matched"
HERDR_SOCKET_PATH="$socket" \
  "$sandbox/herdr-setup" audit >"$out" 2>"$err"
status=$?
assert_status "a matched host reaches the audit skeleton, which exits 2" 2 "$status"
assert_contains "the skeleton says it is incomplete rather than printing an empty report" \
  "$(cat "$out")" "incomplete: the audit runner is not implemented yet"

# --- (3) --since is real argparse, reaching lib/audit.py: 0 is out of range
# (1..3650) and argparse itself refuses, naming the flag ---
out="$work/out_since"; err="$work/err_since"
HERDR_SOCKET_PATH="$socket" \
  "$sandbox/herdr-setup" audit --since 0 >"$out" 2>"$err"
status=$?
assert_status "--since 0 is out of range and argparse refuses" 2 "$status"
assert_contains "the refusal names --since" "$(cat "$err")" "--since"

hs_test_report
