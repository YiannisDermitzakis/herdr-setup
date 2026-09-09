# Journal: 2026-09-08-herdr-setup-design

<!-- fr:journal kind=review scope=spec id=8599ccd4de43 created=2026-09-08T09:28:37 -->
### 8599ccd4de43 · review · Spec reviewed against the host; three findings fixed

Verified every path and command the spec names against this machine. Confirmed: Claude per-pid session files, Codex `session_meta` with cwd and id, the opencode `session` table, Herdr's four resume commands, plugin block markers, bash 3.2.57 and /usr/bin/python3 3.9.6. Findings fixed inline: (1) `herdr agent list` and `plugin list` are blocked by a CLI/server protocol mismatch and answer with a JSON error object, so feed and onboard need a preflight gate and diff must read from disk; (2) the Test Plan assumed feed could run before the restart, which the mismatch forbids, so the restart moved into the middle of the sequence; (3) absorb now names the real plugins.json fields.
