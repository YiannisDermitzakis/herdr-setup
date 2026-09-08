#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""The decision and the report (lib/feed.py).

This is the file where being wrong costs the most. A reported session id is
what a pane resumes with after a server restart, so a wrong one does not fail
loudly -- it brings the pane back running somebody else's conversation. The
rule the whole subsystem is built on is therefore narrow on purpose:

    report without asking ONLY when exactly one candidate comes back at
    `exact` confidence. Everything else is a question.

And a question nobody is there to answer is a skip, never a guess. A
non-interactive run without --yes leaves the pane alone and says so.

The tests here use a real AF_UNIX server that records the lines it is sent,
not a stub, for one specific reason: asserting that a --dry-run sent nothing
is only evidence if a real socket was sitting there ready to receive it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import (  # noqa: E402
    REPO_ROOT,
    RecordingServer,
    agent_entry,
    agent_list,
    isolate_environment,
    load_feed,
    process_entry,
    process_info,
    write_adapter,
)

isolate_environment()

feed = load_feed()

PROBE = {
    "agent": "claude",
    "source": "herdr:claude",
    "available": True,
    "confidence": "exact",
}

# A pid that cannot be running, so nothing here depends on the host.
DEAD_PID = 0x7FFFFFF

# Both Herdr answers are built from the captures in tests/fixtures/herdr/ by
# replacing VALUES. Nothing in this file writes a response shape by hand --
# that is exactly how the first cut of the runner came to read three fields
# Herdr does not return. See that directory's README.
AGENT_LIST = agent_list(
    [
        agent_entry(0, pane_id="w1:p1", agent="claude", cwd="/work/beta"),
        agent_entry(1, pane_id="w2:p2", agent="claude", cwd="/work/herdr"),
    ]
)


def info(pane_id, cwd, pid, argv0="claude"):
    """A captured process-info answer, repointed at one pane."""
    return process_info(
        pane_id=pane_id,
        processes=[
            process_entry(0, cwd=cwd),
            process_entry(-1, pid=pid, argv0=argv0, cwd=cwd),
        ],
        group_id=pid,
    )


def exact(session_id, **extra):
    return dict({"session_id": session_id, "confidence": "exact", "label": session_id}, **extra)


def heuristic(session_id, **extra):
    return dict(
        {"session_id": session_id, "confidence": "heuristic", "label": session_id}, **extra
    )


