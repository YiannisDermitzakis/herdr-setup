#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Pane gathering and adapter resolution (lib/feed.py).

Two things are under test here, and the second one was learned the hard way.

**No panes and could not find out are different answers.** A blocked Herdr
does not answer an empty list. It answers a JSON error object, and it may put
that object on stderr, and it may return it while exiting 0. Every one of
those, read by a parser that reaches for a key and shrugs when it is missing,
becomes zero panes -- and zero panes prints a cheerful summary having fed
nothing, which is exactly what the operator's stranded Claude sessions looked
like. So every unreadable answer raises, and each test below drives the fake
herdr into one of those shapes to prove it.

**A fixture for a Herdr call is a capture, not a construction.** The first cut
of this file built its own `agent list` and `pane process-info` payloads by
hand and got three things wrong at once -- a `result.processes` list, a
`foreground` boolean on each process, and a `pid_start_epoch` field. Herdr
returns none of the three. The tests passed anyway, because the fixture and
the code shared one misunderstanding, and the code failed against the first
real host it met. Every Herdr answer below is now loaded from
tests/fixtures/herdr/ and edited by VALUE only; TestAgainstTheCapturedShape
replays a capture verbatim. See that directory's README.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import (  # noqa: E402
    agent_entry,
    agent_list,
    captured,
    isolate_environment,
    load_feed,
    process_entry,
    process_info,
    write_adapter,
)

isolate_environment()

feed = load_feed()

# A pid that cannot be running, so pid_start_epoch is deterministically None.
# Every assertion about a real start time uses this process's own pid instead.
DEAD_PID = 0x7FFFFFF


def claude_pane(pane_id, cwd, index=0):
    """A captured agent-list entry, repointed at a pane and a directory."""
    return agent_entry(index, pane_id=pane_id, cwd=cwd, foreground_cwd=cwd)


AGENT_LIST = agent_list(
    [
        claude_pane("w1:p1", "/work/beta", 0),
        claude_pane("w2:p2", "/work/herdr", 1),
        agent_entry(
            2, pane_id="w3:p3", agent="codex", cwd="/work/other", foreground_cwd="/work/other"
        ),
    ]
)


def info(pane_id, cwd, pid, argv0="claude", extra=None):
    """A captured process-info answer for one pane.

    The capture holds two foreground processes -- a `node` child and the agent
    itself -- and both are kept, because "several foreground processes, one of
    which is the agent" is the ordinary case, not a special one.
    """
    processes = [process_entry(0, cwd=cwd)]
    if extra:
        processes.extend(extra)
    processes.append(process_entry(-1, pid=pid, argv0=argv0, cwd=cwd))
    return process_info(pane_id=pane_id, processes=processes, group_id=pid)


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


class TestAgainstTheCapturedShape(FakeHerdrCase):
    """Replay a real Herdr answer, verbatim, and read it.

    Nothing in this class can be satisfied by the code agreeing with itself:
    the bytes come from tests/fixtures/herdr/, captured from a live server.
    """

    def setUp(self) -> None:
        super().setUp()
        self.fixture(["agent", "list"], captured("agent-list"))
        for entry in captured("agent-list")["result"]["agents"]:
            self.fixture(
                ["pane", "process-info", "--pane", entry["pane_id"]],
                process_info(pane_id=entry["pane_id"]),
            )

    def test_it_reads_every_captured_pane(self):
        entries = captured("agent-list")["result"]["agents"]
        panes = self.panes()
        self.assertEqual([p["pane_id"] for p in panes], [e["pane_id"] for e in entries])

    def test_it_finds_the_agent_among_several_foreground_processes(self):
        """The capture has a `node` child beside the agent. argv0 picks it out."""
        self.assertEqual(self.panes()[0]["pid"], 38080)

    def test_it_does_not_match_on_name_which_is_the_agent_s_version(self):
        """`name` for the Claude process in the capture is "2.1.260".

        Matching a pane's agent on `name` finds nothing, silently. The capture
        is the only reason anyone would know that.
        """
        processes = captured("pane-process-info")["result"]["process_info"][
            "foreground_processes"
        ]
        self.assertIn("2.1.260", [p["name"] for p in processes])
        self.assertNotIn("claude", [p["name"] for p in processes])
        self.assertEqual(self.panes()[0]["pid"], 38080)

    def test_the_agent_s_own_working_directory_comes_through(self):
        self.assertEqual(self.panes()[0]["cwd"], "/work/beta")

    def test_herdr_reports_no_start_time_and_the_runner_supplies_one(self):
        blob = json.dumps(captured("pane-process-info"))
        self.assertNotIn("pid_start_epoch", blob)
        self.assertNotIn("start_epoch", blob)
        self.assertNotIn("lstart", blob)
        self.assertIn("pid_start_epoch", self.panes()[0])

    def test_the_capture_has_no_foreground_flag_to_test(self):
        """Asserted, because the first implementation tested for one."""
        processes = captured("pane-process-info")["result"]["process_info"][
            "foreground_processes"
        ]
        for process in processes:
            self.assertNotIn("foreground", process)


