#!/usr/bin/env bash
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

# shellcheck disable=SC2329
# both are invoked indirectly, by name, as the
# $lister argument absent() calls below; shellcheck's own message already
# names this exact case ("or ignored if invoked indirectly").
tracked() {
  git ls-files \
    | grep -vE '^docs/(acceptance/report_|superpowers/)' \
    | grep -vE '^tests/test_public_hygiene\.sh$'
}
# shellcheck disable=SC2329
# invoked indirectly too, as absent()'s $lister.
fixtures() { tracked | grep -E '^tests/fixtures/'; }

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

hs_test_report
