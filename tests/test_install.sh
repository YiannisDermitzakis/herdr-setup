#!/usr/bin/env bash
# shellcheck disable=SC2015
# this suite's idiom throughout: pass()/fail()
# (tests/helpers/assert.sh) never return nonzero, so "A && pass || fail C"
# cannot silently take the wrong branch.
#
# `herdr-setup install` -- puts this checkout's entrypoint on PATH by
# symlink into ~/.local/bin (design doc, Commands > install). It needs no
# Herdr, no server, and no uv, so every case here is free of the fake herdr
# entirely -- the whole point of the command.
#
# Bash 3.2 safe (no associative arrays, no `local -n`, no `mapfile`) and run
# under `/bin/bash` explicitly (tests/run.sh's own floor-proving invocation),
# per the design doc's own portability section.
#
# Each case builds its own sandbox checkout (a copy of the entrypoint and
# lib/common.sh, so nothing here is ever written to this checkout) and its
# own fresh $HOME, and runs with an explicit PATH.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"

# Resolved with `cd -P` so every path built under $work already matches what
# hs_resolve_root/hs_resolve_path (both `cd -P`-based) will report back --
# on macOS, mktemp -d hands out a path under /var/folders, and /var is
# itself a symlink to /private/var. Comparing an unresolved $work path
# against the entrypoint's own physically-resolved $target failed on every
# assertion below for that reason alone, not because install did anything
# wrong.
work="$(cd -P "$(mktemp -d)" && pwd)"
trap 'rm -rf "$work"' EXIT

sandbox_n=0
# hs_test_sandbox: a fresh copy of the entrypoint (+ lib/common.sh) under its
# own directory, so one case's install (or refusal) never touches another's,
# and never touches this checkout. Prints the sandbox's own herdr-setup path.
hs_test_sandbox() {
  sandbox_n=$((sandbox_n + 1))
  local dir="$work/sandbox-$sandbox_n"
  mkdir -p "$dir/lib"
  cp "$repo_root/herdr-setup" "$dir/herdr-setup"
  cp "$repo_root/lib/common.sh" "$dir/lib/common.sh"
  chmod +x "$dir/herdr-setup"
  printf '%s/herdr-setup\n' "$dir"
}

# run_install <entry> <home> <path> <out> <err> [args...]
run_install() {
  local entry="$1" home="$2" path_val="$3" out="$4" err="$5"
  shift 5
  HOME="$home" PATH="$path_val" "$entry" install "$@" >"$out" 2>"$err"
  echo $?
}

# run_install_watchdog <entry> <home> <path> <out> <err> [args...]: same as
# run_install, but for a case whose whole POINT is a symlink loop -- a
# regression in hs_resolve_path's hop cap would hang, not fail, and a hang
# here would hang the entire suite rather than turn one assertion red.
#
# This is a POLLING loop (kill -0 "$pid" every 0.1s, up to 10s, then a force
# kill), not a separate "sleep 10 & kill -9 $pid" watchdog job. That first
# approach was tried and measured broken: every call site here invokes this
# function through a command substitution (`status="$(run_install_watchdog
# ...)"`), and inside that subshell context, killing the WRAPPING watchdog
# subshell did not reliably reach the `sleep 10` it was blocked in -- `wait
# "$watchdog"` then blocked until that sleep finished naturally regardless
# of how quickly install itself returned, so every case using it took the
# full 10s. A polling loop in THIS shell has no second job whose signal
# delivery can misbehave, in any subshell context.
#
# The RC this prints is the forced kill's (never 4) when the timeout is
# reached, so a regression is a normal failed assertion, not a stuck run.
run_install_watchdog() {
  local entry="$1" home="$2" path_val="$3" out="$4" err="$5"
  shift 5
  ( HOME="$home" PATH="$path_val" "$entry" install "$@" >"$out" 2>"$err" ) &
  local pid=$!

  local waited=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge 100 ]; then
      kill -9 "$pid" 2>/dev/null
      break
    fi
    sleep 0.1
    waited=$((waited + 1))
  done

  local rc=0
  wait "$pid" 2>/dev/null || rc=$?
  echo "$rc"
}

