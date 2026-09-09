#!/usr/bin/env bash
# shellcheck disable=SC2015
# this suite's idiom throughout: pass()/fail()
# (tests/helpers/assert.sh) never return nonzero, so "A && pass || fail C" cannot
# silently take the wrong branch.
# Tests for the herdr-setup entrypoint: usage, subcommand dispatch, and
# global flags. The entrypoint does not exist yet; expect failure until
# P1.T2.S2 writes it.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"

entry="$repo_root/herdr-setup"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# run_entry <out-file> <err-file> [args...] -- runs the entrypoint, prints
# its exit status on stdout (the caller captures it via command substitution).
run_entry() {
  local out_file="$1" err_file="$2"
  shift 2
  "$entry" "$@" >"$out_file" 2>"$err_file"
  echo $?
}

if [ ! -x "$entry" ]; then
  fail "herdr-setup does not exist yet (or is not executable) at $entry"
  hs_test_report
fi

# --- no arguments: usage to stderr, exit 2 ---
out="$work/out_noargs"; err="$work/err_noargs"
status="$(run_entry "$out" "$err")"
assert_status "no args exits 2" 2 "$status"
[ -s "$err" ] && pass || fail "no-args usage goes to stderr"
[ ! -s "$out" ] && pass || fail "no-args prints nothing to stdout"
assert_contains "no-args usage names the commands" "$(cat "$err")" "diff"

# --- unknown subcommand: exit 2, naming the subcommand ---
out="$work/out_unknown"; err="$work/err_unknown"
status="$(run_entry "$out" "$err" frobnicate)"
assert_status "unknown subcommand exits 2" 2 "$status"
assert_contains "unknown subcommand names itself" "$(cat "$err")" "frobnicate"

# --- --help: usage to stdout, exit 0 ---
out="$work/out_help"; err="$work/err_help"
status="$(run_entry "$out" "$err" --help)"
assert_status "--help exits 0" 0 "$status"
assert_contains "--help prints usage" "$(cat "$out")" "usage:"

# --- diff (phase 2), apply (phase 4), absorb (phase 5), feed (phase 6) and
# onboard (phase 9) all now have real implementations. Phase 10 seeds this
# checkout's own manifest/plugins.list and manifest/config.toml, so diff
# against the real checkout now has a real manifest to read -- run in the
# fresh, herdr-less $HOME tests/run.sh hands every test file, every seeded
# plugin reads as missing and the seeded [ui] section as config drift, so
# diff reports drift (exit 1) rather than the fail-closed "manifest not
# found" (exit 2) this block asserted back when manifest/ did not exist yet.
# The missing-manifest fail-closed path itself stays covered directly, by
# tests/test_diff_plugins.sh and tests/test_diff_config.sh, against a
# manifest file this suite deletes on purpose. ---
# --- diff and absorb both read (and absorb WRITES) $HS_ROOT/manifest, and
# HS_ROOT follows $0. Run against "$repo_root/herdr-setup" they read the real
# checkout this suite lives in, which is somebody's working tree: the
# assertions then depend on whether the developer happens to be holding an
# uncommitted manifest edit, or on whether the checkout carries a manifest at
# all. That is not a property of the entrypoint, and it failed four
# assertions for a state the program never created -- one of them saying
# absorb "left the real checkout's manifest/ dirty" when the edit was the
# developer's own. It also reddens for any CI step that writes into the tree
# before the suite runs.
#
# So both run against a sandbox with a git repository and a manifest this
# test controls, the pattern tests/test_absorb.sh already uses. Nothing here
# reads or writes $repo_root. ---

# hs_test_git <repo> [args...]: git with an identity of its own -- tests/run.sh
# gives every file a throwaway $HOME, so there is no user.name to inherit.
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

sandbox="$work/sandbox"
mkdir -p "$sandbox/lib" "$sandbox/manifest"
cp "$repo_root/herdr-setup" "$sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$sandbox/lib/hs.py"
chmod +x "$sandbox/herdr-setup"
cat > "$sandbox/manifest/plugins.list" <<'EOF'
kryptamine/herdr-auto-title v0.3.3
EOF
cat > "$sandbox/manifest/config.toml" <<'EOF'
[ui]
agent_panel_sort = "priority"
EOF
hs_test_git "$sandbox" init -q
hs_test_git "$sandbox" add -A
hs_test_git "$sandbox" commit -q -m "sandbox checkout with a committed manifest"

