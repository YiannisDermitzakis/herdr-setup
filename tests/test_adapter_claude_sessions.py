#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""adapters/claude's `sessions` subcommand -- the store walk, identity, title,
SDK filtering and the `git-branch-field` evidence.

Built from the line SHAPES captured in tests/fixtures/claude/transcript.jsonl
(see that file's README): a `user` line, an `assistant` line with a Bash
`tool_use`, a `custom-title` line and an `ai-title` line, all sharing one
`sessionId`. Every store here is a throwaway `CLAUDE_CONFIG_DIR` under a
temp directory; nothing touches a real home. `adapters/claude sessions` is
run as an actual subprocess throughout, the same way tests/test_contract.py
and tests/test_adapter_claude.py exercise `probe`/`resolve`.

Command and worktree-path evidence (task 3) are tested separately, in the
same file, once that evidence extractor exists -- see the header comment
further down.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import REPO_ROOT, isolate_environment, load_feed  # noqa: E402

isolate_environment()

feed = load_feed()

ADAPTER = REPO_ROOT / "adapters" / "claude"
TRANSCRIPT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "claude" / "transcript.jsonl"

DAY = 86400


def run_sessions(
    config_dir: Path, *extra_args: str, timeout: float = 30
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
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


class TempConfigCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name) / "claude-config"
        self.config_dir.mkdir(parents=True)

    def project_dir(self, slug: str = "example") -> Path:
        d = self.config_dir / "projects" / slug
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_transcript(self, lines, slug: str = "example", name: str = "s1") -> Path:
        path = self.project_dir(slug) / f"{name}.jsonl"
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        return path


def line(**overrides) -> dict:
    """A minimal well-formed transcript line, one field short of nothing."""
    base = {
        "type": "user",
        "sessionId": "00000000-0000-4000-8000-000000000001",
        "cwd": "/work/alpha",
        "timestamp": "2026-09-12T18:04:11.000Z",
        "entrypoint": "cli",
    }
    base.update(overrides)
    return base


class TestProbe(TempConfigCase):
    def test_probe_declares_sessions_true(self):
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = str(self.config_dir)
        proc = subprocess.run(
            [str(ADAPTER), "probe"], capture_output=True, text=True, timeout=10, env=env
        )
        obj = json.loads(proc.stdout)
        self.assertIs(obj["sessions"], True)
        feed.validate_probe(obj)


class TestIdentityAndFields(TempConfigCase):
    def test_a_top_level_transcript_yields_expected_identity(self):
        self.write_transcript(
            [
                line(cwd="/work/alpha", timestamp="2026-09-12T18:00:00.000Z"),
                line(type="assistant", cwd="/work/alpha", timestamp="2026-09-12T18:04:11.000Z"),
            ],
            name="00000000-0000-4000-8000-0000000000aa",
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session["id"], "00000000-0000-4000-8000-0000000000aa")
        self.assertEqual(session["cwd"], "/work/alpha")
        self.assertEqual(session["last_active"], "2026-09-12T18:04:11.000Z")

    def test_cwd_is_the_last_one_seen(self):
        self.write_transcript(
            [
                line(cwd="/work/alpha", timestamp="2026-09-12T18:00:00.000Z"),
                line(cwd="/work/beta", timestamp="2026-09-12T18:01:00.000Z"),
            ]
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["cwd"], "/work/beta")

    def test_title_prefers_the_latest_custom_title_over_ai_title(self):
        self.write_transcript(
            [
                line(),
                {"type": "ai-title", "aiTitle": "an ai guess", "sessionId": "x"},
                {"type": "custom-title", "customTitle": "the real title", "sessionId": "x"},
            ]
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["title"], "the real title")

    def test_title_falls_back_to_ai_title_when_no_custom_title(self):
        self.write_transcript(
            [line(), {"type": "ai-title", "aiTitle": "an ai guess", "sessionId": "x"}]
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["title"], "an ai guess")

    def test_title_is_omitted_when_neither_is_present(self):
        self.write_transcript([line()])
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertNotIn("title", sessions[0])

    def test_the_latest_of_several_custom_titles_wins(self):
        self.write_transcript(
            [
                line(),
                {"type": "custom-title", "customTitle": "first", "sessionId": "x"},
                {"type": "custom-title", "customTitle": "second", "sessionId": "x"},
            ]
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["title"], "second")


class TestSubagentsAndSidechains(TempConfigCase):
    def test_a_subagent_directory_is_never_walked(self):
        self.write_transcript([line()], name="top")
        subagent_dir = self.project_dir() / "top" / "subagents"
        subagent_dir.mkdir(parents=True)
        (subagent_dir / "sub.jsonl").write_text(
            json.dumps(line(cwd="/work/should-not-appear")) + "\n", encoding="utf-8"
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["cwd"], "/work/alpha")

    def test_a_sidechain_line_is_ignored(self):
        self.write_transcript(
            [
                line(cwd="/work/alpha", timestamp="2026-09-12T18:00:00.000Z"),
                line(
                    cwd="/work/should-not-appear",
                    timestamp="2026-09-12T18:05:00.000Z",
                    isSidechain=True,
                ),
            ]
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["cwd"], "/work/alpha")
        self.assertEqual(sessions[0]["last_active"], "2026-09-12T18:00:00.000Z")


class TestSdkFiltering(TempConfigCase):
    def test_an_sdk_cli_entrypoint_is_skipped_by_default(self):
        self.write_transcript([line(entrypoint="sdk-cli")])
        self.assertEqual(sessions_of(self.config_dir, "--since", "30"), [])

    def test_an_sdk_py_entrypoint_is_skipped_by_default(self):
        self.write_transcript([line(entrypoint="sdk-py")])
        self.assertEqual(sessions_of(self.config_dir, "--since", "30"), [])

    def test_include_sdk_reports_it(self):
        self.write_transcript([line(entrypoint="sdk-cli")])
        sessions = sessions_of(self.config_dir, "--since", "30", "--include-sdk")
        self.assertEqual(len(sessions), 1)

    def test_cli_entrypoint_is_included_without_the_flag(self):
        self.write_transcript([line(entrypoint="cli")])
        self.assertEqual(len(sessions_of(self.config_dir, "--since", "30")), 1)

    def test_claude_desktop_entrypoint_is_included(self):
        self.write_transcript([line(entrypoint="claude-desktop")])
        self.assertEqual(len(sessions_of(self.config_dir, "--since", "30")), 1)


class TestWindowAndTolerance(TempConfigCase):
    def test_a_file_older_than_since_is_ignored(self):
        path = self.write_transcript([line()])
        old_mtime = path.stat().st_mtime - 40 * DAY
        os.utime(path, (old_mtime, old_mtime))
        self.assertEqual(sessions_of(self.config_dir, "--since", "30"), [])

    def test_a_file_within_since_is_included(self):
        path = self.write_transcript([line()])
        recent = 5 * DAY
        mtime = path.stat().st_mtime - recent
        os.utime(path, (mtime, mtime))
        self.assertEqual(len(sessions_of(self.config_dir, "--since", "30")), 1)

    def test_a_truncated_last_line_is_skipped_the_rest_still_read(self):
        path = self.write_transcript(
            [line(cwd="/work/alpha", timestamp="2026-09-12T18:00:00.000Z")]
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"type":"user","cwd":"/work/trunc')  # deliberately truncated
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["cwd"], "/work/alpha")

    def test_an_unparseable_middle_line_is_skipped(self):
        path = self.project_dir() / "s1.jsonl"
        text = "\n".join(
            [
                json.dumps(line(cwd="/work/alpha", timestamp="2026-09-12T18:00:00.000Z")),
                "not json at all",
                json.dumps(line(cwd="/work/alpha", timestamp="2026-09-12T18:01:00.000Z")),
            ]
        )
        path.write_text(text + "\n", encoding="utf-8")
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["last_active"], "2026-09-12T18:01:00.000Z")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores chmod 000")
    def test_an_unreadable_file_is_skipped(self):
        self.write_transcript([line()], name="good")
        bad = self.write_transcript([line(cwd="/work/should-not-appear")], name="bad")
        bad.chmod(0o000)
        try:
            sessions = sessions_of(self.config_dir, "--since", "30")
        finally:
            bad.chmod(0o644)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["id"], "good")