# ---------------------------------------------------------------------
# 1. no ~/.local/bin -> created, link made, stdout names both, exit 0
# ---------------------------------------------------------------------
entry="$(hs_test_sandbox)"
home="$work/home1"
mkdir -p "$home"
out="$work/out1"; err="$work/err1"
status="$(run_install "$entry" "$home" "$home/.local/bin:/usr/bin:/bin" "$out" "$err")"
assert_status "1: no ~/.local/bin: install exits 0" 0 "$status"
assert_contains "1: install prints the actual 'created:' message" "$(cat "$out")" "created: $home/.local/bin"
assert_contains "1: install names the link and target" "$(cat "$out")" "installed: $home/.local/bin/herdr-setup -> $entry"
link_target="$(readlink "$home/.local/bin/herdr-setup" 2>/dev/null || true)"
assert_eq "1: the link points at the sandbox entrypoint" "$entry" "$link_target"
# shellcheck disable=SC2012
link_inode="$(ls -i "$home/.local/bin/herdr-setup" | awk '{print $1}')"

# ---------------------------------------------------------------------
# 2. second run -> "already installed:" and exit 0, link unchanged (same
#    inode, not just the same readlink string -- the inode is what proves
#    nothing removed and recreated the link behind an unchanged readlink)
# ---------------------------------------------------------------------
out="$work/out2"; err="$work/err2"
status="$(run_install "$entry" "$home" "$home/.local/bin:/usr/bin:/bin" "$out" "$err")"
assert_status "2: second run exits 0" 0 "$status"
assert_contains "2: second run says already installed" "$(cat "$out")" "already installed: $home/.local/bin/herdr-setup"
link_target2="$(readlink "$home/.local/bin/herdr-setup" 2>/dev/null || true)"
assert_eq "2: readlink is unchanged" "$link_target" "$link_target2"
# shellcheck disable=SC2012
link_inode2="$(ls -i "$home/.local/bin/herdr-setup" | awk '{print $1}')"
assert_eq "2: the link's inode is unchanged (nothing was removed and recreated)" "$link_inode" "$link_inode2"

# ---------------------------------------------------------------------
# 3. link to another file -> exit 4, stderr names what it points at, link
#    unchanged; same for a dangling link
# ---------------------------------------------------------------------
entry3="$(hs_test_sandbox)"
home3="$work/home3"
mkdir -p "$home3/.local/bin"
decoy="$work/decoy-entrypoint"
: > "$decoy"
ln -s "$decoy" "$home3/.local/bin/herdr-setup"
out="$work/out3"; err="$work/err3"
status="$(run_install "$entry3" "$home3" "/usr/bin:/bin" "$out" "$err")"
assert_status "3: a symlink to another file is refused" 4 "$status"
assert_contains "3: stderr names what it points at" "$(cat "$err")" "$decoy"
assert_eq "3: nothing was written to stdout" "" "$(cat "$out")"
link_after="$(readlink "$home3/.local/bin/herdr-setup" 2>/dev/null || true)"
assert_eq "3: the link is unchanged" "$decoy" "$link_after"

home3b="$work/home3b"
mkdir -p "$home3b/.local/bin"
dangling="$work/does-not-exist"
ln -s "$dangling" "$home3b/.local/bin/herdr-setup"
out="$work/out3b"; err="$work/err3b"
status="$(run_install "$entry3" "$home3b" "/usr/bin:/bin" "$out" "$err")"
assert_status "3b: a dangling symlink is refused" 4 "$status"
assert_contains "3b: stderr names what it points at" "$(cat "$err")" "$dangling"
link_after_b="$(readlink "$home3b/.local/bin/herdr-setup" 2>/dev/null || true)"
assert_eq "3b: the dangling link is unchanged" "$dangling" "$link_after_b"

