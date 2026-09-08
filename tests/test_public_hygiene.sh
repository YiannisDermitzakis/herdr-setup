#!/usr/bin/env bash
# shellcheck disable=SC2015
# this suite's idiom throughout: pass()/fail()
# (tests/helpers/assert.sh) never return nonzero, so "A && pass || fail C"
# cannot silently take the wrong branch.
# This repository is public, and tests/fixtures/ holds captures taken from a
# live machine. Captures are the HIGHEST-risk files here, not the lowest. An
# earlier one reached the tree still carrying the operator's repository names
# and work-in-progress branch titles, and the session stores this tool reads
# carry more than that: real session ids, a git remote naming a private
# organisation, a socket path under the operator's home, and in one case a
# vendor's entire twenty-kilobyte system prompt.
#
# So this is a test rather than advice. Two scopes:
#
#   whole tree   home directory paths, real email addresses
#   fixtures     also session ids, git remotes, embedded system prompts
#
# The fixture scope is stricter because a fixture is a capture of somebody's
# real machine, while test code legitimately names the public plugin
# repositories this tool installs. Generated acceptance reports and the plan
# and spec documents are excluded: the reports carry this repository's own
# GitHub URL, and the plan text quotes the very patterns searched for here.
set -u

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/helpers/assert.sh
. "$test_dir/helpers/assert.sh"
repo_root="$(cd "$test_dir/.." && pwd)"
cd "$repo_root" || exit 1

# The file list is taken ONCE, and this test refuses to run on a list it
# could not get. `tracked()` used to shell out to `git ls-files` per call,
# and absent() passes when its list is empty -- so anywhere git could not
# answer (no .git, a broken repository, a CI step that runs the suite from a
# tarball) every assertion below passed having scanned zero files. Six green
# checks, a poisoned fixture sitting in the tree, and nothing said. A guard
# that cannot reach its evidence must fail, not pass.
all_tracked="$(mktemp)"
trap 'rm -f "$all_tracked"' EXIT
if ! git ls-files > "$all_tracked" 2>/dev/null; then
  fail "git ls-files failed in $repo_root: this test cannot see the tree it is meant to police"
  hs_test_report
fi
if [ ! -s "$all_tracked" ]; then
  fail "git ls-files listed no files in $repo_root: every check below would pass having scanned nothing"
  hs_test_report
fi

# shellcheck disable=SC2329
# both are invoked indirectly, by name, as the
# $lister argument absent() calls below; shellcheck's own message already
# names this exact case ("or ignored if invoked indirectly").
tracked() {
  grep -vE '^docs/(acceptance/report_|superpowers/)' < "$all_tracked" \
    | grep -vE '^tests/test_public_hygiene\.sh$'
}
# shellcheck disable=SC2329
# invoked indirectly too, as absent()'s $lister.
fixtures() { tracked | grep -E '^tests/fixtures/'; }

# The fixture scope is the stricter one, and it is the one most able to go
# quietly empty: a capture is the highest-risk file here, so a fixture list
# of zero is a reason to stop, not a clean bill of health.
if [ -z "$(fixtures)" ]; then
  fail "no tracked files under tests/fixtures/: the capture-only checks below would scan nothing"
  hs_test_report
fi

# $1 label, $2 pattern, $3 file list command, $4 optional allow pattern
absent() {
  label="$1"; pattern="$2"; lister="$3"; allow="${4:-}"
  hits="$($lister | while read -r f; do
            [ -f "$f" ] || continue
            grep -HnE "$pattern" "$f" 2>/dev/null
          done)"
  if [ -n "$allow" ]; then
    hits="$(printf '%s\n' "$hits" | grep -vE "$allow" || true)"
  fi
  hits="$(printf '%s' "$hits" | sed '/^$/d' | head -5)"
  if [ -z "$hits" ]; then pass; else fail "$label:
$hits"; fi
}

# A home directory belonging to a real person, anywhere in the tree. The
# documented placeholder is the only permitted form.
absent "a real home directory path" \
  '(/Users/|/home/)[A-Za-z0-9._-]+' tracked \
  '/home/placeholder-user|/Users/example|/Users/placeholder-user'

# A real email address. Reserved test domains are fine.
# An SSH git remote (`git@host:owner/repo`) matches an email pattern but is not
# one, and the git-remote check below owns it. Reporting it as an address sent
# one phase off debugging the wrong thing.
absent "a real email address" \
  '(^|[^A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' tracked \
  '@(example|test|invalid|localhost)([./]|$)|example\.(com|org|net|invalid)|(^|[^A-Za-z0-9])git@'

# From here on, fixtures only.

# Every session id in a capture must be the all-zero placeholder form. A real
# one identifies a real conversation on a real machine.
absent "a session id that is not the all-zero placeholder" \
  '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' fixtures \
  '"?0{8}-0{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-0{11}[0-9a-f]"?'

# A git remote naming a real account or organisation, swept up from a session
# store that records the repository it was working in.
absent "a git remote URL in a capture" \
  'git@[A-Za-z0-9.-]+:|https://[A-Za-z0-9.-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+\.git' fixtures \
  'placeholder|example'

# A commit hash from the operator's OWN repository, which a session store
# records alongside the branch and remote it was working in. Note this does not
# cover `resolved_commit`: that pins a public plugin the manifest names on
# purpose, and is the manifest's whole job.
absent "a commit hash from a captured session's own repository" \
  '"commit_hash"[[:space:]]*:[[:space:]]*"[0-9a-f]{7,40}"' fixtures \
  '"(a+|b+|c+|0+|9+|deadbeef[0-9a-f]*)"'

# A vendor's system prompt. Nothing this tool reads needs one, it is twenty
# kilobytes of bloat, and it is not ours to redistribute.
absent "an embedded agent system prompt in a capture" \
  'You are a coding agent running in|You are Claude Code' fixtures

# --- and the guard on the guard: run this same file where git cannot list
# anything, and it must FAIL. Written the way tests/test_harness.sh proves
# tests/run.sh works -- against a throwaway copy of the real thing, never a
# description of it. HS_HYGIENE_NESTED stops the copy from doing this again.
if [ "${HS_HYGIENE_NESTED:-0}" != "1" ]; then
  nested="$(mktemp -d)"
  mkdir -p "$nested/tests/helpers"
  cp "$test_dir/helpers/assert.sh" "$nested/tests/helpers/assert.sh"
  cp "$test_dir/test_public_hygiene.sh" "$nested/tests/test_public_hygiene.sh"
  # A poisoned file, so a run that scanned anything at all would have to fail
  # for the right reason rather than for the missing repository.
  printf 'watch_dir = "/Users/somebody/Projects"\n' > "$nested/poisoned.toml"
  ( cd "$nested" && HS_HYGIENE_NESTED=1 GIT_CEILING_DIRECTORIES="$nested" \
      "${BASH:-bash}" "$nested/tests/test_public_hygiene.sh" ) >/dev/null 2>&1
  nested_status=$?
  [ "$nested_status" -ne 0 ] && pass \
    || fail "the hygiene test passed in a directory where git could list nothing"
  rm -rf "$nested"
fi

hs_test_report