class TestGitBranchFieldEvidence(TempConfigCase):
    def test_gitbranch_becomes_a_git_branch_field_branch(self):
        self.write_transcript(
            [
                line(
                    cwd="/work/alpha",
                    timestamp="2026-09-12T18:00:00.000Z",
                    gitBranch="feat/health-endpoint",
                )
            ]
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        branches = sessions[0]["branches"]
        self.assertEqual(len(branches), 1)
        branch = branches[0]
        self.assertEqual(branch["name"], "feat/health-endpoint")
        self.assertEqual(branch["dir"], "/work/alpha")
        self.assertEqual(branch["evidence"], "git-branch-field")
        self.assertEqual(branch["seen_at"], "2026-09-12T18:00:00.000Z")

    def test_main_is_filtered_out(self):
        self.write_transcript(
            [line(cwd="/work/alpha", timestamp="2026-09-12T18:00:00.000Z", gitBranch="main")]
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["branches"], [])

    def test_gitbranch_is_deduped_per_name_and_dir_keeping_the_latest(self):
        self.write_transcript(
            [
                line(
                    cwd="/work/alpha",
                    timestamp="2026-09-12T18:00:00.000Z",
                    gitBranch="feat/x",
                ),
                line(
                    cwd="/work/alpha",
                    timestamp="2026-09-12T19:00:00.000Z",
                    gitBranch="feat/x",
                ),
            ]
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        branches = sessions[0]["branches"]
        self.assertEqual(len(branches), 1)
        self.assertEqual(branches[0]["seen_at"], "2026-09-12T19:00:00.000Z")

    def test_a_real_capture_line_is_read_correctly(self):
        """tests/fixtures/claude/transcript.jsonl's own line 1 -- gitBranch: main, filtered."""
        lines = [json.loads(text) for text in TRANSCRIPT_FIXTURE.read_text("utf-8").splitlines()]
        path = self.project_dir() / "00000000-0000-4000-8000-000000000001.jsonl"
        path.write_text("\n".join(json.dumps(entry) for entry in lines) + "\n", encoding="utf-8")
        sessions = sessions_of(self.config_dir, "--since", "3650")
        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session["cwd"], "/work/example-repo")
        # customTitle always wins over aiTitle when both exist, regardless of
        # which line came later in the file (docs/adapters.md: "the latest
        # customTitle ... else the latest aiTitle") -- the fixture's own
        # ai-title line (4) comes after its custom-title line (3).
        self.assertEqual(session["title"], "renaming the config module")
        self.assertEqual(session["branches"], [], "the fixture's gitBranch is main, filtered")


class TestNoProjectsDirectory(unittest.TestCase):
    def test_no_projects_dir_answers_an_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "claude-config"
            config_dir.mkdir()
            proc = run_sessions(config_dir, "--since", "30")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout), {"sessions": []})


class TestUsageAndReadOnly(TempConfigCase):
    def test_an_unknown_subcommand_exits_2(self):
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = str(self.config_dir)
        proc = subprocess.run(
            [str(ADAPTER), "bogus"], capture_output=True, text=True, timeout=10, env=env
        )
        self.assertEqual(proc.returncode, 2)

    def test_sessions_never_writes_anything(self):
        self.write_transcript([line()])
        before = sorted(str(p) for p in self.config_dir.rglob("*"))
        before_mtimes = {str(p): p.stat().st_mtime for p in self.config_dir.rglob("*")}
        sessions_of(self.config_dir, "--since", "30")
        after = sorted(str(p) for p in self.config_dir.rglob("*"))
        after_mtimes = {str(p): p.stat().st_mtime for p in self.config_dir.rglob("*")}
        self.assertEqual(before, after)
        self.assertEqual(before_mtimes, after_mtimes)


if __name__ == "__main__":
    unittest.main(verbosity=1)
