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
#   2. No base64 token under tests/fixtures/ (any of it, not only gh/ --
#      nothing rules out a cursor-shaped string turning up in a captured
#      transcript line) may decode to `cursor:v2:` followed by anything
#      other than the one sanctioned placeholder payload
#      (tests/helpers/check_fixture_cursors.py), padded or not: GitHub (and
#      other real captures) sometimes emit unpadded base64.
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

# --- check 2: any fixture cursor decoding to more than the placeholder,
# anywhere under tests/fixtures/ -- not scoped to gh/, since nothing rules
# out a cursor-shaped token turning up in some other capture ---
if [ -d tests/fixtures ]; then
  if command -v uv >/dev/null 2>&1; then
    cursor_out="$(uv run --quiet --script "$test_dir/helpers/check_fixture_cursors.py" tests/fixtures 2>&1)"
    cursor_rc=$?
    if [ "$cursor_rc" -eq 0 ]; then
      pass
    else
      fail "a fixture cursor decodes to more than the sanctioned placeholder:
$cursor_out"
    fi
  else
    fail "uv is not on PATH: cannot run the cursor-decode check, and a guard that cannot reach its evidence must not report the safe answer"
  fi
fi

# --- guard on the guard: this file must FAIL against a poisoned copy, the
# same proof tests/test_public_hygiene.sh's own nested self-test gives.
# HS_FIXTURE_HYGIENE_NESTED stops the copy from doing this again.
#
# A nested run's exit status alone is NOT proof: git setup (init/add/commit)
# can fail for reasons that have nothing to do with the guard -- an
# operator's global hook, a missing identity -- and a `&& chain` ending in
# the nested test invocation reports that same non-zero status either way.
# That is read as "the guard fired" whether or not it ever actually ran, so
# this must instead (1) make the nested git commands fail LOUDLY rather than
# silently inheriting ambient config (`-c core.hooksPath=/dev/null` defeats
# a global hook; explicit `-c user.name`/`-c user.email` defeats a missing
# identity), (2) run setup and the nested test as SEPARATE steps so a setup
# failure is reported as ITS OWN failure, never mistaken for the guard, and
# (3) require the nested run's OUTPUT contain the SPECIFIC `FAIL:` line the
# planted poison is expected to produce -- not just any non-zero exit.
if [ "${HS_FIXTURE_HYGIENE_NESTED:-0}" != "1" ]; then
  # hs_test_nested_git <dir>: git init + add + commit in <dir>, immune to
  # ambient global config. Prints combined output and returns git's own
  # exit status; a caller that gets non-zero here has a SETUP failure, not
  # evidence about the guard.
  hs_test_nested_git() {
    local dir="$1"
    ( cd "$dir" \
        && git -c core.hooksPath=/dev/null init -q \
        && git -c core.hooksPath=/dev/null -c user.name="t" -c user.email="t@example.invalid" add -A \
        && git -c core.hooksPath=/dev/null -c user.name="t" -c user.email="t@example.invalid" \
             -c commit.gpgsign=false commit -q -m poison
    ) 2>&1
  }

  # hs_test_nested_run <dir> <expect-pattern>: runs the nested guard in
  # <dir> and passes only when it BOTH exits non-zero AND its output
  # contains <expect-pattern> -- the specific FAIL: line the planted poison
  # is expected to produce. Anything else (git setup failing, or the nested
  # run failing for an unrelated reason) is reported as its own failure,
  # with the captured output, rather than credited to the guard.
  hs_test_nested_run() {
    local label="$1" dir="$2" expect="$3"
    local setup_out setup_rc nested_out nested_rc

    setup_out="$(hs_test_nested_git "$dir")"
    setup_rc=$?
    if [ "$setup_rc" -ne 0 ]; then
      fail "$label: nested git setup itself failed (rc=$setup_rc), so this proves nothing about the guard:
$setup_out"
      return
    fi

    nested_out="$(cd "$dir" && HS_FIXTURE_HYGIENE_NESTED=1 "${BASH:-bash}" tests/test_fixture_owner_hygiene.sh 2>&1)"
    nested_rc=$?

    if [ "$nested_rc" -eq 0 ]; then
      fail "$label: the guard passed (rc=0) on a poisoned fixture:
$nested_out"
    elif printf '%s' "$nested_out" | grep -qF "$expect"; then
      pass
    else
      fail "$label: the nested run failed (rc=$nested_rc) but not with the expected line ('$expect'):
$nested_out"
    fi
  }

  nested_owner="$(mktemp -d)"
  mkdir -p "$nested_owner/tests/helpers" "$nested_owner/tests/fixtures/gh" "$nested_owner/docs/acceptance"
  cp "$test_dir/helpers/assert.sh" "$nested_owner/tests/helpers/assert.sh"
  cp "$test_dir/helpers/check_fixture_cursors.py" "$nested_owner/tests/helpers/check_fixture_cursors.py"
  cp "$test_dir/test_fixture_owner_hygiene.sh" "$nested_owner/tests/test_fixture_owner_hygiene.sh"
  printf 'org: poisoned-owner\nrepo: poisoned-repo\nrows: []\n' > "$nested_owner/docs/acceptance/matrix.yaml"
  printf '{"owner":"Poisoned-Owner"}\n' > "$nested_owner/tests/fixtures/gh/poisoned.json"
  hs_test_nested_run "owner hit" "$nested_owner" \
    "FAIL: the matrix's own owner ('poisoned-owner') appears in a tracked fixture:"
  rm -rf "$nested_owner"

  nested_cursor="$(mktemp -d)"
  mkdir -p "$nested_cursor/tests/helpers" "$nested_cursor/tests/fixtures/gh" "$nested_cursor/docs/acceptance"
  cp "$test_dir/helpers/assert.sh" "$nested_cursor/tests/helpers/assert.sh"
  cp "$test_dir/helpers/check_fixture_cursors.py" "$nested_cursor/tests/helpers/check_fixture_cursors.py"
  cp "$test_dir/test_fixture_owner_hygiene.sh" "$nested_cursor/tests/test_fixture_owner_hygiene.sh"
  printf 'org: example-user\nrepo: example-repo\nrows: []\n' > "$nested_cursor/docs/acceptance/matrix.yaml"
  # base64("cursor:v2:1234567890") -- a synthetic non-placeholder payload,
  # never a real one, the same shape the real leak this guard was written
  # for had (base64("cursor:v2:<real-id>")).
  printf '{"endCursor":"Y3Vyc29yOnYyOjEyMzQ1Njc4OTA="}\n' > "$nested_cursor/tests/fixtures/gh/poisoned.json"
  hs_test_nested_run "cursor hit" "$nested_cursor" \
    "FAIL: a fixture cursor decodes to more than the sanctioned placeholder:"
  rm -rf "$nested_cursor"

  nested_cursor_unpadded="$(mktemp -d)"
  mkdir -p "$nested_cursor_unpadded/tests/helpers" "$nested_cursor_unpadded/tests/fixtures/gh" "$nested_cursor_unpadded/docs/acceptance"
  cp "$test_dir/helpers/assert.sh" "$nested_cursor_unpadded/tests/helpers/assert.sh"
  cp "$test_dir/helpers/check_fixture_cursors.py" "$nested_cursor_unpadded/tests/helpers/check_fixture_cursors.py"
  cp "$test_dir/test_fixture_owner_hygiene.sh" "$nested_cursor_unpadded/tests/test_fixture_owner_hygiene.sh"
  printf 'org: example-user\nrepo: example-repo\nrows: []\n' > "$nested_cursor_unpadded/docs/acceptance/matrix.yaml"
  # base64("cursor:v2:abcdefghi") with its trailing "==" stripped -- real
  # captures (GitHub's own base64url convention among them) sometimes omit
  # padding, and a decoder that requires it would silently skip this.
  printf '{"endCursor":"Y3Vyc29yOnYyOmFiY2RlZmdoaQ"}\n' > "$nested_cursor_unpadded/tests/fixtures/gh/poisoned.json"
  hs_test_nested_run "unpadded cursor hit" "$nested_cursor_unpadded" \
    "FAIL: a fixture cursor decodes to more than the sanctioned placeholder:"
  rm -rf "$nested_cursor_unpadded"

  nested_cursor_outside_gh="$(mktemp -d)"
  mkdir -p "$nested_cursor_outside_gh/tests/helpers" "$nested_cursor_outside_gh/tests/fixtures/herdr" "$nested_cursor_outside_gh/docs/acceptance"
  cp "$test_dir/helpers/assert.sh" "$nested_cursor_outside_gh/tests/helpers/assert.sh"
  cp "$test_dir/helpers/check_fixture_cursors.py" "$nested_cursor_outside_gh/tests/helpers/check_fixture_cursors.py"
  cp "$test_dir/test_fixture_owner_hygiene.sh" "$nested_cursor_outside_gh/tests/test_fixture_owner_hygiene.sh"
  printf 'org: example-user\nrepo: example-repo\nrows: []\n' > "$nested_cursor_outside_gh/docs/acceptance/matrix.yaml"
  # Same synthetic non-placeholder cursor as above, planted OUTSIDE
  # tests/fixtures/gh/ -- proving the scan is not scoped to that one
  # directory.
  printf '{"someOtherField":"Y3Vyc29yOnYyOjEyMzQ1Njc4OTA="}\n' > "$nested_cursor_outside_gh/tests/fixtures/herdr/poisoned.json"
  hs_test_nested_run "cursor outside gh/ hit" "$nested_cursor_outside_gh" \
    "FAIL: a fixture cursor decodes to more than the sanctioned placeholder:"
  rm -rf "$nested_cursor_outside_gh"
fi

hs_test_report
