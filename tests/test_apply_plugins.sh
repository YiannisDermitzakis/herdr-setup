#!/usr/bin/env bash
# shellcheck disable=SC2015
# this suite's idiom throughout: pass()/fail()
# (tests/helpers/assert.sh) never return nonzero, so "A && pass || fail C" cannot
# silently take the wrong branch.
# Tests for the plugin-installation half of `apply`: hs_apply_plugins in
# lib/common.sh, and its wiring into cmd_apply (herdr-setup) alongside the
# hs_require_socket preflight gate. Neither exists yet; expect failure
# until P4.T1.S2 writes them.
#
# hs_apply_plugins relies on hs_resolve_ref (git ls-remote) exactly like
# hs_diff_plugins does, so this file carries the same fake-git-on-PATH
# technique as tests/test_diff_plugins.sh -- local to this file only.
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

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# --- fake git: same technique as tests/test_diff_plugins.sh ---

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

fixtures="$work/fixtures"
mkdir -p "$fixtures"

git_fixture_name() {
  printf '%s' "$1" | tr '/:' '__'
  printf -- '->%s.sha' "$2"
}

sha_current="cccccccccccccccccccccccccccccccccccccccc"
sha_new="dddddddddddddddddddddddddddddddddddddddd"
echo "$sha_current" > "$fixtures/$(git_fixture_name 'https://github.com/kryptamine/herdr-auto-title.git' 'v0.3.3')"
echo "$sha_new"     > "$fixtures/$(git_fixture_name 'https://github.com/ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay.git' 'main')"
# some-org/missing-plugin is absent from the host entirely -- no fixture
# needed, hs_apply_plugins must not even need to resolve its ref.

# --- fixtures shared by every hs_apply_plugins scenario below: one plugin
# already matching (ok, no call), one missing from the host (install),
# one whose ref moved (install). ---

manifest="$work/plugins.list"
cat > "$manifest" <<EOF
kryptamine/herdr-auto-title   v0.3.3
some-org/missing-plugin       v1.0.0
ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay   main
EOF

plugins_json="$work/plugins.json"
cat > "$plugins_json" <<'JSONEOF'
[
  {
    "plugin_id": "kryptamine.auto-title",
    "source": {
      "kind": "github", "owner": "kryptamine", "repo": "herdr-auto-title",
      "requested_ref": "v0.3.3",
      "resolved_commit": "cccccccccccccccccccccccccccccccccccccccc",
      "managed_path": "/placeholder/plugins/kryptamine.auto-title"
    }
  },
  {
    "plugin_id": "ez-corp.space-usage",
    "source": {
      "kind": "github", "owner": "ezcorp-org", "repo": "herdr-pc-ram-and-cpu-usage-overlay",
      "requested_ref": "main",
      "resolved_commit": "94a2ea3bf21ec35c6da51b9657c97167e68034ce",
      "managed_path": "/placeholder/plugins/ez-corp.space-usage"
    }
  }
]
JSONEOF

if ! command -v hs_apply_plugins >/dev/null 2>&1; then
  fail "hs_apply_plugins is not defined yet"
  hs_test_report
fi

log="$work/herdr.log"

# --- a normal run: ok plugin makes no call, missing and moved plugins
# each make exactly one plugin-install call, no --yes since HS_YES=0 ---

rm -f "$log"
HS_DRY_RUN=0 HS_YES=0 FAKE_HERDR_LOG="$log" FAKE_GIT_FIXTURES="$fixtures" \
  hs_apply_plugins "$manifest" "$plugins_json" >/dev/null
[ -f "$log" ] || : > "$log"

assert_eq "exactly two install calls (missing + moved), none for ok" \
  "2" "$(grep -c '^plugin install ' "$log")"
assert_eq "install call for the missing plugin" "1" \
  "$(grep -c '^plugin install some-org/missing-plugin --ref v1.0.0$' "$log")"
assert_eq "install call for the moved plugin" "1" \
  "$(grep -c '^plugin install ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay --ref main$' "$log")"
assert_eq "no call at all for the plugin that already matches" "0" \
  "$(grep -c '^plugin install kryptamine/herdr-auto-title' "$log")"

# --- HS_YES=1 appends --yes to each install call ---

