## Shape of the work

Ten phases, built bottom-up. The first three give the tool a spine and its
read-only half; four and five make it write; six through eight build the agent
seam and its adapters; nine is the interactive onboarding; ten is the paperwork.

Phases 2 and 3 both depend only on phase 1 and touch different files, so they can
be worked in either order. The same is true of 4 and 5, and of 7 and 8.

## The two things that are easy to get wrong

**A blocked Herdr answers with an error object, not an empty result.** When the
command line is newer than the running server, `herdr agent list` prints
`{"error":{"code":"protocol_mismatch",...}}` and exits 1. A parser that reads
`.result.agents` and shrugs at a missing key sees zero panes and reports a
successful run that fed nothing. Phase 1 therefore builds one wrapper,
`hs_herdr_json`, that checks the exit status first and refuses to return a result
from a failed call; every later phase goes through it.

**A wrong session id resumes the wrong conversation.** Only the Claude adapter can
tie a session to a specific process, because Claude Code names its session file
after the process id. The other three can only ask which sessions were open in this
directory. So the runner, not the adapter, decides: it reports unasked when a
single candidate comes back at `exact` confidence, and confirms in every other
case. An adapter never opens the Herdr socket.

## Testing without a Herdr

Every phase runs against a fake `herdr` on PATH that records the calls made to it
and serves canned JSON, with a switch that makes it behave like a version-mismatched
one. No test touches a real Herdr, a real socket, or the operator's home directory.
The adapters are tested against fixture session stores built in a temporary
directory, the Copilot one included, which is what lets an adapter that nobody can
run here still ship with tests.

## What this plan does not do

It does not install Herdr, remove anything, or verify the Copilot adapter against a
real installation. The live end-to-end proof, that a restart resumes every session
rather than stranding it, is the post-merge test plan in the spec.
