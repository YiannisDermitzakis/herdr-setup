#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Pane gathering and adapter resolution (lib/feed.py).

The whole point of this file is one distinction: **no panes** and **could not
find out** are different answers, and only one of them is safe to carry on
from.

A blocked Herdr does not answer an empty list. It answers a JSON error
object, and it may put that object on stderr, and it may return it while
exiting 0. Every one of those, read by a parser that reaches for
`result.agents` and shrugs when the key is missing, becomes zero panes -- and
zero panes prints a cheerful summary having fed nothing, which is exactly
what the operator's 21 stranded Claude sessions looked like. So every
unreadable answer here has to raise, and each of these tests drives the fake
herdr into one of those shapes to prove it does.

The second call matters as much as the first. `agent list` succeeding and
`pane process-info` then failing is the same fail-open with one more step:
each pane silently drops out and the run reports success over an empty set.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import isolate_environment, load_feed, write_adapter  # noqa: E402

isolate_environment()

feed = load_feed()

AGENT_LIST = {
    "id": "cli:agent:list",
    "result": {
        "agents": [
            {"pane_id": "w1:p1", "agent": "claude", "state": "working"},
            {"pane_id": "w2:p2", "agent": "claude", "state": "idle"},
            {"pane_id": "w3:p3", "agent": "codex", "state": "idle"},
        ]
    },
}


def process_info(pane_id, cwd, processes):
    return {
        "id": "cli:pane:process-info",
        "result": {"pane_id": pane_id, "cwd": cwd, "processes": processes},
    }


def proc(pid, argv0, *, foreground=True, start=1788500000):
    return {"pid": pid, "argv0": argv0, "foreground": foreground, "pid_start_epoch": start}