# ---------------------------------------------------------------------
# 4. regular file there -> exit 4, file byte-identical
# ---------------------------------------------------------------------
entry4="$(hs_test_sandbox)"
home4="$work/home4"
mkdir -p "$home4/.local/bin"
printf 'not an entrypoint, do not touch\n' > "$home4/.local/bin/herdr-setup"
out="$work/out4"; err="$work/err4"
status="$(run_install "$entry4" "$home4" "/usr/bin:/bin" "$out" "$err")"
assert_status "4: a regular file is refused" 4 "$status"
assert_contains "4: stderr names it" "$(cat "$err")" "$home4/.local/bin/herdr-setup"
if cmp -s <(printf 'not an entrypoint, do not touch\n') "$home4/.local/bin/herdr-setup"; then
  pass
else
  fail "4: the regular file was not left byte-identical"
fi

# ---------------------------------------------------------------------
# 5. a directory there -> exit 4
# ---------------------------------------------------------------------
entry5="$(hs_test_sandbox)"
home5="$work/home5"
mkdir -p "$home5/.local/bin/herdr-setup"
out="$work/out5"; err="$work/err5"
status="$(run_install "$entry5" "$home5" "/usr/bin:/bin" "$out" "$err")"
assert_status "5: a directory is refused" 4 "$status"
assert_contains "5: stderr names it" "$(cat "$err")" "$home5/.local/bin/herdr-setup"

# ---------------------------------------------------------------------
# 6. ~/.local/bin is a regular file -> exit 2
# ---------------------------------------------------------------------
entry6="$(hs_test_sandbox)"
home6="$work/home6"
mkdir -p "$home6/.local"
printf 'not a directory\n' > "$home6/.local/bin"
out="$work/out6"; err="$work/err6"
status="$(run_install "$entry6" "$home6" "/usr/bin:/bin" "$out" "$err")"
assert_status "6: ~/.local/bin being a regular file exits 2" 2 "$status"
assert_contains "6: stderr names it" "$(cat "$err")" "$home6/.local/bin"

# ---------------------------------------------------------------------
# 7. --dry-run with nothing present -> prints the two '+' lines, creates
#    nothing, exit 0
# ---------------------------------------------------------------------
entry7="$(hs_test_sandbox)"
home7="$work/home7"
mkdir -p "$home7"
out="$work/out7"; err="$work/err7"
status="$(run_install "$entry7" "$home7" "/usr/bin:/bin" "$out" "$err" --dry-run)"
assert_status "7: --dry-run with nothing present exits 0" 0 "$status"
assert_contains "7: it prints the mkdir it would run" "$(cat "$out")" "+ mkdir -p $home7/.local/bin"
assert_contains "7: it prints the ln it would run" "$(cat "$out")" "+ ln -s $entry7 $home7/.local/bin/herdr-setup"
[ ! -e "$home7/.local/bin" ] && pass || fail "7: --dry-run created $home7/.local/bin"

# ---------------------------------------------------------------------
# 8. --dry-run with a regular file there -> exit 4, nothing written
# ---------------------------------------------------------------------
entry8="$(hs_test_sandbox)"
home8="$work/home8"
mkdir -p "$home8/.local/bin"
printf 'do not touch\n' > "$home8/.local/bin/herdr-setup"
out="$work/out8"; err="$work/err8"
status="$(run_install "$entry8" "$home8" "/usr/bin:/bin" "$out" "$err" --dry-run)"
assert_status "8: --dry-run against a regular file is refused" 4 "$status"
if cmp -s <(printf 'do not touch\n') "$home8/.local/bin/herdr-setup"; then
  pass
else
  fail "8: --dry-run left the regular file changed"
fi
case "$(cat "$out")" in
  *"+ ln"*) fail "8: --dry-run printed a '+ ln' line for a refused install: $(cat "$out")" ;;
  *) pass ;;
