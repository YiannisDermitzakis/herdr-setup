#!/usr/bin/env bash
# `herdr-setup onboard` through the real entrypoint: cmd_onboard, in
# herdr-setup. None of this exists yet (cmd_onboard is a phase 1 stub);
# expect failure until P9.T2.S2 replaces it.
#
# hs_detect_agents itself is tested directly and thoroughly by
# tests/test_onboard_detect.sh. This file tests the six things only the
# entrypoint can get wrong:
#
#   1. onboard gates on hs_require_socket, FIRST, and prints nothing on a
#      refusal -- same contract as apply and feed (acceptance row
#      socket-commands-refuse-under-mismatch).
#   2. A non-interactive run (no --yes, no terminal -- which every test
#      process here is) prints the table and installs nothing, exit 0.
#   3. --yes installs every offered (absent or outdated) agent, one
#      `integration install <target>` call each, and nothing for an agent
#      already current.
#   4. A failed install is reflected in the exit status.
#   5. feed runs exactly once afterwards, scoped to exactly the agents this
#      run installed -- an adapter for an agent that was already current
#      never gets a chance to report anything, proven by giving it a
#      fixture pane it would otherwise happily report.
#   6. Nothing is installed and feed never runs when nothing was offered
#      (every detected agent already current).
#
# The true single-question, real-terminal y/n path (P9.T2.S1's "answering y
# ... answering n") is deliberately NOT exercised end to end here: this file
# and tests/test_apply_config.sh's shrinking-write gate, hs_confirm's only
# two callers, both drive it with no terminal at all, which is a different
# code path from the one a real y/n answer takes. That real-pty branch --
# y/yes/n/no/an empty answer/end-of-input, and a plain pipe fed "yes" still
# declining -- is covered once, directly against hs_confirm itself, by
# tests/test_confirm.py (phase 10), rather than duplicated per caller here.
# cmd_onboard's own contribution is wiring hs_confirm in per offered agent,
# which the no-terminal and --yes paths below already prove it does.
#
# The sandbox trick is the established one (tests/test_feed_entrypoint.sh):
# HS_ROOT follows $0, so a copy of the entrypoint in $work gets its own
# root, its own adapters/, and nothing is written to this checkout.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# Replaced further down, once the socket listener exists, with one trap that
# also stops it -- bash keeps only the LAST trap registered for a signal.

sandbox="$work/sandbox"
mkdir -p "$sandbox/lib" "$sandbox/adapters"
cp "$repo_root/herdr-setup" "$sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$sandbox/lib/hs.py"
cp "$repo_root/lib/feed.py" "$sandbox/lib/feed.py"
chmod +x "$sandbox/herdr-setup"

# --- fake PATH commands and config dirs, same technique as
# tests/test_onboard_detect.sh: claude is on PATH (no config dir), codex and
# opencode each have a config dir (neither on PATH). $fake_bin is put ahead
# of the REST of $PATH below (tests/test_diff_plugins.sh's own convention),
# not in place of it -- feed is reached through uv (AGENTS.md: Python comes
# from uv, not the host), so the rest of PATH must stay reachable. Shadowing
# claude, rather than excluding the real PATH outright, is enough: this test
# only cares whether ITS claude was found. ---

fake_bin="$work/bin"
mkdir -p "$fake_bin"
cat > "$fake_bin/claude" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$fake_bin/claude"

fake_home="$work/home"
mkdir -p "$fake_home/.codex" "$fake_home/.opencode"

# --- herdr integration status: claude absent, codex outdated, opencode
# current. opencode gets an adapter below (see make_adapter) that would
# happily report a pane if fed -- the test that matters is that,
# detected and adapter-equipped as it is, it never gets the chance,
# because it was never OFFERED (state current).