class TestPidStartEpoch(unittest.TestCase):
    """The start time Herdr does not report, read from the operating system."""

    def test_it_reads_this_process_s_own_start_time(self):
        started = feed.pid_start_epoch(os.getpid())
        self.assertIsNotNone(started)
        # This test process started moments ago, and certainly not in the
        # future nor before this file was written.
        self.assertLessEqual(started, time.time() + 1)
        self.assertGreater(started, time.time() - 86400)

    def test_it_is_epoch_seconds_not_a_wall_clock_string(self):
        self.assertIsInstance(feed.pid_start_epoch(os.getpid()), int)

    def test_a_pid_that_is_not_running_is_none_rather_than_an_error(self):
        self.assertIsNone(feed.pid_start_epoch(DEAD_PID))

    def test_a_missing_pid_is_none(self):
        self.assertIsNone(feed.pid_start_epoch(None))

    def test_it_survives_a_localised_environment(self):
        """`ps` localises month and day names unless the locale is forced.

        A host with LC_TIME set to a locale whose month names are not English
        would otherwise fail to parse on every pane, silently losing the pid
        reuse guard. lib/feed.py forces LC_ALL=C for this call.
        """
        saved = dict(os.environ)
        try:
            os.environ["LC_ALL"] = "de_DE.UTF-8"
            os.environ["LC_TIME"] = "de_DE.UTF-8"
            os.environ["LANG"] = "de_DE.UTF-8"
            self.assertIsNotNone(feed.pid_start_epoch(os.getpid()))
        finally:
            os.environ.clear()
            os.environ.update(saved)


