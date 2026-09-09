#!/usr/bin/env bash
# `herdr-setup feed` through the real entrypoint.
#
# lib/feed.py is tested directly and thoroughly by tests/test_feed_probe.py,
# tests/test_feed_resolve.py and tests/test_feed_report.py. This file tests
# the four things only the entrypoint can get wrong:
#
#   1. feed gates on hs_require_socket, FIRST -- the acceptance row
#      socket-commands-refuse-under-mismatch names feed explicitly, and the
#      gate is the reason the tool exists: a mismatched Herdr answers every
#      call with an error object, and a feed run that read those as empty
#      results would report success having fed nothing.
#   2. The gate holds under --dry-run too. A dry run's job is to say what a
#      real run would do, and a refused host cannot answer that either.
#   3. --dry-run and --yes actually reach lib/feed.py.
#   4. The adapters directory is the one beside the entrypoint, so a
#      checkout's own adapters are what run.
#
# The sandbox trick is the established one (tests/test_diff_plugins.sh):
# HS_ROOT follows $0, so a copy of the entrypoint in $work gets its own root
# and its own adapters/, and nothing is written to this checkout.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

sandbox="$work/sandbox"
mkdir -p "$sandbox/lib" "$sandbox/adapters"
cp "$repo_root/herdr-setup" "$sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$sandbox/lib/hs.py"
cp "$repo_root/lib/feed.py" "$sandbox/lib/feed.py"
chmod +x "$sandbox/herdr-setup"

# The fake herdr is fed the CAPTURES, byte for byte. Nothing here writes a
# Herdr response by hand: the runner's first cut read three fields Herdr does
# not return, and its hand-built fixtures agreed with it, so an end-to-end
# test built the same way would have agreed too. See tests/fixtures/herdr/.
fixtures="$work/fixtures"
mkdir -p "$fixtures"
captures="$repo_root/tests/fixtures/herdr"
cp "$captures/agent-list.json" "$fixtures/agent->list.json"

# The capture holds three panes; every one is answered with the captured
# process-info, repointed at that pane id. `pane` below is the first of them.
pane_ids="$(sed -n 's/.*"pane_id": "\([^"]*\)".*/\1/p' "$captures/agent-list.json")"
for pid_ in $pane_ids; do
  sed "s/\"pane_id\": \"[^\"]*\"/\"pane_id\": \"$pid_\"/" \
    "$captures/pane-process-info.json" > "$fixtures/pane->process-info->--pane->$pid_.json"
done

pane="$(printf '%s\n' "$pane_ids" | head -n1)"
if [ -n "$pane" ]; then
  pass
else
  fail "no pane ids found in the captured agent list"
fi

# One adapter: exact confidence, one candidate. That is the single case the
# runner reports without asking, so a --dry-run here shows a report line.
cat > "$sandbox/adapters/claude" <<ADAPTER
#!/bin/sh
case "\$1" in
  probe)
    echo '{"agent":"claude","source":"herdr:claude","available":true,"confidence":"exact"}'
    ;;
  resolve)
    cat >/dev/null
    echo "{\"results\":[{\"pane_id\":\"$pane\",\"candidates\":[{\"session_id\":\"0260abcd\",\"label\":\"frank\",\"confidence\":\"exact\"}]}]}"
    ;;
esac
ADAPTER
chmod +x "$sandbox/adapters/claude"

# hs_preflight requires the socket path to EXIST before it probes, so the
# fixture socket is a plain file: the probe itself is the fake herdr, and no
# assertion below ever sends anything (every run here is --dry-run).
socket="$work/herdr.sock"
: > "$socket"
missing_socket="$work/no-such-herdr.sock"

# --- (1) a mismatched Herdr refuses feed, exit 3, one line naming the fix ---

out="$work/out_mismatch"; err="$work/err_mismatch"
HERDR_SOCKET_PATH="$socket" FAKE_HERDR_FIXTURES="$fixtures" FAKE_HERDR_PROTOCOL_MISMATCH=1 \
  "$sandbox/herdr-setup" feed >"$out" 2>"$err"
status=$?
assert_status "feed refuses under a protocol mismatch" 3 "$status"
assert_contains "the refusal names the restart" "$(cat "$err")" "restart"
assert_eq "a refused feed prints nothing to stdout" "" "$(cat "$out")"

