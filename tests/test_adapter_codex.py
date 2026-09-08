#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""adapters/codex -- heuristic, by directory.

Codex CLI keeps no per-process session file, only a tree of rollout files at
`<config>/sessions/<yyyy>/<mm>/<dd>/rollout-<timestamp>-<id>.jsonl`, whose
FIRST line is a `session_meta` record carrying the working directory the
session started in. Two panes open in the same directory look identical to
this adapter, so it is `heuristic` and always confirms -- unlike
adapters/claude, which can tie a session to a specific process.

This suite tests against a CAPTURE of a real rollout's first line
(tests/fixtures/codex/), not an invented shape -- see that directory's
README, and tests/fixtures/herdr/README.md for why that discipline exists.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import (  # noqa: E402
    REPO_ROOT,
    codex_session_meta,
    isolate_environment,
    load_feed,
    write_codex_rollout,
)

isolate_environment()

feed = load_feed()

ADAPTER = REPO_ROOT / "adapters" / "codex"


def pane(pane_id="w2:p2", cwd="/work/alpha", pid=1, pid_start_epoch=1) -> dict:
    return {"pane_id": pane_id, "cwd": cwd, "pid": pid, "pid_start_epoch": pid_start_epoch}


class TempHomeCase(unittest.TestCase):
    """Every test gets its own throwaway config dir, in a throwaway HOME.

    tests/run.sh already gives every test FILE a fresh $HOME; $CODEX_HOME is
    this adapter's own override (mirroring $CLAUDE_CONFIG_DIR), so every
    TEST also gets an isolated tree and nothing here can reach a real one.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name) / "codex-home"
        self._env_saved = dict(os.environ)
        self.addCleanup(self._restore_env)
        os.environ["CODEX_HOME"] = str(self.config_dir)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_saved)


class TestProbe(TempHomeCase):
    def test_the_adapter_exists_and_is_executable(self):
        self.assertTrue(ADAPTER.is_file(), ADAPTER)
        self.assertTrue(os.access(ADAPTER, os.X_OK), ADAPTER)

    def test_probe_satisfies_the_general_contract(self):
        feed.validate_probe(feed.probe(ADAPTER))

    def test_probe_declares_codex_heuristic(self):
        (self.config_dir / "sessions").mkdir(parents=True)
        obj = feed.probe(ADAPTER)
        self.assertEqual(obj["agent"], "codex")
        self.assertEqual(obj["source"], "herdr:codex")
        self.assertEqual(obj["confidence"], "heuristic")

    def test_available_true_when_the_config_dir_has_a_sessions_directory(self):
        (self.config_dir / "sessions").mkdir(parents=True)
        self.assertIs(feed.probe(ADAPTER)["available"], True)

    def test_available_false_when_there_is_no_sessions_directory(self):
        self.assertIs(feed.probe(ADAPTER)["available"], False)

    def test_never_writes_anything(self):
        (self.config_dir / "sessions").mkdir(parents=True)
        before = list(self.config_dir.rglob("*"))
        feed.probe(ADAPTER)
        self.assertEqual(list(self.config_dir.rglob("*")), before)


class TestResolve(TempHomeCase):
    def test_a_session_in_the_panes_directory_is_reported_heuristic(self):
        write_codex_rollout(
            self.config_dir, 2026, 9, 6, "rollout-2026-09-06T22-29-39-a.jsonl", cwd="/work/alpha"
        )
        session_id = codex_session_meta()["payload"]["id"]

        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidates = feed.candidates_by_pane(results)["w2:p2"]

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["session_id"], session_id)
        self.assertEqual(candidates[0]["confidence"], "heuristic")

    def test_a_session_under_a_different_cwd_is_excluded(self):
        write_codex_rollout(
            self.config_dir, 2026, 9, 6, "rollout-2026-09-06T22-29-39-a.jsonl", cwd="/work/beta"
        )
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        self.assertEqual(feed.candidates_by_pane(results).get("w2:p2", []), [])

    def test_a_candidate_never_claims_exact_even_when_it_is_the_only_one(self):
        """The runner reads the CANDIDATE's confidence, not the probe's alone.

        A heuristic adapter matching on directory must never promote a lone
        match to `exact` -- two panes in the same directory would still be
        indistinguishable, and this is precisely the case that would fool
        the runner into reporting one unasked (docs/adapters.md).
        """
        write_codex_rollout(
            self.config_dir, 2026, 9, 6, "rollout-2026-09-06T22-29-39-a.jsonl", cwd="/work/alpha"
        )
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidates = feed.candidates_by_pane(results)["w2:p2"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["confidence"], "heuristic")

    def test_newest_session_first_when_several_match_the_same_directory(self):
        write_codex_rollout(
            self.config_dir,
            2026,
            9,
            6,
            "rollout-2026-09-06T10-00-00-a.jsonl",
            cwd="/work/alpha",
            id="00000000-0000-4000-8000-0000000000a1",
            session_id="00000000-0000-4000-8000-0000000000a1",
            timestamp="2026-09-06T10:00:00.000Z",
        )
        write_codex_rollout(
            self.config_dir,
            2026,
            9,
            6,
            "rollout-2026-09-06T22-00-00-b.jsonl",
            cwd="/work/alpha",
            id="00000000-0000-4000-8000-0000000000b2",
            session_id="00000000-0000-4000-8000-0000000000b2",
            timestamp="2026-09-06T22:00:00.000Z",
        )
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidates = feed.candidates_by_pane(results)["w2:p2"]
        self.assertEqual(
            [c["session_id"] for c in candidates],
            ["00000000-0000-4000-8000-0000000000b2", "00000000-0000-4000-8000-0000000000a1"],
        )

    def test_a_rollout_whose_first_line_is_not_json_is_skipped_not_fatal(self):
        day_dir = self.config_dir / "sessions" / "2026" / "09" / "06"
        day_dir.mkdir(parents=True)
        (day_dir / "rollout-broken.jsonl").write_text("not json at all\n", encoding="utf-8")
        # A well-formed rollout beside the broken one must still be found.
        write_codex_rollout(
            self.config_dir, 2026, 9, 6, "rollout-2026-09-06T22-29-39-a.jsonl", cwd="/work/alpha"
        )
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        self.assertEqual(len(feed.candidates_by_pane(results)["w2:p2"]), 1)

    def test_a_rollout_whose_first_line_is_json_but_not_session_meta_is_skipped(self):
        day_dir = self.config_dir / "sessions" / "2026" / "09" / "06"
        day_dir.mkdir(parents=True)
        (day_dir / "rollout-other.jsonl").write_text(
            json.dumps({"type": "turn_context", "payload": {"cwd": "/work/alpha"}}) + "\n",
            encoding="utf-8",
        )
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        self.assertEqual(feed.candidates_by_pane(results).get("w2:p2", []), [])

    def test_only_the_first_line_of_a_rollout_file_is_ever_read(self):
        """A malformed SECOND line must never break parsing of a good first one."""
        path = write_codex_rollout(
            self.config_dir,
            2026,
            9,
            6,
            "rollout-2026-09-06T22-29-39-a.jsonl",
            cwd="/work/alpha",
            extra_lines=["not json at all, and huge enough to matter if ever read"],
        )
        self.assertIn("not json", path.read_text(encoding="utf-8"))
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        self.assertEqual(len(feed.candidates_by_pane(results)["w2:p2"]), 1)

    def test_the_label_is_built_from_the_rollout_timestamp(self):
        write_codex_rollout(
            self.config_dir,
            2026,
            9,
            6,
            "rollout-2026-09-06T22-29-39-a.jsonl",
            cwd="/work/alpha",
            timestamp="2026-09-06T19:29:39.421Z",
        )
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidate = feed.candidates_by_pane(results)["w2:p2"][0]
        self.assertIn("2026-09-06", candidate.get("label", ""))

    def test_answers_about_fewer_panes_than_were_asked_is_fine(self):
        write_codex_rollout(
            self.config_dir, 2026, 9, 6, "rollout-2026-09-06T22-29-39-a.jsonl", cwd="/work/alpha"
        )
        results = feed.resolve(
            ADAPTER,
            [pane(pane_id="w2:p2", cwd="/work/alpha"), pane(pane_id="w9:p9", cwd="/work/z")],
        )
        by_pane = feed.candidates_by_pane(results)
        self.assertEqual(len(by_pane.get("w2:p2", [])), 1)
        self.assertEqual(by_pane.get("w9:p9", []), [])

    def test_the_scan_is_bounded_to_the_newest_30_day_directories(self):
        """A long history must not make feed slow (the phase's own stated bound).

        31 day directories are written, each with a session matching the
        pane's cwd; the oldest one must be excluded from the answer.
        """
        for day in range(1, 32):
            write_codex_rollout(
                self.config_dir,
                2025,
                1,
                day,
                f"rollout-2025-01-{day:02d}T00-00-00-x.jsonl",
                cwd="/work/alpha",
                id=f"00000000-0000-4000-8000-0000000000{day:02x}",
                session_id=f"00000000-0000-4000-8000-0000000000{day:02x}",
                timestamp=f"2025-01-{day:02d}T00:00:00.000Z",
            )
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidates = feed.candidates_by_pane(results)["w2:p2"]
        self.assertEqual(len(candidates), 30, "only the newest 30 day directories may be scanned")
        oldest_id = f"00000000-0000-4000-8000-0000000000{1:02x}"
        self.assertNotIn(oldest_id, [c["session_id"] for c in candidates])

    def test_it_never_writes_anything(self):
        write_codex_rollout(
            self.config_dir, 2026, 9, 6, "rollout-2026-09-06T22-29-39-a.jsonl", cwd="/work/alpha"
        )
        before = sorted(str(p) for p in self.config_dir.rglob("*"))
        feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        after = sorted(str(p) for p in self.config_dir.rglob("*"))
        self.assertEqual(before, after)

    def test_it_answers_an_empty_pane_list_without_falling_over(self):
        results = feed.resolve(ADAPTER, [])
        self.assertEqual(feed.candidates_by_pane(results), {})


class TestNeverTouchesHerdr(unittest.TestCase):
    def test_the_source_never_mentions_the_herdr_socket(self):
        text = ADAPTER.read_text(encoding="utf-8")
        for forbidden in ("herdr.sock", "HERDR_SOCKET_PATH", "report_agent_session"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
