#!/usr/bin/env bash
# Tests for hs_preflight, hs_require_socket, and hs_herdr_json in
# lib/common.sh. None of the three exist yet; expect failure until
# P1.T3.S2 writes them.
#
# hs_preflight/hs_require_socket rely on the fake herdr that tests/run.sh
# put on PATH (see tests/helpers/fake-herdr): "no server" is simulated by
# pointing HERDR_SOCKET_PATH at a file that was never created, and
# "mismatched" by exporting FAKE_HERDR_PROTOCOL_MISMATCH=1.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
. "$test_dir/helpers/assert.sh"

if [ ! -f "$repo_root/lib/common.sh" ]; then
  fail "lib/common.sh does not exist yet"
  hs_test_report
fi
. "$repo_root/lib/common.sh"

if ! command -v hs_preflight >/dev/null 2>&1; then
  fail "hs_preflight is not defined yet"
  hs_test_report
fi

# --- hs_preflight: no-herdr ---
state="$(PATH="/usr/bin:/bin" hs_preflight)"
assert_eq "no herdr on PATH reports no-herdr" "no-herdr" "$state"

# --- hs_preflight: no-server (herdr present, socket path absent) ---
missing_socket="$HOME/does-not-exist.sock"
state="$(HERDR_SOCKET_PATH="$missing_socket" hs_preflight)"
assert_eq "missing socket reports no-server" "no-server" "$state"

# --- hs_preflight: matched (socket present, fake herdr not in mismatch mode) ---
present_socket="$HOME/herdr.sock"
: > "$present_socket"
state="$(HERDR_SOCKET_PATH="$present_socket" hs_preflight)"
assert_eq "present socket with no mismatch reports matched" "matched" "$state"

# --- hs_preflight: mismatched (socket present, fake herdr in mismatch mode) ---
state="$(HERDR_SOCKET_PATH="$present_socket" FAKE_HERDR_PROTOCOL_MISMATCH=1 hs_preflight)"
assert_eq "protocol mismatch reports mismatched" "mismatched" "$state"

# --- hs_preflight: a probe that failed for ANY OTHER reason is not matched.
# The gate used to conclude "matched" from a call it had just watched fail,
# because only the protocol_mismatch code produced a refusal -- so a server
# that answered socket_closed, or a permission error, or anything else, let
# `apply` write to it. ---
state="$(HERDR_SOCKET_PATH="$present_socket" FAKE_HERDR_ERROR_CODE=socket_closed hs_preflight)"
assert_eq "a probe failing on a non-mismatch error reports unreachable" "unreachable" "$state"
case "$state" in
  matched) fail "a failed probe was reported as a healthy host" ;;
  *) pass ;;
esac

# --- hs_preflight: the probe must SEE stderr. A herdr that writes its error
# object there left the old probe (2>/dev/null) with nothing to read, so the
# mismatch it was looking for was invisible and the host read as matched. ---
state="$(HERDR_SOCKET_PATH="$present_socket" FAKE_HERDR_PROTOCOL_MISMATCH=1 \
  FAKE_HERDR_ERROR_STREAM=stderr hs_preflight)"
assert_eq "a mismatch reported on stderr is still seen as mismatched" "mismatched" "$state"

if ! command -v hs_require_socket >/dev/null 2>&1; then
  fail "hs_require_socket is not defined yet"
  hs_test_report
fi

# --- hs_require_socket: matched returns 0 and is silent ---
err="$HOME/require_matched.err"
( HERDR_SOCKET_PATH="$present_socket" hs_require_socket ) >/dev/null 2>"$err"
status=$?
assert_status "hs_require_socket returns 0 when matched" 0 "$status"
[ ! -s "$err" ] && pass || fail "hs_require_socket prints nothing when matched (got: $(cat "$err"))"

# --- hs_require_socket: no-herdr exits 3, one line to stderr naming the fix ---
err="$HOME/require_noherdr.err"
( PATH="/usr/bin:/bin" hs_require_socket ) >/dev/null 2>"$err"
status=$?
assert_status "hs_require_socket exits 3 with no herdr" 3 "$status"
assert_eq "no-herdr fix is exactly one line" "1" "$(wc -l < "$err" | tr -d ' ')"
assert_contains "no-herdr message names herdr" "$(cat "$err")" "herdr"

