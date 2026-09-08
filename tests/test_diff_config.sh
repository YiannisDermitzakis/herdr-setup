#!/usr/bin/env bash
# Tests for the config and integration sections of `diff`: hs_diff_config
# and hs_diff_integrations in lib/common.sh, hs_preflight_banner, and their
# wiring into cmd_diff (herdr-setup). None of these exist yet; expect
# failure until P3.T2.S2 writes them.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
. "$test_dir/helpers/assert.sh"

if [ ! -f "$repo_root/lib/common.sh" ]; then
  fail "lib/common.sh does not exist yet"
  hs_test_report
fi
. "$repo_root/lib/common.sh"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# =====================================================================
# hs_diff_config
# =====================================================================

if ! command -v hs_diff_config >/dev/null 2>&1; then
  fail "hs_diff_config is not defined yet"
  hs_test_report
fi

manifest_config="$work/manifest_config.toml"
cat > "$manifest_config" <<'EOF'
[ui]
agent_panel_sort = "priority"

[misc]
foo = "bar"
EOF

# --- host config, with a plugin block, that matches the manifest once
# the block is stripped ---

host_config_match="$work/host_config_match.toml"
cat > "$host_config_match" <<'EOF'
[ui]
agent_panel_sort = "priority"

# --- added by ez-corp.space-usage ---
[ui.sidebar.spaces]
rows = [ ["state_icon", "workspace"], ["$usage"] ]
# --- end ez-corp.space-usage ---
[misc]
foo = "bar"
EOF

out="$(hs_diff_config "$host_config_match" "$manifest_config")"
status=$?
assert_status "hs_diff_config exits 0 when the stripped host matches the manifest" 0 "$status"
assert_eq "hs_diff_config reports a match" "config match" "$out"

# --- host config that genuinely differs from the manifest, even with
# blocks stripped: a unified diff, labelled host and manifest ---

host_config_drift="$work/host_config_drift.toml"
cat > "$host_config_drift" <<'EOF'
[ui]
agent_panel_sort = "recency"

[misc]
foo = "bar"
EOF

out="$(hs_diff_config "$host_config_drift" "$manifest_config")"
status=$?
assert_status "hs_diff_config exits 1 when the host drifts from the manifest" 1 "$status"
assert_contains "hs_diff_config announces drift" "$out" "config drift:"
assert_contains "hs_diff_config's diff is labelled host" "$out" "host"
assert_contains "hs_diff_config's diff is labelled manifest" "$out" "manifest"
assert_contains "hs_diff_config's diff shows the host's line" "$out" "recency"
assert_contains "hs_diff_config's diff shows the manifest's line" "$out" "priority"

# --- a host with no config.toml yet is treated as empty, not an error:
# it drifts against any non-empty manifest ---

host_config_missing="$work/does-not-exist.toml"
out="$(hs_diff_config "$host_config_missing" "$manifest_config")"
status=$?
assert_status "hs_diff_config exits 1 when the host has no config.toml yet" 1 "$status"
assert_contains "missing-host diff announces drift" "$out" "config drift:"

# --- a missing or unreadable manifest config is fail-closed: exit 2, one
# line naming the file ---

manifest_missing="$work/no-manifest-config.toml"
err="$work/manifest_missing.err"
out="$(hs_diff_config "$host_config_match" "$manifest_missing" 2>"$err")"
status=$?
assert_status "hs_diff_config exits 2 when the manifest config is missing" 2 "$status"
assert_contains "hs_diff_config names the missing manifest file" "$(cat "$err")" "$manifest_missing"

# =====================================================================
# hs_diff_integrations
# =====================================================================

if ! command -v hs_diff_integrations >/dev/null 2>&1; then
  fail "hs_diff_integrations is not defined yet"
  hs_test_report
fi

fixtures="$work/fixtures"
mkdir -p "$fixtures"
cat > "$fixtures/integration->status.json" <<'EOF'
claude: outdated (v8 < v9) (/path/to/hooks/herdr-agent-state.sh)
opencode: current (v8) (/path/to/hooks/herdr-agent-state.sh)
codex: not installed (/path/to/herdr-agent-state.sh)
EOF