class FakeHerdrCase(unittest.TestCase):
    """Drives the real `herdr` command, which tests/run.sh has made the fake.

    Calling feed's own subprocess path rather than stubbing it is deliberate:
    the failures under test are exit statuses and stream choices, and a stub
    cannot get those wrong in the way a real command can.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.fixtures = self.dir / "fixtures"
        self.fixtures.mkdir()
        self._saved = dict(os.environ)
        self.addCleanup(self._restore_env)
        os.environ["FAKE_HERDR_FIXTURES"] = str(self.fixtures)
        self.warnings: list[str] = []

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved)

    def fixture(self, argv: list[str], payload) -> None:
        name = "->".join(argv) + ".json"
        text = payload if isinstance(payload, str) else json.dumps(payload)
        (self.fixtures / name).write_text(text, encoding="utf-8")

    def panes(self, agent="claude", **kwargs):
        return feed.panes_for(agent, warn=self.warnings.append, **kwargs)


class TestPanesFor(FakeHerdrCase):
    def setUp(self) -> None:
        super().setUp()
        self.fixture(["agent", "list"], AGENT_LIST)
        self.fixture(
            ["pane", "process-info", "--pane", "w1:p1"],
            process_info("w1:p1", "/work/frank", [proc(100, "-zsh", foreground=False),
                                                  proc(38080, "claude", start=1788500001)]),
        )
        self.fixture(
            ["pane", "process-info", "--pane", "w2:p2"],
            process_info("w2:p2", "/work/herdr", [proc(38081, "claude", start=1788500002)]),
        )

    def test_returns_one_record_per_matching_pane(self):
        panes = self.panes()
        self.assertEqual(
            panes,
            [
                {
                    "pane_id": "w1:p1",
                    "cwd": "/work/frank",
                    "pid": 38080,
                    "pid_start_epoch": 1788500001,
                },
                {
                    "pane_id": "w2:p2",
                    "cwd": "/work/herdr",
                    "pid": 38081,
                    "pid_start_epoch": 1788500002,
                },
            ],
        )

    def test_panes_of_another_agent_are_not_asked_about(self):
        # w3:p3 is a codex pane and has no process-info fixture at all; if it
        # were asked about, the fake would answer nothing and the call would
        # raise. Getting a clean result is the assertion.
        self.assertEqual([p["pane_id"] for p in self.panes()], ["w1:p1", "w2:p2"])

    def test_an_absolute_argv0_still_matches_the_command(self):
        self.fixture(
            ["pane", "process-info", "--pane", "w2:p2"],
            process_info("w2:p2", "/work/herdr", [proc(38081, "/usr/local/bin/claude")]),
        )
        self.assertEqual([p["pane_id"] for p in self.panes()], ["w1:p1", "w2:p2"])

    def test_an_adapter_may_name_a_command_that_is_not_the_agent(self):
        self.fixture(
            ["pane", "process-info", "--pane", "w1:p1"],
            process_info("w1:p1", "/work/frank", [proc(1, "claude-code")]),
        )
        panes = self.panes(command="claude-code")
        self.assertEqual([p["pane_id"] for p in panes], ["w1:p1"])

    def test_a_pane_with_no_matching_foreground_process_is_dropped_with_a_note(self):
        self.fixture(
            ["pane", "process-info", "--pane", "w2:p2"],
            process_info("w2:p2", "/work/herdr", [proc(500, "vim")]),
        )
        panes = self.panes()
        self.assertEqual([p["pane_id"] for p in panes], ["w1:p1"])
        self.assertTrue(any("w2:p2" in w for w in self.warnings), self.warnings)

    def test_a_background_agent_process_does_not_count(self):
        self.fixture(
            ["pane", "process-info", "--pane", "w2:p2"],
            process_info("w2:p2", "/work/herdr", [proc(38081, "claude", foreground=False)]),
        )
        self.assertEqual([p["pane_id"] for p in self.panes()], ["w1:p1"])
        self.assertTrue(any("w2:p2" in w for w in self.warnings), self.warnings)

    def test_an_empty_agent_list_is_genuinely_no_panes(self):
        self.fixture(["agent", "list"], {"result": {"agents": []}})
        self.assertEqual(self.panes(), [])


class TestPanesForFailsClosed(FakeHerdrCase):
    """Every way Herdr can decline, and none of them may become an empty list."""

    def setUp(self) -> None:
        super().setUp()
        self.fixture(["agent", "list"], AGENT_LIST)
        self.fixture(
            ["pane", "process-info", "--pane", "w1:p1"],
            process_info("w1:p1", "/work/frank", [proc(38080, "claude")]),
        )
        self.fixture(
            ["pane", "process-info", "--pane", "w2:p2"],
            process_info("w2:p2", "/work/herdr", [proc(38081, "claude")]),
        )

    def test_protocol_mismatch_raises(self):
        os.environ["FAKE_HERDR_PROTOCOL_MISMATCH"] = "1"
        with self.assertRaises(feed.HerdrError):
            self.panes()

    def test_an_error_object_on_stderr_raises(self):
        os.environ["FAKE_HERDR_PROTOCOL_MISMATCH"] = "1"
        os.environ["FAKE_HERDR_ERROR_STREAM"] = "stderr"
        with self.assertRaises(feed.HerdrError):
            self.panes()

    def test_an_error_object_returned_with_exit_zero_raises(self):
        """The shape a status-only check reads as a healthy, empty answer."""
        os.environ["FAKE_HERDR_ERROR_CODE"] = "socket_closed"
        os.environ["FAKE_HERDR_ERROR_EXIT"] = "0"
        with self.assertRaises(feed.HerdrError):
            self.panes()

    def test_a_failure_on_the_second_call_raises_rather_than_dropping_panes(self):
        """`agent list` fine, `pane process-info` refused: still not zero panes."""
        os.environ["FAKE_HERDR_FAIL"] = "pane process-info"
        with self.assertRaises(feed.HerdrError):
            self.panes()

    def test_no_output_at_all_raises(self):
        (self.fixtures / "agent->list.json").unlink()
        with self.assertRaises(feed.HerdrError):
            self.panes()

    def test_output_that_is_not_json_raises(self):
        self.fixture(["agent", "list"], "herdr: something went sideways")
        with self.assertRaises(feed.HerdrError):
            self.panes()

    def test_a_shape_with_no_agent_list_raises(self):
        """Valid JSON, successful exit, and no way to read an agent list."""
        self.fixture(["agent", "list"], {"result": {"panes": []}})
        with self.assertRaises(feed.HerdrError):
            self.panes()

    def test_process_info_with_no_process_list_raises(self):
        self.fixture(["pane", "process-info", "--pane", "w2:p2"], {"result": {"cwd": "/x"}})
        with self.assertRaises(feed.HerdrError):
            self.panes()

    def test_a_note_on_stderr_alongside_a_good_answer_does_not_break_the_parse(self):
        os.environ["FAKE_HERDR_STDERR_NOTE"] = "herdr: --pane is deprecated"
        self.assertEqual([p["pane_id"] for p in self.panes()], ["w1:p1", "w2:p2"])


class TestResolve(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.panes = [
            {"pane_id": "w1:p1", "cwd": "/work/frank", "pid": 1, "pid_start_epoch": 10},
            {"pane_id": "w2:p2", "cwd": "/work/herdr", "pid": 2, "pid_start_epoch": 20},
        ]

    def adapter(self, body):
        return write_adapter(self.dir, "claude", body)

    def test_pipes_the_pane_payload_on_stdin_and_parses_the_results(self):
        echo = self.dir / "seen.json"
        path = self.adapter(f'cat > "{echo}"\necho \'{{"results":[]}}\'\n')
        feed.resolve(path, self.panes)
        self.assertEqual(json.loads(echo.read_text()), {"panes": self.panes})

    def test_returns_the_results_list(self):
        payload = {
            "results": [
                {
                    "pane_id": "w1:p1",
                    "candidates": [
                        {"session_id": "abc", "label": "frank", "confidence": "exact"}
                    ],
                }
            ]
        }
        path = self.adapter(f"cat >/dev/null\ncat <<'JSON'\n{json.dumps(payload)}\nJSON\n")
        self.assertEqual(feed.resolve(path, self.panes), payload["results"])

    def test_fewer_results_than_panes_is_tolerated(self):
        payload = {"results": [{"pane_id": "w2:p2", "candidates": []}]}
        path = self.adapter(f"cat >/dev/null\ncat <<'JSON'\n{json.dumps(payload)}\nJSON\n")
        by_pane = feed.candidates_by_pane(feed.resolve(path, self.panes))
        self.assertEqual(by_pane.get("w1:p1", []), [])
        self.assertEqual(by_pane["w2:p2"], [])

    def test_a_resolve_that_exits_non_zero_raises_adapter_error(self):
        path = self.adapter("cat >/dev/null\nexit 2\n")
        with self.assertRaises(feed.AdapterError):
            feed.resolve(path, self.panes)

    def test_a_resolve_that_prints_nothing_raises_adapter_error(self):
        path = self.adapter("cat >/dev/null\n")
        with self.assertRaises(feed.AdapterError):
            feed.resolve(path, self.panes)

    def test_a_resolve_that_prints_non_json_raises_adapter_error(self):
        path = self.adapter("cat >/dev/null\necho nope\n")
        with self.assertRaises(feed.AdapterError):
            feed.resolve(path, self.panes)

    def test_a_resolve_with_no_results_key_raises_adapter_error(self):
        path = self.adapter("cat >/dev/null\necho '{\"panes\":[]}'\n")
        with self.assertRaises(feed.AdapterError):
            feed.resolve(path, self.panes)

    def test_an_adapter_that_hangs_raises_rather_than_hanging_the_run(self):
        path = self.adapter("sleep 30\n")
        with self.assertRaises(feed.AdapterError):
            feed.resolve(path, self.panes, timeout=0.5)

    def test_a_candidate_that_is_not_an_object_is_dropped(self):
        payload = {"results": [{"pane_id": "w1:p1", "candidates": ["abc", {"session_id": "d"}]}]}
        path = self.adapter(f"cat >/dev/null\ncat <<'JSON'\n{json.dumps(payload)}\nJSON\n")
        by_pane = feed.candidates_by_pane(feed.resolve(path, self.panes))
        self.assertEqual(by_pane["w1:p1"], [{"session_id": "d"}])

    def test_a_candidate_with_no_session_id_is_dropped(self):
        """A candidate the runner cannot report is not a candidate.

        Letting one through would make it a prompt option that does nothing,
        or -- worse -- the single `exact` candidate that gets reported unasked.
        """
        payload = {
            "results": [
                {
                    "pane_id": "w1:p1",
                    "candidates": [
                        {"label": "no id here", "confidence": "exact"},
                        {"session_id": "good", "confidence": "exact"},
                    ],
                }
            ]
        }
        path = self.adapter(f"cat >/dev/null\ncat <<'JSON'\n{json.dumps(payload)}\nJSON\n")
        by_pane = feed.candidates_by_pane(feed.resolve(path, self.panes))
        self.assertEqual([c["session_id"] for c in by_pane["w1:p1"]], ["good"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