# --- hs_require_socket: no-server exits 3, names the fix ---
err="$HOME/require_noserver.err"
( HERDR_SOCKET_PATH="$missing_socket" hs_require_socket ) >/dev/null 2>"$err"
status=$?
assert_status "hs_require_socket exits 3 with no server" 3 "$status"
assert_eq "no-server fix is exactly one line" "1" "$(wc -l < "$err" | tr -d ' ')"
assert_contains "no-server message names the server" "$(cat "$err")" "server"

# --- hs_require_socket: mismatched exits 3, names the restart ---
err="$HOME/require_mismatch.err"
( HERDR_SOCKET_PATH="$present_socket" FAKE_HERDR_PROTOCOL_MISMATCH=1 hs_require_socket ) >/dev/null 2>"$err"
status=$?
assert_status "hs_require_socket exits 3 on mismatch" 3 "$status"
assert_eq "mismatch fix is exactly one line" "1" "$(wc -l < "$err" | tr -d ' ')"
assert_contains "mismatch message names the server being newer" "$(cat "$err")" "newer"
assert_contains "mismatch message names the restart" "$(cat "$err")" "restart"

# --- hs_require_socket: an unreachable server refuses too, and says what
# herdr actually told it. This is the gate the whole fail-closed rule rests
# on; a probe that failed must never let a write command through. ---
err="$HOME/require_unreachable.err"
( HERDR_SOCKET_PATH="$present_socket" FAKE_HERDR_ERROR_CODE=socket_closed hs_require_socket ) \
  >/dev/null 2>"$err"
status=$?
assert_status "hs_require_socket exits 3 when the probe failed for any reason" 3 "$status"
assert_eq "unreachable fix is exactly one line" "1" "$(wc -l < "$err" | tr -d ' ')"
assert_contains "unreachable message carries what herdr said" "$(cat "$err")" "socket_closed"

# --- and the same when herdr said it on stderr ---
err="$HOME/require_unreachable_stderr.err"
( HERDR_SOCKET_PATH="$present_socket" FAKE_HERDR_ERROR_CODE=socket_closed \
  FAKE_HERDR_ERROR_STREAM=stderr hs_require_socket ) >/dev/null 2>"$err"
status=$?
assert_status "hs_require_socket exits 3 when the failure was reported on stderr" 3 "$status"

if ! command -v hs_herdr_json >/dev/null 2>&1; then
  fail "hs_herdr_json is not defined yet"
  hs_test_report
fi

# --- hs_herdr_json: a failed call never yields a parsed result, and reports
# the error to stderr. This is the single most important behaviour in this
# phase: a caller must never read .result.* from a failed call. ---
out="$HOME/json_mismatch.out"
err="$HOME/json_mismatch.err"
FAKE_HERDR_PROTOCOL_MISMATCH=1 hs_herdr_json plugin list >"$out" 2>"$err"
status=$?
assert_status "hs_herdr_json returns non-zero on a failed herdr call" 1 "$status"
[ ! -s "$out" ] && pass || fail "hs_herdr_json prints nothing to stdout on failure (got: $(cat "$out"))"
assert_contains "hs_herdr_json reports the protocol mismatch to stderr" "$(cat "$err")" "protocol"

# --- hs_herdr_json: a clean call passes the result through on stdout ---
fixtures="$HOME/fixtures"
mkdir -p "$fixtures"
echo '{"result":{"plugins":["a"]}}' > "$fixtures/plugin->list.json"
out="$HOME/json_ok.out"
err="$HOME/json_ok.err"
FAKE_HERDR_FIXTURES="$fixtures" hs_herdr_json plugin list >"$out" 2>"$err"
status=$?
assert_status "hs_herdr_json returns 0 on success" 0 "$status"
assert_eq "hs_herdr_json prints the herdr result unchanged" \
  '{"result":{"plugins":["a"]}}' "$(cat "$out")"
