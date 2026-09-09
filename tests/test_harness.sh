#!/usr/bin/env bash
# Tests for tests/helpers/fake-herdr and tests/run.sh. Neither exists yet;
# this file is expected to fail (tests/run.sh itself is missing) until
# P1.T1.S2 writes them.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$test_dir/.." && pwd)"
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fake_herdr="$test_dir/helpers/fake-herdr"

# --- (a) fake-herdr logs its full argv, one line per call, and serves the
# matching fixture file when one exists ---

log="$work/herdr.log"
fixtures="$work/fixtures"
mkdir -p "$fixtures"
echo '{"result":{"plugins":[]}}' > "$fixtures/plugin->list.json"

out="$(FAKE_HERDR_LOG="$log" FAKE_HERDR_FIXTURES="$fixtures" "$fake_herdr" plugin list)"
status=$?
assert_status "fake-herdr plugin list exits 0" 0 "$status"
assert_eq "fake-herdr serves the matching fixture" '{"result":{"plugins":[]}}' "$out"
assert_eq "fake-herdr logs its argv" "plugin list" "$(cat "$log" 2>/dev/null)"

out2="$(FAKE_HERDR_LOG="$log" FAKE_HERDR_FIXTURES="$fixtures" "$fake_herdr" agent list)"
assert_eq "fake-herdr with no matching fixture prints nothing" "" "$out2"
assert_eq "fake-herdr appends rather than overwrites the log" \
  "$(printf 'plugin list\nagent list')" "$(cat "$log" 2>/dev/null)"

# --- (b) FAKE_HERDR_PROTOCOL_MISMATCH=1 makes every subcommand fail with a
# protocol_mismatch error object, except `--version` and `integration status` ---

mismatch_out="$(FAKE_HERDR_PROTOCOL_MISMATCH=1 "$fake_herdr" agent list)"
mismatch_status=$?
assert_status "mismatched fake-herdr exits 1 on an ordinary subcommand" 1 "$mismatch_status"
assert_contains "mismatched fake-herdr names the protocol_mismatch code" \
  "$mismatch_out" '"code":"protocol_mismatch"'

version_status=$(FAKE_HERDR_PROTOCOL_MISMATCH=1 "$fake_herdr" --version >/dev/null 2>&1; echo $?)
assert_status "--version still succeeds under mismatch" 0 "$version_status"

integration_status=$(FAKE_HERDR_PROTOCOL_MISMATCH=1 "$fake_herdr" integration status >/dev/null 2>&1; echo $?)
assert_status "integration status still succeeds under mismatch" 0 "$integration_status"

# --- (c) tests/run.sh finds every tests/test_*.sh, runs each with a fresh
# HOME and PATH prefixed by tests/helpers, and exits non-zero if any fails ---

suite="$work/suite/tests"
mkdir -p "$suite/helpers"

missing_runner=1
if [ -f "$repo_root/tests/run.sh" ] && [ -f "$repo_root/tests/helpers/fake-herdr" ] \
  && [ -f "$repo_root/tests/helpers/assert.sh" ]; then
  missing_runner=0
  cp "$repo_root/tests/run.sh" "$suite/run.sh"
  cp "$repo_root/tests/helpers/fake-herdr" "$suite/helpers/fake-herdr"
  cp "$repo_root/tests/helpers/assert.sh" "$suite/helpers/assert.sh"
  ln -s fake-herdr "$suite/helpers/herdr"
  chmod +x "$suite/run.sh" "$suite/helpers/fake-herdr"
fi

if [ "$missing_runner" -eq 1 ]; then
  fail "tests/run.sh (or its helpers) does not exist yet"
else
  outer_home="$HOME"
  capture="$work/capture"
  export HS_TEST_CAPTURE="$capture"

  cat > "$suite/test_env.sh" <<'INNER'
#!/usr/bin/env bash
set -u
test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"
{
  echo "HOME=$HOME"
  echo "HERDR=$(command -v herdr)"
} > "$HS_TEST_CAPTURE"
assert_eq "inner test runs at all" "1" "1"
hs_test_report
INNER
  chmod +x "$suite/test_env.sh"

  bash "$suite/run.sh" >/dev/null 2>&1
  run_status=$?
  assert_status "run.sh exits 0 when every discovered test passes" 0 "$run_status"

  captured="$(cat "$capture" 2>/dev/null || true)"
  captured_home="$(printf '%s\n' "$captured" | sed -n 's/^HOME=//p')"
  captured_herdr="$(printf '%s\n' "$captured" | sed -n 's/^HERDR=//p')"

  # run.sh removes the temp HOME it made once the test finishes, so by now
  # the directory is gone; check its shape (fresh, absolute, distinct from
  # the outer HOME) rather than its continued existence.
  case "$captured_home" in
    /*) home_is_absolute=1 ;;
    *) home_is_absolute=0 ;;
  esac
  if [ -n "$captured_home" ] && [ "$captured_home" != "$outer_home" ] && [ "$home_is_absolute" -eq 1 ]; then
    pass
  else
    fail "run.sh set HOME to a fresh absolute path (got [$captured_home], outer was [$outer_home])"
  fi
  assert_eq "run.sh prefixes PATH with tests/helpers" "$suite/helpers/herdr" "$captured_herdr"

  # a failing discovered test flips run.sh's own exit status
  cat > "$suite/test_broken.sh" <<'INNER'
#!/usr/bin/env bash
set -u
test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"
assert_eq "deliberately wrong, to prove run.sh propagates failure" "a" "b"
hs_test_report
INNER
  chmod +x "$suite/test_broken.sh"

  bash "$suite/run.sh" >/dev/null 2>&1
  broken_status=$?
  assert_status "run.sh exits non-zero when a discovered test fails" 1 "$broken_status"
fi

hs_test_report
