#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""adapters/codex's `sessions` subcommand -- the rollout walk and `session-meta`
evidence.

Built from the captures in tests/fixtures/codex/ (see that directory's
README): session-meta.json (an ordinary thread with a `git` block),
session-meta-no-git.json (no `git` key at all) and session-meta-subthread.json
(`payload.parent_thread_id` set). Version 1 does no command parsing at all
(docs/adapters.md), so this file, unlike
tests/test_adapter_claude_sessions.py, has no shape-per-evidence section --
`session-meta` is the only evidence Codex can ever report.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import (  # noqa: E402
    REAL_BRANCH_NAMES,
    REPO_ROOT,
    UNREAL_BRANCH_NAMES,
    codex_session_meta,
    isolate_environment,
    load_adapter_module,
    load_feed,
    write_codex_rollout,
)

isolate_environment()

feed = load_feed()

ADAPTER = REPO_ROOT / "adapters" / "codex"
DAY = 86400


def run_sessions(config_dir: Path, *extra_args: str, timeout: float = 30):
    env = dict(os.environ)
    env["CODEX_HOME"] = str(config_dir)
    return subprocess.run(
        [str(ADAPTER), "sessions", *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def sessions_of(config_dir: Path, *extra_args: str) -> list[dict]:
    proc = run_sessions(config_dir, *extra_args)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["sessions"]


class TempHomeCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name) / "codex-home"
        self.config_dir.mkdir(parents=True)


class TestProbe(TempHomeCase):
    def test_probe_declares_sessions_true(self):
        env = dict(os.environ)
        env["CODEX_HOME"] = str(self.config_dir)
        proc = subprocess.run(
            [str(ADAPTER), "probe"], capture_output=True, text=True, timeout=10, env=env
        )
        obj = json.loads(proc.stdout)
        self.assertIs(obj["sessions"], True)
        feed.validate_probe(obj)


class TestIdentityAndGitBranch(TempHomeCase):
    def test_a_rollout_yields_expected_identity(self):
        # session-meta.json's own captured branch is "main" (filtered) -- see
        # test_main_is_filtered_out below; identity is checked here instead.
        write_codex_rollout(
            self.config_dir, 2026, 9, 6, "rollout-2026-09-06T19-29-39-a.jsonl", cwd="/work/alpha"
        )
        rollout_path = (
            self.config_dir
            / "sessions"
            / "2026"
            / "09"
            / "06"
            / "rollout-2026-09-06T19-29-39-a.jsonl"
        )
        meta = codex_session_meta()["payload"]

        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session["id"], meta["id"])
        self.assertEqual(session["cwd"], "/work/alpha")

        expected_mtime = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(rollout_path.stat().st_mtime)
        )
        self.assertEqual(session["last_active"], expected_mtime)

    def test_a_real_branch_yields_session_meta_evidence(self):
        write_codex_rollout(
            self.config_dir,
            2026,
            9,
            6,
            "rollout-2026-09-06T19-29-39-a.jsonl",
            cwd="/work/alpha",
            git={
                "commit_hash": "0" * 40,
                "branch": "feat/health-endpoint",
                "repository_url": "https://github.com/example-org/example-repo.git",
            },
        )
        meta = codex_session_meta()["payload"]
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(len(sessions[0]["branches"]), 1)
        branch = sessions[0]["branches"][0]
        self.assertEqual(branch["name"], "feat/health-endpoint")
        self.assertEqual(branch["dir"], "/work/alpha")
        self.assertEqual(branch["evidence"], "session-meta")
        self.assertEqual(branch["seen_at"], meta["timestamp"])

    def test_main_is_filtered_out(self):
        # session-meta.json's own captured branch value is "main".
        write_codex_rollout(
            self.config_dir, 2026, 9, 6, "rollout-2026-09-06T19-29-39-a.jsonl", cwd="/work/alpha"
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["branches"], [])


