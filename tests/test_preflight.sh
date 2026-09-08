#!/usr/bin/env bash
# shellcheck disable=SC2015
# this suite's idiom throughout: pass()/fail()
# (tests/helpers/assert.sh) never return nonzero, so "A && pass || fail C" cannot
# silently take the wrong branch.
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
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"

if [ ! -f "$repo_root/lib/common.sh" ]; then
  fail "lib/common.sh does not exist yet"
  hs_test_report
fi
# shellcheck source=lib/common.sh
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

# --- hs_preflight: a refusal that exits 0. This is the nastiest shape a
# server has, and the gate could not see it at all: it decided purely on the
# exit status, so a well-formed error object returned alongside exit 0 was
# read as a successful probe and reported `matched`. hs_require_socket then
# let `apply` rewrite a live config against a server that had refused every
# call. All four combinations -- either code, on either stream -- are the
# same defect, so all four are written down. ---
for hs_t_code in protocol_mismatch socket_closed; do
  if [ "$hs_t_code" = "protocol_mismatch" ]; then
    hs_t_want="mismatched"
  else
    hs_t_want="unreachable"
  fi
  for hs_t_stream in stdout stderr; do
    state="$(HERDR_SOCKET_PATH="$present_socket" FAKE_HERDR_ERROR_CODE="$hs_t_code" \
      FAKE_HERDR_ERROR_STREAM="$hs_t_stream" FAKE_HERDR_ERROR_EXIT=0 hs_preflight)"
    assert_eq "an error object with exit 0 ($hs_t_code on $hs_t_stream) is not matched" \
      "$hs_t_want" "$state"
    case "$state" in
      matched) fail "a server that refused the probe and exited 0 was reported as healthy ($hs_t_code on $hs_t_stream)" ;;
      *) pass ;;
    esac
  done
done

if ! command -v hs_require_socket >/dev/null 2>&1; then
  fail "hs_require_socket is not defined yet"
  hs_test_report
fi

# --- and the write gate must stop there too: this is the whole point of the
# four states above. hs_require_socket is what `apply` calls. ---
for hs_t_stream in stdout stderr; do
  err="$HOME/require_exit0_$hs_t_stream.err"
  ( HERDR_SOCKET_PATH="$present_socket" FAKE_HERDR_ERROR_CODE=protocol_mismatch \
    FAKE_HERDR_ERROR_STREAM="$hs_t_stream" FAKE_HERDR_ERROR_EXIT=0 hs_require_socket ) \
    >/dev/null 2>"$err"
  status=$?
  assert_status "hs_require_socket exits 3 on an error object with exit 0 (on $hs_t_stream)" \
    3 "$status"
done

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
printf '%s\n' '{"id":"x","error":{"code":"boom","message":"first line\nsecond line"}}' \
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

# --- hs_herdr_json must see an error object on STDERR too. Its own contract
# says it never returns a result from a failed call, but it only ever looked
# at stdout: an error reported on stderr left the filter with an empty string,
# so with a zero exit status the call was reported as a SUCCESS and the error
# text was passed through as if it were a harmless notice. `apply` then wrote
# the config and "successfully" called reload-config against a server that had
# refused it.
#
# This is the second half of the same fix as the probe above: an earlier
# change taught the probe to read stderr, a later one taught this function to
# stop MERGING stderr, and nobody re-asked whether it could still SEE an error
# there. ---
rm -f "$fixtures/plugin->list.json"
for hs_t_exit in 0 1; do
  out="$HOME/json_stderr_err_$hs_t_exit.out"
  err="$HOME/json_stderr_err_$hs_t_exit.err"
  FAKE_HERDR_ERROR_CODE=boom FAKE_HERDR_ERROR_STREAM=stderr \
    FAKE_HERDR_ERROR_EXIT="$hs_t_exit" hs_herdr_json plugin list >"$out" 2>"$err"
  status=$?
  [ "$status" -ne 0 ] && pass \
    || fail "hs_herdr_json returned 0 for an error object on stderr (herdr exited $hs_t_exit)"
  [ ! -s "$out" ] && pass \
    || fail "hs_herdr_json passed an error object on stderr through as a result (got: $(cat "$out"))"
  assert_contains "the error object on stderr is reported (herdr exited $hs_t_exit)" \
    "$(cat "$err")" "boom"
done

# --- a response that carries the token but is not JSON at all. hs.py used to
# answer 1 for BOTH "read it, no error key" and "could not read it", and the
# shell read 1 as the former -- so a response nobody could parse passed as a
# clean result. The two answers are now told apart, and only the first one
# means the call was fine. ---
printf '%s\n' 'herdr: internal failure, "error" while talking to the server' \
  > "$fixtures/plugin->list.json"
out="$HOME/json_notjson.out"; err="$HOME/json_notjson.err"
FAKE_HERDR_FIXTURES="$fixtures" hs_herdr_json plugin list >"$out" 2>"$err"
status=$?
[ "$status" -ne 0 ] && pass \
  || fail "hs_herdr_json passed an unparseable response carrying 'error' as a result"
[ ! -s "$out" ] && pass \
  || fail "hs_herdr_json returned an unparseable response as a result (got: $(cat "$out"))"
assert_contains "it says it could not parse the response" "$(cat "$err")" "could not parse"
rm -f "$fixtures/plugin->list.json"

# --- and the fix must NOT be a blanket "empty stdout means failure": `server
# reload-config` legitimately answers nothing at all, and apply calls it on
# every write. ---
out="$HOME/json_silent.out"; err="$HOME/json_silent.err"
FAKE_HERDR_FIXTURES="$fixtures" hs_herdr_json server reload-config >"$out" 2>"$err"
status=$?
assert_status "a call that legitimately prints nothing still succeeds" 0 "$status"
[ ! -s "$err" ] && pass || fail "a silent successful call wrote to stderr: $(cat "$err")"

hs_test_report
