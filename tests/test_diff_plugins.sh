#!/usr/bin/env bash
# Tests for hs_resolve_ref and the plugin section of `diff`
# (hs_diff_plugins in lib/common.sh, wired into cmd_diff by herdr-setup).
# None of these exist yet; expect failure until P2.T3.S2 writes them.
#
# hs_resolve_ref shells out to `git ls-remote`; this file puts a fake `git`
# on PATH ahead of any real one, local to this test file only (it is not
# tests/helpers material -- no other phase needs a fake git).
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

# --- fake git: understands `ls-remote <url> <ref>...`, one pattern or
# several. For each pattern it looks up
# $FAKE_GIT_FIXTURES/<url-with-/-and-:-turned-to-_>-><ref>.sha, which holds
# the COMMIT the ref resolves to; if there is no such file it prints nothing
# for that pattern (ref gone).
#
# A sibling `.tagobj` file makes the ref an ANNOTATED TAG, and that is the
# shape that matters: an annotated tag is an object of its own pointing at a
# commit, so real git answers a bare `v0.3.3` with the TAG OBJECT's sha and
# answers `v0.3.3^{}` with the commit. Herdr records the commit. The fake
# could only ever produce one line, so nothing could tell the two apart and
# the sha this tool handed back for an annotated tag was never the one it
# was comparing against. ---

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
shift 2
slug="$(printf '%s' "$url" | tr '/:' '__')"
for pattern in "$@"; do
  base="${pattern%"^{}"}"
  sha_file="$FAKE_GIT_FIXTURES/${slug}->${base}.sha"
  tag_file="$FAKE_GIT_FIXTURES/${slug}->${base}.tagobj"
  [ -f "$sha_file" ] || continue
  if [ "$pattern" = "$base" ]; then
    if [ -f "$tag_file" ]; then
      printf '%s\trefs/tags/%s\n' "$(cat "$tag_file")" "$base"
    else
      printf '%s\trefs/heads/%s\n' "$(cat "$sha_file")" "$base"
    fi
  elif [ -f "$tag_file" ]; then
    printf '%s\trefs/tags/%s^{}\n' "$(cat "$sha_file")" "$base"
  fi
done
exit 0
GITEOF
chmod +x "$fake_git_bin/git"
export PATH="$fake_git_bin:$PATH"

fixtures="$work/fixtures"
mkdir -p "$fixtures"

# git_fixture_name <url> <ref>: mirrors the fake git's own slug construction
# (tr '/:' '__'), so the fixture filename always matches whatever URL
# hs_resolve_ref actually builds -- no hand-computed slug to keep in sync.
git_fixture_name() {
  printf '%s' "$1" | tr '/:' '__'
  printf -- '->%s.sha' "$2"
}

sha_current="cccccccccccccccccccccccccccccccccccccccc"
sha_new="dddddddddddddddddddddddddddddddddddddddd"
echo "$sha_current" > "$fixtures/$(git_fixture_name 'https://github.com/kryptamine/herdr-auto-title.git' 'v0.3.3')"
echo "$sha_new"     > "$fixtures/$(git_fixture_name 'https://github.com/ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay.git' 'main')"
# no fixture at all for the "gone-ref" plugin -> ls-remote prints nothing

if ! command -v hs_resolve_ref >/dev/null 2>&1; then
  fail "hs_resolve_ref is not defined yet"
  hs_test_report
fi

# --- hs_resolve_ref: a ref that resolves prints the full sha, exits 0 ---
out="$(FAKE_GIT_FIXTURES="$fixtures" hs_resolve_ref "kryptamine/herdr-auto-title" "v0.3.3")"
status=$?
assert_status "hs_resolve_ref exits 0 when the ref resolves" 0 "$status"
assert_eq "hs_resolve_ref prints the full sha" "$sha_current" "$out"

# --- hs_resolve_ref: subdir is ignored when building the repo URL ---
out="$(FAKE_GIT_FIXTURES="$fixtures" hs_resolve_ref "kryptamine/herdr-auto-title/tools/subdir" "v0.3.3")"
assert_eq "hs_resolve_ref ignores a subdir when resolving" "$sha_current" "$out"

# --- hs_resolve_ref: a gone ref (empty ls-remote) prints nothing, exits 1 ---
out="$(FAKE_GIT_FIXTURES="$fixtures" hs_resolve_ref "some-org/gone-repo" "v9.9.9")"
status=$?
assert_status "hs_resolve_ref exits 1 when the ref is gone" 1 "$status"
assert_eq "hs_resolve_ref prints nothing when the ref is gone" "" "$out"

# --- hs_resolve_ref: an ANNOTATED tag resolves to the COMMIT, not to the
# tag object. ls-remote answers with two lines for one; taking the first
# handed back the tag object's sha, which can never equal the commit Herdr
# recorded, so a plugin pinned to an annotated tag was reported moved on
# every diff and reinstalled on every apply, for ever, without converging.
# The design doc's own example manifest pins v0.3.3. ---