class TestNoGitOrDetachedHead(TempHomeCase):
    def test_no_git_block_at_all_yields_no_branches(self):
        day_dir = self.config_dir / "sessions" / "2026" / "07" / "10"
        day_dir.mkdir(parents=True)
        fixture = REPO_ROOT / "tests" / "fixtures" / "codex" / "session-meta-no-git.json"
        line = json.loads(fixture.read_text("utf-8"))
        path = day_dir / "rollout-2026-07-10T05-35-43-b.jsonl"
        path.write_text(json.dumps(line) + "\n", encoding="utf-8")

        sessions = sessions_of(self.config_dir, "--since", "3650")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["cwd"], "/work/beta")
        self.assertEqual(sessions[0]["branches"], [])

    def test_a_null_branch_detached_head_yields_no_branches(self):
        """A construction: no real capture had a detached-HEAD rollout (README)."""
        write_codex_rollout(
            self.config_dir,
            2026,
            9,
            6,
            "rollout-2026-09-06T19-29-39-a.jsonl",
            cwd="/work/alpha",
            git={
                "commit_hash": "0" * 40,
                "branch": None,
                "repository_url": "https://github.com/example-org/example-repo.git",
            },
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["branches"], [])

    def test_an_empty_branch_string_yields_no_branches(self):
        write_codex_rollout(
            self.config_dir,
            2026,
            9,
            6,
            "rollout-2026-09-06T19-29-39-a.jsonl",
            cwd="/work/alpha",
            git={
                "commit_hash": "0" * 40,
                "branch": "",
                "repository_url": "https://github.com/example-org/example-repo.git",
            },
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["branches"], [])


class TestSubthreadsAreSkipped(TempHomeCase):
    def test_a_parent_thread_id_rollout_is_skipped_entirely(self):
        day_dir = self.config_dir / "sessions" / "2026" / "07" / "06"
        day_dir.mkdir(parents=True)
        fixture = REPO_ROOT / "tests" / "fixtures" / "codex" / "session-meta-subthread.json"
        line = json.loads(fixture.read_text("utf-8"))
        path = day_dir / "rollout-2026-07-06T11-05-17-c.jsonl"
        path.write_text(json.dumps(line) + "\n", encoding="utf-8")

        self.assertEqual(sessions_of(self.config_dir, "--since", "3650"), [])

    def test_a_subthread_beside_a_normal_thread_only_the_normal_one_is_reported(self):
        day_dir = self.config_dir / "sessions" / "2026" / "07" / "06"
        day_dir.mkdir(parents=True)
        fixture = REPO_ROOT / "tests" / "fixtures" / "codex" / "session-meta-subthread.json"
        (day_dir / "rollout-2026-07-06T11-05-17-c.jsonl").write_text(
            fixture.read_text("utf-8"), encoding="utf-8"
        )
        write_codex_rollout(
            self.config_dir, 2026, 7, 6, "rollout-2026-07-06T12-00-00-d.jsonl", cwd="/work/alpha"
        )
        sessions = sessions_of(self.config_dir, "--since", "3650")
        self.assertEqual(len(sessions), 1)