fixtures="$work/fixtures"
mkdir -p "$fixtures"
cat > "$fixtures/integration->status.json" <<'EOF'
claude: not installed (/home/placeholder-user/.claude/hooks/herdr-agent-state.sh)
codex: outdated (v8 < v9) (/home/placeholder-user/.codex/herdr-agent-state.sh)
opencode: current (v8) (/home/placeholder-user/.opencode/hooks/herdr-agent-state.sh)
EOF

# --- one live pane each for claude and codex, in the exact shape
# tests/fixtures/herdr/ was captured in (agent-list's own agent/pane_id/cwd
# keys, process-info's foreground_processes/argv0/foreground_process_group_id
# keys -- lib/feed.py's panes_for reads these by name). Hand-built here,
# not a capture: this file is proving onboard's OWN scoping, already
# covered against real captures by tests/test_feed_entrypoint.sh, so a
# minimal shape in the same key names is enough. Pane ids match
# make_adapter's own below. ---
cat > "$fixtures/agent->list.json" <<'EOF'
{
  "id": "cli:agent:list",
  "result": {
    "type": "agent_list",
    "agents": [
      {"agent": "claude", "pane_id": "w1:p1", "cwd": "/work/claude", "foreground_cwd": "/work/claude"},
      {"agent": "codex", "pane_id": "w2:p2", "cwd": "/work/codex", "foreground_cwd": "/work/codex"}
    ]
  }
}
EOF
cat > "$fixtures/pane->process-info->--pane->w1:p1.json" <<'EOF'
{
  "id": "cli:pane:process_info",
  "result": {
    "type": "pane_process_info",
    "process_info": {
      "pane_id": "w1:p1",
      "shell_pid": 1000,
      "foreground_process_group_id": 1001,
      "foreground_processes": [
        {"pid": 1001, "name": "1.0", "argv0": "claude", "argv": ["claude"], "cmdline": "claude", "cwd": "/work/claude"}
      ]
    }
  }
}
EOF
cat > "$fixtures/pane->process-info->--pane->w2:p2.json" <<'EOF'
{
  "id": "cli:pane:process_info",
  "result": {
    "type": "pane_process_info",
    "process_info": {
      "pane_id": "w2:p2",
      "shell_pid": 2000,
      "foreground_process_group_id": 2001,
      "foreground_processes": [
        {"pid": 2001, "name": "1.0", "argv0": "codex", "argv": ["codex"], "cmdline": "codex", "cwd": "/work/codex"}
      ]
    }
  }
}
EOF

socket="$work/herdr.sock"
missing_socket="$work/no-such-herdr.sock"

# A real listening AF_UNIX socket, not a placeholder file: hs_preflight only
# needs the path to exist (tests 1, 2 and 5 below never get past the
# detection/offer stage, so it is never dialled), but tests 3 and 4 actually
# install and feed, and lib/feed.py's own send() makes a real
# socket.connect() -- a plain file there fails with ENOTSOCK, the same
# failure test_feed_report.py's real AF_UNIX server exists to avoid. `-k`
# keeps it listening for the several sequential reports this file drives.
nc -klU "$socket" >/dev/null 2>&1 &
nc_pid=$!
trap 'kill "$nc_pid" 2>/dev/null; rm -rf "$work"' EXIT
# Give it a moment to bind before anything tries to connect.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ -S "$socket" ] && break
  sleep 0.1
done

# --- adapters: one per offered agent, each logging when its own `probe`
# runs, so a probe log proves (or disproves) that feed's adapters directory
# held exactly the agents onboard actually installed. ---

probe_log="$work/probes.log"

make_adapter() {
  local name="$1" pane_id="$2"
  cat > "$sandbox/adapters/$name" <<ADAPTER
#!/bin/sh
echo "$name" >> "$probe_log"
case "\$1" in
  probe)
    echo '{"agent":"$name","source":"herdr:$name","available":true,"confidence":"exact"}'
    ;;
  resolve)
    cat >/dev/null
    echo '{"results":[{"pane_id":"$pane_id","candidates":[{"session_id":"sess-$name","confidence":"exact"}]}]}'
    ;;
