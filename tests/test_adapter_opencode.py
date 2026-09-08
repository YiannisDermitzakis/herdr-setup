#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""adapters/opencode -- heuristic, by directory, against a real SQLite schema.

opencode keeps one SQLite database, `opencode.db`, under its XDG data
directory. Two panes open in the same repository look identical to this
adapter -- same discipline as adapters/codex -- so it is `heuristic` and
never promotes a lone match to `exact`.

This suite tests against tests/fixtures/opencode/schema.sql, a byte-for-byte
capture of the real `session` table's `CREATE TABLE` statement (see that
directory's README for provenance and for why the schema is captured but
every row here is synthesised, never copied out of a real database --
tests/fixtures/herdr/README.md is why that split exists at all).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import REPO_ROOT, isolate_environment, load_feed, write_opencode_db  # noqa: E402

isolate_environment()

feed = load_feed()

ADAPTER = REPO_ROOT / "adapters" / "opencode"


def pane(pane_id="w2:p2", cwd="/work/alpha", pid=1, pid_start_epoch=1) -> dict:
    return {"pane_id": pane_id, "cwd": cwd, "pid": pid, "pid_start_epoch": pid_start_epoch}


def session_row(**values) -> dict:
    """A minimal synthetic `session` row; only what a test needs to override."""
    row = {
        "id": "test-session-a",
        "parent_id": None,
        "directory": "/work/alpha",
        "title": "a synthetic session",
        "time_updated": 1_700_000_000_000,
        "time_archived": None,
    }
    row.update(values)
    return row