class TestToleranceAndWindow(TempHomeCase):
    def test_an_empty_file_is_skipped(self):
        day_dir = self.config_dir / "sessions" / "2026" / "09" / "06"
        day_dir.mkdir(parents=True)
        (day_dir / "rollout-empty.jsonl").write_text("", encoding="utf-8")
        self.assertEqual(sessions_of(self.config_dir, "--since", "30"), [])

    def test_a_truncated_first_line_is_skipped(self):
        day_dir = self.config_dir / "sessions" / "2026" / "09" / "06"
        day_dir.mkdir(parents=True)
        (day_dir / "rollout-trunc.jsonl").write_text(
            '{"type":"session_meta","payload":{"id":"x"', encoding="utf-8"
        )
        self.assertEqual(sessions_of(self.config_dir, "--since", "30"), [])

    def test_a_non_session_meta_first_line_is_skipped(self):
        day_dir = self.config_dir / "sessions" / "2026" / "09" / "06"
        day_dir.mkdir(parents=True)
        (day_dir / "rollout-other.jsonl").write_text(
            json.dumps({"type": "turn_context", "payload": {"cwd": "/work/alpha"}}) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(sessions_of(self.config_dir, "--since", "30"), [])

    def test_a_file_older_than_since_is_ignored(self):
        path = write_codex_rollout(
            self.config_dir, 2026, 9, 6, "rollout-2026-09-06T19-29-39-a.jsonl", cwd="/work/alpha"
        )
        old_mtime = path.stat().st_mtime - 40 * DAY
        os.utime(path, (old_mtime, old_mtime))
        self.assertEqual(sessions_of(self.config_dir, "--since", "30"), [])

    def test_a_file_within_since_is_included(self):
        path = write_codex_rollout(
            self.config_dir, 2026, 9, 6, "rollout-2026-09-06T19-29-39-a.jsonl", cwd="/work/alpha"
        )
        recent_mtime = path.stat().st_mtime - 5 * DAY
        os.utime(path, (recent_mtime, recent_mtime))
        self.assertEqual(len(sessions_of(self.config_dir, "--since", "30")), 1)

    def test_include_sdk_is_accepted_and_ignored(self):
        write_codex_rollout(
            self.config_dir, 2026, 9, 6, "rollout-2026-09-06T19-29-39-a.jsonl", cwd="/work/alpha"
        )
        with_flag = sessions_of(self.config_dir, "--since", "30", "--include-sdk")
        without_flag = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(with_flag, without_flag)

    def test_only_the_first_line_is_ever_read(self):
        """A malformed, non-UTF-8 SECOND line must never break reading a good first one."""
        path = write_codex_rollout(
            self.config_dir,
            2026,
            9,
            6,
            "rollout-2026-09-06T19-29-39-a.jsonl",
            cwd="/work/alpha",
        )
        with path.open("ab") as handle:
            handle.write(b"\xff\xfe not valid utf-8, and not json either\n")
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(len(sessions), 1)


class TestNoSessionsDirectory(unittest.TestCase):
    def test_no_sessions_dir_answers_an_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "codex-home"
            config_dir.mkdir()
            proc = run_sessions(config_dir, "--since", "30")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout), {"sessions": []})


class TestUsageAndReadOnly(TempHomeCase):
    def test_an_unknown_subcommand_exits_2(self):
        env = dict(os.environ)
        env["CODEX_HOME"] = str(self.config_dir)
        proc = subprocess.run(
            [str(ADAPTER), "bogus"], capture_output=True, text=True, timeout=10, env=env
        )
        self.assertEqual(proc.returncode, 2)

    def test_sessions_never_writes_anything(self):
        write_codex_rollout(
            self.config_dir, 2026, 9, 6, "rollout-2026-09-06T19-29-39-a.jsonl", cwd="/work/alpha"
        )
        before = sorted(str(p) for p in self.config_dir.rglob("*"))
        before_mtimes = {str(p): p.stat().st_mtime for p in self.config_dir.rglob("*")}
        sessions_of(self.config_dir, "--since", "30")
        after = sorted(str(p) for p in self.config_dir.rglob("*"))
        after_mtimes = {str(p): p.stat().st_mtime for p in self.config_dir.rglob("*")}
        self.assertEqual(before, after)
        self.assertEqual(before_mtimes, after_mtimes)


class TestBranchNameFilterAgreesWithTheRunner(unittest.TestCase):
    """checklist item 7, this adapter's own copy versus lib/feed.py's."""

    def setUp(self) -> None:
        self.adapter_module = load_adapter_module("codex")

    def test_every_unreal_name_is_rejected_by_the_adapters_own_copy(self):
        for name in UNREAL_BRANCH_NAMES:
            self.assertFalse(self.adapter_module.branch_name_ok(name), name)

    def test_every_real_name_is_accepted_by_the_adapters_own_copy(self):
        for name in REAL_BRANCH_NAMES:
            self.assertTrue(self.adapter_module.branch_name_ok(name), name)


if __name__ == "__main__":
    unittest.main(verbosity=1)