esac
ADAPTER
  chmod +x "$sandbox/adapters/$name"
}

make_adapter claude "w1:p1"
make_adapter codex "w2:p2"
make_adapter opencode "w3:p3"

# =====================================================================
# (1) a mismatched Herdr refuses onboard, exit 3, nothing on stdout
# =====================================================================

out="$work/out_mismatch"; err="$work/err_mismatch"
HERDR_SOCKET_PATH="$socket" FAKE_HERDR_FIXTURES="$fixtures" FAKE_HERDR_PROTOCOL_MISMATCH=1 \
  HOME="$fake_home" PATH="$fake_bin:$test_dir/helpers:$PATH" \
  "$sandbox/herdr-setup" onboard >"$out" 2>"$err" </dev/null
status=$?
assert_status "onboard refuses under a protocol mismatch" 3 "$status"
assert_contains "the refusal names the restart" "$(cat "$err")" "restart"
assert_eq "a refused onboard prints nothing to stdout" "" "$(cat "$out")"

# --- same refusal with no server at all ---

out="$work/out_noserver"; err="$work/err_noserver"
HERDR_SOCKET_PATH="$missing_socket" FAKE_HERDR_FIXTURES="$fixtures" \
  HOME="$fake_home" PATH="$fake_bin:$test_dir/helpers:$PATH" \
  "$sandbox/herdr-setup" onboard >"$out" 2>"$err" </dev/null
status=$?
assert_status "onboard refuses with no Herdr server" 3 "$status"

# =====================================================================
# (2) a non-interactive run (no --yes, no terminal): prints the table,
# installs nothing, exits 0. Every test process's stdin is not a terminal,
# so redirecting from /dev/null just makes that explicit.
# =====================================================================

rm -f "$probe_log"
out="$work/out_noninteractive"; err="$work/err_noninteractive"
HERDR_SOCKET_PATH="$socket" FAKE_HERDR_FIXTURES="$fixtures" \
  HOME="$fake_home" PATH="$fake_bin:$test_dir/helpers:$PATH" \
  "$sandbox/herdr-setup" onboard >"$out" 2>"$err" </dev/null
status=$?
assert_status "a non-interactive onboard with no --yes exits 0" 0 "$status"
assert_contains "the table names claude" "$(cat "$out")" "claude"
assert_contains "the table names codex" "$(cat "$out")" "codex"
assert_contains "codex's outdated row carries both versions" "$(cat "$out")" "v8 < v9"
case "$(cat "$out")" in
  *"integration install"*) fail "a non-interactive run without --yes printed an install line: $(cat "$out")" ;;
  *) pass ;;
esac
if [ -s "$probe_log" ]; then
  fail "no agent was installed, but feed's adapters ran a probe anyway: $(cat "$probe_log")"
else
  pass
fi

# =====================================================================
# (3) --yes installs every offered agent, one call each, and feeds exactly
# them -- opencode's own adapter probe must never run, even though it is
# detected, adapter-equipped, and sitting in the same directory as the
# other two: it was already current and so never offered or installed.
# =====================================================================

log="$work/herdr.log"
rm -f "$log" "$probe_log"
out="$work/out_yes"; err="$work/err_yes"
HERDR_SOCKET_PATH="$socket" FAKE_HERDR_FIXTURES="$fixtures" FAKE_HERDR_LOG="$log" \
  HOME="$fake_home" PATH="$fake_bin:$test_dir/helpers:$PATH" \
  "$sandbox/herdr-setup" --yes onboard >"$out" 2>"$err" </dev/null
status=$?
assert_status "onboard --yes exits 0 when every install succeeds" 0 "$status"
assert_eq "claude is installed exactly once" "1" \
  "$(grep -c '^integration install claude$' "$log")"