sha_tag_commit="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
sha_tag_object="ffffffffffffffffffffffffffffffffffffffff"
annotated_repo='https://github.com/some-org/annotated-plugin.git'
echo "$sha_tag_commit" > "$fixtures/$(git_fixture_name "$annotated_repo" 'v1.2.3')"
echo "$sha_tag_object" > "$fixtures/$(git_fixture_name "$annotated_repo" 'v1.2.3' | sed 's/\.sha$/.tagobj/')"

out="$(FAKE_GIT_FIXTURES="$fixtures" hs_resolve_ref "some-org/annotated-plugin" "v1.2.3")"
status=$?
assert_status "hs_resolve_ref exits 0 on an annotated tag" 0 "$status"
assert_eq "hs_resolve_ref returns the commit an annotated tag points at" "$sha_tag_commit" "$out"
assert_eq "hs_resolve_ref does not return the tag object's own sha" "" \
  "$(printf '%s' "$out" | grep -F "$sha_tag_object" || true)"

# --- and a lightweight tag or branch, which has only the one line, is
# unaffected ---
out="$(FAKE_GIT_FIXTURES="$fixtures" hs_resolve_ref "ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay" "main")"
assert_eq "a ref with no peeled line still resolves to its only sha" "$sha_new" "$out"

if ! command -v hs_diff_plugins >/dev/null 2>&1; then
  fail "hs_diff_plugins is not defined yet"
  hs_test_report
fi

# --- hs_diff_plugins: one manifest entry ok, one missing from the host, one
# moved (ref still resolves but to a different sha than recorded), one
# moved because its ref is gone; plus one host plugin the manifest doesn't
# name, reported unmanaged. Exit 1 because something drifted. ---

manifest="$work/plugins.list"
cat > "$manifest" <<EOF
kryptamine/herdr-auto-title   v0.3.3
some-org/missing-plugin       v1.0.0
ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay   main
some-org/gone-repo            v9.9.9
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
  },
  {
    "plugin_id": "some-org.gone-repo",
    "source": {
      "kind": "github", "owner": "some-org", "repo": "gone-repo",
      "requested_ref": "v9.9.9",
      "resolved_commit": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
      "managed_path": "/placeholder/plugins/some-org.gone-repo"
    }
  },
  {
    "plugin_id": "unmanaged.plugin",
    "source": {
      "kind": "github", "owner": "unmanaged-org", "repo": "unmanaged-repo",
      "requested_ref": "main",
      "resolved_commit": "ffffffffffffffffffffffffffffffffffffffff",
      "managed_path": "/placeholder/plugins/unmanaged.plugin"
    }
  }
]
JSONEOF

out="$(FAKE_GIT_FIXTURES="$fixtures" hs_diff_plugins "$manifest" "$plugins_json")"
status=$?
assert_status "hs_diff_plugins exits 1 when anything is missing or moved" 1 "$status"

assert_contains "reports the exact-match plugin as ok" "$out" "ok: kryptamine/herdr-auto-title"
assert_contains "reports the absent-from-host plugin as missing" "$out" "missing: some-org/missing-plugin"
assert_contains "reports the ref-moved plugin as moved" "$out" "moved: ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay"
assert_contains "moved output names the recorded short sha" "$out" "94a2ea3"
assert_contains "moved output names the newly-resolved short sha" "$out" "dddddd"
assert_contains "reports the gone-ref plugin as moved" "$out" "moved: some-org/gone-repo"
assert_contains "reports the host-only plugin as unmanaged" "$out" "unmanaged: unmanaged-org/unmanaged-repo"

# --- same run under FAKE_HERDR_PROTOCOL_MISMATCH=1: identical result, since
# neither hs_manifest_plugins nor hs_host_plugins nor hs_resolve_ref ever
# calls herdr ---
out_mismatch="$(FAKE_HERDR_PROTOCOL_MISMATCH=1 FAKE_GIT_FIXTURES="$fixtures" hs_diff_plugins "$manifest" "$plugins_json")"
status_mismatch=$?
assert_status "hs_diff_plugins exits 1 under a protocol mismatch too" 1 "$status_mismatch"
assert_eq "hs_diff_plugins output is unchanged under a protocol mismatch" "$out" "$out_mismatch"

# --- an all-clean manifest against a matching host exits 0 ---

clean_manifest="$work/clean.list"
echo "kryptamine/herdr-auto-title   v0.3.3" > "$clean_manifest"

clean_json="$work/clean.json"
cat > "$clean_json" <<'JSONEOF'
[
  {
    "plugin_id": "kryptamine.auto-title",
    "source": {
      "kind": "github", "owner": "kryptamine", "repo": "herdr-auto-title",
      "requested_ref": "v0.3.3",
      "resolved_commit": "cccccccccccccccccccccccccccccccccccccccc",
      "managed_path": "/placeholder/plugins/kryptamine.auto-title"
    }
  }
]
JSONEOF

