#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""adapters/copilot -- GitHub Copilot CLI, heuristic, by directory, UNVERIFIED.

GitHub Copilot CLI is not installed on this machine, so this adapter's own
`probe` output carries `unverified: true` and this suite's fixture
(tests/fixtures/copilot/event.json) is a CONSTRUCTION written from GitHub's
own documentation
(https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference)
and from Herdr's own `copilot` integration hook, not a capture of a real
installation -- see tests/fixtures/copilot/README.md for exactly what is
confirmed (the `$COPILOT_HOME` / `~/.copilot` pair, the `session-state/<id>/`
layout, the `events.jsonl` filename) versus guessed (that a session's
working directory lives at `data.cwd` inside the first event).

Two things this adapter must get right regardless of that uncertainty,
because docs/adapters.md requires them of every adapter: it never promotes a
lone directory match to `exact`, and an unreadable or unexpectedly shaped
store yields no candidates and a warning, not an exception -- one broken
session must not take the whole store down, the same discipline
adapters/codex already applies to a malformed rollout.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import REPO_ROOT, isolate_environment, load_feed, write_copilot_session  # noqa: E402

isolate_environment()

feed = load_feed()

ADAPTER = REPO_ROOT / "adapters" / "copilot"


def pane(pane_id="w2:p2", cwd="/work/alpha", pid=1, pid_start_epoch=1) -> dict:
    return {"pane_id": pane_id, "cwd": cwd, "pid": pid, "pid_start_epoch": pid_start_epoch}