rm -f "$log"
HS_DRY_RUN=0 HS_YES=1 FAKE_HERDR_LOG="$log" FAKE_GIT_FIXTURES="$fixtures" \
  hs_apply_plugins "$manifest" "$plugins_json" >/dev/null
assert_eq "both install calls carry --yes when HS_YES=1" "2" \
  "$(grep -c -- '--yes$' "$log")"

# --- HS_DRY_RUN=1: no calls at all, but the commands are printed ---

rm -f "$log"
out="$(HS_DRY_RUN=1 HS_YES=0 FAKE_HERDR_LOG="$log" FAKE_GIT_FIXTURES="$fixtures" \
  hs_apply_plugins "$manifest" "$plugins_json")"
[ ! -f "$log" ] && pass || fail "hs_apply_plugins under --dry-run made a real herdr call: $(cat "$log" 2>/dev/null)"
assert_contains "dry-run prints the missing-plugin install it would run" "$out" \
  "plugin install some-org/missing-plugin --ref v1.0.0"
assert_contains "dry-run prints the moved-plugin install it would run" "$out" \
  "plugin install ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay --ref main"

# =====================================================================
# Herdr's own trust preview, on the DEFAULT path (no --yes).
#
# Herdr shows a preview of what a plugin will be allowed to do and waits
# for an answer. Run through hs_herdr_json, that whole exchange went into a
# command substitution: the operator got a silent, apparently hung command
# while herdr sat blocked on a read behind a prompt they never saw. The fake
# herdr could not express this at all -- it never prompted and never read --
# so a test suite of thirteen files was green over a default path that
# stopped dead at a real terminal. FAKE_HERDR_PROMPT=1 is that missing mode.
# =====================================================================

rm -f "$log"
out="$work/prompt.out"; err="$work/prompt.err"
HS_DRY_RUN=0 HS_YES=0 FAKE_HERDR_LOG="$log" FAKE_GIT_FIXTURES="$fixtures" \
  FAKE_HERDR_PROMPT=1 hs_apply_plugins "$manifest" "$plugins_json" \
  >"$out" 2>"$err" <<'ANSWERS'
y
y
ANSWERS
status=$?
assert_status "an interactive install run exits 0 when the operator accepts" 0 "$status"
assert_contains "Herdr's trust preview reaches the operator's own stdout" \
  "$(cat "$out")" "TRUST PREVIEW"
assert_contains "the preview names the plugin being installed" "$(cat "$out")" \
  "some-org/missing-plugin"
assert_eq "the operator's answer reached herdr, once per install" "2" \
  "$(grep -c '^prompt-answer y$' "$log")"

# --- declining is a failure, not a silent success ---

rm -f "$log"
HS_DRY_RUN=0 HS_YES=0 FAKE_HERDR_LOG="$log" FAKE_GIT_FIXTURES="$fixtures" \
  FAKE_HERDR_PROMPT=1 hs_apply_plugins "$manifest" "$plugins_json" \
  >/dev/null 2>/dev/null <<'ANSWERS'
n
n
ANSWERS
status=$?
assert_status "a declined install is reported as a failure" 1 "$status"

# --- --yes means no preview and no question, which is what it is for ---

rm -f "$log"
HS_DRY_RUN=0 HS_YES=1 FAKE_HERDR_LOG="$log" FAKE_GIT_FIXTURES="$fixtures" \
  FAKE_HERDR_PROMPT=1 hs_apply_plugins "$manifest" "$plugins_json" \
  >"$work/yes.out" 2>/dev/null </dev/null
status=$?
assert_status "an install with --yes exits 0 with nothing to answer" 0 "$status"
assert_eq "--yes asks nothing" "0" "$(grep -c '^prompt-answer' "$log")"
case "$(cat "$work/yes.out")" in
  *"TRUST PREVIEW"*) fail "--yes still printed a trust preview" ;;
  *) pass ;;
esac

# --- a failed install. The fake exited 0 for every `plugin install` no
# matter what, so hs_apply_plugins' own failure branch was unreachable from
# a test and the "returns 1 if any install call itself failed" half of its
# contract was never checked. ---

