#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Scan any tracked file under tests/fixtures/ for a GraphQL pagination
cursor that decodes to more than the sanctioned placeholder.

GitHub's own `endCursor` encoding is `base64("cursor:v2:<opaque-id>")`, and
that opaque id has been observed to embed a real repository id -- decoding a
captured cursor is not a hypothetical, it is what a curious reader (or an
adapter test asserting the runner never inspects one) would do first. So the
one cursor shape this repository's fixtures are allowed to carry is the
literal base64 encoding of `cursor:v2:placeholder`, and this scan is the
guard that keeps a future capture from smuggling a real one back in.

Usage: check_fixture_cursors.py <path>... -- one or more files or
directories to scan. Prints one line per offending token and exits 1 if any
was found, exits 0 (silently) otherwise.

Standard library only, and the interpreter comes from uv (see AGENTS.md).
"""

from __future__ import annotations

import base64
import binascii
import re
import sys
from pathlib import Path

# A candidate base64 token: the charset base64 uses, long enough that a
# short incidental match (a hex id, a word) is not mistaken for one. 16 was
# chosen to comfortably clear `cursor:v2:` (10 bytes) once base64-encoded
# (14 chars before padding).
TOKEN_RE = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")

PLACEHOLDER = b"cursor:v2:placeholder"
PREFIX = b"cursor:v2:"


def offending_tokens(text: str) -> list[str]:
    found = []
    for token in TOKEN_RE.findall(text):
        # Padding restored before decoding, and validated only AFTER that:
        # GitHub (and other real captures) sometimes emit unpadded base64
        # (the base64url convention strips trailing `=`), and
        # `b64decode(..., validate=True)` on an unpadded token raises rather
        # than accepting it, so the token was silently skipped -- exactly
        # the "did not even look" failure this scan exists to avoid.
        padded = token + "=" * (-len(token) % 4)
        try:
            decoded = base64.b64decode(padded, validate=True)
        except (binascii.Error, ValueError):
            continue
        if decoded.startswith(PREFIX) and decoded != PLACEHOLDER:
            found.append(token)
    return found


def iter_files(paths: list[str]):
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            yield from (f for f in sorted(p.rglob("*")) if f.is_file())
        elif p.is_file():
            yield p


def main(argv: list[str]) -> int:
    if not argv:
        print("check_fixture_cursors: usage: check_fixture_cursors.py <path>...", file=sys.stderr)
        return 2

    bad = False
    for f in iter_files(argv):
        try:
            text = f.read_text(errors="ignore")
        except OSError as exc:
            print(f"check_fixture_cursors: {f}: {exc}", file=sys.stderr)
            return 2
        for token in offending_tokens(text):
            print(f"{f}: cursor token decodes to more than the placeholder: {token}")
            bad = True

    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