out="$(FAKE_HERDR_FIXTURES="$fixtures" hs_diff_integrations)"
status=$?
assert_status "hs_diff_integrations always returns 0" 0 "$status"
assert_contains "reports the outdated integration" "$out" "claude"
assert_contains "outdated report shows both versions" "$out" "v8 < v9"
assert_contains "reports the current integration" "$out" "opencode"
assert_contains "current report shows its version" "$out" "current (v8)"
case "$out" in
  *'not installed'*) fail "hs_diff_integrations reports a not-installed agent: $out" ;;
  *) pass ;;
esac

# --- same result under a protocol mismatch: `herdr integration status`
# is exempt from the fake herdr's mismatch simulation, matching the real
# command, which does not cross the socket ---

out_mismatch="$(FAKE_HERDR_FIXTURES="$fixtures" FAKE_HERDR_PROTOCOL_MISMATCH=1 hs_diff_integrations)"
status_mismatch=$?
assert_status "hs_diff_integrations still returns 0 under a protocol mismatch" 0 "$status_mismatch"
assert_eq "hs_diff_integrations output is unchanged under a protocol mismatch" "$out" "$out_mismatch"

# =====================================================================
# hs_preflight_banner
# =====================================================================

if ! command -v hs_preflight_banner >/dev/null 2>&1; then
  fail "hs_preflight_banner is not defined yet"
  hs_test_report
fi

present_socket="$work/herdr.sock"
: > "$present_socket"

out="$(HERDR_SOCKET_PATH="$present_socket" hs_preflight_banner)"
status=$?
assert_status "hs_preflight_banner exits 0" 0 "$status"
assert_eq "hs_preflight_banner reports the matched state" "preflight: matched" "$out"

out="$(HERDR_SOCKET_PATH="$present_socket" FAKE_HERDR_PROTOCOL_MISMATCH=1 hs_preflight_banner)"
assert_eq "hs_preflight_banner reports the mismatched state" "preflight: mismatched" "$out"

# =====================================================================
# cmd_diff wiring: the real entrypoint end to end, from a sandbox copy
# (never this checkout -- HS_ROOT follows $0, same technique as
# tests/test_diff_plugins.sh). Proves: hs_preflight_banner is the first
# line; the config section reads $HS_ROOT/manifest/config.toml against
# the host's config.toml (via HERDR_CONFIG_DIR); the integration section
# never moves the exit status; and a hard manifest error still short-
# circuits before the config/integration sections run.
# =====================================================================

# cmd_diff's plugin section still calls hs_resolve_ref, which shells out
# to `git ls-remote`. Put a fake `git` on PATH ahead of any real one, the
# same technique tests/test_diff_plugins.sh uses, so this never reaches
# the real kryptamine/herdr-auto-title repository on GitHub.
fake_git_bin="$work/bin"
mkdir -p "$fake_git_bin"
cat > "$fake_git_bin/git" <<'GITEOF'
#!/usr/bin/env bash
set -u
if [ "${1:-}" != "ls-remote" ]; then
  echo "fake-git: unsupported invocation: $*" >&2
  exit 2
fi
url="$2"
ref="$3"
slug="$(printf '%s' "$url" | tr '/:' '__')"
fixture="$FAKE_GIT_FIXTURES/${slug}->${ref}.sha"
if [ -f "$fixture" ]; then
  printf '%s\trefs/heads/%s\n' "$(cat "$fixture")" "$ref"
fi
exit 0
GITEOF
chmod +x "$fake_git_bin/git"
export PATH="$fake_git_bin:$PATH"

sandbox="$work/sandbox"
mkdir -p "$sandbox/lib" "$sandbox/manifest"
cp "$repo_root/herdr-setup" "$sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$sandbox/lib/hs.py"
chmod +x "$sandbox/herdr-setup"
echo "kryptamine/herdr-auto-title   v0.3.3" > "$sandbox/manifest/plugins.list"
cp "$manifest_config" "$sandbox/manifest/config.toml"