class TestPanesFor(FakeHerdrCase):
    def setUp(self) -> None:
        super().setUp()
        self.fixture(["agent", "list"], AGENT_LIST)
        self.fixture(["pane", "process-info", "--pane", "w1:p1"],
                     info("w1:p1", "/work/beta", DEAD_PID))
        self.fixture(["pane", "process-info", "--pane", "w2:p2"],
                     info("w2:p2", "/work/herdr", DEAD_PID + 1))

    def test_returns_one_record_per_matching_pane(self):
        self.assertEqual(
            self.panes(),
            [
                {
                    "pane_id": "w1:p1",
                    "cwd": "/work/beta",
                    "pid": DEAD_PID,
                    "pid_start_epoch": None,
                },
                {
                    "pane_id": "w2:p2",
                    "cwd": "/work/herdr",
                    "pid": DEAD_PID + 1,
                    "pid_start_epoch": None,
                },
            ],
        )

    def test_a_pane_whose_process_is_alive_carries_a_start_time(self):
        self.fixture(["pane", "process-info", "--pane", "w1:p1"],
                     info("w1:p1", "/work/beta", os.getpid()))
        self.assertIsInstance(self.panes()[0]["pid_start_epoch"], int)

    def test_panes_of_another_agent_are_not_asked_about(self):
        # w3:p3 is a codex pane and has no process-info fixture at all; if it
        # were asked about, the fake would answer nothing and the call would
        # raise. Getting a clean result is the assertion.
        self.assertEqual([p["pane_id"] for p in self.panes()], ["w1:p1", "w2:p2"])

    def test_an_absolute_argv0_still_matches_the_command(self):
        self.fixture(["pane", "process-info", "--pane", "w2:p2"],
                     info("w2:p2", "/work/herdr", DEAD_PID, argv0="/usr/local/bin/claude"))
        self.assertEqual([p["pane_id"] for p in self.panes()], ["w1:p1", "w2:p2"])

    def test_an_adapter_may_name_a_command_that_is_not_the_agent(self):
        self.fixture(["pane", "process-info", "--pane", "w1:p1"],
                     info("w1:p1", "/work/beta", DEAD_PID, argv0="claude-code"))
        panes = self.panes(command="claude-code")
        self.assertEqual([p["pane_id"] for p in panes], ["w1:p1"])

    def test_a_pane_with_no_matching_process_is_dropped_with_a_note(self):
        self.fixture(["pane", "process-info", "--pane", "w2:p2"],
                     info("w2:p2", "/work/herdr", DEAD_PID, argv0="vim"))
        self.assertEqual([p["pane_id"] for p in self.panes()], ["w1:p1"])
        self.assertTrue(any("w2:p2" in w for w in self.warnings), self.warnings)

    def test_the_process_group_leader_wins_when_two_processes_match(self):
        """A pane can hold the agent and a child of it that shares its argv0.

        `foreground_process_group_id` names the job the pane is actually
        running, so it is the tie-break rather than list order.
        """
        self.fixture(
            ["pane", "process-info", "--pane", "w1:p1"],
            process_info(
                pane_id="w1:p1",
                processes=[
                    process_entry(-1, pid=DEAD_PID + 5, argv0="claude", cwd="/work/beta"),
                    process_entry(-1, pid=DEAD_PID + 6, argv0="claude", cwd="/work/beta"),
                ],
                group_id=DEAD_PID + 6,
            ),
        )
        self.assertEqual(self.panes()[0]["pid"], DEAD_PID + 6)

    def test_an_empty_agent_list_is_genuinely_no_panes(self):
        self.fixture(["agent", "list"], agent_list([]))
        self.assertEqual(self.panes(), [])


class TestPanesForFailsClosed(FakeHerdrCase):
    """Every way Herdr can decline, and none of them may become an empty list."""

    def setUp(self) -> None:
        super().setUp()
        self.fixture(["agent", "list"], AGENT_LIST)
        self.fixture(["pane", "process-info", "--pane", "w1:p1"],
                     info("w1:p1", "/work/beta", DEAD_PID))
        self.fixture(["pane", "process-info", "--pane", "w2:p2"],
                     info("w2:p2", "/work/herdr", DEAD_PID + 1))

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
        answer = captured("agent-list")
        del answer["result"]["agents"]
        self.fixture(["agent", "list"], answer)
        with self.assertRaises(feed.HerdrError):
            self.panes()

    def test_a_shape_with_no_process_info_raises(self):
        answer = captured("pane-process-info")
        del answer["result"]["process_info"]
        self.fixture(["pane", "process-info", "--pane", "w2:p2"], answer)
        with self.assertRaises(feed.HerdrError):
            self.panes()

    def test_a_shape_with_no_foreground_process_list_raises(self):
        answer = captured("pane-process-info")
        del answer["result"]["process_info"]["foreground_processes"]
        self.fixture(["pane", "process-info", "--pane", "w2:p2"], answer)
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
            {"pane_id": "w1:p1", "cwd": "/work/beta", "pid": 1, "pid_start_epoch": 10},
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