class TempHomeCase(unittest.TestCase):
    """Every test gets its own throwaway XDG data dir, in a throwaway HOME.

    tests/run.sh already gives every test FILE a fresh $HOME; $XDG_DATA_HOME
    is this adapter's own override, so every TEST also gets an isolated tree
    and nothing here can reach a real opencode.db.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_home = Path(self._tmp.name) / "xdg-data"
        self._env_saved = dict(os.environ)
        self.addCleanup(self._restore_env)
        os.environ["XDG_DATA_HOME"] = str(self.data_home)
        self.db_path = self.data_home / "opencode" / "opencode.db"

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_saved)


class TestProbe(TempHomeCase):
    def test_the_adapter_exists_and_is_executable(self):
        self.assertTrue(ADAPTER.is_file(), ADAPTER)
        self.assertTrue(os.access(ADAPTER, os.X_OK), ADAPTER)

    def test_probe_satisfies_the_general_contract(self):
        feed.validate_probe(feed.probe(ADAPTER))

    def test_probe_declares_opencode_heuristic(self):
        write_opencode_db(self.db_path, [])
        obj = feed.probe(ADAPTER)
        self.assertEqual(obj["agent"], "opencode")
        self.assertEqual(obj["source"], "herdr:opencode")
        self.assertEqual(obj["confidence"], "heuristic")

    def test_available_true_when_the_database_file_exists(self):
        write_opencode_db(self.db_path, [])
        self.assertIs(feed.probe(ADAPTER)["available"], True)

    def test_available_false_when_the_database_file_does_not_exist(self):
        self.assertIs(feed.probe(ADAPTER)["available"], False)

    def test_falls_back_to_the_xdg_default_when_the_env_var_is_unset(self):
        del os.environ["XDG_DATA_HOME"]
        fake_home = self.data_home.parent / "home"
        db_path = fake_home / ".local" / "share" / "opencode" / "opencode.db"
        write_opencode_db(db_path, [])
        os.environ["HOME"] = str(fake_home)
        self.assertIs(feed.probe(ADAPTER)["available"], True)

    def test_never_writes_anything(self):
        write_opencode_db(self.db_path, [])
        before = sorted(str(p) for p in self.data_home.rglob("*"))
        feed.probe(ADAPTER)
        after = sorted(str(p) for p in self.data_home.rglob("*"))
        self.assertEqual(before, after)


class TestResolve(TempHomeCase):
    def test_a_session_in_the_panes_directory_is_reported_heuristic(self):
        write_opencode_db(self.db_path, [session_row(id="s1", directory="/work/alpha")])
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidates = feed.candidates_by_pane(results)["w2:p2"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["session_id"], "s1")
        self.assertEqual(candidates[0]["confidence"], "heuristic")

    def test_a_session_under_a_different_directory_is_excluded(self):
        write_opencode_db(self.db_path, [session_row(id="s1", directory="/work/beta")])
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        self.assertEqual(feed.candidates_by_pane(results).get("w2:p2", []), [])

    def test_a_child_session_is_never_reported(self):
        """P8.T1.S1: `parent_id IS NULL` -- a sub-session never appears on its own."""
        write_opencode_db(
            self.db_path,
            [
                session_row(id="parent", directory="/work/alpha"),
                session_row(id="child", directory="/work/alpha", parent_id="parent"),
            ],
        )
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        ids = [c["session_id"] for c in feed.candidates_by_pane(results)["w2:p2"]]
        self.assertEqual(ids, ["parent"])

    def test_an_archived_session_is_never_reported(self):
        write_opencode_db(
            self.db_path,
            [session_row(id="s1", directory="/work/alpha", time_archived=1_700_000_000_500)],
        )
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        self.assertEqual(feed.candidates_by_pane(results).get("w2:p2", []), [])

    def test_a_candidate_never_claims_exact_even_when_it_is_the_only_one(self):
        write_opencode_db(self.db_path, [session_row(id="s1", directory="/work/alpha")])
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidates = feed.candidates_by_pane(results)["w2:p2"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["confidence"], "heuristic")

    def test_newest_session_first_when_several_match_the_same_directory(self):
        write_opencode_db(
            self.db_path,
            [
                session_row(id="older", directory="/work/alpha", time_updated=1_700_000_000_000),
                session_row(id="newer", directory="/work/alpha", time_updated=1_700_000_500_000),
            ],
        )
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidates = feed.candidates_by_pane(results)["w2:p2"]
        self.assertEqual([c["session_id"] for c in candidates], ["newer", "older"])

    def test_the_label_is_built_from_the_title(self):
        write_opencode_db(
            self.db_path,
            [session_row(id="s1", directory="/work/alpha", title="fix the flaky test")],
        )
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidate = feed.candidates_by_pane(results)["w2:p2"][0]
        self.assertEqual(candidate["label"], "fix the flaky test")

    def test_updated_is_the_time_updated_column_converted_from_milliseconds(self):
        """tests/fixtures/opencode/README.md: time_updated is epoch MILLISECONDS.

        A naive adapter that treats it as epoch seconds does not fail loudly
        -- it prints a date decades in the future -- so this test pins the
        conversion rather than trusting the ordering test alone to catch it.
        """
        write_opencode_db(
            self.db_path,
            [session_row(id="s1", directory="/work/alpha", time_updated=1_774_731_087_364)],
        )
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        candidate = feed.candidates_by_pane(results)["w2:p2"][0]
        updated = candidate.get("updated", "")
        self.assertTrue(updated.startswith("2026-"), updated)

    def test_answers_about_fewer_panes_than_were_asked_is_fine(self):
        write_opencode_db(self.db_path, [session_row(id="s1", directory="/work/alpha")])
        results = feed.resolve(
            ADAPTER,
            [pane(pane_id="w2:p2", cwd="/work/alpha"), pane(pane_id="w9:p9", cwd="/work/z")],
        )
        by_pane = feed.candidates_by_pane(results)
        self.assertEqual(len(by_pane.get("w2:p2", [])), 1)
        self.assertEqual(by_pane.get("w9:p9", []), [])

    def test_the_database_is_never_disturbed(self):
        """Opened `file:...?mode=ro` -- a running opencode must never be blocked.

        The file is made read-only at the filesystem level too, which is
        belt and braces: SQLite's own `mode=ro` already refuses a write, so
        if the adapter opened read-write instead this would fail loudly
        rather than silently succeeding on a developer machine where the
        permission bits happen to allow it.
        """
        write_opencode_db(self.db_path, [session_row(id="s1", directory="/work/alpha")])
        os.chmod(self.db_path, 0o444)
        self.addCleanup(os.chmod, self.db_path, 0o644)
        before = self.db_path.stat().st_mtime
        results = feed.resolve(ADAPTER, [pane(cwd="/work/alpha")])
        after = self.db_path.stat().st_mtime
        self.assertEqual(before, after)
        self.assertEqual(len(feed.candidates_by_pane(results)["w2:p2"]), 1)

    def test_it_answers_an_empty_pane_list_without_falling_over(self):
        write_opencode_db(self.db_path, [])
        results = feed.resolve(ADAPTER, [])
        self.assertEqual(feed.candidates_by_pane(results), {})

    def test_a_pane_with_no_matching_directory_type_yields_no_candidates(self):
        write_opencode_db(self.db_path, [session_row(id="s1", directory="/work/alpha")])
        results = feed.resolve(ADAPTER, [pane(cwd=None)])
        self.assertEqual(feed.candidates_by_pane(results).get("w2:p2", []), [])


class TestNeverTouchesHerdr(unittest.TestCase):
    def test_the_source_never_mentions_the_herdr_socket(self):
        text = ADAPTER.read_text(encoding="utf-8")
        for forbidden in ("herdr.sock", "HERDR_SOCKET_PATH", "report_agent_session"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
