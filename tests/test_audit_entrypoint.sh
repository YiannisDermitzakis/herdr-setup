#!/usr/bin/env bash
# `herdr-setup audit` through the real entrypoint.
#
# lib/audit.py's logic is tested directly by tests/test_audit_*.py. This file
# proves what only a run through the entrypoint can:
#
#   1. audit gates on hs_require_socket, FIRST -- exactly like feed's own
#      gate (tests/test_feed_entrypoint.sh), because audit needs `herdr
#      agent list` and `herdr tab list`.
#   2. A matched host reaches lib/audit.py through the hs_audit door, and a
#      source it cannot read fails closed: one line, exit 2, no report.
#   3. Arguments (here, --since) actually reach lib/audit.py's own argparse.
#   4. cmd_audit forwards --adapters and --socket before the operator's flags.
#
# The sandbox trick is the established one (tests/test_feed_entrypoint.sh):
# HS_ROOT follows $0, so a copy of the entrypoint in $work gets its own root,
# and nothing is written to this checkout.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"

# Resolved with `cd -P`, matching tests/test_install.sh's own fix: HS_ROOT is
# `cd -P`-resolved from $0, and on macOS /var/folders is itself a symlink to
# /private/var/folders, so an unresolved $work built the --adapters path
# cmd_audit is expected to forward one path form off from the one it
# actually built.
work="$(cd -P "$(mktemp -d)" && pwd)"
trap 'rm -rf "$work"' EXIT

sandbox="$work/sandbox"
mkdir -p "$sandbox/lib"
cp "$repo_root/herdr-setup" "$sandbox/herdr-setup"
cp "$repo_root/lib/common.sh" "$sandbox/lib/common.sh"
cp "$repo_root/lib/hs.py" "$sandbox/lib/hs.py"
cp "$repo_root/lib/feed.py" "$sandbox/lib/feed.py"
cp "$repo_root/lib/audit.py" "$sandbox/lib/audit.py"
chmod +x "$sandbox/herdr-setup" "$sandbox/lib/audit.py"

# hs_preflight requires the socket path to EXIST before it probes. The probe
# itself is the fake herdr, and -- with no fixture for `plugin list` -- it
# answers with nothing at all, which hs_response_error reads as success.
socket="$work/herdr.sock"
: > "$socket"

# --- (1) a mismatched Herdr refuses audit, exit 3, before lib/audit.py ever
# runs -- the same one-line refusal feed's own gate prints ---
out="$work/out_mismatch"; err="$work/err_mismatch"
HERDR_SOCKET_PATH="$socket" FAKE_HERDR_PROTOCOL_MISMATCH=1 \
  "$sandbox/herdr-setup" audit >"$out" 2>"$err"
status=$?
assert_status "audit refuses under a protocol mismatch" 3 "$status"
assert_contains "the refusal names the restart" "$(cat "$err")" "restart"
assert_eq "a refused audit prints nothing to stdout" "" "$(cat "$out")"

# --- (2) a matched host reaches lib/audit.py through hs_audit, and a source
# it cannot read fails closed: here the fake gh has no state to answer
# `gh api user` from, so the run stops with one line and never prints a
# clean, empty report ---
out="$work/out_matched"; err="$work/err_matched"
HERDR_SOCKET_PATH="$socket" \
  "$sandbox/herdr-setup" audit >"$out" 2>"$err"
status=$?
assert_status "a matched host reaches the audit runner, which exits 2 on a failed gh call" 2 "$status"
assert_eq "no report is printed" "" "$(cat "$out")"
assert_contains "one line naming the gh call" "$(cat "$err")" "herdr-setup: audit: gh api user"
assert_eq "and only one line" "1" "$(wc -l < "$err" | tr -d ' ')"

# --- (3) --since is real argparse, reaching lib/audit.py: 0 is out of range
# (1..3650) and argparse itself refuses, naming the flag ---
out="$work/out_since"; err="$work/err_since"
HERDR_SOCKET_PATH="$socket" \
  "$sandbox/herdr-setup" audit --since 0 >"$out" 2>"$err"
status=$?
assert_status "--since 0 is out of range and argparse refuses" 2 "$status"
assert_contains "the refusal names --since" "$(cat "$err")" "--since"

# --- (4) cmd_audit forwards --adapters and --socket BEFORE the operator's
# own flags, exactly the way cmd_feed builds its own args (herdr-setup's own
# comment on cmd_audit). Proven against a SANDBOX STUB standing in for
# lib/audit.py that just echoes its argv -- the real lib/audit.py is never
# touched for this. ---
sandbox4="$work/sandbox4"
mkdir -p "$sandbox4/lib"
cp "$repo_root/herdr-setup" "$sandbox4/herdr-setup"
cp "$repo_root/lib/common.sh" "$sandbox4/lib/common.sh"
chmod +x "$sandbox4/herdr-setup"
cat > "$sandbox4/lib/audit.py" <<'STUB'
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
# A stand-in for lib/audit.py, used ONLY to prove what argv cmd_audit
# builds. It never touches the real audit runner.
import sys