out_clean="$(FAKE_GIT_FIXTURES="$fixtures" hs_diff_plugins "$clean_manifest" "$clean_json")"
status_clean=$?
assert_status "hs_diff_plugins exits 0 when everything matches" 0 "$status_clean"
assert_contains "clean run reports ok" "$out_clean" "ok: kryptamine/herdr-auto-title"

# --- a plugin pinned to an annotated tag, sitting at exactly the commit
# that tag points at, is `ok`. Reported `moved`, it would be reinstalled by
# every apply and never converge, and diff would never return 0. ---

annotated_manifest="$work/annotated.list"
printf 'some-org/annotated-plugin v1.2.3\n' > "$annotated_manifest"
annotated_json="$work/annotated_plugins.json"
cat > "$annotated_json" <<JSONEOF
[
  {
    "plugin_id": "some-org.annotated",
    "source": {
      "kind": "github", "owner": "some-org", "repo": "annotated-plugin",
      "requested_ref": "v1.2.3",
      "resolved_commit": "$sha_tag_commit",
      "managed_path": "/placeholder/plugins/some-org.annotated"
    }
  }
]
JSONEOF

out="$(FAKE_GIT_FIXTURES="$fixtures" hs_diff_plugins "$annotated_manifest" "$annotated_json")"
status=$?
assert_status "a plugin pinned to an annotated tag reports no drift" 0 "$status"
assert_contains "an annotated-tag plugin at its commit is ok" "$out" \
  "plugin ok: some-org/annotated-plugin"
case "$out" in
  *"moved"*) fail "an annotated-tag plugin at its own commit was reported as moved: $out" ;;
  *) pass ;;
esac

# --- cmd_diff wiring: run the REAL entrypoint end to end, from a sandbox
# copy of herdr-setup + lib/ (never the checkout this test file lives in --
# HS_ROOT follows $0, so a copy in $work gets its own HS_ROOT and its own
# manifest/, with nothing written to this repo). Proves cmd_diff reads
# $HS_ROOT/manifest/plugins.list and the host's plugins.json (located via
# HERDR_CONFIG_DIR), accumulates the plugin section's exit status without
# tripping `set -e`, and never calls herdr. ---

sandbox="$work/sandbox"
mkdir -p "$sandbox/lib" "$sandbox/manifest"
cp "$repo_root/herdr-setup" "$sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$sandbox/lib/hs.py"
chmod +x "$sandbox/herdr-setup"
echo "kryptamine/herdr-auto-title   v0.3.3" > "$sandbox/manifest/plugins.list"
# Phase 3 added a config section to cmd_diff that fails closed on a
# missing manifest config.toml (see tests/test_diff_config.sh); an empty
# one here keeps these plugin-focused assertions about the plugin section
# only -- none of the HERDR_CONFIG_DIR fixtures below carry a host
# config.toml either, so the config section compares empty against empty
# and always reports a match.
: > "$sandbox/manifest/config.toml"

config_ok="$work/config_ok"
mkdir -p "$config_ok"
cp "$clean_json" "$config_ok/plugins.json"

out="$work/cli_ok_out"; err="$work/cli_ok_err"
FAKE_GIT_FIXTURES="$fixtures" HERDR_CONFIG_DIR="$config_ok" "$sandbox/herdr-setup" diff >"$out" 2>"$err"
status=$?
assert_status "herdr-setup diff exits 0 when the plugin matches" 0 "$status"
assert_contains "herdr-setup diff reports the plugin as ok" "$(cat "$out")" "ok: kryptamine/herdr-auto-title"

config_missing="$work/config_missing"
mkdir -p "$config_missing"
# no plugins.json in this one -> the manifest plugin is missing from the host

out="$work/cli_missing_out"; err="$work/cli_missing_err"
FAKE_GIT_FIXTURES="$fixtures" HERDR_CONFIG_DIR="$config_missing" "$sandbox/herdr-setup" diff >"$out" 2>"$err"
status=$?
assert_status "herdr-setup diff exits 1 when the plugin is missing from the host" 1 "$status"
assert_contains "herdr-setup diff reports the plugin as missing" "$(cat "$out")" "missing: kryptamine/herdr-auto-title"

out="$work/cli_mismatch_out"; err="$work/cli_mismatch_err"
FAKE_GIT_FIXTURES="$fixtures" HERDR_CONFIG_DIR="$config_ok" FAKE_HERDR_PROTOCOL_MISMATCH=1 "$sandbox/herdr-setup" diff >"$out" 2>"$err"
status=$?
assert_status "herdr-setup diff still exits 0 under a protocol mismatch" 0 "$status"
assert_contains "herdr-setup diff still reports ok under a protocol mismatch" "$(cat "$out")" "ok: kryptamine/herdr-auto-title"

hs_test_report
