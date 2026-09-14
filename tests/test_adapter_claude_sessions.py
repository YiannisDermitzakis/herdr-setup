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
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import (  # noqa: E402
    REAL_BRANCH_NAMES,
    REPO_ROOT,
    UNREAL_BRANCH_NAMES,
    isolate_environment,
    load_adapter_module,
    load_feed,
)

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


def bash_line(
    command: str, *, cwd: str = "/work/alpha", timestamp="2026-09-12T18:04:11.000Z"
) -> dict:
    """An `assistant` line carrying one Bash `tool_use` block, task 3's own shape."""
    return {
        "type": "assistant",
        "cwd": cwd,
        "timestamp": timestamp,
        "sessionId": "00000000-0000-4000-8000-000000000001",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": command}}
            ],
        },
    }


def tool_result_line(
    text: str, *, cwd: str = "/work/alpha", timestamp="2026-09-12T18:05:00.000Z"
) -> dict:
    """A `user` line carrying a `tool_result` block -- never scanned for evidence."""
    return {
        "type": "user",
        "cwd": cwd,
        "timestamp": timestamp,
        "sessionId": "00000000-0000-4000-8000-000000000001",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": text}],
        },
    }


def branch_names(sessions) -> list[str]:
    return [b["name"] for b in sessions[0]["branches"]]


def one_branch(sessions) -> dict:
    branches = sessions[0]["branches"]
    assert len(branches) == 1, branches
    return branches[0]


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
        # Normalised to second precision (review item 5): the input carries
        # ".000Z", the output does not.
        self.assertEqual(session["last_active"], "2026-09-12T18:04:11Z")

    def test_last_active_falls_back_to_the_files_own_mtime(self):
        """review item 8: a transcript with no `timestamp` field anywhere."""
        path = self.write_transcript([{k: v for k, v in line().items() if k != "timestamp"}])
        expected = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["last_active"], expected)

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


