#!/usr/bin/env bash
# Tests for hs_detect_agents (lib/common.sh) -- the table `onboard` (phase 9)
# prints before it offers anything. None of this exists yet; expect failure
# until P9.T1.S2 writes hs_detect_agents.
#
# hs_detect_agents drives its target LIST from `herdr integration status`
# itself (never a hard-coded list of Herdr's agents), and a small local
# table (hs_agent_detect_spec) supplies only the command name and
# configuration directory used to decide `how` an agent was detected. An
# agent `herdr integration status` names that our table has never heard of
# is left out entirely -- same as an agent our table knows but this host
# shows no sign of.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
. "$test_dir/helpers/assert.sh"

if [ ! -f "$repo_root/lib/common.sh" ]; then
  fail "lib/common.sh does not exist yet"
  hs_test_report
fi
. "$repo_root/lib/common.sh"

if ! command -v hs_detect_agents >/dev/null 2>&1; then
  fail "hs_detect_agents is not defined yet"
  hs_test_report
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# --- fake PATH commands: claude, opencode and hermes are "installed";
# codex, cursor and copilot are not. This is deliberately not the same set
# as the config-directory fixtures below, so `how` can distinguish path,
# config and both. ---

fake_bin="$work/bin"
mkdir -p "$fake_bin"
for cmd in claude opencode hermes; do
  cat > "$fake_bin/$cmd" <<'EOF'
#!/bin/sh
exit 0
EOF
  chmod +x "$fake_bin/$cmd"
done

# --- fake $HOME: codex, opencode and cursor have a configuration
# directory present; claude, hermes and copilot do not. ---

fake_home="$work/home"
mkdir -p "$fake_home/.codex" "$fake_home/.opencode" "$fake_home/.cursor"

# --- herdr integration status fixture. Six known targets plus one
# ("pi") this tool's table has no entry for at all, to prove an unknown
# target is left out regardless of state or path. ---

fixtures="$work/fixtures"
mkdir -p "$fixtures"
cat > "$fixtures/integration->status.json" <<'EOF'
claude: outdated (v8 < v9) (/home/placeholder-user/.claude/hooks/herdr-agent-state.sh)
codex: not installed (/home/placeholder-user/.codex/herdr-agent-state.sh)
opencode: current (v8) (/home/placeholder-user/.opencode/hooks/herdr-agent-state.sh)
copilot: not installed (/home/placeholder-user/.copilot/herdr-agent-state.sh)
cursor: outdated (v3 < v4) (/home/placeholder-user/.cursor/herdr-agent-state.sh)
hermes: current (v2) (/home/placeholder-user/.hermes/herdr-agent-state.sh)
pi: current (v8) (/home/placeholder-user/.pi/agent/extensions/herdr-agent-state.ts)
EOF

out="$(PATH="$fake_bin:$test_dir/helpers:/usr/bin:/bin" HOME="$fake_home" \
  FAKE_HERDR_FIXTURES="$fixtures" hs_detect_agents)"
status=$?

assert_status "hs_detect_agents returns 0" 0 "$status"

# --- claude: on PATH, no config dir -> path; outdated; detail carries
# both versions ---
claude_line="$(printf '%s\n' "$out" | awk -F'\t' '$1 == "claude"')"
assert_contains "claude is reported" "$claude_line" "claude"
assert_eq "claude how=path" "path" "$(printf '%s' "$claude_line" | cut -f2)"
assert_eq "claude state=outdated" "outdated" "$(printf '%s' "$claude_line" | cut -f3)"
assert_contains "claude detail carries both versions" "$claude_line" "v8 < v9"

# --- codex: config dir only, not on PATH -> config; absent; empty detail ---
codex_line="$(printf '%s\n' "$out" | awk -F'\t' '$1 == "codex"')"
assert_contains "codex is reported" "$codex_line" "codex"
assert_eq "codex how=config" "config" "$(printf '%s' "$codex_line" | cut -f2)"
assert_eq "codex state=absent" "absent" "$(printf '%s' "$codex_line" | cut -f3)"
assert_eq "codex detail is empty" "" "$(printf '%s' "$codex_line" | cut -f4)"

# --- opencode: on PATH AND config dir present -> both; current ---
opencode_line="$(printf '%s\n' "$out" | awk -F'\t' '$1 == "opencode"')"
assert_contains "opencode is reported" "$opencode_line" "opencode"
assert_eq "opencode how=both" "both" "$(printf '%s' "$opencode_line" | cut -f2)"
assert_eq "opencode state=current" "current" "$(printf '%s' "$opencode_line" | cut -f3)"
assert_contains "opencode detail carries its version" "$opencode_line" "v8"

# --- copilot: neither on PATH nor config dir present -> omitted entirely,
# even though herdr integration status named it ---
case "$out" in
  *copilot*) fail "hs_detect_agents reports copilot, which is neither on PATH nor has a config dir: $out" ;;
  *) pass ;;
esac

# --- cursor: config dir only -> config; outdated; both versions ---
cursor_line="$(printf '%s\n' "$out" | awk -F'\t' '$1 == "cursor"')"
assert_eq "cursor how=config" "config" "$(printf '%s' "$cursor_line" | cut -f2)"
assert_eq "cursor state=outdated" "outdated" "$(printf '%s' "$cursor_line" | cut -f3)"
assert_contains "cursor detail carries both versions" "$cursor_line" "v3 < v4"

# --- hermes: on PATH only -> path; current ---
hermes_line="$(printf '%s\n' "$out" | awk -F'\t' '$1 == "hermes"')"
assert_eq "hermes how=path" "path" "$(printf '%s' "$hermes_line" | cut -f2)"
assert_eq "hermes state=current" "current" "$(printf '%s' "$hermes_line" | cut -f3)"

# --- pi: not in our detection table at all -> omitted, regardless of
# herdr reporting it as current and regardless of PATH/config -- proves the
# target LIST comes from herdr integration status, and the local table only
# supplies command/dir for the agents it actually knows ---
case "$out" in
  *$'\n'pi*|pi*) fail "hs_detect_agents reports 'pi', which is not in its detection table: $out" ;;
  *) pass ;;
esac

# --- exactly five lines: claude, codex, opencode, cursor, hermes ---
line_count="$(printf '%s\n' "$out" | grep -c .)"
assert_eq "exactly five agents are reported" "5" "$line_count"

hs_test_report