esac

# ---------------------------------------------------------------------
# 9. PATH warning: absent -> warning naming it; present, with and without
#    a trailing slash -> no warning
# ---------------------------------------------------------------------
entry9a="$(hs_test_sandbox)"
home9a="$work/home9a"
mkdir -p "$home9a"
out="$work/out9a"; err="$work/err9a"
status="$(run_install "$entry9a" "$home9a" "/usr/bin:/bin" "$out" "$err")"
assert_status "9a: install still succeeds" 0 "$status"
assert_contains "9a: PATH warning names the directory" "$(cat "$err")" "$home9a/.local/bin"

entry9b="$(hs_test_sandbox)"
home9b="$work/home9b"
mkdir -p "$home9b"
out="$work/out9b"; err="$work/err9b"
status="$(run_install "$entry9b" "$home9b" "$home9b/.local/bin:/usr/bin:/bin" "$out" "$err")"
assert_status "9b: install still succeeds" 0 "$status"
case "$(cat "$err")" in
  *"$home9b/.local/bin"*"PATH"*) fail "9b: PATH warning fired though the directory is on PATH: $(cat "$err")" ;;
  *) pass ;;
esac

entry9c="$(hs_test_sandbox)"
home9c="$work/home9c"
mkdir -p "$home9c"
out="$work/out9c"; err="$work/err9c"
status="$(run_install "$entry9c" "$home9c" "$home9c/.local/bin/:/usr/bin:/bin" "$out" "$err")"
assert_status "9c: install still succeeds" 0 "$status"
case "$(cat "$err")" in
  *"$home9c/.local/bin"*"PATH"*) fail "9c: PATH warning fired though the directory is on PATH (trailing slash): $(cat "$err")" ;;
  *) pass ;;
esac

# ---------------------------------------------------------------------
# 10. another herdr-setup earlier on PATH -> shadowing warning naming it;
#     exit status unchanged
# ---------------------------------------------------------------------
entry10="$(hs_test_sandbox)"
home10="$work/home10"
mkdir -p "$home10"
decoy_dir="$work/decoy-bin"
mkdir -p "$decoy_dir"
cat > "$decoy_dir/herdr-setup" <<'EOF'
#!/bin/sh
echo "not the real thing"
EOF
chmod +x "$decoy_dir/herdr-setup"
out="$work/out10"; err="$work/err10"
status="$(run_install "$entry10" "$home10" "$decoy_dir:$home10/.local/bin:/usr/bin:/bin" "$out" "$err")"
assert_status "10: install still succeeds despite the shadow" 0 "$status"
assert_contains "10: shadowing warning names the earlier one" "$(cat "$err")" "$decoy_dir/herdr-setup"

# --- 10b: the SAME decoy, but AFTER ~/.local/bin on PATH -> no warning ---
entry10b="$(hs_test_sandbox)"
home10b="$work/home10b"
mkdir -p "$home10b"
out="$work/out10b"; err="$work/err10b"
status="$(run_install "$entry10b" "$home10b" "$home10b/.local/bin:$decoy_dir:/usr/bin:/bin" "$out" "$err")"
assert_status "10b: install succeeds" 0 "$status"
case "$(cat "$err")" in
  *"shadows"*) fail "10b: a later herdr-setup on PATH warned as if it shadowed: $(cat "$err")" ;;
  *) pass ;;
esac

# --- 10c: an EARLIER herdr-setup that resolves to the SAME entrypoint
# (another link to it) -> no warning; it is the same tool, not a shadow ---
entry10c="$(hs_test_sandbox)"
home10c="$work/home10c"
mkdir -p "$home10c"
decoy_dir_c="$work/decoy-bin-c"
mkdir -p "$decoy_dir_c"
ln -s "$entry10c" "$decoy_dir_c/herdr-setup"
out="$work/out10c"; err="$work/err10c"
status="$(run_install "$entry10c" "$home10c" "$decoy_dir_c:$home10c/.local/bin:/usr/bin:/bin" "$out" "$err")"
assert_status "10c: install succeeds" 0 "$status"
case "$(cat "$err")" in
  *"shadows"*) fail "10c: an earlier link to the SAME entrypoint warned as a shadow: $(cat "$err")" ;;
  *) pass ;;