assert_eq "codex is installed exactly once" "1" \
  "$(grep -c '^integration install codex$' "$log")"
assert_eq "opencode is never installed (it was current, never offered)" "0" \
  "$(grep -c '^integration install opencode$' "$log")"

# --- feed ran, scoped to claude and codex: both probes fired, and both
# panes got reported. opencode's adapter -- present, working, sitting right
# there in the sandbox -- never ran at all. ---
assert_contains "claude's adapter was probed by the feed hand-off" "$(cat "$probe_log")" "claude"
assert_contains "codex's adapter was probed by the feed hand-off" "$(cat "$probe_log")" "codex"
assert_contains "claude's pane was reported" "$(cat "$out")" "sess-claude"
assert_contains "codex's pane was reported" "$(cat "$out")" "sess-codex"
case "$(cat "$probe_log")" in
  *opencode*) fail "opencode was never installed, but feed probed its adapter anyway: $(cat "$probe_log")" ;;
  *) pass ;;
esac
case "$(cat "$out")" in
  *sess-opencode*) fail "opencode's session was reported despite never being fed: $(cat "$out")" ;;
  *) pass ;;
esac

# =====================================================================
# (4) a failed install is reflected in the exit status, and the OTHER
# offered agent is still tried -- one bad apple does not stop the loop.
# =====================================================================

rm -f "$log" "$probe_log"
out="$work/out_fail"; err="$work/err_fail"
HERDR_SOCKET_PATH="$socket" FAKE_HERDR_FIXTURES="$fixtures" FAKE_HERDR_LOG="$log" \
  FAKE_HERDR_FAIL="integration install claude" \
  HOME="$fake_home" PATH="$fake_bin:$test_dir/helpers:$PATH" \
  "$sandbox/herdr-setup" --yes onboard >"$out" 2>"$err" </dev/null
status=$?
assert_status "a failed install makes onboard exit non-zero" 1 "$status"
assert_eq "the failing agent's install was still attempted" "1" \
  "$(grep -c '^integration install claude$' "$log")"
assert_eq "the other offered agent's install still ran" "1" \
  "$(grep -c '^integration install codex$' "$log")"
# Only codex actually succeeded, so only codex should have been fed.
assert_contains "codex was still fed despite claude's failed install" "$(cat "$probe_log")" "codex"
case "$(cat "$probe_log")" in
  *claude*) fail "claude's failed install still triggered a feed probe: $(cat "$probe_log")" ;;
  *) pass ;;
esac

# =====================================================================
# (5) nothing offered (every detected agent already current): no install,
# no feed call at all, exit 0.
# =====================================================================

all_current="$work/all_current"
mkdir -p "$all_current"
cat > "$all_current/integration->status.json" <<'EOF'
claude: current (v9) (/home/placeholder-user/.claude/hooks/herdr-agent-state.sh)
EOF

rm -f "$log" "$probe_log"
out="$work/out_allcurrent"; err="$work/err_allcurrent"
HERDR_SOCKET_PATH="$socket" FAKE_HERDR_FIXTURES="$all_current" FAKE_HERDR_LOG="$log" \
  HOME="$fake_home" PATH="$fake_bin:$test_dir/helpers:$PATH" \
  "$sandbox/herdr-setup" --yes onboard >"$out" 2>"$err" </dev/null
status=$?
assert_status "nothing offered exits 0" 0 "$status"
assert_contains "the table still names claude" "$(cat "$out")" "claude"
# The log still carries the preflight probe (`plugin list`) and the
# detection call (`integration status`) -- neither is what this asserts.
# What must NOT be there is an install.
assert_eq "nothing offered means no install call at all" "0" \
  "$(grep -c '^integration install' "$log")"
if [ -s "$probe_log" ]; then
  fail "nothing was installed, but feed ran an adapter probe anyway: $(cat "$probe_log")"
else
  pass
fi

hs_test_report
