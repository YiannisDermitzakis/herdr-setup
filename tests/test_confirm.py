#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""hs_confirm (lib/common.sh), driven through a real pty.

hs_confirm gates the two consequential things onboard and apply do: whether
to install a per-agent integration, and whether to let a config write that
SHRINKS the operator's own Herdr config through. Its `[ ! -t 0 ]` -- "is
there anyone to ask" -- branch had never been exercised anywhere in this
suite. Every existing caller runs it with no terminal at all
(tests/test_apply_config.sh's shrinking-write gate, tests/test_onboard_offer.sh,
both documented as such in their own headers), which proves the no-terminal
refusal but nothing about what happens when hs_confirm is actually given a
terminal and reads an answer off it. A misread there -- treating an empty
answer or end-of-input as yes -- would pass every test in this repository
and destroy the operator's config on the very first real run.

Phase 6 already built the machinery a real terminal needs
(tests/test_feed_report.py::TestAtARealTerminal, `pty.openpty()`); this file
reuses the same approach rather than inventing a second one, driving `bash`
directly instead of `feed.py` since hs_confirm is a shell function with no
Python side to it.
"""

from __future__ import annotations

import os
import pty
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMON_SH = REPO_ROOT / "lib" / "common.sh"

# Source common.sh, ask exactly one question, and exit with hs_confirm's own
# status -- nothing else in this one-liner may fail first.
SCRIPT = f'. "{COMMON_SH}"; hs_confirm "a test question"; exit $?'


def run_in_pty(answer: bytes, timeout: float = 10) -> int:
    """Runs hs_confirm with a real pty on stdin, writes `answer` (which may
    be empty, for end-of-input with nothing typed at all), and returns the
    exit status. hs_confirm's own "is there a terminal" question can only be
    answered honestly by giving it a real one -- a pipe or /dev/null proves
    the OTHER branch (see TestWithoutATerminal below), not this one.
    """
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["bash", "-c", SCRIPT],
        stdin=slave,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(slave)
    if answer:
        # Data written: let hs_confirm's `read` consume it and let the
        # process finish before closing the master side. Closing first (as
        # for end-of-input below) can hang up the pty before the write is
        # read, which is not "end of input" -- it is the test getting in
        # its own way.
        os.write(master, answer)
        status = proc.wait(timeout=timeout)
        os.close(master)
    else:
        # Nothing to write at all: closing the master immediately is what
        # PRODUCES end-of-input -- `read` on the slave side then hits EOF
        # rather than blocking forever for a byte that will never come.
        os.close(master)
        status = proc.wait(timeout=timeout)
    return status


class TestAtARealTerminal(unittest.TestCase):
    """The `read` branch: a real pty on stdin, so `[ -t 0 ]` is true and
    hs_confirm actually reads an answer instead of refusing outright."""

    def test_y_accepts(self):
        self.assertEqual(run_in_pty(b"y\n"), 0)

    def test_yes_accepts(self):
        self.assertEqual(run_in_pty(b"yes\n"), 0)

    def test_other_accepted_case_variants(self):
        for word in (b"Y\n", b"Yes\n", b"YES\n"):
            with self.subTest(answer=word):
                self.assertEqual(run_in_pty(word), 0)

    def test_n_declines(self):
        self.assertEqual(run_in_pty(b"n\n"), 1)

    def test_no_declines(self):
        self.assertEqual(run_in_pty(b"no\n"), 1)

    def test_an_empty_answer_declines(self):
        # Enter alone: read succeeds with an empty string, which is not one
        # of the accepted spellings and must not fall through to yes.
        self.assertEqual(run_in_pty(b"\n"), 1)

    def test_a_nonsense_answer_declines(self):
        self.assertEqual(run_in_pty(b"banana\n"), 1)

    def test_end_of_input_declines(self):
        # Nothing typed at all before the pty closes: `read` hits EOF and
        # fails. hs_confirm's own `read -r answer || answer=""` exists for
        # exactly this case -- treat a failed read the same as an empty
        # answer, not as a hang or a crash, and never as consent.
        self.assertEqual(run_in_pty(b""), 1)


class TestWithoutATerminal(unittest.TestCase):
    """No pty at all: `[ ! -t 0 ]` must refuse before ever reading an
    answer. Feeding "yes" here proves the refusal happens BEFORE the read --
    if the terminal check ever fell through instead of returning early, this
    is the case that would silently start accepting answers from a pipe."""

    def test_a_plain_pipe_declines_even_when_fed_yes(self):
        proc = subprocess.run(
            ["bash", "-c", SCRIPT],
            input=b"yes\n",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"no terminal to confirm", proc.stderr)

    def test_devnull_stdin_declines(self):
        proc = subprocess.run(
            ["bash", "-c", SCRIPT],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main()
