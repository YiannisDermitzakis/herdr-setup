#!/usr/bin/env bash
# Tests for `absorb`: hs_absorb_plugins, hs_absorb_config, hs_absorb_header,
# hs_require_clean_manifest in lib/common.sh, and cmd_absorb's wiring into
# the real entrypoint. None of these exist yet (cmd_absorb is a phase-1
# stub); expect failure until P5.T1.S2/P5.T2.S2 write them.
#
# absorb never calls herdr -- it reads plugins.json and config.toml
# straight from disk, exactly like the config/plugin sections of `diff`
# do, and needs no fake-git-on-PATH for ref resolution (it never calls
# hs_resolve_ref). It DOES need a fake `git` on PATH for the dirty-manifest
# guard (`git status --porcelain -- manifest/`), local to this file only.
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
# fake git: only understands `-C <dir> status --porcelain -- manifest/`.
# Prints $FAKE_GIT_STATUS_OUTPUT verbatim (empty = clean tree).
# =====================================================================

fake_git_bin="$work/gitbin"
mkdir -p "$fake_git_bin"
cat > "$fake_git_bin/git" <<'GITEOF'
#!/usr/bin/env bash
set -u
if [ "${1:-}" = "-C" ]; then
  shift 2
fi
if [ "${1:-}" = "status" ]; then
  printf '%s' "${FAKE_GIT_STATUS_OUTPUT:-}"
  exit 0
fi
echo "fake-git: unsupported invocation: $*" >&2
exit 2
GITEOF
chmod +x "$fake_git_bin/git"
export PATH="$fake_git_bin:$PATH"

# =====================================================================
# hs_absorb_header
# =====================================================================

if ! command -v hs_absorb_header >/dev/null 2>&1; then
  fail "hs_absorb_header is not defined yet"
  hs_test_report
fi