class RunCase(unittest.TestCase):
    """A whole feed run: fake herdr, one adapter, and a recording socket."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.fixtures = self.dir / "fixtures"
        self.fixtures.mkdir()
        self.adapters = self.dir / "adapters"
        self.adapters.mkdir()
        # A short socket path: AF_UNIX paths are capped near 104 bytes on
        # macOS, and a TemporaryDirectory under /var/folders eats most of it.
        self._sock_dir = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self._sock_dir.cleanup)
        self.socket_path = Path(self._sock_dir.name) / "h.sock"

        self._saved = dict(os.environ)
        self.addCleanup(self._restore_env)
        os.environ["FAKE_HERDR_FIXTURES"] = str(self.fixtures)

        self.fixture(["agent", "list"], AGENT_LIST)
        self.fixture(["pane", "process-info", "--pane", "w1:p1"],
                     info("w1:p1", "/work/beta", DEAD_PID))
        self.fixture(["pane", "process-info", "--pane", "w2:p2"],
                     info("w2:p2", "/work/herdr", DEAD_PID + 1))

        self.warnings: list[str] = []
        self.out: list[str] = []

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved)

    def fixture(self, argv, payload) -> None:
        name = "->".join(argv) + ".json"
        text = payload if isinstance(payload, str) else json.dumps(payload)
        (self.fixtures / name).write_text(text, encoding="utf-8")

    def adapter(self, results, probe=PROBE) -> None:
        payload = json.dumps({"results": results})
        write_adapter(
            self.adapters,
            "claude",
            'case "$1" in\n'
            f"  probe) cat <<'JSON'\n{json.dumps(probe)}\nJSON\n    ;;\n"
            f"  resolve) cat >/dev/null; cat <<'JSON'\n{payload}\nJSON\n    ;;\n"
            "esac\n",
        )

    def run_feed(self, **kwargs):
        class Out:
            def __init__(self, sink):
                self.sink = sink

            def write(self, text):
                self.sink.append(text)

            def flush(self):
                pass

        kwargs.setdefault("adapter_dir", self.adapters)
        kwargs.setdefault("socket_path", self.socket_path)
        kwargs.setdefault("interactive", False)
        kwargs.setdefault("warn", self.warnings.append)
        kwargs.setdefault("out", Out(self.out))
        return feed.run(**kwargs)

    @property
    def stdout(self) -> str:
        return "".join(self.out)


class TestReportsOnlyWhenSure(RunCase):
    def test_a_single_exact_candidate_is_reported_without_a_prompt(self):
        self.adapter([
            {"pane_id": "w1:p1", "candidates": [exact("aaa")]},
            {"pane_id": "w2:p2", "candidates": []},
        ])
        asked = []
        with RecordingServer(self.socket_path) as server:
            rc = self.run_feed(ask=lambda *a, **k: asked.append(a) or None)
        self.assertEqual(rc, 0)
        self.assertEqual(asked, [], "a single exact candidate must not be asked about")
        self.assertEqual(len(server.received), 1)
        self.assertEqual(server.received[0]["params"]["agent_session_id"], "aaa")

    def test_two_candidates_are_never_reported_unasked(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": [exact("aaa"), exact("bbb")]}])
        with RecordingServer(self.socket_path) as server:
            rc = self.run_feed()
        self.assertEqual(server.received, [], "two candidates must never be guessed between")
        self.assertEqual(rc, 0)
        self.assertTrue(any("w1:p1" in w for w in self.warnings), self.warnings)

    def test_one_heuristic_candidate_is_never_reported_unasked(self):
        """One candidate is not enough. The adapter said it is not sure."""
        self.adapter([{"pane_id": "w1:p1", "candidates": [heuristic("aaa")]}])
        with RecordingServer(self.socket_path) as server:
            self.run_feed()
        self.assertEqual(server.received, [])
        self.assertTrue(any("w1:p1" in w for w in self.warnings), self.warnings)

    def test_a_candidate_with_no_confidence_is_never_reported_unasked(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": [{"session_id": "aaa"}]}])
        with RecordingServer(self.socket_path) as server:
            self.run_feed()
        self.assertEqual(server.received, [])

    def test_a_pane_with_no_candidates_is_skipped_with_a_note(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": []}])
        with RecordingServer(self.socket_path) as server:
            self.run_feed()
        self.assertEqual(server.received, [])
        self.assertTrue(any("w1:p1" in w for w in self.warnings), self.warnings)

    def test_yes_takes_the_best_candidate_rather_than_skipping(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": [heuristic("first"), heuristic("2nd")]}])
        with RecordingServer(self.socket_path) as server:
            self.run_feed(assume_yes=True)
        self.assertEqual(len(server.received), 1)
        self.assertEqual(server.received[0]["params"]["agent_session_id"], "first")

    def test_the_prompt_answer_decides_which_candidate_is_reported(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": [heuristic("a"), heuristic("b")]}])
        with RecordingServer(self.socket_path) as server:
            self.run_feed(interactive=True, ask=lambda pane_id, candidates: candidates[1])
        self.assertEqual(len(server.received), 1)
        self.assertEqual(server.received[0]["params"]["agent_session_id"], "b")

    def test_declining_the_prompt_reports_nothing(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": [heuristic("a"), heuristic("b")]}])
        with RecordingServer(self.socket_path) as server:
            self.run_feed(interactive=True, ask=lambda pane_id, candidates: None)
        self.assertEqual(server.received, [])

    def test_yes_wins_over_a_terminal_so_an_answered_run_never_stops(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": [heuristic("a"), heuristic("b")]}])
        asked = []
        with RecordingServer(self.socket_path) as server:
            self.run_feed(
                interactive=True, assume_yes=True, ask=lambda *a: asked.append(a) or None
            )
        self.assertEqual(asked, [])
        self.assertEqual(len(server.received), 1)


class TestReportedPayload(RunCase):
    def report_once(self, candidate):
        self.adapter([{"pane_id": "w1:p1", "candidates": [candidate]}])
        with RecordingServer(self.socket_path) as server:
            self.run_feed()
        self.assertEqual(len(server.received), 1, server.received)
        return server.received[0]

    def test_it_is_a_pane_report_agent_session_request(self):
        request = self.report_once(exact("aaa"))
        self.assertEqual(request["method"], "pane.report_agent_session")
        self.assertTrue(request.get("id"), "a request carries an id")

    def test_params_carry_pane_id_source_agent_seq_and_session_id(self):
        params = self.report_once(exact("aaa"))["params"]
        self.assertEqual(params["pane_id"], "w1:p1")
        self.assertEqual(params["source"], "herdr:claude")
        self.assertEqual(params["agent"], "claude")
        self.assertEqual(params["agent_session_id"], "aaa")
        self.assertIsInstance(params["seq"], int)

    def test_session_path_is_sent_only_when_the_adapter_supplied_one(self):
        params = self.report_once(exact("aaa"))["params"]
        self.assertNotIn("agent_session_path", params)
        params = self.report_once(exact("aaa", session_path="/t/aaa.jsonl"))["params"]
        self.assertEqual(params["agent_session_path"], "/t/aaa.jsonl")

    def test_seq_is_monotonic_across_panes(self):
        self.adapter([
            {"pane_id": "w1:p1", "candidates": [exact("aaa")]},
            {"pane_id": "w2:p2", "candidates": [exact("bbb")]},
        ])
        with RecordingServer(self.socket_path) as server:
            self.run_feed()
        seqs = [r["params"]["seq"] for r in server.received]
        self.assertEqual(len(seqs), 2)
        self.assertLess(seqs[0], seqs[1])

    def test_every_request_is_one_line(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": [exact("aaa")]}])
        with RecordingServer(self.socket_path) as server:
            self.run_feed()
        self.assertEqual(len(server.raw), 1)
        self.assertNotIn("\n", server.raw[0])


class TestDryRunAndSummary(RunCase):
    def test_dry_run_opens_no_socket(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": [exact("aaa")]}])
        with RecordingServer(self.socket_path) as server:
            rc = self.run_feed(dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(server.received, [], "a dry run must send nothing at all")
        self.assertIn("pane.report_agent_session", self.stdout)

    def test_dry_run_still_says_what_it_would_have_done(self):
        self.adapter([
            {"pane_id": "w1:p1", "candidates": [exact("aaa")]},
            {"pane_id": "w2:p2", "candidates": []},
        ])
        with RecordingServer(self.socket_path):
            self.run_feed(dry_run=True)
        self.assertRegex(self.stdout, r"reported 1 pane\b")
        self.assertRegex(self.stdout, r"skipped 1\b")

    def test_the_summary_states_reported_and_skipped_counts(self):
        self.adapter([
            {"pane_id": "w1:p1", "candidates": [exact("aaa")]},
            {"pane_id": "w2:p2", "candidates": [heuristic("x"), heuristic("y")]},
        ])
        with RecordingServer(self.socket_path):
            self.run_feed()
        summary = [line for line in self.stdout.splitlines() if line.startswith("feed:")][-1]
        self.assertRegex(summary, r"reported 1 pane\b")
        self.assertRegex(summary, r"skipped 1\b")

    def test_a_run_with_no_panes_still_prints_a_summary(self):
        self.fixture(["agent", "list"], agent_list([]))
        self.adapter([])
        with RecordingServer(self.socket_path):
            rc = self.run_feed()
        self.assertEqual(rc, 0)
        self.assertRegex(self.stdout, r"reported 0 panes\b")

    def test_a_run_with_no_adapters_says_so_rather_than_claiming_success(self):
        with RecordingServer(self.socket_path):
            rc = self.run_feed(adapter_dir=self.dir / "nothing-here")
        self.assertEqual(rc, 0)
        self.assertTrue(any("adapter" in w.lower() for w in self.warnings), self.warnings)


class TestFailuresAreNotSilent(RunCase):
    def test_a_send_that_cannot_connect_is_a_failure_not_a_report(self):
        """Nothing listening: the pane was NOT fed, and the run must say so."""
        self.adapter([{"pane_id": "w1:p1", "candidates": [exact("aaa")]}])
        rc = self.run_feed()  # no RecordingServer: the socket does not exist
        self.assertEqual(rc, 1)
        self.assertRegex(self.stdout, r"reported 0 panes\b")
        self.assertTrue(any("w1:p1" in w for w in self.warnings), self.warnings)

    def test_a_blocked_herdr_fails_the_run_rather_than_feeding_nothing(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": [exact("aaa")]}])
        os.environ["FAKE_HERDR_PROTOCOL_MISMATCH"] = "1"
        with RecordingServer(self.socket_path) as server:
            rc = self.run_feed()
        self.assertEqual(rc, 2, "a Herdr that refused must not look like an empty host")
        self.assertEqual(server.received, [])
        self.assertNotRegex(self.stdout, r"reported 0 panes, skipped 0")

    def test_an_adapter_whose_resolve_breaks_does_not_stop_the_other_adapters(self):
        write_adapter(
            self.adapters,
            "broken",
            'case "$1" in\n'
            f"  probe) echo '{json.dumps(dict(PROBE, agent='codex', source='herdr:codex'))}' ;;\n"
            "  resolve) exit 1 ;;\n"
            "esac\n",
        )
        self.fixture(["agent", "list"], agent_list([
            agent_entry(0, pane_id="w1:p1", agent="claude", cwd="/work/beta"),
            agent_entry(1, pane_id="w2:p2", agent="codex", cwd="/work/other"),
        ]))
        self.fixture(["pane", "process-info", "--pane", "w2:p2"],
                     info("w2:p2", "/work/other", DEAD_PID + 2, argv0="codex"))
        self.adapter([{"pane_id": "w1:p1", "candidates": [exact("aaa")]}])
        with RecordingServer(self.socket_path) as server:
            rc = self.run_feed()
        self.assertEqual(len(server.received), 1)
        self.assertEqual(server.received[0]["params"]["agent"], "claude")
        self.assertTrue(any("broken" in w for w in self.warnings), self.warnings)
        self.assertEqual(rc, 1, "an adapter that could not answer is not a clean run")


class TestAtARealTerminal(RunCase):
    """The prompt, driven through a pty, with feed.py started as a program.

    Every other test in this file passes `interactive` in. This one does not:
    it gives feed.py an actual terminal on stdin and lets it work that out for
    itself, because "there is nobody to ask" is a decision the tool makes from
    the environment and a test that hands it the answer proves nothing about
    it.
    """

    def feed_in_a_pty(self, answer: str, extra_args=()):
        import pty

        master, slave = pty.openpty()
        env = dict(os.environ)
        env["FAKE_HERDR_FIXTURES"] = str(self.fixtures)
        env["PATH"] = f"{REPO_ROOT / 'tests' / 'helpers'}{os.pathsep}{env['PATH']}"
        proc = subprocess.Popen(
            [
                "uv", "run", "--quiet", "--script", str(REPO_ROOT / "lib" / "feed.py"),
                "--adapters", str(self.adapters),
                "--socket", str(self.socket_path),
                *extra_args,
            ],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        os.close(slave)
        os.write(master, answer.encode())
        out, err = proc.communicate(timeout=120)
        os.close(master)
        return proc.returncode, out, err

    def test_an_answered_prompt_reports_the_chosen_candidate(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": [heuristic("a"), heuristic("b")]}])
        with RecordingServer(self.socket_path) as server:
            rc, out, err = self.feed_in_a_pty("2\n")
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(server.received), 1, err)
        self.assertEqual(server.received[0]["params"]["agent_session_id"], "b")

    def test_an_empty_answer_skips_the_pane(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": [heuristic("a"), heuristic("b")]}])
        with RecordingServer(self.socket_path) as server:
            rc, out, err = self.feed_in_a_pty("\n")
        self.assertEqual(server.received, [], err)
        self.assertRegex(out, r"reported 0 panes\b")

    def test_a_nonsense_answer_skips_rather_than_picking_something(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": [heuristic("a"), heuristic("b")]}])
        with RecordingServer(self.socket_path) as server:
            rc, out, err = self.feed_in_a_pty("banana\n")
        self.assertEqual(server.received, [], err)

    def test_without_a_terminal_the_same_run_skips_and_says_why(self):
        self.adapter([{"pane_id": "w1:p1", "candidates": [heuristic("a"), heuristic("b")]}])
        env = dict(os.environ)
        env["PATH"] = f"{REPO_ROOT / 'tests' / 'helpers'}{os.pathsep}{env['PATH']}"
        with RecordingServer(self.socket_path) as server:
            proc = subprocess.run(
                [
                    "uv", "run", "--quiet", "--script", str(REPO_ROOT / "lib" / "feed.py"),
                    "--adapters", str(self.adapters),
                    "--socket", str(self.socket_path),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
            )
        self.assertEqual(server.received, [], proc.stderr)
        self.assertRegex(proc.stdout, r"reported 0 panes\b")
        self.assertTrue(re.search(r"--yes|terminal", proc.stderr), proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