class TestTimestampNormalization(TempConfigCase):
    """review item 5: last_active and seen_at are second-precision UTC, `Z`.

    docs/adapters.md requires an adapter to convert an offset and strip a
    fraction itself, never leaving that to the runner.
    """

    def test_an_offset_timestamp_is_converted_to_utc(self):
        self.write_transcript([line(timestamp="2026-09-12T20:04:11+02:00")])
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["last_active"], "2026-09-12T18:04:11Z")

    def test_a_millisecond_timestamp_is_stripped_to_second_precision(self):
        self.write_transcript([line(timestamp="2026-09-12T18:04:11.987Z")])
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["last_active"], "2026-09-12T18:04:11Z")

    def test_git_branch_field_seen_at_is_normalised_too(self):
        self.write_transcript([line(timestamp="2026-09-12T18:04:11.987Z", gitBranch="feat/x")])
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["branches"][0]["seen_at"], "2026-09-12T18:04:11Z")

    def test_command_evidence_seen_at_is_normalised_too(self):
        self.write_transcript(
            [bash_line("git checkout -b feat/y", timestamp="2026-09-12T18:04:11.987+02:00")]
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(sessions[0]["branches"][0]["seen_at"], "2026-09-12T16:04:11Z")

    def test_every_timestamp_matches_the_runner_s_own_pattern(self):
        self.write_transcript(
            [
                line(
                    timestamp="2026-09-12T18:04:11.987+02:00",
                    gitBranch="feat/z",
                )
            ]
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        session = sessions[0]
        self.assertRegex(session["last_active"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        for branch in session["branches"]:
            self.assertRegex(branch["seen_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_an_out_of_range_offset_timestamp_does_not_crash_the_whole_query(self):
        """review re-round item 2: astimezone() raises OverflowError on an
        out-of-range instant (a year-1 date at a positive UTC offset moves
        before datetime.min) -- the Tolerant rule forbids that crashing the
        whole query. Falls back to the file's own mtime, same as any other
        unusable timestamp."""
        path = self.write_transcript([line(timestamp="0001-01-01T00:30:00+01:00")])
        expected = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))
        proc = run_sessions(self.config_dir, "--since", "30")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        sessions = json.loads(proc.stdout)["sessions"]
        self.assertEqual(sessions[0]["last_active"], expected)


class TestNormalizeTimestampDirectly(unittest.TestCase):
    """normalize_timestamp's own rules, called directly rather than through
    a transcript -- review re-round item 2."""

    def setUp(self) -> None:
        self.adapter_module = load_adapter_module("claude")

    def test_no_zone_is_read_as_utc(self):
        self.assertEqual(
            self.adapter_module.normalize_timestamp("2026-09-12T18:04:11"),
            "2026-09-12T18:04:11Z",
        )

    def test_a_date_only_value_is_not_a_timestamp(self):
        self.assertIsNone(self.adapter_module.normalize_timestamp("2026-09-12"))

    def test_an_out_of_range_offset_returns_none_rather_than_raising(self):
        self.assertIsNone(self.adapter_module.normalize_timestamp("0001-01-01T00:30:00+01:00"))

    def test_garbage_returns_none(self):
        self.assertIsNone(self.adapter_module.normalize_timestamp("not-a-timestamp"))


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
        self.assertEqual(sessions[0]["last_active"], "2026-09-12T18:00:00Z")


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

    def test_a_skipped_sdk_transcript_with_invalid_later_lines_is_still_skipped_cleanly(self):
        """review item 11: the SDK skip decides on the FIRST line carrying
        `entrypoint` and stops reading -- proven here by making every later
        line something that would be a problem if it were actually read
        (invalid UTF-8, unparseable JSON, a truncated line)."""
        path = self.write_transcript([line(entrypoint="sdk-cli")])
        with path.open("ab") as handle:
            handle.write(b"\xff\xfe not valid utf-8, and not json either\n")
            handle.write(b'{"type":"user","cwd":"/work/trunc')  # truncated, no closing brace
        self.assertEqual(sessions_of(self.config_dir, "--since", "30"), [])


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
        self.assertEqual(sessions[0]["last_active"], "2026-09-12T18:01:00Z")

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
        self.assertEqual(branch["seen_at"], "2026-09-12T18:00:00Z")

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
        self.assertEqual(branches[0]["seen_at"], "2026-09-12T19:00:00Z")

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


class TestCommandEvidence(TempConfigCase):
    """docs/adapters.md's `command` evidence shapes -- one test per shape."""

    def sessions_for(self, command: str, **kwargs) -> list[dict]:
        self.write_transcript([bash_line(command, **kwargs)])
        return sessions_of(self.config_dir, "--since", "30")

    def test_fr_isolation_up_branch(self):
        sessions = self.sessions_for("fr isolation up --branch feat/a")
        branch = one_branch(sessions)
        self.assertEqual(branch["name"], "feat/a")
        self.assertEqual(branch["evidence"], "command")
        self.assertEqual(branch["dir"], "/work/alpha")

    def test_fr_isolation_up_branch_equals_form(self):
        sessions = self.sessions_for("fr isolation up --branch=feat/a")
        self.assertEqual(one_branch(sessions)["name"], "feat/a")

    def test_fr_isolation_attach_with_repo_sets_dir(self):
        sessions = self.sessions_for(
            "fr isolation attach --session s --branch feat/b --repo /work/r"
        )
        branch = one_branch(sessions)
        self.assertEqual(branch["name"], "feat/b")
        self.assertEqual(branch["dir"], "/work/r")

    def test_git_checkout_dash_b(self):
        sessions = self.sessions_for("git checkout -b fix/c")
        self.assertEqual(one_branch(sessions)["name"], "fix/c")

    def test_git_switch_dash_c(self):
        sessions = self.sessions_for("git switch -c fix/d")
        self.assertEqual(one_branch(sessions)["name"], "fix/d")

    def test_git_checkout_dash_cap_b(self):
        sessions = self.sessions_for("git checkout -B e/f")
        self.assertEqual(one_branch(sessions)["name"], "e/f")

    def test_git_worktree_add_path_then_branch_flag(self):
        sessions = self.sessions_for("git worktree add ../wt -b feat/g", cwd="/work/alpha")
        branch = one_branch(sessions)
        self.assertEqual(branch["name"], "feat/g")
        self.assertEqual(branch["dir"], "/work/wt")

    def test_git_worktree_add_branch_flag_then_path(self):
        sessions = self.sessions_for("git worktree add -b feat/h /work/wt2")
        branch = one_branch(sessions)
        self.assertEqual(branch["name"], "feat/h")
        self.assertEqual(branch["dir"], "/work/wt2")

    def test_git_push_u_origin_branch(self):
        sessions = self.sessions_for("git push -u origin feat/i")
        self.assertEqual(one_branch(sessions)["name"], "feat/i")

    def test_git_push_set_upstream_refspec_with_plus_and_colon(self):
        sessions = self.sessions_for("git push --set-upstream origin +feat/j:feat/j")
        self.assertEqual(one_branch(sessions)["name"], "feat/j")

    def test_git_push_u_origin_head_is_filtered(self):
        sessions = self.sessions_for("git push -u origin HEAD")
        self.assertEqual(branch_names(sessions), [])

    def test_git_push_delete_yields_nothing(self):
        """review re-round item 3: deleting a branch is not evidence of work on it."""
        sessions = self.sessions_for("git push -u origin --delete feat/x")
        self.assertEqual(branch_names(sessions), [])

    def test_git_push_set_upstream_delete_yields_nothing(self):
        sessions = self.sessions_for("git push --set-upstream origin --delete feat/x")
        self.assertEqual(branch_names(sessions), [])

    def test_git_push_dash_lowercase_d_delete_yields_nothing(self):
        sessions = self.sessions_for("git push -u origin -d feat/x")
        self.assertEqual(branch_names(sessions), [])

    def test_git_push_dash_o_value_flag_is_skipped(self):
        """A no-op mutation of _PUSH_VALUE_FLAGS must fail this."""
        sessions = self.sessions_for("git push -u -o ci.skip origin feat/x")
        self.assertEqual(one_branch(sessions)["name"], "feat/x")

    def test_gh_pr_create_head(self):
        sessions = self.sessions_for("gh pr create --head feat/k")
        self.assertEqual(one_branch(sessions)["name"], "feat/k")

    def test_gh_pr_create_dash_cap_h_with_owner_prefix(self):
        sessions = self.sessions_for("gh pr create -H example-user:feat/l")
        self.assertEqual(one_branch(sessions)["name"], "feat/l")

    def test_cd_then_and_and_sets_dir_for_the_later_segment(self):
        sessions = self.sessions_for("cd /work/other && git checkout -b feat/m")
        branch = one_branch(sessions)
        self.assertEqual(branch["name"], "feat/m")
        self.assertEqual(branch["dir"], "/work/other")

    def test_git_dash_cap_c_sets_dir_for_its_own_segment_only(self):
        sessions = self.sessions_for("git -C /work/third switch -c feat/n")
        branch = one_branch(sessions)
        self.assertEqual(branch["name"], "feat/n")
        self.assertEqual(branch["dir"], "/work/third")

    def test_no_spaces_around_the_segment_separator(self):
        sessions = self.sessions_for("a&&git checkout -b feat/o")
        self.assertEqual(one_branch(sessions)["name"], "feat/o")

    def test_an_unbalanced_quote_falls_back_to_whitespace_splitting(self):
        sessions = self.sessions_for('echo "unterminated && git checkout -b feat/p')
        self.assertEqual(one_branch(sessions)["name"], "feat/p")

    def test_a_shell_variable_reference_is_filtered(self):
        sessions = self.sessions_for('git checkout -b "$BR"')
        self.assertEqual(branch_names(sessions), [])

    def test_relative_worktree_add_path_resolves_against_cwd(self):
        sessions = self.sessions_for(
            "git worktree add ../sibling -b feat/rel", cwd="/work/checkout"
        )
        self.assertEqual(one_branch(sessions)["dir"], "/work/sibling")

    # -- item 1: shlex treats "\n" as ordinary whitespace, so a multi-line
    # Bash command must be split into physical lines (joining backslash-
    # newline continuations first) before tokenising, or two independent
    # lines fuse into one segment that matches no shape at all. --

    def test_a_second_line_is_still_read_as_its_own_command(self):
        sessions = self.sessions_for("git fetch\ngit checkout -b feat/nl")
        self.assertEqual(one_branch(sessions)["name"], "feat/nl")

    def test_a_cd_line_does_not_swallow_the_next_lines_command(self):
        sessions = self.sessions_for("cd /work/other\ngit checkout -b feat/nl2")
        branch = one_branch(sessions)
        self.assertEqual(branch["name"], "feat/nl2")
        self.assertEqual(branch["dir"], "/work/other")

    def test_a_backslash_newline_continuation_is_joined(self):
        sessions = self.sessions_for("git checkout \\\n-b feat/cont")
        self.assertEqual(one_branch(sessions)["name"], "feat/cont")

    # -- item 3: ~ expansion, both for `cd` and for `git -C`. --

    def test_cd_tilde_expands_to_home(self):
        home = os.environ.get("HOME", "")
        sessions = self.sessions_for("cd ~/x && git checkout -b b")
        self.assertEqual(one_branch(sessions)["dir"], f"{home}/x")

    def test_git_dash_cap_c_tilde_expands_to_home(self):
        home = os.environ.get("HOME", "")
        sessions = self.sessions_for("git -C ~/x switch -c b")
        self.assertEqual(one_branch(sessions)["dir"], f"{home}/x")

    # -- item 4: a relative `cd` resolves against the CURRENT directory (the
    # latest `cd` in this command), not always the line's own `cwd`. --

    def test_a_second_relative_cd_resolves_against_the_first_cds_result(self):
        sessions = self.sessions_for("cd /work/x && cd y && git checkout -b b", cwd="/work/alpha")
        self.assertEqual(one_branch(sessions)["dir"], "/work/x/y")

    # -- item 6: further command-parsing gaps. --

    def test_git_push_dash_u_dash_f_skips_the_option_before_remote(self):
        sessions = self.sessions_for("git push -u -f origin feat/f")
        self.assertEqual(one_branch(sessions)["name"], "feat/f")

    def test_git_push_refspec_strips_a_leading_refs_heads(self):
        sessions = self.sessions_for("git push -u origin refs/heads/x:x")
        self.assertEqual(one_branch(sessions)["name"], "x")

    def test_git_worktree_add_reason_value_is_not_mistaken_for_the_path(self):
        sessions = self.sessions_for("git worktree add --lock --reason why ../wt -b b")
        self.assertEqual(one_branch(sessions)["dir"], "/work/wt")

    def test_git_checkout_flags_before_dash_b_are_skipped(self):
        sessions = self.sessions_for("git checkout -q -b x")
        self.assertEqual(one_branch(sessions)["name"], "x")

    def test_git_dash_lowercase_c_global_option_before_the_subcommand(self):
        sessions = self.sessions_for("git -c k=v checkout -b x")
        self.assertEqual(one_branch(sessions)["name"], "x")

    def test_a_subshell_is_its_own_segment(self):
        sessions = self.sessions_for("(git checkout -b x)")
        self.assertEqual(one_branch(sessions)["name"], "x")

    def test_cd_dash_falls_back_to_the_lines_cwd(self):
        sessions = self.sessions_for("cd - && git checkout -b b", cwd="/work/alpha")
        self.assertEqual(one_branch(sessions)["dir"], "/work/alpha")

    def test_bare_cd_falls_back_to_the_lines_cwd(self):
        sessions = self.sessions_for("cd /work/other && cd && git checkout -b b", cwd="/work/alpha")
        self.assertEqual(one_branch(sessions)["dir"], "/work/alpha")


class TestBranchNameFilterAgreesWithTheRunner(unittest.TestCase):
    """checklist item 7: this adapter's own copy must not drift from lib/feed.py's.

    Both filters run over the exact same two name lists
    (tests/helpers/feedlib.py's UNREAL_BRANCH_NAMES / REAL_BRANCH_NAMES,
    also exercised by tests/test_feed_sessions.py against the runner's own
    copy) -- proof the two independently-written implementations still
    agree, not just that each one individually matches the document.
    """

    def setUp(self) -> None:
        self.adapter_module = load_adapter_module("claude")

    def test_every_unreal_name_is_rejected_by_the_adapters_own_copy(self):
        for name in UNREAL_BRANCH_NAMES:
            self.assertFalse(self.adapter_module.branch_name_ok(name), name)

    def test_every_real_name_is_accepted_by_the_adapters_own_copy(self):
        for name in REAL_BRANCH_NAMES:
            self.assertTrue(self.adapter_module.branch_name_ok(name), name)


class TestWorktreePathEvidence(TempConfigCase):
    # Deliberately not the plan brief's own suggested "/work/" + "home" +
    # "/.cache/..." example: tests/test_public_hygiene.sh's home-directory
    # check matches that shape anywhere in the tree (fixture or not), so a
    # different placeholder segment is used here instead.
    WT_PATH = "/work/box/.cache/fr/worktrees/example-repo/feat__q"

    def test_a_worktree_path_in_cwd_yields_its_branch(self):
        self.write_transcript([line(cwd=f"{self.WT_PATH}/src")])
        sessions = sessions_of(self.config_dir, "--since", "30")
        branch = one_branch(sessions)
        self.assertEqual(branch["name"], "feat/q")
        self.assertEqual(branch["dir"], self.WT_PATH)
        self.assertEqual(branch["evidence"], "worktree-path")

    def test_a_worktree_path_inside_a_command_yields_its_branch(self):
        self.write_transcript([bash_line(f"cat {self.WT_PATH}/src/notes.txt", cwd="/work/alpha")])
        sessions = sessions_of(self.config_dir, "--since", "30")
        branch = one_branch(sessions)
        self.assertEqual(branch["name"], "feat/q")
        self.assertEqual(branch["dir"], self.WT_PATH)
        self.assertEqual(branch["evidence"], "worktree-path")

    def test_a_worktree_path_inside_a_tool_result_is_not_evidence(self):
        self.write_transcript(
            [
                bash_line("ls", cwd="/work/alpha", timestamp="2026-09-12T18:00:00.000Z"),
                tool_result_line(f"total 3\n{self.WT_PATH}/src/notes.txt\n"),
            ]
        )
        sessions = sessions_of(self.config_dir, "--since", "30")
        self.assertEqual(branch_names(sessions), [])

    def test_a_tilde_prefixed_worktree_path_expands_to_home(self):
        home = os.environ.get("HOME", "")
        tilde_path = "~/.cache/fr/worktrees/example-repo/feat__q"
        self.write_transcript([line(cwd=f"{tilde_path}/src")])
        sessions = sessions_of(self.config_dir, "--since", "30")
        branch = one_branch(sessions)
        self.assertEqual(branch["name"], "feat/q")
        self.assertEqual(branch["dir"], f"{home}/.cache/fr/worktrees/example-repo/feat__q")

    def test_a_slug_followed_by_a_semicolon_does_not_swallow_it(self):
        self.write_transcript([bash_line(f"(cd {self.WT_PATH}; ls)", cwd="/work/alpha")])
        sessions = sessions_of(self.config_dir, "--since", "30")
        branch = one_branch(sessions)
        self.assertEqual(branch["name"], "feat/q")
        self.assertEqual(branch["dir"], self.WT_PATH)

    def test_a_slug_followed_by_and_and_does_not_swallow_it(self):
        self.write_transcript([bash_line(f"cd {self.WT_PATH}&&ls", cwd="/work/alpha")])
        sessions = sessions_of(self.config_dir, "--since", "30")
        branch = one_branch(sessions)
        self.assertEqual(branch["name"], "feat/q")
        self.assertEqual(branch["dir"], self.WT_PATH)


if __name__ == "__main__":
    unittest.main(verbosity=1)
