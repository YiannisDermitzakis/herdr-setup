# Agent contract

This file is the whole contract for any coding agent working on this repository.
Per-harness files (`CLAUDE.md`, `.github/copilot-instructions.md`) are pointers to
it and carry no content of their own.

## What this repository is

A host-side tool for reproducing and syncing a Herdr configuration across machines.
It is a bash entrypoint plus a small set of Python helpers. It has no build step.
Its prerequisites are Herdr, git, and uv, plus gh for `audit` only.

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

Targets macOS and Linux.

**Shell.** bash 3.2 is the floor, because that is what macOS ships. No associative
arrays, no `mapfile`, no `local -n`. Never name a local `status`: it is read-only
in zsh and breaks anyone who sources the library there.

**Python comes from uv, not from the host.** Every Python file carries PEP 723
inline metadata naming the interpreter it needs, and a
`#!/usr/bin/env -S uv run --script` shebang. uv resolves and if necessary
downloads that interpreter, so every host runs the same one. Write modern Python;
the version in `pyproject.toml` is the contract.

**uv runs our scripts, and only ours.** The host's Herdr is invoked exactly as
it is installed. herdr-setup never replaces, shadows, upgrades or reconfigures
it, and it never asks the host for an interpreter of its own.

**This tool runs seldom.** It is invoked by hand when a host is set up, or when
it has drifted, and it is idle the rest of the time. Startup cost is not a design
consideration: prefer the robust and obvious construction over the fast one.
There are three sanctioned doors to the interpreter, all in `lib/common.sh`,
and what the Python does decides between them. `hs_py` runs `lib/hs.py`, where
structured work is batched behind subcommands for coherence, not for speed. A
program that spawns other programs gets a door of its own: `hs_feed` runs
`lib/feed.py`, which spawns adapters, and `hs_audit` runs `lib/audit.py`, which
spawns adapters, `git` and `gh`. Nothing reaches the interpreter any other way.

**Development tools are pinned too**: `uv run --group dev pytest`,
`uv run --group dev ruff check`, and
`uv run --group dev shellcheck -s bash -x herdr-setup lib/*.sh tests/*.sh`.
