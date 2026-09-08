#!/usr/bin/env bash
# Guards the runner's environment isolation.
#
# Herdr exports HERDR_ENV, HERDR_SOCKET_PATH, HERDR_PANE_ID and friends into
# every process it starts. A suite run from inside a Herdr pane therefore
# inherits the operator's LIVE socket unless the runner clears them, and a test
# meaning to exercise the "no server" path finds a real server and quietly
# proves nothing. This file fails if that isolation ever regresses.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$test_dir/helpers/assert.sh"

for var in HERDR_ENV HERDR_SOCKET_PATH HERDR_BIN_PATH HERDR_PANE_ID \
           HERDR_TAB_ID HERDR_WORKSPACE_ID HERDR_CONFIG_DIR; do
  if [ -z "${!var:-}" ]; then
    pass
  else
    fail "$var leaked into the test environment"
  fi
done

# HOME is a throwaway, so nothing a test writes can reach the real one.
case "$HOME" in
  /tmp/*|/var/folders/*|/private/var/folders/*) pass ;;
  *) fail "HOME is not a temporary directory: $HOME" ;;
esac

# And the fake herdr, not the real one, is what `herdr` resolves to.
resolved="$(command -v herdr || true)"
case "$resolved" in
  */tests/helpers/herdr) pass ;;
  *) fail "herdr resolves outside tests/helpers: $resolved" ;;
esac

hs_test_report
