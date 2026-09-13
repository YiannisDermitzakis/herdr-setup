## Shape of the work

Four phases, each depending on the one before it.

1. **The skeleton and `install`.** The skeleton teaches the entrypoint both new
   words, gates `audit` on the preflight, and makes the audit runner exist and
   fail closed. `install` is small and self-contained, so it ships whole in this
   phase. The same phase captures every external fixture the later phases test
   against.
2. **The adapter `sessions` query.** First the contract and its runner-side
   validation, then Claude Code, then Codex.
3. **The audit runner's evidence machinery.** A fake `gh` and a fake `fr` that
   can fail, the GitHub layer, the git layer, and merge state.
4. **The report.** The three sections, text and JSON, exit codes, an end-to-end
   run through the entrypoint, the documentation, and the acceptance matrix.

## The things that are easy to get wrong

**GitHub answers a missing ref with partial data.** A GraphQL `compare`
against a deleted branch returns the rest of the query's data, a `NOT_FOUND`
entry in `errors`, and a `gh` exit status of 1. A caller that forgives
`NOT_FOUND` has started forgiving errors. Phase 3 never asks `compare` about a
ref it has not already seen exist, so the rule stays absolute: any error, and
any non-zero exit, is a failure. The fake `gh` can also return errors with exit
0, the shape a caller that checks only the exit status reads as success.

**Squash merges defeat ancestry.** A branch merged by squash is never an
ancestor of the default branch. Merge state therefore asks GitHub for a merged
pull request by head branch first, and only then asks about ancestry. The head
repository has to match too, or a fork's same-named branch reads as this
repository's merge.

**`gh api -F` guesses types.** A branch named `123` or `true` would be sent as a
number or a boolean. Branch names always go through `-f`, and the fake `gh`
type-infers `-F` the way the real one does, so a regression fails a test rather
than a host.

**Transcripts mention branches that never existed.** Test runs and
documentation leave `x`, `<branch>` and `$BR` behind. Adapters drop names that
cannot be real branches and report every other name. The runner then counts
branches with no ref and no pull request as gone, so an overeager adapter costs
a footer count, never a false finding.

**A worktree listing is not evidence.** Only `cwd` fields and command text
count as worktree-path evidence. `fr isolation status` output lists every
worktree on the host, and would attribute all of them to whichever session ran
it.

## Testing without GitHub, Herdr or a real home

`tests/helpers/` gains a fake `gh` and a fake `fr` beside the fake `herdr`, so
every one of the three commands the audit calls resolves to a stand-in in
every test. Each fake can fail in the ways its real counterpart fails. Their
response shapes are pinned to captures taken in phase 1, because a fixture
built from a guess agrees with whatever code was written from the same guess.
Merge state is tested against real git repositories built in temporary
directories. Suite runs read their exit status from a log, never from a
pipeline.

## What this plan does not do

It does not act on a finding, fetch, add session history for opencode or
Copilot CLI, or read pull requests from any forge but GitHub. The live
comparison against a hand audit is the spec's post-merge Test Plan, run by the
operator on the Herdr host.