header_out="$(hs_absorb_header)"
assert_contains "the header names the tool" "$header_out" "herdr-setup"
case "$header_out" in
  *"$HOME"*|*/Users/*|*/home/*)
    fail "the header itself leaks a home directory path: $header_out" ;;
  *)
    pass ;;
esac
case "$header_out" in
  *"$(hostname 2>/dev/null)"*)
    [ -z "$(hostname 2>/dev/null)" ] && pass || fail "the header leaks the hostname: $header_out" ;;
  *)
    pass ;;
esac

# =====================================================================
# hs_absorb_plugins <plugins-json>: one "<source> <ref>" line per plugin,
# sorted by source, keeping owner/repo[/subdir] + requested_ref only.
# =====================================================================

if ! command -v hs_absorb_plugins >/dev/null 2>&1; then
  fail "hs_absorb_plugins is not defined yet"
  hs_test_report
fi

plugins_json="$work/plugins.json"
cat > "$plugins_json" <<'JSONEOF'
[
  {
    "plugin_id": "kryptamine.auto-title",
    "source": {
      "kind": "github", "owner": "kryptamine", "repo": "herdr-auto-title",
      "requested_ref": "v0.3.3",
      "resolved_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "managed_path": "/home/placeholder-user/.config/herdr/plugins/kryptamine.auto-title"
    }
  },
  {
    "plugin_id": "ez-corp.space-usage",
    "source": {
      "kind": "github", "owner": "ezcorp-org", "repo": "herdr-pc-ram-and-cpu-usage-overlay",
      "requested_ref": "main",
      "resolved_commit": "94a2ea3bf21ec35c6da51b9657c97167e68034ce",
      "managed_path": "/home/placeholder-user/.config/herdr/plugins/ez-corp.space-usage"
    }
  }
]
JSONEOF

out="$(hs_absorb_plugins "$plugins_json")"
status=$?
assert_status "hs_absorb_plugins exits 0 on a valid plugins.json" 0 "$status"
assert_contains "carries the ezcorp-org plugin, source and ref only" "$out" \
  "ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay main"
assert_contains "carries the kryptamine plugin, source and ref only" "$out" \
  "kryptamine/herdr-auto-title v0.3.3"
case "$out" in
  *"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"*|*"94a2ea3bf21ec35c6da51b9657c97167e68034ce"*)
    fail "hs_absorb_plugins leaked a resolved_commit: $out" ;;
  *) pass ;;
esac
case "$out" in
  *"managed_path"*|*"/home/placeholder-user"*)
    fail "hs_absorb_plugins leaked managed_path: $out" ;;
  *) pass ;;
esac
first_line=$(printf '%s\n' "$out" | grep -n '^ezcorp-org/' | cut -d: -f1)
second_line=$(printf '%s\n' "$out" | grep -n '^kryptamine/' | cut -d: -f1)
[ -n "$first_line" ] && [ -n "$second_line" ] && [ "$first_line" -lt "$second_line" ] \
  && pass || fail "plugins are not sorted by source: $out"

missing_json="$work/does-not-exist-plugins.json"
out="$(hs_absorb_plugins "$missing_json")"
status=$?
assert_status "hs_absorb_plugins exits 0 with no plugins.json (host has no plugins)" 0 "$status"
assert_contains "still carries the header with no plugins.json" "$out" "herdr-setup"
header_lines="$(hs_absorb_header | wc -l | tr -d ' ')"
out_lines="$(printf '%s\n' "$out" | wc -l | tr -d ' ')"
assert_eq "no plugins.json produces header lines only, no plugin line" "$header_lines" "$out_lines"

malformed_json="$work/malformed_plugins.json"
echo '{not valid json' > "$malformed_json"
err="$work/absorb_plugins_malformed.err"
out="$(hs_absorb_plugins "$malformed_json" 2>"$err")"
status=$?
assert_status "hs_absorb_plugins exits 2 on a malformed plugins.json" 2 "$status"
assert_contains "names the malformed file" "$(cat "$err")" "malformed_plugins.json"

# =====================================================================
# hs_absorb_config <host-config>: the host config with plugin-written
# blocks stripped (hs_strip_plugin_blocks, built in phase 3).
# =====================================================================

if ! command -v hs_absorb_config >/dev/null 2>&1; then
  fail "hs_absorb_config is not defined yet"
  hs_test_report
fi

host_config="$work/host_config.toml"
cat > "$host_config" <<'EOF'
[ui]
agent_panel_sort = "priority"

# --- added by ez-corp.space-usage (removed by `status-disable`) ---
[ui.sidebar.spaces]
rows = [ ["state_icon", "workspace"], ["$usage"] ]
# --- end ez-corp.space-usage ---

[misc]
foo = "bar"
EOF

out="$(hs_absorb_config "$host_config")"
status=$?
assert_status "hs_absorb_config exits 0" 0 "$status"
assert_contains "keeps the operator's own lines" "$out" 'agent_panel_sort = "priority"'
assert_contains "keeps other operator lines" "$out" 'foo = "bar"'
case "$out" in
  *'# --- added by ez-corp.space-usage'*|*'rows = [ ["state_icon"'*)
    fail "hs_absorb_config kept a plugin-written block: $out" ;;
  *) pass ;;
esac

no_host_config="$work/no-such-config.toml"
out="$(hs_absorb_config "$no_host_config")"
status=$?
assert_status "hs_absorb_config exits 0 with no host config yet" 0 "$status"
case "$out" in
  *'agent_panel_sort'*) fail "a missing host config produced operator content out of nowhere: $out" ;;
  *) pass ;;
esac

# =====================================================================
# hs_require_clean_manifest <repo-root>: exit 4 + names dirty files when
# `git status --porcelain -- manifest/` reports anything; exit 0 (clean)
# otherwise.
# =====================================================================

if ! command -v hs_require_clean_manifest >/dev/null 2>&1; then
  fail "hs_require_clean_manifest is not defined yet"
  hs_test_report
fi

repo_dir="$work/somerepo"
mkdir -p "$repo_dir"

export FAKE_GIT_STATUS_OUTPUT=$' M manifest/config.toml\n?? manifest/plugins.list\n'
err="$work/dirty.err"
hs_require_clean_manifest "$repo_dir" 2>"$err"
status=$?
assert_status "hs_require_clean_manifest exits 4 on a dirty manifest/" 4 "$status"
assert_contains "names the modified file" "$(cat "$err")" "manifest/config.toml"
assert_contains "names the untracked file" "$(cat "$err")" "manifest/plugins.list"

export FAKE_GIT_STATUS_OUTPUT=""
hs_require_clean_manifest "$repo_dir" 2>/dev/null
status=$?
assert_status "hs_require_clean_manifest exits 0 on a clean manifest/" 0 "$status"
unset FAKE_GIT_STATUS_OUTPUT

# =====================================================================
# cmd_absorb wiring: the real entrypoint, from a sandbox copy (never this
# checkout -- same technique as tests/test_apply_plugins.sh).
# =====================================================================

sandbox="$work/sandbox"
mkdir -p "$sandbox/lib"
cp "$repo_root/herdr-setup" "$sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$sandbox/lib/hs.py"
chmod +x "$sandbox/herdr-setup"

host_dir="$work/host"
mkdir -p "$host_dir"
cp "$plugins_json" "$host_dir/plugins.json"
cp "$host_config" "$host_dir/config.toml"

# --- --dry-run: prints both files to stdout, writes neither ---

export FAKE_GIT_STATUS_OUTPUT=""
out="$work/dryrun.out"; err="$work/dryrun.err"
HERDR_CONFIG_DIR="$host_dir" "$sandbox/herdr-setup" --dry-run absorb >"$out" 2>"$err"
status=$?
assert_status "herdr-setup --dry-run absorb exits 0" 0 "$status"
assert_contains "dry-run prints the plugins content" "$(cat "$out")" \
  "ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay main"
assert_contains "dry-run prints the config content" "$(cat "$out")" 'agent_panel_sort = "priority"'
[ ! -e "$sandbox/manifest" ] && pass || fail "dry-run absorb created manifest/ on disk"

# --- a real run: writes both files, sorted/stripped/headed correctly,
# touches nothing outside manifest/, and the plugins.list it wrote parses
# back cleanly with hs_manifest_plugins ---

before_listing="$(find "$sandbox" -type f -not -path "$sandbox/manifest/*" | sort)"

out="$work/real.out"; err="$work/real.err"
HERDR_CONFIG_DIR="$host_dir" "$sandbox/herdr-setup" absorb >"$out" 2>"$err"
status=$?
assert_status "herdr-setup absorb exits 0 on a clean tree" 0 "$status"
[ -f "$sandbox/manifest/plugins.list" ] && pass || fail "absorb did not write manifest/plugins.list"
[ -f "$sandbox/manifest/config.toml" ] && pass || fail "absorb did not write manifest/config.toml"

after_listing="$(find "$sandbox" -type f -not -path "$sandbox/manifest/*" | sort)"
assert_eq "absorb touches nothing outside manifest/" "$before_listing" "$after_listing"

assert_contains "written plugins.list carries the ezcorp-org plugin" \
  "$(cat "$sandbox/manifest/plugins.list")" "ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay main"
assert_contains "written config.toml keeps operator lines" \
  "$(cat "$sandbox/manifest/config.toml")" 'agent_panel_sort = "priority"'
case "$(cat "$sandbox/manifest/config.toml")" in
  *'# --- added by ez-corp.space-usage'*) fail "written config.toml kept a plugin block" ;;
  *) pass ;;
esac
assert_contains "written plugins.list carries the header" \
  "$(cat "$sandbox/manifest/plugins.list")" "herdr-setup"
case "$(cat "$sandbox/manifest/config.toml")" in
  *"herdr-setup"*)
    fail "written config.toml carries a header -- it gets spliced verbatim into a live host config and must not" ;;
  *) pass ;;
esac

manifest_parsed="$(hs_manifest_plugins "$sandbox/manifest/plugins.list")"
status=$?
assert_status "the written plugins.list parses back with hs_manifest_plugins" 0 "$status"
assert_contains "the parsed manifest carries the ezcorp-org plugin" "$manifest_parsed" \
  "$(printf 'ezcorp-org/herdr-pc-ram-and-cpu-usage-overlay\tmain')"

# --- the dirty-manifest guard, wired end to end: exits 4, writes nothing,
# names the dirty files ---

export FAKE_GIT_STATUS_OUTPUT=$' M manifest/config.toml\n'
before_plugins="$(cat "$sandbox/manifest/plugins.list")"
before_config="$(cat "$sandbox/manifest/config.toml")"

out="$work/dirty_run.out"; err="$work/dirty_run.err"
HERDR_CONFIG_DIR="$host_dir" "$sandbox/herdr-setup" absorb >"$out" 2>"$err"
status=$?
assert_status "herdr-setup absorb exits 4 on a dirty manifest/" 4 "$status"
assert_contains "names the dirty file on stderr" "$(cat "$err")" "manifest/config.toml"
assert_eq "a refused absorb leaves plugins.list untouched" "$before_plugins" \
  "$(cat "$sandbox/manifest/plugins.list")"
assert_eq "a refused absorb leaves config.toml untouched" "$before_config" \
  "$(cat "$sandbox/manifest/config.toml")"
unset FAKE_GIT_STATUS_OUTPUT

# --- a host config value that itself carries a home directory path:
# absorb must refuse rather than write it into the public checkout ---

leaky_host_dir="$work/leaky_host"
mkdir -p "$leaky_host_dir"
cp "$plugins_json" "$leaky_host_dir/plugins.json"
cat > "$leaky_host_dir/config.toml" <<'EOF'
[misc]
watch_dir = "/Users/example/Projects/notes"
EOF

leaky_sandbox="$work/leaky_sandbox"
mkdir -p "$leaky_sandbox/lib"
cp "$repo_root/herdr-setup" "$leaky_sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$leaky_sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$leaky_sandbox/lib/hs.py"
chmod +x "$leaky_sandbox/herdr-setup"

out="$work/leak.out"; err="$work/leak.err"
HERDR_CONFIG_DIR="$leaky_host_dir" "$leaky_sandbox/herdr-setup" absorb >"$out" 2>"$err"
status=$?
[ "$status" -ne 0 ] && pass || fail "absorb exited 0 despite a leaking home path in the host config"
[ ! -e "$leaky_sandbox/manifest/config.toml" ] && pass || fail "absorb wrote a config.toml containing a home directory path"
[ ! -e "$leaky_sandbox/manifest/plugins.list" ] && pass || fail "absorb wrote plugins.list despite refusing on the leak"

hs_test_report
