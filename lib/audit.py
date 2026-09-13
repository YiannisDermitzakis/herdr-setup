#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""The audit runner: join Herdr panes, agent session history and GitHub
pull requests into one read-only report.

See docs/superpowers/specs/2026-09-13-session-audit-design.md, "The audit
runner" and "`audit` (read-only)".

This is the phase 1 walking skeleton. It accepts every flag `herdr-setup
audit` documents, and nothing else: the gather/join/resolve machinery and the
report itself arrive in phases 2 through 4. Until then it FAILS CLOSED --
AGENTS.md's own rule -- rather than printing an empty, clean-looking report a
partial run could not actually back up. An incomplete report is exit 2 per
the design doc's own exit-code table ("An incomplete report is printed and
still exits 2"), and this skeleton IS that incomplete report, in its most
extreme form: no sections at all.

Standard library only, and the interpreter comes from uv (see AGENTS.md).
"""

from __future__ import annotations

import argparse

# Mirrors the design doc's own table (Commands > audit): a positive integer
# up to 3650 (ten years), which is generous enough that no real session
# history bumps into it while still rejecting nonsense like a negative or
# zero window.
SINCE_MIN = 1
SINCE_MAX = 3650


def _since_days(value: str) -> int:
    """argparse `type=` for --since: an integer in [SINCE_MIN, SINCE_MAX].

    Raising ArgumentTypeError (rather than returning something argparse has
    to double-check) is what makes argparse itself print the usage line
    naming `--since` and exit 2 -- the exact shape the design doc's "A
    positive integer up to 3650" needs, with no hand-rolled validation here.
    """
    try:
        days = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be an integer, got {value!r}") from None
    if not SINCE_MIN <= days <= SINCE_MAX:
        raise argparse.ArgumentTypeError(f"must be between {SINCE_MIN} and {SINCE_MAX}, got {days}")
    return days


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit.py",
        description="Report open sessions, stranded branches and unmatched pull requests.",
    )
    parser.add_argument(
        "--since",
        type=_since_days,
        default=30,
        help="session history window in days, 1-3650 (default: 30)",
    )
    parser.add_argument(
        "--owner",
        dest="owners",
        action="append",
        default=None,
        help="a GitHub owner login; repeatable, replaces the default owner list",
    )
    parser.add_argument(
        "--include-sdk",
        action="store_true",
        help="include sessions an automated caller drove",
    )
    parser.add_argument(
        "--include-bots",
        action="store_true",
        help="list bot-authored pull requests in section 3",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one JSON object instead of the text tables",
    )
    parser.add_argument(
        "--adapters",
        dest="adapter_dir",
        default=None,
        help="directory of adapter executables (default: adapters/ beside this script)",
    )
    parser.add_argument(
        "--socket",
        dest="socket_path",
        default=None,
        help="Herdr socket path (default: $HERDR_SOCKET_PATH, else ~/.config/herdr/herdr.sock)",
    )
    # audit makes no writes and asks no questions -- both are accepted and
    # change nothing (design doc: "The global --dry-run and --yes are
    # accepted and change nothing, since audit makes no writes and asks no
    # questions"), so a caller that always passes them (as herdr-setup's own
    # entrypoint does for feed) can do the same here without a special case.
    parser.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)

    # The walking skeleton's whole body: say so, and fail closed. Sections 1
    # through 3 (panes, stranded branches, unmatched pull requests) do not
    # exist yet, and printing an empty report here would read as "nothing to
    # act on" -- the one thing this run does not know.
    print("incomplete: the audit runner is not implemented yet")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