[ ! -s "$err" ] && pass || fail "hs_herdr_json is silent on stderr when successful"

# A successful response may carry the token "error" inside a value. Deciding by
# substring reported that as a failed call and threw the result away, so the
# decision is made by parsing the response for a top-level error key.
echo '{"id":"x","result":{"plugins":[{"plugin_id":"p","status":"error"}]}}' \
  > "$fixtures/plugin->list.json"
out="$HOME/json_token.out"
err="$HOME/json_token.err"
FAKE_HERDR_FIXTURES="$fixtures" hs_herdr_json plugin list >"$out" 2>"$err"
status=$?
assert_status "hs_herdr_json succeeds when 'error' is only a value" 0 "$status"
assert_contains "hs_herdr_json returns the result when 'error' is only a value" \
  "$(cat "$out")" '"plugin_id":"p"'
[ ! -s "$err" ] && pass || fail "hs_herdr_json is silent when 'error' is only a value"

# And a genuine error object is still caught, with its message on one line.
echo '{"id":"x","error":{"code":"boom","message":"first line\nsecond line"}}' \
  > "$fixtures/plugin->list.json"
out="$HOME/json_realerr.out"
err="$HOME/json_realerr.err"
FAKE_HERDR_FIXTURES="$fixtures" hs_herdr_json plugin list >"$out" 2>"$err"
status=$?
[ "$status" -ne 0 ] && pass || fail "hs_herdr_json returns non-zero on a real error object"
[ ! -s "$out" ] && pass || fail "hs_herdr_json prints nothing to stdout on a real error object"
assert_eq "hs_herdr_json folds the error message onto one line" \
  1 "$(wc -l < "$err" | tr -d ' ')"
assert_contains "hs_herdr_json surfaces the error message" \
  "$(cat "$err")" "first line second line"

# --- hs_herdr_json keeps stdout and stderr APART. Merging them with 2>&1
# meant one deprecation notice on stderr came back glued to the front of the
# JSON, and the caller's parse failed on a response that was fine. No caller
# parsed the result while that was true; `feed` will. ---
echo '{"result":{"plugins":["a"]}}' > "$fixtures/plugin->list.json"
out="$HOME/json_stderr_note.out"
err="$HOME/json_stderr_note.err"
FAKE_HERDR_FIXTURES="$fixtures" FAKE_HERDR_STDERR_NOTE="warning: --legacy is deprecated" \
  hs_herdr_json plugin list >"$out" 2>"$err"
status=$?
assert_status "hs_herdr_json succeeds when herdr also writes a note to stderr" 0 "$status"
assert_eq "hs_herdr_json returns stdout alone, with nothing from stderr glued on" \
  '{"result":{"plugins":["a"]}}' "$(cat "$out")"
assert_contains "hs_herdr_json still shows the note on its own stderr" \
  "$(cat "$err")" "deprecated"

# --- hs_herdr_json reports a parse it could not make, rather than assuming
# the response was fine. hs_py returning 2 (no uv on PATH) used to leave
# is_error at 0, so an error response with a zero exit status passed as a
# successful result. PATH here keeps the fake herdr and the coreutils this
# function needs, and drops uv. ---
echo '{"id":"x","error":{"code":"boom","message":"nope"}}' > "$fixtures/plugin->list.json"
out="$HOME/json_nopy.out"
err="$HOME/json_nopy.err"
( PATH="$test_dir/helpers:/usr/bin:/bin" FAKE_HERDR_FIXTURES="$fixtures" \
  hs_herdr_json plugin list ) >"$out" 2>"$err"
status=$?
[ "$status" -ne 0 ] && pass || fail "hs_herdr_json passed an error response as success when it could not parse it"
[ ! -s "$out" ] && pass || fail "hs_herdr_json returned an unparsed response as a result (got: $(cat "$out"))"
assert_contains "hs_herdr_json says it could not parse the response" \
  "$(cat "$err")" "could not parse"

hs_test_report
