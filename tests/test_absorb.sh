#!/usr/bin/env bash
# shellcheck disable=SC2015
# this suite's idiom throughout: pass()/fail()
# (tests/helpers/assert.sh) never return nonzero, so "A && pass || fail C" cannot
# silently take the wrong branch.
# Tests for `absorb`: hs_absorb_plugins, hs_absorb_config, hs_absorb_header,
# hs_require_clean_manifest in lib/common.sh, and cmd_absorb's wiring into
# the real entrypoint. None of these exist yet (cmd_absorb is a phase-1
# stub); expect failure until P5.T1.S2/P5.T2.S2 write them.
#
# absorb never calls herdr -- it reads plugins.json and config.toml
# straight from disk, exactly like the config/plugin sections of `diff`
# do, and needs no fake git for ref resolution (it never calls
# hs_resolve_ref).
#
# The dirty-manifest guard is exercised against REAL git repositories, not a
# fake git. The fake used here before ignored its arguments, always exited 0,
# and printed whatever a variable said -- so it could express "clean" and
# "dirty" and nothing else, and the sandbox it ran against was not a git
# repository at all. The guard's actual failure, git EXITING NON-ZERO, could
# not be written down: real git in a non-repository exits 128, which the
# guard read as an empty status listing, which reads as clean, so absorb
# overwrote a hand-edited manifest and exited 0. A fake that cannot fail
# cannot catch a guard that fails open. Three states are covered here:
# clean, dirty, and not-a-repository.
set -u

# hs_test_git <repo> [args...]: git with an identity and no dependence on the
# operator's own config. tests/run.sh gives every file a throwaway $HOME, so
# there is no user.name/user.email to inherit.
hs_test_git() {
  local repo="$1"
  shift
  git -C "$repo" \
    -c user.name="herdr-setup tests" \
    -c user.email="tests@example.invalid" \
    -c init.defaultBranch=main \
    -c commit.gpgsign=false \
    "$@"
}

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
mkdir -p "$repo_dir/manifest"
hs_test_git "$repo_dir" init -q
printf 'owner/repo v1\n' > "$repo_dir/manifest/plugins.list"
printf '[ui]\n' > "$repo_dir/manifest/config.toml"
hs_test_git "$repo_dir" add -A
hs_test_git "$repo_dir" commit -q -m "committed manifest"

# --- clean: everything under manifest/ is committed ---
err="$work/clean.err"
hs_require_clean_manifest "$repo_dir" 2>"$err"
status=$?
assert_status "hs_require_clean_manifest exits 0 on a clean manifest/" 0 "$status"
[ ! -s "$err" ] && pass || fail "a clean manifest/ still printed something: $(cat "$err")"

# --- dirty: a tracked file edited by hand, plus an untracked one ---
printf '[ui]\nhand_edit = true\n' > "$repo_dir/manifest/config.toml"
printf 'scratch\n' > "$repo_dir/manifest/notes.txt"
err="$work/dirty.err"
hs_require_clean_manifest "$repo_dir" 2>"$err"
status=$?
assert_status "hs_require_clean_manifest exits 4 on a dirty manifest/" 4 "$status"
assert_contains "names the modified file" "$(cat "$err")" "manifest/config.toml"
assert_contains "names the untracked file" "$(cat "$err")" "manifest/notes.txt"

# --- back to clean once the edits are gone ---
hs_test_git "$repo_dir" checkout -q -- manifest/config.toml
rm -f "$repo_dir/manifest/notes.txt"
hs_require_clean_manifest "$repo_dir" 2>/dev/null
status=$?
assert_status "hs_require_clean_manifest exits 0 again once the edits are reverted" 0 "$status"

# --- NOT a git repository: git exits non-zero and prints nothing useful on
# stdout. Read as text alone, that is indistinguishable from a clean tree,
# and the guard used to say clean and let absorb overwrite. It is a hard
# refusal, exit 2, and it must never be 0. ---
not_a_repo="$work/not_a_repo"
mkdir -p "$not_a_repo/manifest"
printf 'hand edited, never committed\n' > "$not_a_repo/manifest/config.toml"
err="$work/notrepo.err"
( cd "$not_a_repo" && GIT_CEILING_DIRECTORIES="$work" hs_require_clean_manifest "$not_a_repo" ) 2>"$err"
status=$?
[ "$status" -ne 0 ] && pass || fail "hs_require_clean_manifest reported a non-repository as clean"
assert_status "hs_require_clean_manifest exits 2 when git itself fails" 2 "$status"
[ -s "$err" ] && pass || fail "a git failure was refused silently"
assert_contains "the refusal names the reason" "$(cat "$err")" "refusing to overwrite"

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
hs_test_git "$sandbox" init -q
hs_test_git "$sandbox" add -A
hs_test_git "$sandbox" commit -q -m "sandbox checkout"