class TempHomeCase(unittest.TestCase):
    """Every test gets its own throwaway config dir, in a throwaway HOME.

    tests/run.sh already gives every test FILE a fresh $HOME; $COPILOT_HOME
    is this adapter's own override, so every TEST also gets an isolated tree
    and nothing here can reach a real ~/.copilot.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name) / "copilot-home"
        self._env_saved = dict(os.environ)
        self.addCleanup(self._restore_env)
        os.environ["COPILOT_HOME"] = str(self.config_dir)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_saved)


class TestProbe(TempHomeCase):
    def test_the_adapter_exists_and_is_executable(self):
        self.assertTrue(ADAPTER.is_file(), ADAPTER)
        self.assertTrue(os.access(ADAPTER, os.X_OK), ADAPTER)

    def test_probe_satisfies_the_general_contract(self):
        feed.validate_probe(feed.probe(ADAPTER))

    def test_probe_declares_copilot_heuristic_and_unverified(self):
        (self.config_dir / "session-state").mkdir(parents=True)
        obj = feed.probe(ADAPTER)
        self.assertEqual(obj["agent"], "copilot")
        self.assertEqual(obj["source"], "herdr:copilot")
        self.assertEqual(obj["confidence"], "heuristic")
        self.assertIs(obj.get("unverified"), True, "this integration has never been confirmed")

    def test_available_true_when_the_config_dir_has_a_session_state_directory(self):
        (self.config_dir / "session-state").mkdir(parents=True)
        self.assertIs(feed.probe(ADAPTER)["available"], True)

    def test_available_false_when_there_is_no_session_state_directory(self):
        self.config_dir.mkdir(parents=True)
        self.assertIs(feed.probe(ADAPTER)["available"], False)

    def test_falls_back_to_home_copilot_when_the_env_var_is_unset(self):
        del os.environ["COPILOT_HOME"]
        fake_home = self.config_dir.parent / "home"
        (fake_home / ".copilot" / "session-state").mkdir(parents=True)
        os.environ["HOME"] = str(fake_home)
        self.assertIs(feed.probe(ADAPTER)["available"], True)

    def test_never_writes_anything(self):
        (self.config_dir / "session-state").mkdir(parents=True)
        before = list(self.config_dir.rglob("*"))
        feed.probe(ADAPTER)
        self.assertEqual(list(self.config_dir.rglob("*")), before)


class TestResolve(TempHomeCase):
    def test_a_session_in_the_panes_directory_is_reported_heuristic(self):
        write_copilot_session(self.config_dir, "sess-a", data={"cwd": "/work/alpha"})
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidates = feed.candidates_by_pane(results)["w2:p2"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["session_id"], "sess-a")
        self.assertEqual(candidates[0]["confidence"], "heuristic")

    def test_a_session_under_a_different_directory_is_excluded(self):
        write_copilot_session(self.config_dir, "sess-a", data={"cwd": "/work/beta"})
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        self.assertEqual(feed.candidates_by_pane(results).get("w2:p2", []), [])

    def test_a_candidate_never_claims_exact_even_when_it_is_the_only_one(self):
        write_copilot_session(self.config_dir, "sess-a", data={"cwd": "/work/alpha"})
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidates = feed.candidates_by_pane(results)["w2:p2"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["confidence"], "heuristic")

    def test_the_session_id_is_the_session_state_directory_name(self):
        """The one thing this adapter does NOT have to guess (README: CONFIRMED)."""
        write_copilot_session(self.config_dir, "a-real-looking-id-123", data={"cwd": "/work/alpha"})
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidate = feed.candidates_by_pane(results)["w2:p2"][0]
        self.assertEqual(candidate["session_id"], "a-real-looking-id-123")

    def test_newest_session_first_by_the_first_events_timestamp(self):
        write_copilot_session(
            self.config_dir, "older", data={"cwd": "/work/alpha"}, timestamp="2026-09-04T09:12:00Z"
        )
        write_copilot_session(
            self.config_dir, "newer", data={"cwd": "/work/alpha"}, timestamp="2026-09-06T09:12:00Z"
        )
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidates = feed.candidates_by_pane(results)["w2:p2"]
        self.assertEqual([c["session_id"] for c in candidates], ["newer", "older"])

    def test_a_session_whose_first_event_is_not_json_is_skipped_not_fatal(self):
        broken_dir = self.config_dir / "session-state" / "broken"
        broken_dir.mkdir(parents=True)
        (broken_dir / "events.jsonl").write_text("not json at all\n", encoding="utf-8")
        write_copilot_session(self.config_dir, "good", data={"cwd": "/work/alpha"})
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        self.assertEqual(len(feed.candidates_by_pane(results)["w2:p2"]), 1)

    def test_a_session_whose_first_event_has_no_data_cwd_is_skipped_not_fatal(self):
        shapeless_dir = self.config_dir / "session-state" / "shapeless"
        shapeless_dir.mkdir(parents=True)
        (shapeless_dir / "events.jsonl").write_text('{"type": "other"}\n', encoding="utf-8")
        write_copilot_session(self.config_dir, "good", data={"cwd": "/work/alpha"})
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        self.assertEqual(len(feed.candidates_by_pane(results)["w2:p2"]), 1)

    def test_a_session_directory_with_no_events_file_is_skipped_not_fatal(self):
        (self.config_dir / "session-state" / "empty-dir").mkdir(parents=True)
        write_copilot_session(self.config_dir, "good", data={"cwd": "/work/alpha"})
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        self.assertEqual(len(feed.candidates_by_pane(results)["w2:p2"]), 1)

    def test_only_the_first_line_of_an_events_log_is_ever_read(self):
        path = write_copilot_session(
            self.config_dir,
            "good",
            data={"cwd": "/work/alpha"},
            extra_lines=["not json at all, and huge enough to matter if ever read"],
        )
        self.assertIn("not json", path.read_text(encoding="utf-8"))
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        self.assertEqual(len(feed.candidates_by_pane(results)["w2:p2"]), 1)

    def test_a_missing_session_state_directory_yields_no_candidates_and_a_warning(self):
        """docs/adapters.md's general 'unreadable == error' rule is deliberately
        NOT what this one case does -- the phase brief calls for a store-level
        failure to be a quiet, reportable non-answer instead, the same way a
        probe declaring `available: false` is the adapter working correctly,
        not failing. Warn, but still exit 0 with valid JSON.
        """
        self.config_dir.mkdir(parents=True)
        proc = subprocess.run(
            [str(ADAPTER), "resolve"],
            input='{"panes":[{"pane_id":"w2:p2","cwd":"/work/alpha","pid":1,"pid_start_epoch":1}]}',
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotEqual(proc.stderr.strip(), "", "an unreadable store must warn")
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        self.assertEqual(feed.candidates_by_pane(results).get("w2:p2", []), [])

    def test_answers_about_fewer_panes_than_were_asked_is_fine(self):
        write_copilot_session(self.config_dir, "sess-a", data={"cwd": "/work/alpha"})
        results = feed.resolve(
            ADAPTER,
            [pane(pane_id="w2:p2", cwd="/work/alpha"), pane(pane_id="w9:p9", cwd="/work/z")],
        )
        by_pane = feed.candidates_by_pane(results)
        self.assertEqual(len(by_pane.get("w2:p2", [])), 1)
        self.assertEqual(by_pane.get("w9:p9", []), [])

    def test_it_never_writes_anything(self):
        write_copilot_session(self.config_dir, "sess-a", data={"cwd": "/work/alpha"})
        before = sorted(str(p) for p in self.config_dir.rglob("*"))
        feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        after = sorted(str(p) for p in self.config_dir.rglob("*"))
        self.assertEqual(before, after)

    def test_it_answers_an_empty_pane_list_without_falling_over(self):
        (self.config_dir / "session-state").mkdir(parents=True)
        results = feed.resolve(ADAPTER, [])
        self.assertEqual(feed.candidates_by_pane(results), {})


class TestNeverTouchesHerdr(unittest.TestCase):
    def test_the_source_never_mentions_the_herdr_socket(self):
        text = ADAPTER.read_text(encoding="utf-8")
        for forbidden in ("herdr.sock", "HERDR_SOCKET_PATH", "report_agent_session"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
