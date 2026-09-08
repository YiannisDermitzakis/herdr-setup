#!/usr/bin/env bash
# The end-to-end round trip the whole tool exists for
# (absorb-apply-roundtrip-is-noop in docs/acceptance/matrix.yaml): absorb a
# host, then apply the freshly-absorbed manifest back against the SAME,
# unchanged host, and prove it is a true no-op -- no plugin install call, no
# config write, no reload-config call -- then that a following diff reports
# a clean match. This exercises phase 4's byte-exact anchor case
# (journal 8f3a0c6f882c) by construction: the manifest absorb just wrote IS
# the host's own stripped config content.
#
# Needs a fake `git` on PATH supporting BOTH `ls-remote` (apply/diff's
# plugin-ref resolution) and `-C <dir> status --porcelain` (absorb's
# dirty-manifest guard) -- local to this file only, same technique as
# tests/test_apply_plugins.sh and tests/test_absorb.sh.
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

fake_git_bin="$work/gitbin"
mkdir -p "$fake_git_bin"
cat > "$fake_git_bin/git" <<'GITEOF'
#!/usr/bin/env bash
set -u
if [ "${1:-}" = "-C" ]; then
  shift 2
fi
case "${1:-}" in
  ls-remote)
    url="$2"
    ref="$3"
    slug="$(printf '%s' "$url" | tr '/:' '__')"
    fixture="$FAKE_GIT_FIXTURES/${slug}->${ref}.sha"
    if [ -f "$fixture" ]; then
      printf '%s\trefs/heads/%s\n' "$(cat "$fixture")" "$ref"
    fi
    exit 0
    ;;
  status)
    printf '%s' "${FAKE_GIT_STATUS_OUTPUT:-}"
    exit 0
    ;;
  *)
    echo "fake-git: unsupported invocation: $*" >&2
    exit 2
    ;;
esac
GITEOF
chmod +x "$fake_git_bin/git"
export PATH="$fake_git_bin:$PATH"
export FAKE_GIT_STATUS_OUTPUT=""

git_fixture_name() {
  printf '%s' "$1" | tr '/:' '__'
  printf -- '->%s.sha' "$2"
}

fixtures="$work/fixtures"
mkdir -p "$fixtures"
sha="cccccccccccccccccccccccccccccccccccccccc"
echo "$sha" > "$fixtures/$(git_fixture_name 'https://github.com/kryptamine/herdr-auto-title.git' 'v0.3.3')"

# --- the fake host: one plugin already at the ref/commit absorb will
# record, and a config with one plugin-written block plus operator lines ---

host_dir="$work/host"
mkdir -p "$host_dir"
cat > "$host_dir/plugins.json" <<JSONEOF
[
  {
    "plugin_id": "kryptamine.auto-title",
    "source": {
      "kind": "github", "owner": "kryptamine", "repo": "herdr-auto-title",
      "requested_ref": "v0.3.3",
      "resolved_commit": "$sha",
      "managed_path": "/home/placeholder-user/.config/herdr/plugins/kryptamine.auto-title"
    }
  }
]
JSONEOF
cat > "$host_dir/config.toml" <<'EOF'
[ui]
agent_panel_sort = "priority"

# --- added by kryptamine.auto-title ---
[auto_title]
enabled = true
# --- end kryptamine.auto-title ---

[misc]
foo = "bar"
EOF

sandbox="$work/sandbox"
mkdir -p "$sandbox/lib"
cp "$repo_root/herdr-setup" "$sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$sandbox/lib/hs.py"
chmod +x "$sandbox/herdr-setup"

present_socket="$work/herdr.sock"
: > "$present_socket"

host_plugins_before="$(cat "$host_dir/plugins.json")"
host_config_before="$(cat "$host_dir/config.toml")"

# --- step 1: absorb ---

out1="$work/absorb.out"; err1="$work/absorb.err"
HERDR_CONFIG_DIR="$host_dir" "$sandbox/herdr-setup" absorb >"$out1" 2>"$err1"
absorb_status=$?
assert_status "the first absorb exits 0" 0 "$absorb_status"
[ -f "$sandbox/manifest/plugins.list" ] && pass || fail "absorb did not write manifest/plugins.list"
[ -f "$sandbox/manifest/config.toml" ] && pass || fail "absorb did not write manifest/config.toml"

# --- step 2 + 3: apply against the SAME, unchanged host, then diff again --
# one continuous herdr log across both, so "no install call" covers the
# whole round trip, matching the acceptance wording.

log="$work/herdr.log"
rm -f "$log"

out2="$work/apply.out"; err2="$work/apply.err"
FAKE_GIT_FIXTURES="$fixtures" FAKE_HERDR_LOG="$log" HERDR_CONFIG_DIR="$host_dir" \
  HERDR_SOCKET_PATH="$present_socket" "$sandbox/herdr-setup" apply >"$out2" 2>"$err2"
apply_status=$?

out3="$work/diff.out"; err3="$work/diff.err"
FAKE_GIT_FIXTURES="$fixtures" FAKE_HERDR_LOG="$log" HERDR_CONFIG_DIR="$host_dir" \
  "$sandbox/herdr-setup" diff >"$out3" 2>"$err3"
diff_status=$?

assert_status "apply against the unchanged host exits 0" 0 "$apply_status"
assert_status "the second diff exits 0 (no drift)" 0 "$diff_status"

[ -f "$log" ] || : > "$log"
assert_eq "the round trip makes no plugin install call" "0" "$(grep -c '^plugin install ' "$log")"
assert_eq "the round trip makes no reload-config call" "0" "$(grep -c '^server reload-config$' "$log")"

assert_eq "the host's plugins.json is unchanged by the no-op apply" \
  "$host_plugins_before" "$(cat "$host_dir/plugins.json")"
assert_eq "the host's config.toml is unchanged by the no-op apply" \
  "$host_config_before" "$(cat "$host_dir/config.toml")"

assert_contains "the second diff reports the plugin as ok" "$(cat "$out3")" \
  "plugin ok: kryptamine/herdr-auto-title"
assert_contains "the second diff reports config match" "$(cat "$out3")" "config match"

hs_test_report
