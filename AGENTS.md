# Agent contract

This file is the whole contract for any coding agent working on this repository.
Per-harness files (`CLAUDE.md`, `.github/copilot-instructions.md`) are pointers to
it and carry no content of their own.

## What this repository is

A host-side tool for reproducing and syncing a Herdr configuration across machines.
It is a bash entrypoint plus a small set of Python helpers. It has no build step and
no runtime dependencies beyond what Herdr's own integrations already require.

## Hard rules

- **No secrets, no personal paths.** This repository is public. Never commit a real
  home directory path, username, hostname, email address, or machine name. Derive
  paths at runtime; use placeholders in documentation.
- **Read-only means read-only.** Any subcommand documented as read-only must not
  write to the host, the repository, or Herdr.
- **Fail closed.** A missing dependency, an unreadable manifest, or an unreachable
  Herdr socket stops the run with one clear line. Never guess and continue.
- **Never uninstall or disable.** The tool adds and updates. Removing a plugin or an
  integration is always the operator's own action.
- **Confirm before reporting an uncertain session.** Feeding Herdr the wrong session
  id makes a pane resume the wrong conversation.

## Portability

Targets macOS and Linux. bash 3.2 (the macOS system bash) and Python 3.9 are the
floor; do not use `tomllib`, associative arrays, or `mapfile`.