esac

# --- 10d: an earlier, genuinely different herdr-setup, under --dry-run ->
# the warning still fires (a dry run says what a real run would do) ---
entry10d="$(hs_test_sandbox)"
home10d="$work/home10d"
mkdir -p "$home10d"
out="$work/out10d"; err="$work/err10d"
status="$(run_install "$entry10d" "$home10d" "$decoy_dir:$home10d/.local/bin:/usr/bin:/bin" "$out" "$err" --dry-run)"
assert_status "10d: --dry-run still succeeds" 0 "$status"
assert_contains "10d: the shadowing warning fires under --dry-run too" "$(cat "$err")" "$decoy_dir/herdr-setup"

# ---------------------------------------------------------------------
# 11. installing by running a symlink to the sandbox entrypoint installs
#     the sandbox entrypoint's real path
# ---------------------------------------------------------------------
entry11="$(hs_test_sandbox)"
runner="$work/runner-herdr-setup"
ln -s "$entry11" "$runner"
home11="$work/home11"
mkdir -p "$home11"
out="$work/out11"; err="$work/err11"
status="$(run_install "$runner" "$home11" "/usr/bin:/bin" "$out" "$err")"
assert_status "11: install through a symlink exits 0" 0 "$status"
link_target11="$(readlink "$home11/.local/bin/herdr-setup" 2>/dev/null || true)"
assert_eq "11: it installs the sandbox entrypoint's real path, not the runner symlink" \
  "$entry11" "$link_target11"

# ---------------------------------------------------------------------
# 12. a self-loop symlink at the link path -> exit 4, promptly (not a hang)
# ---------------------------------------------------------------------
entry12="$(hs_test_sandbox)"
home12="$work/home12"
mkdir -p "$home12/.local/bin"
ln -s "$home12/.local/bin/herdr-setup" "$home12/.local/bin/herdr-setup"
out="$work/out12"; err="$work/err12"
t12_before=$(date +%s)
status="$(run_install_watchdog "$entry12" "$home12" "/usr/bin:/bin" "$out" "$err")"
t12_after=$(date +%s)
assert_status "12: a self-loop symlink is refused promptly, not hung" 4 "$status"
[ "$((t12_after - t12_before))" -lt 5 ] && pass \
  || fail "12: took $((t12_after - t12_before))s, expected well under the 10s watchdog cap (under 5s)"

# ---------------------------------------------------------------------
# 13. a two-link symlink cycle at the link path -> exit 4, promptly
# ---------------------------------------------------------------------
entry13="$(hs_test_sandbox)"
home13="$work/home13"
mkdir -p "$home13/.local/bin"
ln -s "$home13/.local/bin/herdr-setup" "$home13/.local/bin/other-link"
ln -s "$home13/.local/bin/other-link" "$home13/.local/bin/herdr-setup"
out="$work/out13"; err="$work/err13"
t13_before=$(date +%s)
status="$(run_install_watchdog "$entry13" "$home13" "/usr/bin:/bin" "$out" "$err")"
t13_after=$(date +%s)
assert_status "13: a two-link symlink cycle is refused promptly, not hung" 4 "$status"
[ "$((t13_after - t13_before))" -lt 5 ] && pass \
  || fail "13: took $((t13_after - t13_before))s, expected well under the 10s watchdog cap (under 5s)"

