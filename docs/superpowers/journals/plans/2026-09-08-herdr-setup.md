# Journal: 2026-09-08-herdr-setup

<!-- fr:journal kind=discovery scope=plan id=5089dcc1b70e created=2026-09-08T09:47:36 phase=1 -->
### 5089dcc1b70e · discovery · tests/helpers/ needs a herdr symlink, not just fake-herdr (phase 1)

lib/common.sh and the entrypoint call the bare `herdr` command (never
`fake-herdr`). PATH resolution therefore needs a file literally named
`herdr` on tests/helpers. This dev machine has a real herdr installed at
/usr/local/bin/herdr, so getting this wrong does not error — it silently
falls through to the real binary and every preflight/hs_herdr_json test
would exercise the wrong command with no obvious symptom beyond wrong
output. Fixed by adding `tests/helpers/herdr` as a relative symlink
(`ln -s fake-herdr herdr`) alongside the fake-herdr script, so `command -v
herdr` on tests/helpers-prefixed PATH resolves to the symlink (whose target
is the real logic). Anything in a later phase that spawns its own PATH
(adapters, feed.py subprocess calls) must include tests/helpers on PATH the
same way run.sh does, or it will hit the real system herdr instead of the
fake one.

<!-- fr:journal kind=finding scope=plan id=998bc29faa4a created=2026-09-08T09:54:14 phase=1 state=fixed -->
### 998bc29faa4a · finding [fixed] · lib/common.sh used 'status' as a local, which is read-only in zsh (phase 1)

Verifying the phase-1 gates by sourcing lib/common.sh into an interactive zsh produced 'read-only variable: status' and hs_preflight returned an empty state, which hs_require_socket then reported as an unrecognized state. Harmless in normal use, because every script carries a bash shebang, but it is a trap for anyone sourcing the library and it would have been copied into later phases. Renamed the locals to rc. Also changed the herdr invocation from 2>/dev/null to 2>&1 so an error herdr writes to stderr still reaches the operator instead of producing an empty message. Behaviour under bash is unchanged and the suite stays green; the library now sources cleanly under both shells.
