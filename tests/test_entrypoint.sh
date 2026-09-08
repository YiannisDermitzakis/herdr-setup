#!/usr/bin/env bash
# Tests for the herdr-setup entrypoint: usage, subcommand dispatch, and
# global flags. The entrypoint does not exist yet; expect failure until
# P1.T2.S2 writes it.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
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
# onboard (phase 9) all now have real implementations. diff never had a
# stub state to check here in the first place: this checkout has no
# manifest/ directory yet (that lands only when a real absorb or apply
# runs, and this file never lets that happen against the real checkout --
# see below), so diff fails closed per AGENTS.md ("an unreadable manifest
# ... stops the run") rather than reporting a stub success. ---
out="$work/out_diff"; err="$work/err_diff"
status="$(run_entry "$out" "$err" diff)"
assert_status "diff is accepted; fails closed on a missing manifest" 2 "$status"
assert_contains "diff names the missing manifest on stderr" "$(cat "$err")" "manifest"

# --- absorb is real now too (phase 5): it WRITES manifest/plugins.list
# and manifest/config.toml under $HS_ROOT, which resolves to this actual
# checkout when invoked as "$repo_root/herdr-setup" -- a real (non-dry-run)
# absorb here would create files in the real repository this test suite
# lives in. --dry-run never writes, so it is the only safe way to exercise
# absorb directly against $repo_root; the full read/write/dirty-guard
# behaviour is covered end to end, via sandbox copies, by
# tests/test_absorb.sh and tests/test_roundtrip.sh. ---
out="$work/out_absorb"; err="$work/err_absorb"
status="$(run_entry "$out" "$err" --dry-run absorb)"
assert_status "absorb --dry-run is accepted and exits 0 against a clean checkout" 0 "$status"
assert_contains "absorb --dry-run names the plugins.list target" "$(cat "$out")" "manifest/plugins.list"
assert_contains "absorb --dry-run names the config.toml target" "$(cat "$out")" "manifest/config.toml"
[ ! -e "$repo_root/manifest" ] && pass || fail "absorb --dry-run created manifest/ in the real checkout"

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
