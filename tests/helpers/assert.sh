# tests/helpers/assert.sh
#
# Shared plain-bash assertion helpers, sourced by every tests/test_*.sh file.
# No bats, no framework: a fail() helper plus a pass/fail counter. Bash 3.2
# safe (no associative arrays, no `local -n`).
#
# A test file sources this, calls the assert_* functions, and finishes with
# hs_test_report, which prints the tally and exits 1 if anything failed.

HS_TEST_PASS=0
HS_TEST_FAIL=0

# Refuse to run at all unless herdr and gh resolve to the fakes in a
# tests/helpers directory. A test run on its own without tests/helpers first
# on PATH, or one whose fake is missing, otherwise falls through PATH to the
# host's REAL command -- which is how a RED run once created a real branch and
# fr workspace (journal p3-red-reached-real-fr). tests/run.sh checks the same
# before its loop, and feedlib.require_fakes for the Python side.
#
# Any tests/helpers directory, not only this file's own: the hygiene tests
# source a COPY of this file from a nested directory with no fakes in it,
# while the PATH they inherit still resolves to the real suite's fakes.
for hs_fake_cmd in herdr gh; do
  hs_fake_path="$(command -v "$hs_fake_cmd" 2>/dev/null || true)"
  case "$hs_fake_path" in
    */tests/helpers/"$hs_fake_cmd" | tests/helpers/"$hs_fake_cmd")
      if [ -x "$hs_fake_path" ]; then
        continue
      fi
      ;;
  esac
  echo "refusing to run: $hs_fake_cmd does not resolve to the fake in tests/helpers" \
    "(got '${hs_fake_path:-nothing}'); put tests/helpers first on PATH" >&2
  exit 2
done
unset hs_fake_cmd hs_fake_path

fail() {
  echo "FAIL: $1" >&2
  HS_TEST_FAIL=$((HS_TEST_FAIL + 1))
}

pass() {
  HS_TEST_PASS=$((HS_TEST_PASS + 1))
}

# assert_eq <description> <expected> <actual>
assert_eq() {
  if [ "$2" = "$3" ]; then
    pass
  else
    fail "$1: expected [$2], got [$3]"
  fi
}

# assert_status <description> <expected-exit-status> <actual-exit-status>
assert_status() {
  if [ "$2" -eq "$3" ]; then
    pass
  else
    fail "$1: expected exit [$2], got [$3]"
  fi
}

# assert_contains <description> <haystack> <needle>
assert_contains() {
  case "$2" in
    *"$3"*) pass ;;
    *) fail "$1: [$2] does not contain [$3]" ;;
  esac
}

# hs_test_report: print the tally and exit 1 if anything failed.
hs_test_report() {
  echo "$HS_TEST_PASS passed, $HS_TEST_FAIL failed"
  if [ "$HS_TEST_FAIL" -gt 0 ]; then
    exit 1
  fi
  exit 0
}