plugin_fixture_json='[
  {
    "plugin_id": "kryptamine.auto-title",
    "source": {
      "kind": "github", "owner": "kryptamine", "repo": "herdr-auto-title",
      "requested_ref": "v0.3.3",
      "resolved_commit": "cccccccccccccccccccccccccccccccccccccccc",
      "managed_path": "/placeholder/plugins/kryptamine.auto-title"
    }
  }
]'

git_fixtures="$work/git_fixtures"
mkdir -p "$git_fixtures"
echo "cccccccccccccccccccccccccccccccccccccccc" \
  > "$git_fixtures/https___github.com_kryptamine_herdr-auto-title.git->v0.3.3.sha"

# --- everything matches: plugins ok, config matches, integration
# outdated (never affects the exit status) -> overall exit 0 ---

config_ok="$work/config_ok"
mkdir -p "$config_ok"
printf '%s' "$plugin_fixture_json" > "$config_ok/plugins.json"
cp "$manifest_config" "$config_ok/config.toml"

out="$work/cli_ok_out"; err="$work/cli_ok_err"
FAKE_GIT_FIXTURES="$git_fixtures" FAKE_HERDR_FIXTURES="$fixtures" \
  HERDR_CONFIG_DIR="$config_ok" HERDR_SOCKET_PATH="$present_socket" \
  "$sandbox/herdr-setup" diff >"$out" 2>"$err"
status=$?
assert_status "herdr-setup diff exits 0 when everything matches" 0 "$status"
assert_eq "herdr-setup diff prints the preflight banner first" \
  "preflight: matched" "$(head -1 "$out")"
assert_contains "herdr-setup diff reports the config as matching" "$(cat "$out")" "config match"
assert_contains "herdr-setup diff still reports the outdated integration" "$(cat "$out")" "claude"

# --- config drifts: overall exit 1, integration section still runs and
# still does not change that ---

config_drift="$work/config_drift"
mkdir -p "$config_drift"
printf '%s' "$plugin_fixture_json" > "$config_drift/plugins.json"
cp "$host_config_drift" "$config_drift/config.toml"

out="$work/cli_drift_out"; err="$work/cli_drift_err"
FAKE_GIT_FIXTURES="$git_fixtures" FAKE_HERDR_FIXTURES="$fixtures" \
  HERDR_CONFIG_DIR="$config_drift" HERDR_SOCKET_PATH="$present_socket" \
  "$sandbox/herdr-setup" diff >"$out" 2>"$err"
status=$?
assert_status "herdr-setup diff exits 1 when the config drifts" 1 "$status"
assert_contains "herdr-setup diff reports config drift" "$(cat "$out")" "config drift:"
assert_contains "herdr-setup diff still reports integrations on a drifting host" "$(cat "$out")" "claude"

# --- under a protocol mismatch: diff still runs every section in full,
# the banner says so up front, and the exit status is unaffected ---

out="$work/cli_mismatch_out"; err="$work/cli_mismatch_err"
FAKE_GIT_FIXTURES="$git_fixtures" FAKE_HERDR_FIXTURES="$fixtures" \
  HERDR_CONFIG_DIR="$config_ok" HERDR_SOCKET_PATH="$present_socket" \
  FAKE_HERDR_PROTOCOL_MISMATCH=1 \
  "$sandbox/herdr-setup" diff >"$out" 2>"$err"
status=$?
assert_status "herdr-setup diff still exits 0 under a protocol mismatch" 0 "$status"
assert_eq "herdr-setup diff banners the mismatch first" \
  "preflight: mismatched" "$(head -1 "$out")"
assert_contains "herdr-setup diff still reports config as matching under mismatch" "$(cat "$out")" "config match"
assert_contains "herdr-setup diff still reports integrations under mismatch" "$(cat "$out")" "claude"

hs_test_report
