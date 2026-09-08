#!/usr/bin/env bash
# tests/run.sh
#
# Finds every tests/test_*.sh and tests/test_*.py next to this script, runs
# each one with a fresh HOME (a new mktemp -d, so no test can touch the
# operator's real home directory), PATH prefixed with tests/helpers (so
# `herdr` resolves to the fake one), and every HERDR_* variable cleared.
# Exits non-zero if any test file fails.
#
# A .sh file runs under the same bash that is running this script (see below).
# A .py file is a PEP 723 uv script and runs under `uv run --script`, the same
# door lib/hs.py and lib/feed.py are reached through: Python comes from uv,
# not from the host (AGENTS.md), so the suite must not reach for a host
# interpreter either. Both kinds get the identical environment.
#
# Clearing HERDR_* matters more than it looks. Herdr exports HERDR_ENV,
# HERDR_SOCKET_PATH, HERDR_PANE_ID and friends into every process it starts,
# so a suite run from inside a Herdr pane inherits the operator's LIVE
# socket. A test that means to exercise the "no server" path then finds a
# real one and quietly proves nothing. Tests that need such a variable set
# it themselves. The FAKE_HERDR_* switches are cleared for the same reason
# from the other direction: one left set in the developer's own shell would
# silently reconfigure the fake herdr under every test file.
#
# This script is self-contained by design: copying it, tests/helpers/, and
# a set of test_*.sh files anywhere runs the same way, which is what lets
# tests/test_harness.sh exercise it against a throwaway nested suite.
#
# Each test file runs under the SAME bash that is running this script, via
# $BASH, rather than whatever `bash` resolves to on PATH. That is the whole
# point: `/bin/bash tests/run.sh` then exercises the declared 3.2 floor end to
# end. Invoking a bare `bash` here silently ran every test on the newest bash
# installed, which is how a tool that could not start at all on 3.2 kept a green
# suite for five phases.
#
# Bash 3.2 safe: no associative arrays, no mapfile, no `local -n`.
set -u

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
helpers_dir="$script_dir/helpers"

total=0
failed=0

for test_file in "$script_dir"/test_*.sh "$script_dir"/test_*.py; do
  [ -e "$test_file" ] || continue
  total=$((total + 1))
  name="$(basename "$test_file")"
  tmp_home="$(mktemp -d)"

  case "$test_file" in
    *.py) runner=(uv run --quiet --script "$test_file") ;;
    *)    runner=("${BASH:-bash}" "$test_file") ;;
  esac

  if env -u HERDR_ENV -u HERDR_SOCKET_PATH -u HERDR_BIN_PATH -u HERDR_PANE_ID \
         -u HERDR_TAB_ID -u HERDR_WORKSPACE_ID -u HERDR_CONFIG_DIR \
         -u FAKE_HERDR_LOG -u FAKE_HERDR_FIXTURES -u FAKE_HERDR_PROTOCOL_MISMATCH \
         -u FAKE_HERDR_ERROR_CODE -u FAKE_HERDR_ERROR_STREAM -u FAKE_HERDR_FAIL \
         -u FAKE_HERDR_STDERR_NOTE -u FAKE_HERDR_PROMPT -u FAKE_HERDR_ERROR_EXIT \
         HOME="$tmp_home" PATH="$helpers_dir:$PATH" "${runner[@]}"; then
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