# --- (2) the gate holds under --dry-run as well ---

out="$work/out_mismatch_dry"; err="$work/err_mismatch_dry"
HERDR_SOCKET_PATH="$socket" FAKE_HERDR_FIXTURES="$fixtures" FAKE_HERDR_PROTOCOL_MISMATCH=1 \
  "$sandbox/herdr-setup" --dry-run feed >"$out" 2>"$err"
status=$?
assert_status "feed --dry-run refuses under a protocol mismatch too" 3 "$status"

# --- (3) no server at all is the same refusal ---

out="$work/out_noserver"; err="$work/err_noserver"
HERDR_SOCKET_PATH="$missing_socket" FAKE_HERDR_FIXTURES="$fixtures" \
  "$sandbox/herdr-setup" feed >"$out" 2>"$err"
status=$?
assert_status "feed refuses with no Herdr server" 3 "$status"
assert_contains "the refusal names the server" "$(cat "$err")" "server"

# --- (4) an unreachable server (the probe fails for some other reason) is
# also a refusal, not a healthy host with nothing to feed ---

out="$work/out_unreachable"; err="$work/err_unreachable"
HERDR_SOCKET_PATH="$socket" FAKE_HERDR_FIXTURES="$fixtures" \
  FAKE_HERDR_ERROR_CODE=socket_closed FAKE_HERDR_ERROR_STREAM=stderr \
  "$sandbox/herdr-setup" feed >"$out" 2>"$err"
status=$?
assert_status "feed refuses when the probe fails for any other reason" 3 "$status"

# --- (5) a matched host runs the adapters beside the entrypoint, and
# --dry-run prints the call it would make and sends nothing ---

out="$work/out_dry"; err="$work/err_dry"
HERDR_SOCKET_PATH="$socket" FAKE_HERDR_FIXTURES="$fixtures" \
  "$sandbox/herdr-setup" --dry-run feed >"$out" 2>"$err"
status=$?
assert_status "feed --dry-run exits 0 on a matched host" 0 "$status"
assert_contains "it names the report method" "$(cat "$out")" "pane.report_agent_session"
assert_contains "it carries the pane it found" "$(cat "$out")" "$pane"
assert_contains "it carries the session the adapter resolved" "$(cat "$out")" "0260abcd"
assert_contains "it says the run was a dry one" "$(cat "$out")" "dry run"
assert_contains "the summary counts the pane" "$(cat "$out")" "reported 1 pane"

# --- (6) --yes reaches lib/feed.py: with two heuristic candidates the runner
# would otherwise skip (no terminal here), and with --yes it takes the best ---

cat > "$sandbox/adapters/claude" <<ADAPTER
#!/bin/sh
case "\$1" in
  probe)
    echo '{"agent":"claude","source":"herdr:claude","available":true,"confidence":"heuristic"}'
    ;;
  resolve)
    cat >/dev/null
    echo "{\"results\":[{\"pane_id\":\"$pane\",\"candidates\":[{\"session_id\":\"first\",\"confidence\":\"heuristic\"},{\"session_id\":\"second\",\"confidence\":\"heuristic\"}]}]}"
    ;;
esac
ADAPTER
chmod +x "$sandbox/adapters/claude"

out="$work/out_noyes"; err="$work/err_noyes"
HERDR_SOCKET_PATH="$socket" FAKE_HERDR_FIXTURES="$fixtures" \
  "$sandbox/herdr-setup" --dry-run feed >"$out" 2>"$err"
assert_contains "without --yes an uncertain pane is skipped" "$(cat "$out")" "reported 0 panes"
assert_contains "and the skip says why" "$(cat "$err")" "$pane"

out="$work/out_yes"; err="$work/err_yes"
HERDR_SOCKET_PATH="$socket" FAKE_HERDR_FIXTURES="$fixtures" \
  "$sandbox/herdr-setup" --dry-run --yes feed >"$out" 2>"$err"
assert_contains "with --yes the best candidate is taken" "$(cat "$out")" "first"
assert_contains "and the summary counts it" "$(cat "$out")" "reported 1 pane"

hs_test_report
