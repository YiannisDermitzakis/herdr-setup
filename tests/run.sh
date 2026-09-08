#!/usr/bin/env bash
# tests/run.sh
#
# Finds every tests/test_*.sh next to this script, runs each one with a
# fresh HOME (a new mktemp -d, so no test can touch the operator's real
# home directory) and PATH prefixed with tests/helpers (so `herdr` resolves
# to the fake one). Exits non-zero if any test file fails.
#
# This script is self-contained by design: copying it, tests/helpers/, and
# a set of test_*.sh files anywhere runs the same way, which is what lets
# tests/test_harness.sh exercise it against a throwaway nested suite.
#
# Bash 3.2 safe: no associative arrays, no mapfile, no `local -n`.
set -u

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
helpers_dir="$script_dir/helpers"

total=0
failed=0

for test_file in "$script_dir"/test_*.sh; do
  [ -e "$test_file" ] || continue
  total=$((total + 1))
  name="$(basename "$test_file")"
  tmp_home="$(mktemp -d)"

  if HOME="$tmp_home" PATH="$helpers_dir:$PATH" bash "$test_file"; then
    echo "PASS: $name"
  else
    echo "FAIL: $name"
    failed=$((failed + 1))
  fi

  rm -rf "$tmp_home"
done

echo "$((total - failed))/$total test files passed"

if [ "$failed" -gt 0 ]; then
  exit 1
fi
exit 0
