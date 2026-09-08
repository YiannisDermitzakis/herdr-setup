# lib/common.sh
#
# Shared constants and helpers for herdr-setup. Sourced by the entrypoint and
# by tests/test_*.sh — never executed directly.
#
# Deliberately free of `set -e`/`set -u`/`set -o pipefail`: this file is
# sourced into scripts that need to inspect the exit status of a command
# that is expected to fail (the preflight gate, herdr calls). Setting shell
# options here would apply them to whatever sources this file.
#
# Bash 3.2 / Python 3.9 floor: no associative arrays, no mapfile, no
# `local -n`, no tomllib.

HS_DEFAULT_SOCKET_PATH="$HOME/.config/herdr/herdr.sock"

hs_socket_path() {
  echo "${HERDR_SOCKET_PATH:-$HS_DEFAULT_SOCKET_PATH}"
}

# hs_preflight: echoes exactly one of no-herdr | no-server | mismatched |
# matched. Never writes anything and never fails: every command decides
# what to do with the state itself (diff keeps going on no-server/
# mismatched; hs_require_socket below is what the write commands use to
# stop).
hs_preflight() {
  if ! command -v herdr >/dev/null 2>&1; then
    echo "no-herdr"
    return 0
  fi

  local socket_path
  socket_path="$(hs_socket_path)"
  if [ ! -e "$socket_path" ]; then
    echo "no-server"
    return 0
  fi

  # Detect a version mismatch by running a real socket call and checking
  # its exit status FIRST: a blocked herdr answers with a JSON error object
  # and exits non-zero, never with an empty success. Only after confirming
  # the call failed do we look at what it said.
  local output rc
  output="$(herdr plugin list 2>/dev/null)"
  rc=$?
  if [ "$rc" -ne 0 ] && printf '%s' "$output" | grep -q '"code":"protocol_mismatch"'; then
    echo "mismatched"
    return 0
  fi

  echo "matched"
  return 0
}

# hs_require_socket: returns 0 when hs_preflight reports matched. Otherwise
# prints exactly one line to stderr naming the state and the fix, and exits
# 3. Commands that need the socket (apply, onboard, feed) call this first;
# diff does not, since it works from disk and `herdr integration status`
# regardless of preflight state.
hs_require_socket() {
  local state
  state="$(hs_preflight)"
  case "$state" in
    matched)
      return 0
      ;;
    no-herdr)
      echo "herdr-setup: herdr is not on PATH; install Herdr, then retry." >&2
      return 3
      ;;
    no-server)
      echo "herdr-setup: no Herdr server found at $(hs_socket_path); start the Herdr server, then retry." >&2
      return 3
      ;;
    mismatched)
      echo "herdr-setup: the herdr command line is newer than the running server; restart the Herdr server, then retry." >&2
      return 3
      ;;
    *)
      echo "herdr-setup: unrecognized preflight state '$state'." >&2
      return 3
      ;;
  esac
}

# hs_herdr_json: the single wrapper every later phase uses to call herdr.
# Runs `herdr "$@"`, checks the exit status FIRST, and NEVER returns a
# result from a failed call — a blocked herdr answers with a JSON error
# object rather than an empty result, and a caller that reads .result.*
# without checking the exit status would see zero of everything and report
# success having done nothing. Returns non-zero on a failed exit status or
# on an "error" key in the output (even if the exit status was 0), and
# prints the error message to stderr in either case. On success, prints the
# herdr output on stdout unchanged and returns 0.
hs_herdr_json() {
  local output rc message
  output="$(herdr "$@" 2>&1)"
  rc=$?

  message=""
  if [ -n "$output" ]; then
    message="$(printf '%s' "$output" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    data = None
if isinstance(data, dict):
    err = data.get("error")
    if isinstance(err, dict):
        print(err.get("message", ""))
' 2>/dev/null)"
  fi

  if [ "$rc" -ne 0 ] || [ -n "$message" ]; then
    echo "herdr-setup: herdr $*: ${message:-$output}" >&2
    if [ "$rc" -eq 0 ]; then
      return 1
    fi
    return "$rc"
  fi

  printf '%s\n' "$output"
  return 0
}
