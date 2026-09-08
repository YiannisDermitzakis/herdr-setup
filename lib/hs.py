#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Structured helpers for herdr-setup.

The shell side owns orchestration and anything on a hot path. Everything that
needs real parsing lives here, behind subcommands, so one interpreter start
serves a whole command rather than one start per call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def die(message: str, code: int = 2) -> None:
    sys.stderr.write(f"herdr-setup: {message}\n")
    raise SystemExit(code)


def host_plugins(path: Path) -> int:
    """Print the host's installed plugins, one TSV row each, sorted by id.

    Reads plugins.json from disk and never invokes herdr, which is what lets
    `diff` keep working when the command line and the server disagree on the
    protocol version. A missing file means a host with no plugins, not an error.
    """
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        die(f"{path}: {exc}")
    if not isinstance(data, list):
        die(f"{path}: expected a JSON array of plugins")

    rows: list[tuple[str, str, str, str]] = []
    for entry in data:
        try:
            plugin_id = entry["plugin_id"]
            source = entry["source"]
            src = f"{source['owner']}/{source['repo']}"
            if source.get("subdir"):
                src = f"{src}/{source['subdir']}"
            rows.append((plugin_id, src, source["requested_ref"], source["resolved_commit"]))
        except (KeyError, TypeError):
            die(f"{path}: malformed plugin entry: {entry!r}")

    for row in sorted(rows, key=lambda r: r[0]):
        print("\t".join(row))
    return 0


def herdr_error() -> int:
    """Decide whether a Herdr response is an error, and print its message.

    Exits 0 having printed the message when the response is a JSON object with
    an `error` key, and 1 when it is not. Reading the parsed structure is the
    whole point: a substring test for `"error"` also matches a perfectly good
    response that happens to carry the token in a value, and reports a
    successful call as a failure.
    """
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except ValueError:
        return 1
    if not isinstance(data, dict):
        return 1
    err = data.get("error")
    if err is None:
        return 1
    message = err.get("message", "") if isinstance(err, dict) else str(err)
    print(" ".join(str(message).split()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hs.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("host-plugins", help="read the host's plugins.json from disk")
    p.add_argument("path", type=Path)
    sub.add_parser("herdr-error", help="detect a Herdr error response on stdin")
    args = parser.parse_args(argv)
    if args.command == "host-plugins":
        return host_plugins(args.path)
    if args.command == "herdr-error":
        return herdr_error()
    die(f"unknown subcommand: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