# ---------------------------------------------------------------------
# 14 & 15: the hs_resolve_path branch of the "already installed" test --
# a link to an INTERMEDIATE link (not the entrypoint's own readlink target),
# and a RELATIVE link to the entrypoint. Both only pass because
# hs_resolve_path actually resolves the chain; mutate_always_fail_resolve
# below proves that by breaking hs_resolve_path and watching both flip red.
# ---------------------------------------------------------------------

# mutate_always_fail_resolve <entrypoint>: rewrites <entrypoint> IN PLACE so
# its hs_resolve_path unconditionally returns 1 (an always-dangling
# resolve), inserted right after the function's opening line via awk rather
# than `sed -i`, which needs a different flag on BSD (macOS) and GNU sed and
# is not worth portability-testing for a one-off test mutation. In place,
# not a copy elsewhere, because HS_ROOT (and so `target`) is derived from
# the entrypoint's OWN path -- a copy under a different directory would
# change what "this checkout" means and refuse for the wrong reason.
mutate_always_fail_resolve() {
  local entry="$1" tmp
  tmp="$(mktemp)"
  awk '
    { print }
    /^hs_resolve_path\(\) \{$/ { print "  return 1" }
  ' "$entry" > "$tmp"
  chmod +x "$tmp"
  mv "$tmp" "$entry"
}

# --- 14: link -> intermediate-link -> entrypoint ---
entry14="$(hs_test_sandbox)"
home14="$work/home14"
mkdir -p "$home14/.local/bin"
intermediate14="$home14/.local/bin/intermediate-link"
ln -s "$entry14" "$intermediate14"
ln -s "$intermediate14" "$home14/.local/bin/herdr-setup"
out="$work/out14"; err="$work/err14"
status="$(run_install "$entry14" "$home14" "/usr/bin:/bin" "$out" "$err")"
assert_status "14: a link to an intermediate link exits 0" 0 "$status"
assert_contains "14: it reads as already installed" "$(cat "$out")" "already installed:"

# --- 15: a RELATIVE link to the entrypoint ---
entry15="$(hs_test_sandbox)"
home15="$work/home15"
mkdir -p "$home15/.local/bin"
entry15_dir="${entry15%/*}"
entry15_base="${entry15##*/}"
entry15_sandbox_name="${entry15_dir##*/}"
# $home15/.local/bin is $work/home15/.local/bin (three levels below $work);
# $entry15 is $work/<sandbox>/herdr-setup (one level below $work). Relative
# from the link's own directory: up three (bin -> .local -> home15 -> work),
# then down into the sandbox.
relative15="../../../$entry15_sandbox_name/$entry15_base"
ln -s "$relative15" "$home15/.local/bin/herdr-setup"
out="$work/out15"; err="$work/err15"
status="$(run_install "$entry15" "$home15" "/usr/bin:/bin" "$out" "$err")"
assert_status "15: a relative link to the entrypoint exits 0" 0 "$status"
assert_contains "15: it reads as already installed" "$(cat "$out")" "already installed:"

# --- mutation check: with hs_resolve_path forced to fail, both 14 and 15
# must now be refused (exit 4), proving the assertions above actually
# exercise hs_resolve_path rather than passing some other way. Mutated IN
# PLACE, after the real assertions above are done with these two sandboxes,
# so `target` (derived from the entrypoint's own path) is unchanged. ---
mutate_always_fail_resolve "$entry14"
mutate_always_fail_resolve "$entry15"

out="$work/out14mut"; err="$work/err14mut"
status="$(run_install "$entry14" "$home14" "/usr/bin:/bin" "$out" "$err")"
assert_status "14 (mutated): a broken hs_resolve_path refuses the intermediate-link case" 4 "$status"

out="$work/out15mut"; err="$work/err15mut"
status="$(run_install "$entry15" "$home15" "/usr/bin:/bin" "$out" "$err")"
assert_status "15 (mutated): a broken hs_resolve_path refuses the relative-link case" 4 "$status"

# ---------------------------------------------------------------------
# 16 & 17: "mkdir or ln fails -> 2", naming the step. Skipped under root,
# which ignores permission bits entirely and would make both directories
# writable regardless of mode -- turning a real refusal into a silent
# success and reporting nothing wrong.
# ---------------------------------------------------------------------
if [ "$(id -u)" -eq 0 ]; then
  echo "SKIP: 16/17 (mkdir/ln permission failures): running as root, which ignores permission bits" >&2
else
  # --- 16: ~/.local mode 555 -> mkdir -p ~/.local/bin fails -> exit 2 ---
  entry16="$(hs_test_sandbox)"
  home16="$work/home16"
  mkdir -p "$home16/.local"
  chmod 555 "$home16/.local"
  out="$work/out16"; err="$work/err16"
  status="$(run_install "$entry16" "$home16" "/usr/bin:/bin" "$out" "$err")"
  chmod 755 "$home16/.local"
  assert_status "16: mkdir failing under a read-only ~/.local exits 2" 2 "$status"
  # The tool's OWN wording, not just "the path appears somewhere in
  # stderr" -- mkdir's own "Permission denied" line ALSO names the path
  # (stderr here is two lines: mkdir's own, then herdr-setup's), so a
  # substring check on the whole file cannot fail even if herdr-setup's own
  # message text were replaced by something unrelated. Matched as one
  # exact, whole LINE rather than the whole file, since mkdir's own line
  # precedes it. See mutate_blank_create_message below for the proof.
  if grep -qxF "herdr-setup: install: could not create $home16/.local/bin." "$err"; then
    pass
  else
    fail "16: stderr does not carry herdr-setup's own 'could not create' line: $(cat "$err")"
  fi

  # --- 17: ~/.local/bin mode 555 -> ln -s into it fails -> exit 2 ---
  entry17="$(hs_test_sandbox)"
  home17="$work/home17"
  mkdir -p "$home17/.local/bin"
  chmod 555 "$home17/.local/bin"
  out="$work/out17"; err="$work/err17"
  status="$(run_install "$entry17" "$home17" "/usr/bin:/bin" "$out" "$err")"
  chmod 755 "$home17/.local/bin"
  assert_status "17: ln failing under a read-only ~/.local/bin exits 2" 2 "$status"
  if grep -qxF "herdr-setup: install: could not create $home17/.local/bin/herdr-setup." "$err"; then
    pass
  else
    fail "17: stderr does not carry herdr-setup's own 'could not create' line: $(cat "$err")"
  fi

  # --- mutation check: with both "could not create" messages blanked out,
  # 16 and 17's assertions above must now FAIL -- proving they actually
  # read the tool's own wording rather than passing on any stderr output
  # that happens to mention the path (which mkdir's/ln's own OS error
  # already does, on both platforms this runs on). ---
  mutate_blank_create_message() {
    local entry="$1" tmp
    tmp="$(mktemp)"
    awk '
      { gsub(/could not create \$dir\./, "refused.") }
      { gsub(/could not create \$link\./, "refused.") }
      { print }
    ' "$entry" > "$tmp"
    chmod +x "$tmp"
    mv "$tmp" "$entry"
  }

  entry16m="$(hs_test_sandbox)"
  mutate_blank_create_message "$entry16m"
  home16m="$work/home16m"
  mkdir -p "$home16m/.local"
  chmod 555 "$home16m/.local"
  out="$work/out16m"; err="$work/err16m"
  run_install "$entry16m" "$home16m" "/usr/bin:/bin" "$out" "$err" >/dev/null
  chmod 755 "$home16m/.local"
  if grep -qxF "herdr-setup: install: could not create $home16m/.local/bin." "$err"; then
    fail "16 (mutated): the literal-message assertion still matched a mutated tool, so it proves nothing"
  else
    pass
  fi

  entry17m="$(hs_test_sandbox)"
  mutate_blank_create_message "$entry17m"
  home17m="$work/home17m"
  mkdir -p "$home17m/.local/bin"
  chmod 555 "$home17m/.local/bin"
  out="$work/out17m"; err="$work/err17m"
  run_install "$entry17m" "$home17m" "/usr/bin:/bin" "$out" "$err" >/dev/null
  chmod 755 "$home17m/.local/bin"
  if grep -qxF "herdr-setup: install: could not create $home17m/.local/bin/herdr-setup." "$err"; then
    fail "17 (mutated): the literal-message assertion still matched a mutated tool, so it proves nothing"
  else
    pass
  fi
fi

# ---------------------------------------------------------------------
# 18. an unexpected argument -> exit 2, one stderr line naming it
# ---------------------------------------------------------------------
entry18="$(hs_test_sandbox)"
home18="$work/home18"
mkdir -p "$home18"
out="$work/out18"; err="$work/err18"
status="$(run_install "$entry18" "$home18" "/usr/bin:/bin" "$out" "$err" some-extra-argument)"
assert_status "18: an unexpected argument exits 2" 2 "$status"
assert_contains "18: stderr names the unexpected argument" "$(cat "$err")" "some-extra-argument"
[ ! -e "$home18/.local" ] && pass || fail "18: install wrote something despite the refusal"

# ---------------------------------------------------------------------
# 19. a PATH entry containing a literal '*' does not glob-expand into
#     unrelated directories when the shadow check walks PATH
# ---------------------------------------------------------------------
entry19="$(hs_test_sandbox)"
home19="$work/home19"
mkdir -p "$home19"
star_dir="$work/star*dir"
sibling_dir="$work/starXdir"
mkdir -p "$star_dir" "$sibling_dir"
# The star_dir PATH entry itself carries no herdr-setup at all -- only the
# GLOB-MATCHED sibling does, so a fixed (noglob) walk finds nothing there
# and moves on to $home19/.local/bin (this run's own, freshly installed
# link, which matches the target and warns nothing); a broken (globbing)
# walk expands "$work/star*dir" into BOTH directories and wrongly treats
# the sibling's decoy as a PATH entry that was never actually on PATH.
cat > "$sibling_dir/herdr-setup" <<'EOF'
#!/bin/sh
echo "decoy, glob-matched, never actually on PATH"
EOF
chmod +x "$sibling_dir/herdr-setup"
out="$work/out19"; err="$work/err19"
status="$(run_install "$entry19" "$home19" "$star_dir:$home19/.local/bin:/usr/bin:/bin" "$out" "$err")"
assert_status "19: install still succeeds" 0 "$status"
case "$(cat "$err")" in
  *"shadows"*) fail "19: a '*' PATH entry glob-expanded into an unrelated directory: $(cat "$err")" ;;
  *) pass ;;
esac

# ---------------------------------------------------------------------
# 20. the shadow warning's wording when $dir (~/.local/bin) is not on
#     PATH at all -- it must not say "ahead of" a position that does not
#     exist on PATH
# ---------------------------------------------------------------------
entry20="$(hs_test_sandbox)"
home20="$work/home20"
mkdir -p "$home20"
decoy_dir20="$work/decoy-bin-20"
mkdir -p "$decoy_dir20"
cat > "$decoy_dir20/herdr-setup" <<'EOF'
#!/bin/sh
echo "not the real thing"
EOF
chmod +x "$decoy_dir20/herdr-setup"
out="$work/out20"; err="$work/err20"
# $home20/.local/bin is deliberately NOT on this PATH.
status="$(run_install "$entry20" "$home20" "$decoy_dir20:/usr/bin:/bin" "$out" "$err")"
assert_status "20: install still succeeds" 0 "$status"
assert_contains "20: it still names the shadowing executable" "$(cat "$err")" "$decoy_dir20/herdr-setup"
case "$(cat "$err")" in
  *"ahead of"*) fail "20: worded as if \$dir were on PATH when it is not: $(cat "$err")" ;;
  *) pass ;;
esac

hs_test_report