host_dir="$work/host"
mkdir -p "$host_dir"
cp "$plugins_json" "$host_dir/plugins.json"
cp "$host_config" "$host_dir/config.toml"

# --- --dry-run: prints both files to stdout, writes neither ---

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

before_listing="$(find "$sandbox" -type f -not -path "$sandbox/manifest/*" -not -path "$sandbox/.git/*" | sort)"

out="$work/real.out"; err="$work/real.err"
HERDR_CONFIG_DIR="$host_dir" "$sandbox/herdr-setup" absorb >"$out" 2>"$err"
status=$?
assert_status "herdr-setup absorb exits 0 on a clean tree" 0 "$status"
[ -f "$sandbox/manifest/plugins.list" ] && pass || fail "absorb did not write manifest/plugins.list"
[ -f "$sandbox/manifest/config.toml" ] && pass || fail "absorb did not write manifest/config.toml"

after_listing="$(find "$sandbox" -type f -not -path "$sandbox/manifest/*" -not -path "$sandbox/.git/*" | sort)"
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

# --- the manifest absorb just wrote is untracked, so the tree is dirty and
# a second absorb refuses, end to end, without any test having to arrange it ---

before_plugins="$(cat "$sandbox/manifest/plugins.list")"
before_config="$(cat "$sandbox/manifest/config.toml")"

out="$work/untracked_run.out"; err="$work/untracked_run.err"
HERDR_CONFIG_DIR="$host_dir" "$sandbox/herdr-setup" absorb >"$out" 2>"$err"
status=$?
assert_status "a second absorb exits 4 while the first one's output is uncommitted" 4 "$status"
assert_contains "names the untracked manifest on stderr" "$(cat "$err")" "manifest/"

# --- the dirty-manifest guard on a HAND EDIT, wired end to end: the
# manifest is committed, then edited, and absorb must refuse rather than
# overwrite the edit. This is the case the guard exists for. ---

hs_test_git "$sandbox" add -A
hs_test_git "$sandbox" commit -q -m "absorbed manifest"

printf '\n# a hand edit that must survive\n' >> "$sandbox/manifest/config.toml"
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
assert_contains "the hand edit is still there" "$(cat "$sandbox/manifest/config.toml")" \
  "a hand edit that must survive"

hs_test_git "$sandbox" checkout -q -- manifest/config.toml

# --- a checkout that is NOT a git repository at all. The guard cannot tell
# whether the manifest is clean, so it must refuse. It used to read git's
# failure as an empty status listing, call that clean, and overwrite the
# hand edit below with exit 0 and not one word of warning. ---

bare_sandbox="$work/bare_sandbox"
mkdir -p "$bare_sandbox/lib" "$bare_sandbox/manifest"
cp "$repo_root/herdr-setup" "$bare_sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$bare_sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$bare_sandbox/lib/hs.py"
chmod +x "$bare_sandbox/herdr-setup"
hand_edit='# hand-written, never committed, must not be eaten'
printf '%s\n' "$hand_edit" > "$bare_sandbox/manifest/config.toml"
printf '%s\n' "$hand_edit" > "$bare_sandbox/manifest/plugins.list"

out="$work/norepo_run.out"; err="$work/norepo_run.err"
GIT_CEILING_DIRECTORIES="$work" HERDR_CONFIG_DIR="$host_dir" \
  "$bare_sandbox/herdr-setup" absorb >"$out" 2>"$err"
status=$?
[ "$status" -ne 0 ] && pass || fail "absorb exited 0 in a directory that is not a git repository"
assert_eq "the hand-edited config.toml survived" "$hand_edit" \
  "$(cat "$bare_sandbox/manifest/config.toml")"
assert_eq "the hand-edited plugins.list survived" "$hand_edit" \
  "$(cat "$bare_sandbox/manifest/plugins.list")"
[ -s "$err" ] && pass || fail "absorb refused silently outside a git repository"

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
# A real, clean repository, so absorb gets PAST the dirty-manifest guard and
# the home-path check is the thing actually under test here.
hs_test_git "$leaky_sandbox" init -q
hs_test_git "$leaky_sandbox" add -A
hs_test_git "$leaky_sandbox" commit -q -m "leaky sandbox checkout"

out="$work/leak.out"; err="$work/leak.err"
HERDR_CONFIG_DIR="$leaky_host_dir" "$leaky_sandbox/herdr-setup" absorb >"$out" 2>"$err"
status=$?
[ "$status" -ne 0 ] && pass || fail "absorb exited 0 despite a leaking home path in the host config"
[ ! -e "$leaky_sandbox/manifest/config.toml" ] && pass || fail "absorb wrote a config.toml containing a home directory path"
[ ! -e "$leaky_sandbox/manifest/plugins.list" ] && pass || fail "absorb wrote plugins.list despite refusing on the leak"

# =====================================================================
# A manifest/ that git IGNORES. `git status --porcelain -- manifest/` reports
# nothing for an ignored path -- the same empty answer a clean tree gives --
# so the guard said clean and absorb overwrote the hand edit, exit 0. An
# untracked file is caught (it shows as `??`); an ignored one was not, and a
# fork that keeps its manifest private is exactly the case that produces one.
#
# The rule that closes it: every file under manifest/ must be TRACKED. Git
# cannot vouch for one it is ignoring, and "git cannot tell me" is a refusal
# here, never a clean tree.
# =====================================================================

ignored_sandbox="$work/ignored_sandbox"
mkdir -p "$ignored_sandbox/lib" "$ignored_sandbox/manifest"
cp "$repo_root/herdr-setup" "$ignored_sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$ignored_sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$ignored_sandbox/lib/hs.py"
chmod +x "$ignored_sandbox/herdr-setup"
printf 'manifest/\n' > "$ignored_sandbox/.gitignore"
ignored_edit='# a private hand edit in an ignored manifest, must not be eaten'
printf '%s\n' "$ignored_edit" > "$ignored_sandbox/manifest/config.toml"
printf '%s\n' "$ignored_edit" > "$ignored_sandbox/manifest/plugins.list"
hs_test_git "$ignored_sandbox" init -q
hs_test_git "$ignored_sandbox" add -A
hs_test_git "$ignored_sandbox" commit -q -m "ignored-manifest sandbox checkout"

# The premise of the test: git really does report this tree as clean.
assert_eq "git itself reports an ignored manifest/ as clean" "" \
  "$(hs_test_git "$ignored_sandbox" status --porcelain -- manifest/)"

err="$work/ignored_guard.err"
hs_require_clean_manifest "$ignored_sandbox" 2>"$err"
status=$?
[ "$status" -ne 0 ] && pass || fail "hs_require_clean_manifest reported an ignored manifest/ as clean"
assert_status "hs_require_clean_manifest exits 4 on an ignored manifest/" 4 "$status"
assert_contains "the refusal names the file git cannot vouch for" "$(cat "$err")" \
  "manifest/config.toml"

out="$work/ignored_run.out"; err="$work/ignored_run.err"
HERDR_CONFIG_DIR="$host_dir" "$ignored_sandbox/herdr-setup" absorb >"$out" 2>"$err"
status=$?
assert_status "absorb refuses an ignored manifest/ with 4" 4 "$status"
assert_eq "the hand edit in the ignored config.toml survived" "$ignored_edit" \
  "$(cat "$ignored_sandbox/manifest/config.toml")"
assert_eq "the hand edit in the ignored plugins.list survived" "$ignored_edit" \
  "$(cat "$ignored_sandbox/manifest/plugins.list")"
[ -s "$err" ] && pass || fail "absorb refused an ignored manifest/ silently"

# =====================================================================
# A write that FAILS must fail loudly here too. absorb writes each manifest
# file through a temp file and a rename, and neither the write nor the rename
# had its status checked -- the same unchecked shape as hs_apply_config's.
# A full disk left the manifest unchanged, or left plugins.list rewritten and
# config.toml not, and absorb exited 0 either way. The operator then commits
# a manifest they believe was refreshed.
#
# The stub goes on PATH rather than being a shell function: the entrypoint is
# a separate process here, so a function in this shell would not reach it.
# =====================================================================

stub_bin="$work/stub_bin"
mkdir -p "$stub_bin"
cat > "$stub_bin/mv" <<'EOF'
#!/bin/sh
exit 1
EOF
chmod +x "$stub_bin/mv"

write_sandbox="$work/write_sandbox"
mkdir -p "$write_sandbox/lib"
cp "$repo_root/herdr-setup" "$write_sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$write_sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$write_sandbox/lib/hs.py"
chmod +x "$write_sandbox/herdr-setup"
hs_test_git "$write_sandbox" init -q
hs_test_git "$write_sandbox" add -A
hs_test_git "$write_sandbox" commit -q -m "write sandbox checkout"

out="$work/mvfail.out"; err="$work/mvfail.err"
PATH="$stub_bin:$PATH" HERDR_CONFIG_DIR="$host_dir" \
  "$write_sandbox/herdr-setup" absorb >"$out" 2>"$err"
status=$?
assert_status "absorb returns 2 when a manifest rename fails" 2 "$status"
[ ! -e "$write_sandbox/manifest/plugins.list" ] && pass \
  || fail "a failed rename still left a plugins.list behind"
[ ! -e "$write_sandbox/manifest/config.toml" ] && pass \
  || fail "a failed rename still left a config.toml behind"
[ -s "$err" ] && fail_check=0 || fail_check=1
[ "$fail_check" -eq 0 ] && pass || fail "a failed absorb write was refused silently"
leftover="$(find "$write_sandbox/manifest" -maxdepth 1 -name '.herdr-setup-absorb.*' 2>/dev/null)"
[ -z "$leftover" ] && pass || fail "a failed absorb write left its temp file behind: $leftover"

hs_test_report