print("argv:" + " ".join(sys.argv[1:]))
STUB
chmod +x "$sandbox4/lib/audit.py"

out="$work/out_args"; err="$work/err_args"
HERDR_SOCKET_PATH="$socket" "$sandbox4/herdr-setup" audit --since 5 >"$out" 2>"$err"
status=$?
assert_status "the stub run exits 0" 0 "$status"
assert_eq "cmd_audit forwards --adapters and --socket before the operator's own flags" \
  "argv:--adapters $sandbox4/adapters --socket $socket --since 5" "$(cat "$out")"

# --- (5) end to end, through the entrypoint, with the REAL claude and codex
# adapters, a real git repository and the fake gh. ONE sandbox for every run
# below (tests/helpers/audit_e2e.py builds it in a single uv start), and as
# few runs as the cases allow: the findings run's own output carries the exit
# status, all three headings, the rows and the footer; the JSON run is also
# the --dry-run run; the failure cases each need their own. ---
mkdir -p "$sandbox/adapters"
cp "$repo_root/adapters/claude" "$repo_root/adapters/codex" "$sandbox/adapters/"
chmod +x "$sandbox/adapters/claude" "$sandbox/adapters/codex"

uv run --quiet --script "$test_dir/helpers/audit_e2e.py" setup "$work" >"$work/setup.log" 2>&1
setup_rc=$?
assert_status "the end-to-end sandbox is built" 0 "$setup_rc"
repo="$work/repos/example-repo"

# What a read-only command must leave exactly as it found it.
fingerprint() {
  GIT_OPTIONAL_LOCKS=0 git -C "$repo" for-each-ref --format='%(refname) %(objectname)'
  GIT_OPTIONAL_LOCKS=0 git -C "$repo" status --porcelain
  find "$work/stores" | LC_ALL=C sort
}
fingerprint_before="$(fingerprint)"
marker="$work/written-before-the-runs"
: > "$marker"

# audit_run <label> <findings|clean> [VAR=value ...] -- <herdr-setup arguments...>
# Leaves the exit status in $rc and the output in $work/out_<label>, err_<label>.
audit_run() {
  label="$1"; state="$2"; shift 2
  extra=()
  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    extra+=("$1"); shift
  done
  shift
  env HERDR_SOCKET_PATH="$socket" FAKE_HERDR_FIXTURES="$work/herdr" \
    CLAUDE_CONFIG_DIR="$work/stores/$state/claude" CODEX_HOME="$work/stores/$state/codex" \
    FAKE_GH_STATE="$work/gh-$state.json" FAKE_GH_LOG="$work/gh.log" \
    ${extra[@]+"${extra[@]}"} "$sandbox/herdr-setup" "$@" >"$work/out_$label" 2>"$work/err_$label"
  rc=$?
}

section() {  # section <label> <heading>: that section's lines, heading included
  awk -v h="$2" '$0 == h { on = 1 } on && $0 == "" { exit } on { print }' "$work/out_$1"
}

# (5a) findings: exit 1, all three sections, the rows the sandbox implies.
audit_run findings findings -- audit
assert_status "findings in section 2 or 3 exit 1" 1 "$rc"
out="$(cat "$work/out_findings")"
assert_contains "section 1 heading" "$out" "open in Herdr"
assert_contains "section 2 heading" "$out" "closed, with unmerged branches"
assert_contains "section 3 heading" "$out" "open PRs no session is working on"
assert_contains "the open pane's branch is in section 1" \
  "$(section findings "open in Herdr")" "feat/open-work"
assert_contains "a pane whose session is not in history says so" \
  "$(section findings "open in Herdr")" "session not found in history"
assert_contains "a pane with no session says so" \
  "$(section findings "open in Herdr")" "session not reported to Herdr"
closed="$(section findings "closed, with unmerged branches")"
assert_contains "the stranded branch is in section 2" "$closed" "feat/stranded"
assert_contains "with the older session counted" "$closed" "+1"
assert_eq "the open pane's branch is not stranded" "" "$(printf '%s\n' "$closed" | grep 'feat/open-work')"
prs="$(section findings "open PRs no session is working on")"
assert_contains "the unmatched draft is in section 3" "$prs" "#31"
assert_contains "and labelled a draft" "$prs" "draft"
assert_eq "the bot pull request is hidden" "" "$(printf '%s\n' "$prs" | grep 'deps/bump')"
assert_eq "the squash-merged branch is listed nowhere" "" "$(grep 'feat/squashed' "$work/out_findings")"
assert_contains "the footer counts the gone branch and the hidden bot" "$out" \
  "not listed: 1 branch gone, 0 unresolved, 1 bot PR hidden."

