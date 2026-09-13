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

# ---------------------------------------------------------------------
# 1. no ~/.local/bin -> created, link made, stdout names both, exit 0
# ---------------------------------------------------------------------
entry="$(hs_test_sandbox)"
home="$work/home1"
mkdir -p "$home"
out="$work/out1"; err="$work/err1"
status="$(run_install "$entry" "$home" "$home/.local/bin:/usr/bin:/bin" "$out" "$err")"
assert_status "1: no ~/.local/bin: install exits 0" 0 "$status"
assert_contains "1: install names the created directory" "$(cat "$out")" "$home/.local/bin"
assert_contains "1: install names the link and target" "$(cat "$out")" "installed: $home/.local/bin/herdr-setup -> $entry"
link_target="$(readlink "$home/.local/bin/herdr-setup" 2>/dev/null || true)"
assert_eq "1: the link points at the sandbox entrypoint" "$entry" "$link_target"

# ---------------------------------------------------------------------
# 2. second run -> "already installed:" and exit 0, link unchanged
# ---------------------------------------------------------------------
out="$work/out2"; err="$work/err2"
status="$(run_install "$entry" "$home" "$home/.local/bin:/usr/bin:/bin" "$out" "$err")"
assert_status "2: second run exits 0" 0 "$status"
assert_contains "2: second run says already installed" "$(cat "$out")" "already installed: $home/.local/bin/herdr-setup"
link_target2="$(readlink "$home/.local/bin/herdr-setup" 2>/dev/null || true)"
assert_eq "2: the link is unchanged" "$link_target" "$link_target2"

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

hs_test_report