# run_sandbox <out-file> <err-file> [args...] -- as run_entry, against the
# sandbox copy. The host has no plugins.json and no config.toml (tests/run.sh
# hands every file a fresh empty $HOME), so every manifest plugin is missing
# and the whole config reads as drift, without any ref ever being resolved.
run_sandbox() {
  local out_file="$1" err_file="$2"
  shift 2
  "$sandbox/herdr-setup" "$@" >"$out_file" 2>"$err_file"
  echo $?
}

out="$work/out_diff"; err="$work/err_diff"
status="$(run_sandbox "$out" "$err" diff)"
assert_status "diff is accepted; reports drift against the manifest" 1 "$status"
assert_contains "diff reports the manifest plugins as missing on a fresh host" \
  "$(cat "$out")" "kryptamine/herdr-auto-title"
assert_contains "diff reports the manifest config as drift on a fresh host" \
  "$(cat "$out")" "config drift:"
[ ! -s "$err" ] && pass || fail "a drift report, not an error, prints nothing to stderr: $(cat "$err")"

# --- absorb --dry-run: names both targets, writes neither, and leaves the
# manifest byte for byte as it was and the repository clean ---
plugins_before="$(cat "$sandbox/manifest/plugins.list")"
config_before="$(cat "$sandbox/manifest/config.toml")"

out="$work/out_absorb"; err="$work/err_absorb"
status="$(run_sandbox "$out" "$err" --dry-run absorb)"
assert_status "absorb --dry-run is accepted and exits 0" 0 "$status"
assert_contains "absorb --dry-run names the plugins.list target" "$(cat "$out")" "manifest/plugins.list"
assert_contains "absorb --dry-run names the config.toml target" "$(cat "$out")" "manifest/config.toml"
assert_eq "absorb --dry-run left manifest/plugins.list untouched" \
  "$plugins_before" "$(cat "$sandbox/manifest/plugins.list")"
assert_eq "absorb --dry-run left manifest/config.toml untouched" \
  "$config_before" "$(cat "$sandbox/manifest/config.toml")"
[ -z "$(hs_test_git "$sandbox" status --porcelain -- manifest/)" ] && pass \
  || fail "absorb --dry-run left the sandbox's manifest/ dirty"

# --- apply calls hs_require_socket first, unconditionally, and fails
# closed there before it ever gets to a missing manifest -- proving the
# gate runs before anything else, exactly as AGENTS.md's fail-closed rule
# and the design doc's preflight section require. HERDR_SOCKET_PATH is
# forced to a path that cannot exist rather than relying on the default
# derived from $HOME: a host that itself runs Herdr (as this one does)
# exports HERDR_SOCKET_PATH into every child process, which would
# otherwise silently point this test at the operator's own real socket
# regardless of the sandboxed $HOME tests/run.sh sets up. ---
out="$work/out_apply"; err="$work/err_apply"
status="$(HERDR_SOCKET_PATH="$work/no-such-herdr.sock" run_entry "$out" "$err" apply)"
assert_status "apply is accepted; fails closed with no Herdr server reachable" 3 "$status"
assert_contains "apply names the missing server on stderr" "$(cat "$err")" "server"

# --- feed gates on the same hs_require_socket, for the same reason and
# before anything else, so it refuses here identically. The rest of feed's
# entrypoint behaviour (the mismatch refusal, the adapters directory, the
# flag pass-through) is tests/test_feed_entrypoint.sh's, against a sandbox
# copy that has its own adapters/. ---
out="$work/out_feed"; err="$work/err_feed"
status="$(HERDR_SOCKET_PATH="$work/no-such-herdr.sock" run_entry "$out" "$err" feed)"
assert_status "feed is accepted; fails closed with no Herdr server reachable" 3 "$status"
assert_contains "feed names the missing server on stderr" "$(cat "$err")" "server"
assert_eq "a refused feed prints nothing to stdout" "" "$(cat "$out")"

