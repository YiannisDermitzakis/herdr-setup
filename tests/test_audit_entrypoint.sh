#!/usr/bin/env bash
# `herdr-setup audit` through the real entrypoint.
#
# lib/audit.py's logic is tested directly by tests/test_audit_*.py. This file
# proves what only a run through the entrypoint can:
#
#   1. audit gates on hs_require_socket, FIRST -- exactly like feed's own
#      gate (tests/test_feed_entrypoint.sh), because audit needs `herdr
#      agent list` and `herdr tab list`.
#   2. A matched host reaches lib/audit.py through the hs_audit door, and a
#      source it cannot read fails closed: one line, exit 2, no report.
#   3. Arguments (here, --since) actually reach lib/audit.py's own argparse.
#   4. cmd_audit forwards --adapters and --socket before the operator's flags.
#
# The sandbox trick is the established one (tests/test_feed_entrypoint.sh):
# HS_ROOT follows $0, so a copy of the entrypoint in $work gets its own root,
# and nothing is written to this checkout.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"

# Resolved with `cd -P`, matching tests/test_install.sh's own fix: HS_ROOT is
# `cd -P`-resolved from $0, and on macOS /var/folders is itself a symlink to
# /private/var/folders, so an unresolved $work built the --adapters path
# cmd_audit is expected to forward one path form off from the one it
# actually built.
work="$(cd -P "$(mktemp -d)" && pwd)"
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

# --- (2) a matched host reaches lib/audit.py through hs_audit, and a source
# it cannot read fails closed: here the fake gh has no state to answer
# `gh api user` from, so the run stops with one line and never prints a
# clean, empty report ---
out="$work/out_matched"; err="$work/err_matched"
HERDR_SOCKET_PATH="$socket" \
  "$sandbox/herdr-setup" audit >"$out" 2>"$err"
status=$?
assert_status "a matched host reaches the audit runner, which exits 2 on a failed gh call" 2 "$status"
assert_eq "no report is printed" "" "$(cat "$out")"
assert_contains "one line naming the gh call" "$(cat "$err")" "herdr-setup: audit: gh api user"
assert_eq "and only one line" "1" "$(wc -l < "$err" | tr -d ' ')"

# --- (3) --since is real argparse, reaching lib/audit.py: 0 is out of range
# (1..3650) and argparse itself refuses, naming the flag ---
out="$work/out_since"; err="$work/err_since"
HERDR_SOCKET_PATH="$socket" \
  "$sandbox/herdr-setup" audit --since 0 >"$out" 2>"$err"
status=$?
assert_status "--since 0 is out of range and argparse refuses" 2 "$status"
assert_contains "the refusal names --since" "$(cat "$err")" "--since"

# --- (4) cmd_audit forwards --adapters and --socket BEFORE the operator's
# own flags, exactly the way cmd_feed builds its own args (herdr-setup's own
# comment on cmd_audit). Proven against a SANDBOX STUB standing in for
# lib/audit.py that just echoes its argv -- the real lib/audit.py is never
# touched for this. ---
sandbox4="$work/sandbox4"
mkdir -p "$sandbox4/lib"
cp "$repo_root/herdr-setup" "$sandbox4/herdr-setup"
cp "$repo_root/lib/common.sh" "$sandbox4/lib/common.sh"
chmod +x "$sandbox4/herdr-setup"
cat > "$sandbox4/lib/audit.py" <<'STUB'
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
# A stand-in for lib/audit.py, used ONLY to prove what argv cmd_audit
# builds. It never touches the real audit runner.
import sys

print("argv:" + " ".join(sys.argv[1:]))
STUB
chmod +x "$sandbox4/lib/audit.py"

out="$work/out_args"; err="$work/err_args"
HERDR_SOCKET_PATH="$socket" "$sandbox4/herdr-setup" audit --since 5 >"$out" 2>"$err"
status=$?
assert_status "the stub run exits 0" 0 "$status"
assert_eq "cmd_audit forwards --adapters and --socket before the operator's own flags" \
  "argv:--adapters $sandbox4/adapters --socket $socket --since 5" "$(cat "$out")"

hs_test_report
