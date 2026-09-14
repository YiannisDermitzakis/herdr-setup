#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""The audit report: sections, text, JSON and exit codes, on in-memory data.

Spec: "`audit` (read-only)" -- Text output, JSON output, Exit codes. Nothing
here starts a process: build_report() takes already-joined panes, gathered
sessions, resolutions and pull requests, so every rule of the three sections is
checked without git, gh or herdr. run()'s wiring and its error paths are
checked with each stage replaced, which is where "one line, exit 2, no table"
is proven for every error type.
"""

from __future__ import annotations

import io
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from auditlib import isolate_audit_environment, load_audit  # noqa: E402

isolate_audit_environment()

audit = load_audit()

REPO = "/work/example-repo"
FORK = "/work/example-fork"
SLUG = "example-org/example-repo"
FORK_SLUG = "example-user/example-repo"
GENERATED = "2026-09-13T12:00:00Z"
ID_A = "00000000-0000-4000-8000-00000000000a"
ID_B = "00000000-0000-4000-8000-00000000000b"
ID_C = "00000000-0000-4000-8000-00000000000c"
ID_D = "00000000-0000-4000-8000-00000000000d"
ID_E = "00000000-0000-4000-8000-00000000000e"


class World:
    """Joined panes, sessions, resolutions and pull requests, built by hand."""

    def __init__(self):
        self.panes: list[dict] = []
        self.sessions: list[dict] = []
        self.states: dict = {}
        self.index: dict = {}
        self.prs: list[dict] = []

    def resolve(self, name, state, *, repo=REPO, slug=SLUG, pr=None, reason=None):
        self.states[(repo, name)] = audit.Resolution(state, pr, slug, reason)

    def session(self, agent, sid, *, last_active, cwd=REPO, branches=(), title=None):
        """`branches` are (name, dir, repo) tuples; the index maps each to its key."""
        record = {
            "agent": agent,
            "id": sid,
            "cwd": cwd,
            "last_active": last_active,
            "branches": [
                {"name": n, "dir": d, "evidence": "command", "seen_at": last_active}
                for n, d, _ in branches
            ],
        }
        if title is not None:
            record["title"] = title
        for name, directory, repo in branches:
            self.index[(name, directory, cwd)] = (repo, name)
        self.sessions.append(record)

    def pane(self, pane_id, *, agent="claude", sid=ID_A, tab="alpha", note=None, entries=()):
        """`entries` are (name, dir, cwd, evidence, repo) tuples."""
        self.panes.append(
            {
                "pane_id": pane_id,
                "tab": tab,
                "agent": agent,
                "session_id": sid,
                "note": note,
                "entries": [
                    {"name": n, "dir": d, "cwd": c, "evidence": e} for n, d, c, e, _ in entries
                ],
            }
        )
        for name, directory, cwd, _, repo in entries:
            self.index[(name, directory, cwd)] = (repo, name)

    def pr(
        self,
        number,
        head,
        *,
        repo=SLUG,
        head_repo=None,
        draft=False,
        author="example-user",
        bot=False,
    ):
        self.prs.append(
            {
                "repo": repo,
                "number": number,
                "title": f"placeholder title {number}",
                "url": f"https://github.com/{repo}/pull/{number}",
                "head": head,
                "head_repo": head_repo if head_repo is not None else repo,
                "draft": draft,
                "updated_at": "2026-08-30T10:00:00Z",
                "author": author,
                "bot": bot,
            }
        )

    def report(self, *, include_bots=False, incomplete=()):
        return audit.build_report(
            generated_at=GENERATED,
            since_days=30,
            owners=["example-user", "example-org"],
            joined=self.panes,
            sessions=self.sessions,
            resolutions=audit.Resolutions(self.states, self.index),
            prs=self.prs,
            include_bots=include_bots,
            incomplete=list(incomplete),
        )


PR_12 = {"number": 12, "draft": True, "url": "https://github.com/example-org/example-repo/pull/12"}


class TestSectionOne(unittest.TestCase):
    def test_one_branch_per_pane_and_repository_branch_with_its_evidence_merged(self):
        world = World()
        world.resolve("feat/x", "open-pr", pr=PR_12)
        world.pane(
            "w2:p9",
            entries=[
                ("feat/x", REPO, REPO, "command", REPO),
                ("feat/x", REPO + "/sub", REPO, "worktree-path", REPO),
            ],
        )
        report = world.report()
        self.assertEqual(
            report["open"],
            [
                {
                    "pane_id": "w2:p9",
                    "tab": "alpha",
                    "agent": "claude",
                    "session_id": ID_A,
                    "note": None,
                    "branches": [
                        {
                            "repo": SLUG,
                            "name": "feat/x",
                            "state": "open-pr",
                            "pr": PR_12,
                            "evidence": ["command", "worktree-path"],
                            "reason": None,
                        }
                    ],
                }
            ],
        )

    def test_gone_is_hidden_and_counted_every_other_state_is_shown(self):
        world = World()
        for name, state in (
            ("feat/gone", "gone"),
            ("feat/merged", "merged"),
            ("feat/contained", "contained"),
            ("feat/unmerged", "unmerged"),
        ):
            world.resolve(name, state)
        world.resolve(
            "feat/lost",
            "unresolved",
            repo="/work/nowhere",
            slug=None,
            reason="neither /work/nowhere nor the session cwd is in a git work tree",
        )
        world.pane(
            "w2:p9",
            entries=[
                (n, REPO, REPO, "command", REPO)
                for n in ("feat/gone", "feat/merged", "feat/contained", "feat/unmerged")
            ]
            + [("feat/lost", "/work/nowhere", "/work/nowhere", "command", "/work/nowhere")],
        )
        report = world.report()
        branches = report["open"][0]["branches"]
        self.assertEqual(
            [(b["name"], b["state"]) for b in branches],
            [
                ("feat/merged", "merged"),
                ("feat/contained", "contained"),
                ("feat/unmerged", "unmerged"),
                ("feat/lost", "unresolved"),
            ],
        )
        self.assertEqual(branches[-1]["repo"], "/work/nowhere")
        self.assertIn("work tree", branches[-1]["reason"])
        self.assertEqual(report["counts"]["gone"], 1)
        self.assertEqual(report["counts"]["unresolved"], 1)

    def test_a_pane_with_a_note_keeps_it_and_has_no_branches(self):
        world = World()
        world.pane("w2:p7", sid=None, note="session not reported to Herdr")
        self.assertEqual(
            world.report()["open"][0],
            {
                "pane_id": "w2:p7",
                "tab": "alpha",
                "agent": "claude",
                "session_id": None,
                "note": "session not reported to Herdr",
                "branches": [],
            },
        )


class TestSectionTwo(unittest.TestCase):
    def test_unmerged_and_open_pr_only_one_row_per_repository_branch_newest_session(self):
        world = World()
        world.resolve("feat/retry", "open-pr", pr={"number": 7, "draft": False, "url": "u"})
        world.resolve("feat/stale", "unmerged")
        for name, state in (
            ("feat/done", "merged"),
            ("feat/cut", "contained"),
            ("feat/gone", "gone"),
        ):
            world.resolve(name, state)
        world.resolve("feat/lost", "unresolved", repo="/work/nowhere", slug=None, reason="r")
        world.session(
            "claude",
            ID_A,
            last_active="2026-09-08T00:00:00Z",
            branches=[("feat/retry", REPO, REPO), ("feat/done", REPO, REPO)],
        )
        world.session(
            "codex",
            ID_B,
            last_active="2026-09-10T00:00:00Z",
            title="retry the call",
            branches=[("feat/retry", REPO + "/sub", REPO), ("feat/cut", REPO, REPO)],
        )
        world.session(
            "claude",
            ID_C,
            last_active="2026-09-01T00:00:00Z",
            branches=[
                ("feat/retry", REPO, REPO),
                ("feat/stale", REPO, REPO),
                ("feat/gone", REPO, REPO),
                ("feat/lost", "/work/nowhere", "/work/nowhere"),
            ],
        )
        rows = world.report()["closed_unmerged"]
        self.assertEqual(
            [(r["name"], r["state"]) for r in rows],
            [("feat/retry", "open-pr"), ("feat/stale", "unmerged")],
        )
        retry = rows[0]
        self.assertEqual(
            retry,
            {
                "repo": SLUG,
                "name": "feat/retry",
                "state": "open-pr",
                "pr": {"number": 7, "draft": False, "url": "u"},
                "session": {
                    "agent": "codex",
                    "id": ID_B,
                    "cwd": REPO,
                    "last_active": "2026-09-10T00:00:00Z",
                    "title": "retry the call",
                },
                "older_sessions": 2,
            },
        )
        self.assertEqual(rows[1]["older_sessions"], 0)
        self.assertIsNone(rows[1]["session"]["title"])

    def test_a_branch_an_open_panes_session_touched_is_not_stranded(self):
        world = World()
        world.resolve("feat/live", "unmerged")
        world.session(
            "claude", ID_A, last_active="2026-09-10T00:00:00Z", branches=[("feat/live", REPO, REPO)]
        )
        world.session(
            "claude",
            ID_B,
            last_active="2026-09-01T00:00:00Z",
            branches=[("feat/live", REPO + "/other", REPO)],
        )
        world.pane("w2:p9", sid=ID_A, entries=[("feat/live", REPO, REPO, "command", REPO)])
        self.assertEqual(world.report()["closed_unmerged"], [])

    def test_the_same_name_in_two_repositories_is_two_rows(self):
        world = World()
        world.resolve("feat/x", "unmerged")
        world.resolve("feat/x", "unmerged", repo=FORK, slug=FORK_SLUG)
        world.session(
            "claude",
            ID_A,
            last_active="2026-09-10T00:00:00Z",
            branches=[("feat/x", REPO, REPO), ("feat/x", FORK, FORK)],
        )
        rows = world.report()["closed_unmerged"]
        self.assertEqual(sorted(r["repo"] for r in rows), [SLUG, FORK_SLUG])


class TestSectionThree(unittest.TestCase):
    def world(self):
        world = World()
        world.resolve("feat/mine", "contained")
        world.resolve("feat/forked", "gone", repo=FORK, slug=FORK_SLUG)
        world.session(
            "claude",
            ID_A,
            last_active="2026-09-10T00:00:00Z",
            branches=[("feat/mine", REPO, REPO), ("feat/forked", FORK, FORK)],
        )
        world.pr(1, "feat/mine")  # head matches a session branch in the base repository
        world.pr(2, "feat/forked", head_repo=FORK_SLUG)  # ... in the head repository
        world.pr(3, "FEAT/MINE")  # branch names are case-sensitive: not a match
        world.pr(4, "chore/bump", draft=True)
        world.pr(5, "deps/update", author="dependabot[bot]", bot=True)
        world.pr(6, "feat/mine", repo="EXAMPLE-ORG/example-other")  # another repository
        world.pr(7, "fix/orphan", author=None)  # a deleted author is not a bot
        return world

    def test_session_branches_in_the_base_or_head_repository_are_excluded_bots_hidden(self):
        report = self.world().report()
        self.assertEqual([p["number"] for p in report["unmatched_prs"]], [6, 3, 4, 7])
        self.assertEqual(report["counts"]["bots_hidden"], 1)
        self.assertEqual(
            report["unmatched_prs"][2],
            {
                "repo": SLUG,
                "number": 4,
                "title": "placeholder title 4",
                "head": "chore/bump",
                "author": "example-user",
                "bot": False,
                "draft": True,
                "updated_at": "2026-08-30T10:00:00Z",
                "url": "https://github.com/example-org/example-repo/pull/4",
            },
        )

    def test_matching_ignores_the_letter_case_of_the_repository(self):
        world = World()
        world.resolve("feat/mine", "contained", slug="Example-Org/Example-Repo")
        world.session(
            "claude", ID_A, last_active="2026-09-10T00:00:00Z", branches=[("feat/mine", REPO, REPO)]
        )
        world.pr(1, "feat/mine", repo="example-org/example-repo")
        self.assertEqual(world.report()["unmatched_prs"], [])

    def test_include_bots_lists_them_and_hides_none(self):
        report = self.world().report(include_bots=True)
        self.assertIn(5, [p["number"] for p in report["unmatched_prs"]])
        self.assertEqual(report["counts"]["bots_hidden"], 0)


class TestJsonShape(unittest.TestCase):
    def test_exactly_the_specs_keys(self):
        world = World()
        world.resolve("feat/x", "unmerged")
        world.session(
            "claude", ID_B, last_active="2026-09-10T00:00:00Z", branches=[("feat/x", REPO, REPO)]
        )
        world.resolve("feat/y", "contained")
        world.pane("w2:p9", entries=[("feat/y", REPO, REPO, "command", REPO)])
        world.pr(4, "chore/bump")
        data = json.loads(audit.render_json(world.report(incomplete=["adapter codex: failed"])))
        self.assertEqual(
            list(data),
            [
                "generated_at",
                "since_days",
                "owners",
                "open",
                "closed_unmerged",
                "unmatched_prs",
                "counts",
                "incomplete",
            ],
        )
        self.assertEqual((data["generated_at"], data["since_days"]), (GENERATED, 30))
        self.assertEqual(data["owners"], ["example-user", "example-org"])
        self.assertEqual(
            set(data["open"][0]), {"pane_id", "tab", "agent", "session_id", "note", "branches"}
        )
        self.assertEqual(
            set(data["open"][0]["branches"][0]),
            {"repo", "name", "state", "pr", "evidence", "reason"},
        )
        self.assertEqual(
            set(data["closed_unmerged"][0]),
            {"repo", "name", "state", "pr", "session", "older_sessions"},
        )
        self.assertEqual(
            set(data["closed_unmerged"][0]["session"]),
            {"agent", "id", "cwd", "last_active", "title"},
        )
        self.assertEqual(data["closed_unmerged"][0]["session"]["id"], ID_B)  # whole, in JSON
        self.assertEqual(
            set(data["unmatched_prs"][0]),
            {"repo", "number", "title", "head", "author", "bot", "draft", "updated_at", "url"},
        )
        self.assertEqual(list(data["counts"]), ["gone", "unresolved", "bots_hidden"])
        self.assertEqual(data["incomplete"], ["adapter codex: failed"])


def full_world() -> World:
    world = World()
    world.resolve("feat/health-endpoint", "open-pr", pr=PR_12)
    world.resolve("fix/image-size", "unmerged")
    world.resolve("feat/retry", "open-pr", pr={"number": 7, "draft": False, "url": "u"})
    world.resolve("feat/gone", "gone")
    world.resolve("feat/lost", "unresolved", repo="/work/nowhere", slug=None, reason="r")
    world.pane(
        "w2:p9",
        tab="alpha",
        sid=ID_A,
        entries=[("feat/health-endpoint", REPO, REPO, "command", REPO)],
    )
    world.pane(
        "w2:p2", tab="beta", sid=ID_B, entries=[("fix/image-size", REPO, REPO, "command", REPO)]
    )
    world.pane("w2:p7", tab="beta", agent="codex", sid=None, note="session not reported to Herdr")
    world.session(
        "claude",
        ID_A,
        last_active="2026-09-12T00:00:00Z",
        branches=[("feat/health-endpoint", REPO, REPO)],
    )
    world.session(
        "claude",
        ID_B,
        last_active="2026-09-12T00:00:00Z",
        branches=[("fix/image-size", REPO, REPO)],
    )
    for sid, day in ((ID_C, "10"), (ID_D, "05"), (ID_E, "01")):
        world.session(
            "claude",
            sid,
            last_active=f"2026-09-{day}T00:00:00Z",
            branches=[
                ("feat/retry", REPO, REPO),
                ("feat/gone", REPO, REPO),
                ("feat/lost", "/work/nowhere", "/work/nowhere"),
            ],
        )
    world.pr(31, "chore/bump", draft=True)
    world.pr(32, "deps/x", author="dependabot[bot]", bot=True)
    return world


class TestText(unittest.TestCase):
    def text(self, world=None, **kwargs) -> list[str]:
        world = world or full_world()
        report = world.report(**kwargs)
        return audit.render_text(report, session_count=len(world.sessions)).splitlines()

    def test_the_header_line_counts_panes_and_sessions_and_names_the_owners(self):
        self.assertEqual(
            self.text()[0],
            "audit: 3 panes, 5 sessions since 2026-08-14, owners: example-user, example-org",
        )

    def test_the_three_sections_with_the_specs_columns(self):
        lines = self.text()
        for heading, columns in (
            ("open in Herdr", ["PANE", "TAB", "AGENT", "SESSION", "REPO", "BRANCH", "STATE"]),
            (
                "closed, with unmerged branches",
                ["AGENT", "SESSION", "LAST ACTIVE", "REPO", "BRANCH", "STATE"],
            ),
            (
                "open PRs no session is working on",
                ["REPO", "PR", "BRANCH", "AUTHOR", "UPDATED", "NOTE"],
            ),
        ):
            with self.subTest(heading=heading):
                at = lines.index(heading)
                self.assertEqual(lines[at - 1], "")
                self.assertEqual(re.split(r"\s{2,}", lines[at + 1].strip()), columns)

    def test_rows_in_the_specs_shape(self):
        lines = self.text()
        rows = {
            tuple(re.split(r"\s{2,}", line.strip()))
            for line in lines
            if line.startswith("  ") and not line.strip().isupper()
        }
        self.assertIn(
            (
                "w2:p9",
                "alpha",
                "claude",
                "00000000",
                SLUG,
                "feat/health-endpoint",
                "open PR #12 (draft)",
            ),
            rows,
        )
        self.assertIn(
            ("w2:p2", "beta", "claude", "00000000", SLUG, "fix/image-size", "unmerged"), rows
        )
        self.assertIn(
            ("w2:p7", "beta", "codex", "-", "-", "-", "session not reported to Herdr"), rows
        )
        self.assertIn(
            ("claude", "00000000 +2", "2026-09-10", SLUG, "feat/retry", "open PR #7"), rows
        )
        self.assertIn((SLUG, "#31", "chore/bump", "example-user", "2026-08-30", "draft"), rows)

    def test_columns_line_up(self):
        lines = self.text()
        at = lines.index("open in Herdr")
        block = lines[at + 1 : at + 5]
        starts = [m.start() for m in re.finditer(r"(?<=\s\s)\S", block[0])]
        for line in block[1:]:
            for start in starts:
                self.assertNotEqual(line[start - 1 : start], "", line)
                self.assertEqual(line[start - 2 : start], "  ", (line, start))

    def test_an_empty_section_says_none(self):
        lines = self.text(World())
        at = lines.index("closed, with unmerged branches")
        self.assertEqual(lines[at + 1], "  (none)")

    def test_the_footer_counts_what_was_not_listed_and_no_incomplete_line(self):
        lines = self.text()
        self.assertEqual(lines[-1], "not listed: 1 branch gone, 1 unresolved, 1 bot PR hidden.")
        self.assertFalse(any(line.startswith("incomplete:") for line in lines))
        self.assertEqual(
            self.text(World())[-1], "not listed: 0 branches gone, 0 unresolved, 0 bot PRs hidden."
        )

    def test_an_incomplete_run_ends_with_its_reasons(self):
        lines = self.text(incomplete=["adapter codex: sessions exited 1", "adapter x: skipped"])
        self.assertEqual(
            lines[-1], "incomplete: adapter codex: sessions exited 1; adapter x: skipped"
        )

    def test_no_line_ends_in_whitespace(self):
        for line in self.text():
            self.assertEqual(line, line.rstrip())

    def test_a_pane_whose_every_branch_is_gone_still_has_its_row(self):
        world = World()
        world.resolve("feat/gone", "gone")
        world.pane("w2:p4", tab="gamma", entries=[("feat/gone", REPO, REPO, "command", REPO)])
        report = world.report()
        self.assertEqual((report["open"][0]["note"], report["open"][0]["branches"]), (None, []))
        rows = [line for line in self.text(world) if line.startswith("  w2:p4")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            re.split(r"\s{2,}", rows[0].strip()),
            ["w2:p4", "gamma", "claude", "00000000", "-", "-", "only gone branches (counted)"],
        )


class TestExitCode(unittest.TestCase):
    def test_zero_one_and_two(self):
        def report(closed=(), unmatched=(), incomplete=(), open_=()):
            return {
                "open": list(open_),
                "closed_unmerged": list(closed),
                "unmatched_prs": list(unmatched),
                "incomplete": list(incomplete),
            }

        for expected, kwargs in (
            (0, {}),
            (0, {"open_": [{"pane_id": "w2:p1"}]}),
            (1, {"closed": [{}]}),
            (1, {"unmatched": [{}]}),
            (2, {"incomplete": ["r"]}),
            (2, {"incomplete": ["r"], "closed": [{}], "unmatched": [{}]}),
        ):
            with self.subTest(**{k: len(v) for k, v in kwargs.items()}):
                self.assertEqual(audit.exit_code(report(**kwargs)), expected)


CLONE_A = "/work/clone-a"
CLONE_B = "/work/clone-b"
CLONE_C = "/work/clone-c"
PR_5 = {"number": 5, "draft": False, "url": "https://github.com/example-org/example-repo/pull/5"}


class TestClonesOfOneRepository(unittest.TestCase):
    """Several checkouts of one GitHub repository are that one repository."""

    def clones(self, state="open-pr", pr=PR_5, clones=(CLONE_A, CLONE_B, CLONE_C)):
        world = World()
        for clone in clones:
            world.resolve("feat/x", state, repo=clone, pr=pr)
        return world

    def session_in(self, world, sid, clone, day):
        world.session(
            "claude",
            sid,
            cwd=clone,
            last_active=f"2026-09-{day}T00:00:00Z",
            branches=[("feat/x", clone, clone)],
        )

    def test_an_open_pane_in_one_clone_excludes_the_branch_closed_sessions_worked_in_others(self):
        world = self.clones()
        self.session_in(world, ID_A, CLONE_A, "12")
        self.session_in(world, ID_B, CLONE_B, "10")
        self.session_in(world, ID_C, CLONE_C, "09")
        world.pane("w1:p1", sid=ID_A, entries=[("feat/x", CLONE_A, CLONE_A, "command", CLONE_A)])
        world.pr(5, "feat/x")
        report = world.report()
        self.assertEqual(report["closed_unmerged"], [])
        self.assertEqual(report["unmatched_prs"], [])
        self.assertEqual(audit.exit_code(report), 0)

    def test_the_open_pane_in_a_clone_no_closed_session_used_still_excludes_it(self):
        world = self.clones()
        self.session_in(world, ID_B, CLONE_B, "10")
        self.session_in(world, ID_C, CLONE_C, "09")
        world.pane(
            "w1:p1", sid=None, entries=[("feat/x", CLONE_A, CLONE_A, "worktree-path", CLONE_A)]
        )
        self.assertEqual(world.report()["closed_unmerged"], [])

    def test_closed_sessions_in_two_clones_are_one_row_with_the_older_counted(self):
        world = self.clones(clones=(CLONE_B, CLONE_C))
        self.session_in(world, ID_C, CLONE_C, "09")
        self.session_in(world, ID_B, CLONE_B, "10")
        rows = world.report()["closed_unmerged"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            (rows[0]["repo"], rows[0]["name"], rows[0]["session"]["id"], rows[0]["older_sessions"]),
            (SLUG, "feat/x", ID_B, 1),
        )

    def test_closed_sessions_in_three_clones_are_one_row_with_both_older_counted(self):
        world = self.clones()
        self.session_in(world, ID_A, CLONE_A, "05")
        self.session_in(world, ID_B, CLONE_B, "11")
        self.session_in(world, ID_C, CLONE_C, "09")
        rows = world.report()["closed_unmerged"]
        self.assertEqual([(r["session"]["id"], r["older_sessions"]) for r in rows], [(ID_B, 2)])

    def test_clones_that_disagree_report_the_most_actionable_state_everywhere(self):
        world = World()
        world.resolve("feat/x", "contained", repo=CLONE_A)
        world.resolve("feat/x", "unmerged", repo=CLONE_B)
        world.resolve("feat/x", "gone", repo=CLONE_C)
        self.session_in(world, ID_A, CLONE_A, "12")
        self.session_in(world, ID_B, CLONE_B, "10")
        self.session_in(world, ID_C, CLONE_C, "09")
        report = world.report()
        self.assertEqual(
            [(r["name"], r["state"], r["older_sessions"]) for r in report["closed_unmerged"]],
            [("feat/x", "unmerged", 2)],
        )
        self.assertEqual(report["counts"]["gone"], 0)
        world.pane("w1:p1", sid=ID_A, entries=[("feat/x", CLONE_A, CLONE_A, "command", CLONE_A)])
        report = world.report()
        self.assertEqual(
            [(b["name"], b["state"]) for b in report["open"][0]["branches"]],
            [("feat/x", "unmerged")],
        )
        self.assertEqual(report["closed_unmerged"], [])

    def test_the_state_order_between_clones(self):
        order = ["unmerged", "open-pr", "unresolved", "contained", "merged", "gone"]
        for better, worse in zip(order, order[1:], strict=False):
            with self.subTest(better=better, worse=worse):
                world = World()
                world.resolve("feat/x", worse, repo=CLONE_A)
                world.resolve("feat/x", better, repo=CLONE_B)
                world.pane(
                    "w1:p1",
                    entries=[
                        ("feat/x", CLONE_A, CLONE_A, "command", CLONE_A),
                        ("feat/x", CLONE_B, CLONE_B, "command", CLONE_B),
                    ],
                )
                branches = world.report()["open"][0]["branches"]
                self.assertEqual(
                    [b["state"] for b in branches], [] if better == "gone" else [better]
                )

    def test_the_repository_is_compared_ignoring_the_case_of_its_slug(self):
        world = World()
        world.resolve("feat/x", "unmerged", repo=CLONE_A, slug="Example-Org/Example-Repo")
        world.resolve("feat/x", "unmerged", repo=CLONE_B, slug=SLUG)
        self.session_in(world, ID_A, CLONE_A, "12")
        self.session_in(world, ID_B, CLONE_B, "10")
        self.assertEqual(len(world.report()["closed_unmerged"]), 1)

    def test_checkouts_with_no_github_remote_stay_apart(self):
        world = World()
        world.resolve("feat/x", "unmerged", repo=CLONE_A, slug=None)
        world.resolve("feat/x", "unmerged", repo=CLONE_B, slug=None)
        self.session_in(world, ID_A, CLONE_A, "12")
        self.session_in(world, ID_B, CLONE_B, "10")
        rows = world.report()["closed_unmerged"]
        self.assertEqual(sorted(r["repo"] for r in rows), [CLONE_A, CLONE_B])


class TestRun(unittest.TestCase):
    """run() with every stage replaced: order, output, arguments, and every error path."""

    def stages(self, calls: list[str], **raising):
        world = full_world()
        self.received: dict[str, list] = {}

        def stage(name, value):
            def call(*args, **kwargs):
                calls.append(name)
                self.received.setdefault(name, []).append((args, kwargs))
                if name in raising:
                    raise raising[name]
                return value

            return call

        adapters_value = ([], ["adapter broken: skipped"], {"broken"})
        gathered = audit.Gathered(sessions=world.sessions, failed={}, incomplete=[])
        return {
            "require_tools": stage("require_tools", None),
            "owners": stage("owners", ["example-user", "example-org"]),
            "panes": stage("panes", []),
            "load_adapters": stage("load_adapters", adapters_value),
            "gather_sessions": stage("gather_sessions", gathered),
            "join": stage("join", world.panes),
            "resolve_branches": stage(
                "resolve_branches", audit.Resolutions(world.states, world.index)
            ),
            "open_prs": stage("open_prs", world.prs),
        }

    def run_audit(self, argv, **raising):
        calls: list[str] = []
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.multiple(audit, **self.stages(calls, **raising)):
            code = audit.main(argv, out=out, err=err, now=lambda: GENERATED)
        return code, out.getvalue(), err.getvalue(), calls

    def test_every_stage_in_order_then_the_report(self):
        code, out, _, calls = self.run_audit(["--owner", "example-org"])
        self.assertEqual(
            calls,
            [
                "require_tools",
                "owners",
                "panes",
                "load_adapters",
                "gather_sessions",
                "join",
                "resolve_branches",
                "open_prs",
                "open_prs",
            ],
        )
        self.assertEqual(code, 2)  # load_adapters reported a skipped adapter
        self.assertIn("open in Herdr", out)
        self.assertTrue(out.rstrip().endswith("incomplete: adapter broken: skipped"))

    def test_the_command_line_reaches_every_consumer(self):
        code, out, _, _ = self.run_audit(
            [
                "--since",
                "7",
                "--include-sdk",
                "--include-bots",
                "--owner",
                "example-org",
                "--owner",
                "example-user",
            ]
        )
        self.assertEqual(self.received["owners"], [((["example-org", "example-user"],), {})])
        [(args, kwargs)] = self.received["gather_sessions"]
        self.assertEqual(args[1], 7)
        self.assertIs(kwargs["include_sdk"], True)
        [(_, join_kwargs)] = self.received["join"]
        self.assertEqual(join_kwargs["failed_probes"], {"broken"})
        self.assertEqual(
            [args[0] for args, _ in self.received["open_prs"]], ["example-user", "example-org"]
        )
        prs = out[out.index("open PRs no session is working on") :]
        self.assertIn("deps/x", prs)
        self.assertIn("0 bot PRs hidden", out)

    def test_an_open_pull_request_two_owners_both_list_is_reported_once(self):
        # The open_prs stage answers the same pull requests for both owners,
        # as GitHub did for a repository one owner owns and the other
        # collaborates on: section 3 lists each (repository, number) once.
        _, out, _, calls = self.run_audit(["--json", "--include-bots"])
        self.assertEqual(calls.count("open_prs"), 2)
        rows = json.loads(out)["unmatched_prs"]
        keys = [(row["repo"].lower(), row["number"]) for row in rows]
        self.assertTrue(keys)
        self.assertEqual(len(keys), len(set(keys)), keys)

    def test_without_flags_the_defaults_reach_them(self):
        _, out, _, _ = self.run_audit([])
        self.assertEqual(self.received["owners"], [(([],), {})])
        [(args, kwargs)] = self.received["gather_sessions"]
        self.assertEqual(args[1], 30)
        self.assertIs(kwargs["include_sdk"], False)
        self.assertNotIn("deps/x", out)
        self.assertRegex(out, r"not listed: .*, [1-9][0-9]* bot PRs? hidden\.")

    def test_json_prints_one_object(self):
        code, out, _, _ = self.run_audit(["--json"])
        self.assertEqual(json.loads(out)["generated_at"], GENERATED)
        self.assertEqual(code, 2)

    def test_every_error_type_prints_one_line_and_exits_two_with_no_table(self):
        errors = (
            ("require_tools", audit.ToolError("gh is not authenticated for github.com: x")),
            ("owners", audit.GhError("gh api user: exited 1: boom")),
            ("panes", audit.feed.HerdrError("herdr agent list: exited 1: boom")),
            ("resolve_branches", audit.GitError("git in /work/x: rev-parse warned: y")),
            ("open_prs", audit.GhError("gh api graphql HsOwnerPullRequests: exited 1: z")),
        )
        for stage, error in errors:
            with self.subTest(stage=stage):
                code, out, err, calls = self.run_audit([], **{stage: error})
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertEqual(err.splitlines(), [f"herdr-setup: audit: {error}"])
                self.assertEqual(calls[-1], stage)

    def test_an_unexpected_exception_is_exit_two_never_a_finding(self):
        code, out, err, _ = self.run_audit([], panes=KeyError("surprise"))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("Traceback", err)
        self.assertIn("herdr-setup: audit: unexpected error: KeyError", err)


if __name__ == "__main__":
    unittest.main(verbosity=1)
