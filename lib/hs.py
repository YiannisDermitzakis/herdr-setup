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


def _load_plugins_array(path: Path) -> list:
    """Read plugins.json from disk as a list of entries.

    Shared disk-read step for host_plugins and absorb_plugins: a missing file
    means a host with no plugins yet ([]), not an error; anything that isn't
    valid JSON or isn't a JSON array is fatal, naming the file, exit 2.
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        die(f"{path}: {exc}")
    if not isinstance(data, list):
        die(f"{path}: expected a JSON array of plugins")
    return data


def host_plugins(path: Path) -> int:
    """Print the host's installed plugins, one TSV row each, sorted by id.

    Reads plugins.json from disk and never invokes herdr, which is what lets
    `diff` keep working when the command line and the server disagree on the
    protocol version. A missing file means a host with no plugins, not an error.
    """
    rows: list[tuple[str, str, str, str]] = []
    for entry in _load_plugins_array(path):
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


def absorb_plugins(path: Path) -> int:
    """Print manifest/plugins.list content from the host's plugins.json.

    One "<source> <ref>" line per plugin, sorted by source, keeping only
    source.owner/source.repo[/subdir] and source.requested_ref -- dropping
    source.resolved_commit and source.managed_path entirely, since the
    manifest pins a ref for every host to converge on, not one host's
    installed commit or filesystem path (the latter would also leak a
    real path into this public repository). Same disk-read contract as
    host_plugins: a missing file is a host with no plugins yet (no output,
    exit 0); anything malformed is fatal, naming the file, exit 2.
    """
    rows: list[tuple[str, str]] = []
    for entry in _load_plugins_array(path):
        try:
            source = entry["source"]
            src = f"{source['owner']}/{source['repo']}"
            if source.get("subdir"):
                src = f"{src}/{source['subdir']}"
            rows.append((src, source["requested_ref"]))
        except (KeyError, TypeError):
            die(f"{path}: malformed plugin entry: {entry!r}")

    for src, ref in sorted(rows, key=lambda r: r[0]):
        print(f"{src} {ref}")
    return 0


HS_SPLICE_SENTINEL = "\x01HS_ANCHOR\x01"


def split_lines(text: str) -> list[str]:
    r"""Split on "\n" only, the way the shell side does.

    Not ``str.splitlines()``. That also splits on vertical tab, form feed,
    ``\x85``, `` `` and `` ``, none of which a ``while read`` loop
    in lib/common.sh treats as a line ending. A config carrying any of them
    was therefore counted as more lines here than there, which desynchronises
    the anchors: an anchor is a count of the shell's lines, and it was being
    applied to a longer list. Worse, rejoining with "\n" turned each of those
    characters into a real newline in the operator's own config file.

    A trailing "\n" does not make a final empty line, matching splitlines and
    the shell's own reading of a well-formed text file.
    """
    if not text:
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def splice_config(manifest_path: Path) -> int:
    """Reinsert plugin-written blocks into the manifest's operator lines.

    Reads the manifest config verbatim as the new skeleton, then reads a
    block stream on stdin, produced by lib/common.sh's `splice` walk mode:
    each block is introduced by a sentinel line (HS_SPLICE_SENTINEL followed
    by its anchor, the count of operator lines that preceded it on the
    host) and followed by the block's own lines, markers included. Blocks
    are inserted into the manifest's line list at their anchor position,
    offset by the lines already-inserted blocks added -- see hs_splice_config
    in lib/common.sh for why an anchor computed against the host is only an
    approximation once inserted into a different (the manifest's) skeleton,
    and why it is nonetheless exact in the common case.
    """
    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        die(f"{manifest_path}: {exc}")
    manifest_lines = split_lines(manifest_text)

    raw = sys.stdin.read()
    blocks: list[tuple[int, list[str]]] = []
    if raw:
        current_anchor: int | None = None
        current_lines: list[str] = []
        for line in split_lines(raw):
            if line.startswith(HS_SPLICE_SENTINEL):
                if current_anchor is not None:
                    blocks.append((current_anchor, current_lines))
                current_anchor = int(line[len(HS_SPLICE_SENTINEL) :])
                current_lines = []
            else:
                current_lines.append(line)
        if current_anchor is not None:
            blocks.append((current_anchor, current_lines))

    result = list(manifest_lines)
    offset = 0
    for anchor, block_lines in blocks:
        position = min(anchor + offset, len(result))
        result[position:position] = block_lines
        offset += len(block_lines)

    text = "\n".join(result)
    if text:
        text += "\n"
    sys.stdout.write(text)
    return 0


def herdr_error() -> int:
    """Decide whether a Herdr response is an error, and print its message.

    Exits 0 having printed the message when the response is a JSON object with
    an `error` key. Reading the parsed structure is the whole point: a
    substring test for `"error"` also matches a perfectly good response that
    happens to carry the token in a value, and reports a successful call as a
    failure.

    The two ways of NOT being an error object are kept apart, because the
    caller must treat them differently:

    3  the response could not be read at all -- not JSON, or not an object.
       The caller only asks at all when the text carries the `"error"` token,
       so a response it cannot read is not evidence that the call succeeded.
    1  the response WAS read, and has no top-level `error` key. This is the
       only answer that means "this call was fine".

    Anything else (2) is this helper failing to run, which the caller treats
    the same way as 3. Collapsing 3 into 1 was the bug: a response nobody
    could parse read exactly like a clean one.
    """
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except ValueError:
        return 3
    if not isinstance(data, dict):
        return 3
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
    p = sub.add_parser(
        "absorb-plugins", help="read the host's plugins.json, print manifest/plugins.list content"
    )
    p.add_argument("path", type=Path)
    sub.add_parser("herdr-error", help="detect a Herdr error response on stdin")
    p = sub.add_parser(
        "splice-config",
        help="reinsert plugin blocks (read on stdin) into a manifest config",
    )
    p.add_argument("manifest_path", type=Path)
    args = parser.parse_args(argv)
    if args.command == "host-plugins":
        return host_plugins(args.path)
    if args.command == "absorb-plugins":
        return absorb_plugins(args.path)
    if args.command == "herdr-error":
        return herdr_error()
    if args.command == "splice-config":
        return splice_config(args.manifest_path)
    die(f"unknown subcommand: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
