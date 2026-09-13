#!/usr/bin/env bash
# shellcheck disable=SC2015
# this suite's idiom throughout: pass()/fail()
# (tests/helpers/assert.sh) never return nonzero, so "A && pass || fail C"
# cannot silently take the wrong branch.
#
# A second, narrower hygiene guard alongside tests/test_public_hygiene.sh's
# broad one. It exists because a capture can leak its owner even after every
# check in that file passes: the matrix's own `org:` value (this repository's
# real GitHub account) is not a pattern test_public_hygiene.sh's fixed regexes
# know to look for, and a GraphQL pagination cursor is base64 -- opaque to a
# plain-text grep, but it decodes, and a decoded one has been found to embed a
# real repository id.
#
# Two checks:
#
#   1. The matrix's own `org:` value (read at RUNTIME from
#      docs/acceptance/matrix.yaml, never hard-coded here -- a hard-coded
#      owner string in the guard is exactly the kind of drift the guard
#      exists to catch if this repository is ever forked or renamed) must not
#      appear, case-insensitively, in any file git tracks under
#      tests/fixtures/.
#   2. No base64 token under tests/fixtures/gh/ may decode to `cursor:v2:`
#      followed by anything other than the one sanctioned placeholder
#      payload (tests/helpers/check_fixture_cursors.py).
#
# Bash 3.2 safe (no associative arrays, no `local -n`, no `mapfile`).
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"
repo_root="$(cd "$test_dir/.." && pwd)"
cd "$repo_root" || exit 1

matrix_file="docs/acceptance/matrix.yaml"
if [ ! -r "$matrix_file" ]; then
  fail "cannot read $matrix_file: this guard has no owner to check fixtures for"
  hs_test_report
fi

org="$(sed -n 's/^org: *//p' "$matrix_file" | head -n1)"
if [ -z "$org" ]; then
  fail "$matrix_file has no 'org:' line: this guard has no owner to check fixtures for"
  hs_test_report
fi

# git ls-files is read ONCE, and this test refuses to run on a list it could
# not get -- the same reasoning tests/test_public_hygiene.sh's own file-list
# guard gives: a check that scanned zero files because git could not answer
# would report a clean bill of health having checked nothing.
tracked_fixtures="$(mktemp)"
trap 'rm -f "$tracked_fixtures"' EXIT
if ! git ls-files -- tests/fixtures/ > "$tracked_fixtures" 2>/dev/null; then
  fail "git ls-files failed in $repo_root: this test cannot see the tree it is meant to police"
  hs_test_report
fi
if [ ! -s "$tracked_fixtures" ]; then
  fail "git ls-files listed no tracked files under tests/fixtures/: the check below would scan nothing"
  hs_test_report
fi

# --- check 1: the matrix's own owner, in any tracked fixture ---
hits="$(while read -r f; do
          [ -f "$f" ] || continue
          grep -HniF "$org" "$f" 2>/dev/null
        done < "$tracked_fixtures")"
hits="$(printf '%s' "$hits" | sed '/^$/d' | head -5)"
if [ -z "$hits" ]; then
  pass
else
  fail "the matrix's own owner ('$org') appears in a tracked fixture:
$hits"
fi

# --- check 2: a gh fixture cursor decoding to more than the placeholder ---
if [ -d tests/fixtures/gh ]; then
  if command -v uv >/dev/null 2>&1; then
    cursor_out="$(uv run --quiet --script "$test_dir/helpers/check_fixture_cursors.py" tests/fixtures/gh 2>&1)"
    cursor_rc=$?
    if [ "$cursor_rc" -eq 0 ]; then
      pass
    else
      fail "a gh fixture cursor decodes to more than the sanctioned placeholder:
$cursor_out"
    fi
  else
    fail "uv is not on PATH: cannot run the cursor-decode check, and a guard that cannot reach its evidence must not report the safe answer"
  fi
fi

# --- guard on the guard: this file must FAIL against a poisoned copy, the
# same proof tests/test_public_hygiene.sh's own nested self-test gives.
# HS_FIXTURE_HYGIENE_NESTED stops the copy from doing this again. ---
if [ "${HS_FIXTURE_HYGIENE_NESTED:-0}" != "1" ]; then
  hs_test_nested_owner_hit() {
    local nested
    nested="$(mktemp -d)"
    mkdir -p "$nested/tests/helpers" "$nested/tests/fixtures/gh" "$nested/docs/acceptance"
    cp "$test_dir/helpers/assert.sh" "$nested/tests/helpers/assert.sh"
    cp "$test_dir/helpers/check_fixture_cursors.py" "$nested/tests/helpers/check_fixture_cursors.py"
    cp "$test_dir/test_fixture_owner_hygiene.sh" "$nested/tests/test_fixture_owner_hygiene.sh"
    printf 'org: poisoned-owner\nrepo: poisoned-repo\nrows: []\n' > "$nested/docs/acceptance/matrix.yaml"
    printf '{"owner":"Poisoned-Owner"}\n' > "$nested/tests/fixtures/gh/poisoned.json"
    ( cd "$nested" \
        && git init -q \
        && git -c user.name="t" -c user.email="t@example.invalid" -c commit.gpgsign=false add -A \
        && git -c user.name="t" -c user.email="t@example.invalid" -c commit.gpgsign=false commit -q -m poison \
        && HS_FIXTURE_HYGIENE_NESTED=1 "${BASH:-bash}" tests/test_fixture_owner_hygiene.sh
    ) >/dev/null 2>&1
    local nested_status=$?
    rm -rf "$nested"
    return "$nested_status"
  }

  hs_test_nested_owner_hit
  nested_owner_status=$?
  [ "$nested_owner_status" -ne 0 ] && pass \
    || fail "the guard passed on a poisoned fixture carrying the matrix's own owner (case-differently, too)"

  hs_test_nested_cursor_hit() {
    local nested
    nested="$(mktemp -d)"
    mkdir -p "$nested/tests/helpers" "$nested/tests/fixtures/gh" "$nested/docs/acceptance"
    cp "$test_dir/helpers/assert.sh" "$nested/tests/helpers/assert.sh"
    cp "$test_dir/helpers/check_fixture_cursors.py" "$nested/tests/helpers/check_fixture_cursors.py"
    cp "$test_dir/test_fixture_owner_hygiene.sh" "$nested/tests/test_fixture_owner_hygiene.sh"
    printf 'org: example-user\nrepo: example-repo\nrows: []\n' > "$nested/docs/acceptance/matrix.yaml"
    # base64("cursor:v2:1234567890") -- a synthetic non-placeholder payload,
    # never a real one, the same shape the real leak this guard was written
    # for had (base64("cursor:v2:<real-id>")).
    printf '{"endCursor":"Y3Vyc29yOnYyOjEyMzQ1Njc4OTA="}\n' > "$nested/tests/fixtures/gh/poisoned.json"
    ( cd "$nested" \
        && git init -q \
        && git -c user.name="t" -c user.email="t@example.invalid" -c commit.gpgsign=false add -A \
        && git -c user.name="t" -c user.email="t@example.invalid" -c commit.gpgsign=false commit -q -m poison \
        && HS_FIXTURE_HYGIENE_NESTED=1 "${BASH:-bash}" tests/test_fixture_owner_hygiene.sh
    ) >/dev/null 2>&1
    local nested_status=$?
    rm -rf "$nested"
    return "$nested_status"
  }

  hs_test_nested_cursor_hit
  nested_cursor_status=$?
  [ "$nested_cursor_status" -ne 0 ] && pass \
    || fail "the guard passed on a poisoned fixture whose cursor decodes to more than the placeholder"
fi

hs_test_report