# (5b) --dry-run audit --json: the same answer, as one JSON object that parses
# and whose counts and section sizes equal the text run's.
audit_run json findings -- --dry-run audit --json
assert_status "--dry-run audit --json exits as audit does" 1 "$rc"
uv run --quiet --script "$test_dir/helpers/audit_e2e.py" \
  check "$work/out_findings" "$work/out_json" >"$work/check.log" 2>&1
check_rc=$?
assert_status "the JSON parses and agrees with the text run: $(cat "$work/check.log")" 0 "$check_rc"

# (5c) a clean state: nothing stranded, no unmatched pull request -> exit 0.
audit_run clean clean -- audit --owner example-org
assert_status "a clean state exits 0" 0 "$rc"
assert_contains "section 2 is empty" "$(section clean "closed, with unmerged branches")" "(none)"
assert_contains "section 3 is empty" "$(section clean "open PRs no session is working on")" "(none)"

# (5d) an adapter that cannot answer: the report still prints, marked
# incomplete, and exits 2 even though it would otherwise be clean.
cat > "$sandbox/adapters/zz-broken" <<'ADAPTER'
#!/bin/sh
case "$1" in
  probe) echo '{"agent":"broken","source":"herdr:broken","available":true,"confidence":"heuristic","sessions":true}' ;;
  sessions) echo "the store is unreadable" >&2; exit 1 ;;
  *) echo '{"results":[]}' ;;
esac
ADAPTER
chmod +x "$sandbox/adapters/zz-broken"
audit_run broken clean -- audit --owner example-org
rm -f "$sandbox/adapters/zz-broken"
assert_status "an adapter that could not answer exits 2" 2 "$rc"
assert_contains "the report still prints" "$(cat "$work/out_broken")" "open in Herdr"
assert_contains "and ends incomplete, naming the adapter" \
  "$(tail -n 1 "$work/out_broken")" "incomplete: adapter zz-broken"

# (5e) a failing gh call: exit 2, one line quoting it, and no table at all.
audit_run ghfail findings FAKE_GH_FAIL="api graphql" -- audit --owner example-org
assert_status "a failing gh call exits 2" 2 "$rc"
assert_eq "no report is printed" "" "$(cat "$work/out_ghfail")"
assert_eq "one stderr line" "1" "$(wc -l < "$work/err_ghfail" | tr -d ' ')"
assert_contains "quoting gh" "$(cat "$work/err_ghfail")" "gh api graphql"

# (5f) gh not authenticated: exit 2 before any GraphQL call.
audit_run unauth findings FAKE_GH_UNAUTH=1 FAKE_GH_LOG="$work/gh-unauth.log" -- audit
assert_status "an unauthenticated gh exits 2" 2 "$rc"
assert_eq "no report is printed" "" "$(cat "$work/out_unauth")"
assert_contains "naming the reason" "$(cat "$work/err_unauth")" "not authenticated"
assert_eq "no GraphQL call was made" "0" "$(grep -c '"graphql"' "$work/gh-unauth.log")"

# (5g) no Herdr server at all: the preflight refuses, exit 3, before audit.py
# and so before any gh call.
audit_run noserver findings HERDR_SOCKET_PATH="$work/no-such-herdr.sock" \
  FAKE_GH_LOG="$work/gh-noserver.log" -- audit
assert_status "audit refuses with no Herdr server" 3 "$rc"
assert_eq "and prints nothing on stdout" "" "$(cat "$work/out_noserver")"
if [ -e "$work/gh-noserver.log" ]; then gh_called=yes; else gh_called=no; fi
assert_eq "before any gh call" "no" "$gh_called"

# Nothing written: gh saw only read-only calls, and the repository and the
# session stores are exactly as they were.
assert_eq "gh saw no mutation" "0" "$(grep -c 'mutation' "$work/gh.log")"
assert_eq "gh saw only auth status, api user, api user/orgs and GraphQL queries" "" \
  "$(grep -vE '^\["(auth", "status"|api", "(graphql|user|--paginate", "user/orgs)")' "$work/gh.log")"
assert_eq "refs, status and the store listings are unchanged" "$fingerprint_before" "$(fingerprint)"
assert_eq "no store file was modified" "" "$(find "$work/stores" -newer "$marker")"

hs_test_report
