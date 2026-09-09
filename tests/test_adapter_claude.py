#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""adapters/claude -- exact, by process id.

Claude Code is the only `exact` adapter in this tool, because it writes a
session file named after the process id: `<config>/sessions/<pid>.json`. That
file is real, and this suite tests against a CAPTURE of one
(tests/fixtures/claude/), not an invented shape -- see that directory's
README, and tests/fixtures/herdr/README.md for why the rule exists at all.

The one thing most likely to make this adapter wrong is the time frame of
`pid_start_epoch` versus the session file's own `procStart`: `ps -o lstart=`
is local wall-clock, Claude Code's `procStart` is UTC with no zone marker,
and on this suite's author's own machine the two differ by two hours. Every
test that builds a matching pane computes its `pid_start_epoch` the correct
way (`calendar.timegm`), so a naive `time.mktime`-based comparison in the
adapter fails these tests on any host that is not at UTC -- which is exactly
the failure mode docs/adapters.md warns about.
"""

from __future__ import annotations

import calendar
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import (  # noqa: E402
    REPO_ROOT,
    claude_session,
    isolate_environment,
    load_feed,
    write_claude_session,
)

isolate_environment()

feed = load_feed()

ADAPTER = REPO_ROOT / "adapters" / "claude"


def epoch_utc(text: str) -> int:
    """The correct reading of a Claude Code `procStart` string: UTC, no zone marker."""
    return calendar.timegm(time.strptime(text, "%a %b %d %H:%M:%S %Y"))


def pane(pane_id="w2:p2", cwd="/work/alpha", pid=21940, pid_start_epoch=None) -> dict:
    return {"pane_id": pane_id, "cwd": cwd, "pid": pid, "pid_start_epoch": pid_start_epoch}


class TempHomeCase(unittest.TestCase):
    """Every test gets its own throwaway config dir, in a throwaway HOME.

    tests/run.sh already gives every test FILE a fresh $HOME; this is the
    per-TEST directory an adapter is pointed at via $CLAUDE_CONFIG_DIR, so
    that no two tests share state and nothing here can reach a real one.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name) / "claude-config"
        self._env_saved = dict(os.environ)
        self.addCleanup(self._restore_env)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.config_dir)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_saved)


class TestProbe(TempHomeCase):
    def test_the_adapter_exists_and_is_executable(self):
        self.assertTrue(ADAPTER.is_file(), ADAPTER)
        self.assertTrue(os.access(ADAPTER, os.X_OK), ADAPTER)

    def test_probe_satisfies_the_general_contract(self):
        obj = feed.probe(ADAPTER)
        feed.validate_probe(obj)

    def test_probe_declares_claude_exact(self):
        (self.config_dir / "sessions").mkdir(parents=True)
        obj = feed.probe(ADAPTER)
        self.assertEqual(obj["agent"], "claude")
        self.assertEqual(obj["source"], "herdr:claude")
        self.assertEqual(obj["confidence"], "exact")

    def test_available_true_when_the_config_dir_has_a_sessions_directory(self):
        (self.config_dir / "sessions").mkdir(parents=True)
        self.assertIs(feed.probe(ADAPTER)["available"], True)

    def test_available_false_when_the_config_dir_has_no_sessions_directory(self):
        self.config_dir.mkdir(parents=True)
        self.assertIs(feed.probe(ADAPTER)["available"], False)

    def test_available_false_when_the_config_dir_does_not_exist_at_all(self):
        self.assertIs(feed.probe(ADAPTER)["available"], False)

    def test_falls_back_to_home_claude_when_the_env_var_is_unset(self):
        del os.environ["CLAUDE_CONFIG_DIR"]
        fake_home = self.config_dir.parent / "home"
        (fake_home / ".claude" / "sessions").mkdir(parents=True)
        os.environ["HOME"] = str(fake_home)
        self.assertIs(feed.probe(ADAPTER)["available"], True)

    def test_never_writes_anything(self):
        self.config_dir.mkdir(parents=True)
        before = list(self.config_dir.iterdir())
        feed.probe(ADAPTER)
        self.assertEqual(list(self.config_dir.iterdir()), before)