# --- onboard (phase 9) gates on the same hs_require_socket too, and just
# as unconditionally -- it refuses here identically, before the detection
# table is ever printed. The rest of onboard's own entrypoint behaviour
# (detection, the offer loop, the feed hand-off) is
# tests/test_onboard_offer.sh's, against a sandbox copy with its own
# adapters/, the same split feed gets above. ---
out="$work/out_onboard"; err="$work/err_onboard"
status="$(HERDR_SOCKET_PATH="$work/no-such-herdr.sock" run_entry "$out" "$err" onboard)"
assert_status "onboard is accepted; fails closed with no Herdr server reachable" 3 "$status"
assert_contains "onboard names the missing server on stderr" "$(cat "$err")" "server"
assert_eq "a refused onboard prints nothing to stdout" "" "$(cat "$out")"

# --- --dry-run / --yes are visible to the subcommand as HS_DRY_RUN / HS_YES,
# regardless of whether they come before or after the subcommand. onboard
# used to be the stub that echoed them directly; now that it is real (like
# diff/apply/absorb/feed, none of which echo the flags they saw either),
# the flags are proven visible by their OBSERVABLE EFFECT on a real onboard
# run instead: under --dry-run, an offered agent gets a
# "+ herdr integration install <target>" preview line and no
# `integration install` call ever reaches herdr; with no flags at all (and
# no terminal, which every test process here has none of) nothing is
# installed either, but there is no preview line -- the two are
# distinguishable on stdout alone.
#
# A tiny local sandbox, not the real checkout: HERDR_SOCKET_PATH points at
# a plain file (hs_preflight only needs it to exist; nothing here calls
# real herdr's own preflight probe), FAKE_HERDR_FIXTURES answers
# `integration status` with one absent agent, and $HOME/.claude gives that
# agent a configuration directory so hs_detect_agents finds it without
# depending on whether a real `claude` happens to be on this machine's own
# PATH. `herdr` itself is already the fake one: tests/run.sh puts
# tests/helpers ahead of the rest of PATH for every file in this suite. ---
flags_socket="$work/flags-herdr.sock"
: > "$flags_socket"
flags_fixtures="$work/flags-fixtures"
mkdir -p "$flags_fixtures"
cat > "$flags_fixtures/integration->status.json" <<'EOF'
claude: not installed (/home/placeholder-user/.claude/hooks/herdr-agent-state.sh)
EOF
mkdir -p "$HOME/.claude"
flags_log="$work/flags-herdr.log"

out="$work/out_flags_before"; err="$work/err_flags_before"
rm -f "$flags_log"
status="$(HERDR_SOCKET_PATH="$flags_socket" FAKE_HERDR_FIXTURES="$flags_fixtures" \
  FAKE_HERDR_LOG="$flags_log" run_entry "$out" "$err" --dry-run --yes onboard </dev/null)"
assert_status "flags before the subcommand still exit 0" 0 "$status"
assert_contains "--dry-run visible (flags before): a preview line, not a real call" \
  "$(cat "$out")" "+ herdr integration install claude"
assert_eq "--dry-run visible (flags before): herdr never actually saw the install" "0" \
  "$(grep -c '^integration install' "$flags_log")"

out="$work/out_flags_after"; err="$work/err_flags_after"
rm -f "$flags_log"
status="$(HERDR_SOCKET_PATH="$flags_socket" FAKE_HERDR_FIXTURES="$flags_fixtures" \
  FAKE_HERDR_LOG="$flags_log" run_entry "$out" "$err" onboard --dry-run --yes </dev/null)"
assert_status "flags after the subcommand still exit 0" 0 "$status"
assert_contains "--dry-run visible (flags after): a preview line, not a real call" \
  "$(cat "$out")" "+ herdr integration install claude"
assert_eq "--dry-run visible (flags after): herdr never actually saw the install" "0" \
  "$(grep -c '^integration install' "$flags_log")"

out="$work/out_noflags"; err="$work/err_noflags"
rm -f "$flags_log"
status="$(HERDR_SOCKET_PATH="$flags_socket" FAKE_HERDR_FIXTURES="$flags_fixtures" \
  FAKE_HERDR_LOG="$flags_log" run_entry "$out" "$err" onboard </dev/null)"
assert_status "no flags still exits 0" 0 "$status"
case "$(cat "$out")" in
  *"integration install"*) fail "HS_DRY_RUN defaults to 0, but a preview line still printed: $(cat "$out")" ;;
  *) pass ;;
esac
assert_eq "HS_YES defaults to 0: no terminal and no --yes installs nothing" "0" \
  "$(grep -c '^integration install' "$flags_log")"

hs_test_report