rm -f "$log"
err="$work/install_fail.err"
HS_DRY_RUN=0 HS_YES=1 FAKE_HERDR_LOG="$log" FAKE_GIT_FIXTURES="$fixtures" \
  FAKE_HERDR_FAIL="plugin install" hs_apply_plugins "$manifest" "$plugins_json" \
  >/dev/null 2>"$err"
status=$?
assert_status "a failed install makes hs_apply_plugins return 1" 1 "$status"
assert_eq "it still attempts every drifted plugin rather than stopping at the first" \
  "2" "$(grep -c '^plugin install ' "$log")"
assert_contains "the failure is reported, not swallowed" "$(cat "$err")" "plugin install"

# =====================================================================
# cmd_apply wiring: the real entrypoint, from a sandbox copy (never this
# checkout -- same technique as tests/test_diff_plugins.sh). Proves
# hs_require_socket gates the whole command: a matched host runs the
# plugin section for real, a mismatched host stops with exit 3 and makes
# no herdr call at all.
# =====================================================================

sandbox="$work/sandbox"
mkdir -p "$sandbox/lib" "$sandbox/manifest"
cp "$repo_root/herdr-setup" "$sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$sandbox/lib/hs.py"
chmod +x "$sandbox/herdr-setup"
cp "$manifest" "$sandbox/manifest/plugins.list"
: > "$sandbox/manifest/config.toml"

config_ok="$work/config_ok"
mkdir -p "$config_ok"
cp "$plugins_json" "$config_ok/plugins.json"
: > "$config_ok/config.toml"

present_socket="$work/herdr.sock"
: > "$present_socket"

rm -f "$log"
out="$work/cli_apply_out"; err="$work/cli_apply_err"
FAKE_GIT_FIXTURES="$fixtures" FAKE_HERDR_LOG="$log" HERDR_CONFIG_DIR="$config_ok" \
  HERDR_SOCKET_PATH="$present_socket" "$sandbox/herdr-setup" apply >"$out" 2>"$err"
status=$?
assert_status "herdr-setup apply exits 0 on a successful converge" 0 "$status"
assert_eq "herdr-setup apply installs the two drifted plugins" "2" \
  "$(grep -c '^plugin install ' "$log")"

rm -f "$log"
out="$work/cli_mismatch_out"; err="$work/cli_mismatch_err"
FAKE_GIT_FIXTURES="$fixtures" FAKE_HERDR_LOG="$log" HERDR_CONFIG_DIR="$config_ok" \
  HERDR_SOCKET_PATH="$present_socket" FAKE_HERDR_PROTOCOL_MISMATCH=1 \
  "$sandbox/herdr-setup" apply >"$out" 2>"$err"
status=$?
assert_status "herdr-setup apply exits 3 under a protocol mismatch" 3 "$status"
# hs_require_socket's own preflight probe calls `herdr plugin list` to
# detect the mismatch, so the log is not necessarily empty -- what matters
# is that apply itself never gets to install anything.
[ -f "$log" ] || : > "$log"
assert_eq "herdr-setup apply makes no install call under a protocol mismatch" "0" \
  "$(grep -c '^plugin install ' "$log")"
assert_contains "herdr-setup apply names the mismatch on stderr" "$(cat "$err")" "newer"

# --- the whole entrypoint, on the default path, against a herdr that
# prompts: this is the exact shape the operator runs, and the one that used
# to stop dead with an empty terminal. ---

rm -f "$log"
out="$work/cli_prompt_out"; err="$work/cli_prompt_err"
FAKE_GIT_FIXTURES="$fixtures" FAKE_HERDR_LOG="$log" HERDR_CONFIG_DIR="$config_ok" \
  HERDR_SOCKET_PATH="$present_socket" FAKE_HERDR_PROMPT=1 \
  "$sandbox/herdr-setup" apply >"$out" 2>"$err" <<'ANSWERS'
y
y
ANSWERS
status=$?
assert_status "herdr-setup apply exits 0 when the operator accepts each preview" 0 "$status"
assert_contains "the operator sees the trust preview herdr-setup apply triggered" \
  "$(cat "$out")" "TRUST PREVIEW"
assert_eq "each install was answered by the operator" "2" \
  "$(grep -c '^prompt-answer y$' "$log")"

hs_test_report