class TestResolve(TempHomeCase):
    def test_a_pid_with_a_session_file_is_reported_exact(self):
        session = claude_session()
        session_id = session["sessionId"]
        write_claude_session(self.config_dir, pid=21940, cwd="/work/alpha")
        start_epoch = epoch_utc(session["procStart"])

        results = feed.resolve(
            ADAPTER, [pane(pid=21940, cwd="/work/alpha", pid_start_epoch=start_epoch)]
        )
        by_pane = feed.candidates_by_pane(results)
        candidates = by_pane["w2:p2"]

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["session_id"], session_id)
        self.assertEqual(candidates[0]["confidence"], "exact")

    def test_a_pid_with_no_session_file_yields_an_empty_list_not_an_error(self):
        # No session file written at all for this pid.
        results = feed.resolve(ADAPTER, [pane(pid=99999, pid_start_epoch=None)])
        by_pane = feed.candidates_by_pane(results)
        self.assertEqual(by_pane.get("w2:p2", []), [])

    def test_a_pane_with_no_pid_yields_an_empty_list(self):
        results = feed.resolve(ADAPTER, [pane(pid=None, pid_start_epoch=None)])
        by_pane = feed.candidates_by_pane(results)
        self.assertEqual(by_pane.get("w2:p2", []), [])

    def test_answers_about_fewer_panes_than_were_asked_is_fine(self):
        write_claude_session(self.config_dir, pid=21940, cwd="/work/alpha")
        start_epoch = epoch_utc(claude_session()["procStart"])
        results = feed.resolve(
            ADAPTER,
            [
                pane(pane_id="w2:p2", pid=21940, cwd="/work/alpha", pid_start_epoch=start_epoch),
                pane(pane_id="w9:p9", pid=99999, cwd="/work/beta", pid_start_epoch=None),
            ],
        )
        by_pane = feed.candidates_by_pane(results)
        self.assertEqual(len(by_pane.get("w2:p2", [])), 1)
        self.assertEqual(by_pane.get("w9:p9", []), [])

    def test_the_utc_versus_local_time_frame_is_handled_correctly(self):
        """The trap docs/adapters.md exists to name.

        `procStart` here is deliberately far enough from any plausible local
        offset (it is captured, not invented) that a `time.mktime`-based
        adapter -- which reads it as LOCAL rather than UTC -- would compute a
        `pid_start_epoch` that disagrees with the correctly-computed one
        below by whatever the host's UTC offset is, and reject the match. A
        `calendar.timegm`-based adapter accepts it regardless of the host
        running this test.
        """
        session = claude_session()
        write_claude_session(self.config_dir, pid=21940, cwd="/work/alpha")
        correct_epoch = epoch_utc(session["procStart"])

        results = feed.resolve(
            ADAPTER, [pane(pid=21940, cwd="/work/alpha", pid_start_epoch=correct_epoch)]
        )
        candidates = feed.candidates_by_pane(results)["w2:p2"]
        self.assertEqual(len(candidates), 1, "a correctly UTC-computed epoch must match")

    def test_a_mismatched_start_time_means_a_recycled_pid_and_is_rejected(self):
        write_claude_session(self.config_dir, pid=21940, cwd="/work/alpha")
        # A start time nowhere near the session file's own procStart: some
        # other process now holds this pid, and its session must not be
        # handed back as this pane's.
        wrong_epoch = epoch_utc(claude_session()["procStart"]) + 999_999
        results = feed.resolve(
            ADAPTER, [pane(pid=21940, cwd="/work/alpha", pid_start_epoch=wrong_epoch)]
        )
        candidates = feed.candidates_by_pane(results)["w2:p2"]
        self.assertEqual(candidates, [], "a recycled pid must not be reported")

    def test_a_null_pid_start_epoch_cannot_rule_out_reuse_but_still_reports(self):
        """Null means 'cannot rule out', not 'reject' -- docs/adapters.md is explicit."""
        write_claude_session(self.config_dir, pid=21940, cwd="/work/alpha")
        results = feed.resolve(ADAPTER, [pane(pid=21940, cwd="/work/alpha", pid_start_epoch=None)])
        candidates = feed.candidates_by_pane(results)["w2:p2"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["confidence"], "exact")

    def test_session_path_points_at_the_slugified_transcript_when_it_exists(self):
        write_claude_session(self.config_dir, pid=21940, cwd="/work/alpha")
        session_id = claude_session()["sessionId"]
        transcript_dir = self.config_dir / "projects" / "-work-alpha"
        transcript_dir.mkdir(parents=True)
        transcript = transcript_dir / f"{session_id}.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        start_epoch = epoch_utc(claude_session()["procStart"])

        results = feed.resolve(
            ADAPTER, [pane(pid=21940, cwd="/work/alpha", pid_start_epoch=start_epoch)]
        )
        candidate = feed.candidates_by_pane(results)["w2:p2"][0]
        self.assertEqual(candidate["session_path"], str(transcript))

    def test_session_path_is_omitted_when_the_transcript_is_missing(self):
        write_claude_session(self.config_dir, pid=21940, cwd="/work/alpha")
        start_epoch = epoch_utc(claude_session()["procStart"])
        results = feed.resolve(
            ADAPTER, [pane(pid=21940, cwd="/work/alpha", pid_start_epoch=start_epoch)]
        )
        candidate = feed.candidates_by_pane(results)["w2:p2"][0]
        self.assertNotIn("session_path", candidate)

    def test_slugification_replaces_every_non_alnum_character_with_a_hyphen(self):
        write_claude_session(self.config_dir, pid=21940, cwd="/work/alpha_beta.gamma 1")
        session_id = claude_session()["sessionId"]
        transcript_dir = self.config_dir / "projects" / "-work-alpha-beta-gamma-1"
        transcript_dir.mkdir(parents=True)
        (transcript_dir / f"{session_id}.jsonl").write_text("{}\n", encoding="utf-8")
        start_epoch = epoch_utc(claude_session()["procStart"])

        results = feed.resolve(
            ADAPTER,
            [pane(cwd="/work/alpha_beta.gamma 1", pid=21940, pid_start_epoch=start_epoch)],
        )
        candidate = feed.candidates_by_pane(results)["w2:p2"][0]
        self.assertIn("session_path", candidate)

    def test_the_transcript_path_uses_the_sessions_own_cwd_not_the_panes(self):
        """The session file, once matched by pid, is the adapter's whole source of truth.

        The pane arrives with its own `cwd` from Herdr, but the transcript
        directory is slugified from the SESSION FILE's own `cwd`
        (tests/fixtures/claude/README.md). They usually agree; this test
        pins the case where they do not, so nobody 'simplifies' this to use
        the pane's cwd instead.
        """
        write_claude_session(self.config_dir, pid=21940, cwd="/work/alpha")
        session_id = claude_session()["sessionId"]
        transcript_dir = self.config_dir / "projects" / "-work-alpha"
        transcript_dir.mkdir(parents=True)
        transcript = transcript_dir / f"{session_id}.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        start_epoch = epoch_utc(claude_session()["procStart"])

        # The pane's own cwd disagrees with the session file's.
        results = feed.resolve(
            ADAPTER, [pane(cwd="/work/somewhere-else", pid=21940, pid_start_epoch=start_epoch)]
        )
        candidate = feed.candidates_by_pane(results)["w2:p2"][0]
        self.assertEqual(candidate["session_path"], str(transcript))

    def test_it_never_writes_anything(self):
        write_claude_session(self.config_dir, pid=21940, cwd="/work/alpha")
        before = sorted(str(p) for p in self.config_dir.rglob("*"))
        feed.resolve(ADAPTER, [pane(pid=21940, cwd="/work/alpha", pid_start_epoch=None)])
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
