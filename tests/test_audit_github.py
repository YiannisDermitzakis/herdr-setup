#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""The audit runner's GitHub layer (lib/audit.py), against the fake gh.

docs/superpowers/specs/2026-09-13-session-audit-design.md, "GitHub queries"
and "Bots", is the contract. Three rules carry most of the weight here:

- **Any error is a failure.** A non-zero `gh` exit, and an `errors` array
  returned with exit 0, both raise GhError quoting what gh said. Nothing
  becomes an empty answer.
- **Branch names travel as `-f` strings.** `-F` type-infers, so a branch
  named 123 would reach GitHub as a number; the fake rejects exactly that.
- **The queries are the captured ones.** Every query the module sends is
  re-run through the fake and its answer compared with the capture of the
  same operation, so the module's field selections cannot drift from what
  GitHub was seen to return.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from auditlib import (  # noqa: E402
    GH_CAPTURES,
    ZERO_OID,
    FakeGh,
    capture,
    gh_pr,
    gh_repo,
    gh_state,
    isolate_audit_environment,
    load_audit,
    restricted_path,
    shape,
)

isolate_audit_environment()

audit = load_audit()

ORG = "example-org"


class GhCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.fake = FakeGh(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def use(self, state: dict, **switches) -> None:
        self.fake.write(state)
        self.enterContext(self.fake.active(**switches))

    def rerun(self, argv: list[str], **switches) -> subprocess.CompletedProcess:
        """Send a logged gh call again, exactly, and return the raw answer."""
        env = {**os.environ, **self.fake.env(**switches)}
        return subprocess.run(  # noqa: S603
            ["gh", *argv], capture_output=True, text=True, env=env, timeout=120
        )

    def assert_no_typed_fields(self) -> None:
        for call in self.fake.graphql_calls():
            self.assertEqual(call["typed"], {}, f"{call['operation']} sent -F fields")


def without_nested_page_info(answer, connection_path):
    """Remove and check `pullRequests.pageInfo`, the one construction added.

    The captures were taken with the spec's selection, which cannot tell a
    repository with more than 100 open pull requests from one with 100. The
    module also selects `pageInfo { hasNextPage endCursor }` on that
    connection; its shape is the captured `repositories.pageInfo` shape.
    """
    node = answer
    for key in connection_path:
        node = node[key]
    connections = node if isinstance(node, list) else [node]
    for connection in connections:
        info = connection["pullRequests"].pop("pageInfo")
        assert set(info) == {"hasNextPage", "endCursor"}, info
        assert isinstance(info["hasNextPage"], bool), info
    return answer


class TestRequireTools(GhCase):
    def test_it_passes_and_checks_auth_for_github_com_only(self):
        self.use(gh_state())
        self.assertIsNone(audit.require_tools())
        self.assertEqual(self.fake.calls(), [["auth", "status", "--hostname", "github.com"]])

    def test_git_missing_is_named(self):
        self.use(gh_state())
        path = restricted_path(self.tmp, ["gh", "uv"])
        with mock.patch.dict(os.environ, {"PATH": path}), self.assertRaises(audit.ToolError) as ctx:
            audit.require_tools()
        self.assertRegex(str(ctx.exception), r"\bgit\b")
        self.assertNotRegex(str(ctx.exception), r"\bgh\b")
        self.assertEqual(self.fake.calls(), [])

    def test_gh_missing_is_named(self):
        self.use(gh_state())
        path = restricted_path(self.tmp, ["git"])
        with mock.patch.dict(os.environ, {"PATH": path}), self.assertRaises(audit.ToolError) as ctx:
            audit.require_tools()
        self.assertRegex(str(ctx.exception), r"\bgh\b")
        self.assertNotRegex(str(ctx.exception), r"\bgit\b")

    def test_an_unauthenticated_gh_is_named_and_quoted(self):
        self.use(gh_state(), FAKE_GH_UNAUTH=1)
        with self.assertRaises(audit.ToolError) as ctx:
            audit.require_tools()
        self.assertIn("github.com", str(ctx.exception))
        self.assertIn("token in keyring is invalid", str(ctx.exception))


class TestOwners(GhCase):
    def test_default_is_the_user_then_every_org_across_pages(self):
        self.use(gh_state(orgs=[ORG, "example-org-2"]), FAKE_GH_PAGE_SIZE=1)
        self.assertEqual(audit.owners([]), ["example-user", ORG, "example-org-2"])
        calls = self.fake.calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][:2], ["api", "user"])
        self.assertIn("--jq", calls[0])
        self.assertEqual(calls[0][calls[0].index("--jq") + 1], ".login")
        self.assertIn("--paginate", calls[1])
        self.assertIn("user/orgs", calls[1])

    def test_given_owners_replace_the_default_without_any_gh_call(self):
        self.use(gh_state())
        self.assertEqual(audit.owners(["example-x", "example-y"]), ["example-x", "example-y"])
        self.assertEqual(self.fake.calls(), [])

    def test_given_owners_are_deduplicated_ignoring_case(self):
        self.use(gh_state())
        self.assertEqual(audit.owners([ORG, "Example-Org", "example-y"]), [ORG, "example-y"])

    def test_a_login_that_cannot_be_a_github_login_is_refused_by_name(self):
        self.use(gh_state())
        for bad in ("", "example org", "example-org/example-repo", "-example"):
            with self.subTest(login=bad), self.assertRaises(audit.GhError) as ctx:
                audit.owners([bad])
            self.assertIn(repr(bad), str(ctx.exception))
        self.assertEqual(self.fake.calls(), [])

    def test_a_failing_user_call_raises_quoting_gh(self):
        self.use(gh_state(), FAKE_GH_FAIL="api user")
        with self.assertRaises(audit.GhError) as ctx:
            audit.owners([])
        self.assertIn("FAKE_GH_FAIL", str(ctx.exception))


class TestOpenPullRequests(GhCase):
    def state(self) -> dict:
        repo_1 = gh_repo(
            prs=[
                gh_pr(
                    1,
                    "dependabot/npm_and_yarn/example-2.0.0",
                    repo=f"{ORG}/example-repo-1",
                    author={"login": "dependabot", "__typename": "Bot"},
                ),
                gh_pr(
                    2,
                    "chore/bump",
                    repo=f"{ORG}/example-repo-1",
                    author={"login": "example-app[bot]", "__typename": "User"},
                ),
                gh_pr(3, "feat/orphan", repo=f"{ORG}/example-repo-1", author=None, draft=True),
                gh_pr(4, "feat/merged", repo=f"{ORG}/example-repo-1", state="MERGED"),
                gh_pr(5, "feat/closed", repo=f"{ORG}/example-repo-1", state="CLOSED"),
            ]
        )
        archived = gh_repo(archived=True, prs=[gh_pr(1, "feat/old", repo=f"{ORG}/example-repo-2")])
        repo_3 = gh_repo(
            prs=[gh_pr(7, "feat/from-fork", repo=f"{ORG}/example-repo-3", head_repo=None)]
        )
        return gh_state(
            repos={
                f"{ORG}/example-repo-1": repo_1,
                f"{ORG}/example-repo-2": archived,
                f"{ORG}/example-repo-3": repo_3,
            }
        )

    def test_every_open_pr_across_paginated_repos_and_pull_requests(self):
        self.use(self.state(), FAKE_GH_PAGE_SIZE=1)
        prs = audit.open_prs(ORG)
        found = sorted((pr["repo"], pr["number"]) for pr in prs)
        expected = [
            (f"{ORG}/example-repo-1", 1),
            (f"{ORG}/example-repo-1", 2),
            (f"{ORG}/example-repo-1", 3),
            (f"{ORG}/example-repo-3", 7),
        ]
        self.assertEqual(found, expected)
        # Two repository pages (the archived one is filtered by the query),
        # and repo-1's three open pull requests at page size 1: one in the
        # owner query, two more through HsRepoOpenPullRequests.
        self.assertEqual(len(self.fake.graphql_calls("HsOwnerPullRequests")), 2)
        self.assertEqual(len(self.fake.graphql_calls("HsRepoOpenPullRequests")), 2)
        self.assert_no_typed_fields()

    def test_each_record_carries_the_documented_fields(self):
        self.use(self.state())
        by_number = {pr["number"]: pr for pr in audit.open_prs(ORG) if pr["repo"].endswith("-1")}
        self.assertEqual(
            by_number[3],
            {
                "repo": f"{ORG}/example-repo-1",
                "number": 3,
                "title": "placeholder title 3",
                "url": f"https://github.com/{ORG}/example-repo-1/pull/3",
                "head": "feat/orphan",
                "head_repo": f"{ORG}/example-repo-1",
                "draft": True,
                "updated_at": "2026-01-01T00:00:00Z",
                "author": None,
                "bot": False,
            },
        )

    def test_bots_by_typename_or_login_suffix_and_a_deleted_author_is_not_one(self):
        self.use(self.state())
        prs = {(pr["repo"][-1], pr["number"]): pr for pr in audit.open_prs(ORG)}

        def who(key):
            return prs[key]["author"], prs[key]["bot"]

        self.assertEqual(who(("1", 1)), ("dependabot", True))
        self.assertEqual(who(("1", 2)), ("example-app[bot]", True))
        self.assertEqual(who(("1", 3)), (None, False))
        self.assertEqual(who(("3", 7)), ("example-user", False))
        self.assertIsNone(prs[("3", 7)]["head_repo"])

    def test_an_owner_that_does_not_resolve_is_named(self):
        self.use(gh_state(orgs=[], repos={}))
        with self.assertRaises(audit.GhError) as ctx:
            audit.open_prs("example-nobody")
        self.assertIn("example-nobody", str(ctx.exception))


class TestRepoBranches(GhCase):
    def test_chunks_by_twenty_passes_names_with_dash_f_and_answers_per_name(self):
        names = [f"feat/b{i}" for i in range(24)] + ["123"]
        refs = {"feat/b0": ZERO_OID, "feat/b21": ZERO_OID, "123": ZERO_OID}
        prs = [
            gh_pr(10, "feat/b1", state="OPEN", draft=True),
            gh_pr(11, "feat/b1", state="MERGED"),
            gh_pr(12, "feat/b1", state="CLOSED"),
            gh_pr(13, "123", state="MERGED"),
        ]
        self.use(gh_state(repos={f"{ORG}/example-repo": gh_repo(refs=refs, prs=prs)}))
        result = audit.repo_branches(ORG, "example-repo", names)

        calls = self.fake.graphql_calls("HsRepoBranches")
        self.assertEqual(len(calls), 2)
        heads = [
            value for call in calls for key, value in call["raw"].items() if key.startswith("h")
        ]
        self.assertEqual(sorted(heads), sorted(names))
        self.assertEqual(sum(1 for key in calls[0]["raw"] if key.startswith("h")), 20)
        for call in calls:
            for key, value in call["raw"].items():
                if key.startswith("q"):
                    self.assertEqual(value, f"refs/heads/{call['raw']['h' + key[1:]]}")
        self.assert_no_typed_fields()

        self.assertEqual(result.default, "main")
        self.assertEqual(result.refs, {name: name in refs for name in names})
        self.assertEqual(
            result.prs["feat/b1"],
            [
                {
                    "number": 10,
                    "state": "OPEN",
                    "draft": True,
                    "url": f"https://github.com/{ORG}/example-repo/pull/10",
                    "head_repo": f"{ORG}/example-repo",
                },
                {
                    "number": 11,
                    "state": "MERGED",
                    "draft": False,
                    "url": f"https://github.com/{ORG}/example-repo/pull/11",
                    "head_repo": f"{ORG}/example-repo",
                },
            ],
        )
        self.assertEqual([pr["number"] for pr in result.prs["123"]], [13])
        self.assertTrue(result.refs["123"])
        self.assertEqual(result.prs["feat/b2"], [])

    def test_a_repository_without_a_default_branch(self):
        self.use(gh_state(repos={f"{ORG}/example-repo": gh_repo(default=None)}))
        self.assertIsNone(audit.repo_branches(ORG, "example-repo", ["feat/x"]).default)


class TestCompare(GhCase):
    def test_returns_the_status_per_name_in_chunks(self):
        names = [f"feat/c{i}" for i in range(21)]
        cycle = ("AHEAD", "BEHIND", "IDENTICAL", "DIVERGED")
        statuses = {name: cycle[i % 4] for i, name in enumerate(names)}
        refs = {name: ZERO_OID for name in names}
        repo = gh_repo(refs=refs, compare=statuses)
        self.use(gh_state(repos={f"{ORG}/example-repo": repo}))
        self.assertEqual(audit.compare(ORG, "example-repo", names), statuses)
        self.assertEqual(len(self.fake.graphql_calls("HsCompare")), 2)
        self.assert_no_typed_fields()

    def test_no_names_means_no_call(self):
        self.use(gh_state(repos={f"{ORG}/example-repo": gh_repo()}))
        self.assertEqual(audit.compare(ORG, "example-repo", []), {})
        self.assertEqual(self.fake.calls(), [])


class TestEveryErrorIsAFailure(GhCase):
    def setUp(self) -> None:
        super().setUp()
        repo = gh_repo(
            refs={"feat/x": ZERO_OID}, compare={"feat/x": "AHEAD"}, prs=[gh_pr(1, "feat/x")]
        )
        self.state = gh_state(repos={f"{ORG}/example-repo": repo})

    def calls(self):
        return (
            ("owners", lambda: audit.owners([])),
            ("open_prs", lambda: audit.open_prs(ORG)),
            ("repo_branches", lambda: audit.repo_branches(ORG, "example-repo", ["feat/x"])),
            ("compare", lambda: audit.compare(ORG, "example-repo", ["feat/x"])),
        )

    def test_a_non_zero_gh_exit_raises_quoting_gh(self):
        self.use(self.state, FAKE_GH_FAIL="api")
        for name, call in self.calls():
            with self.subTest(call=name), self.assertRaises(audit.GhError) as ctx:
                call()
            self.assertIn("failed (FAKE_GH_FAIL)", str(ctx.exception))

    def test_errors_returned_with_exit_zero_raise_quoting_the_message(self):
        self.use(self.state, FAKE_GH_GRAPHQL_ERRORS=1)
        for name, call in self.calls()[1:]:
            with self.subTest(call=name), self.assertRaises(audit.GhError) as ctx:
                call()
            self.assertIn("Resource not accessible by integration", str(ctx.exception))

    def test_a_missing_repository_raises(self):
        self.use(gh_state(repos={}))
        with self.assertRaises(audit.GhError) as ctx:
            audit.repo_branches(ORG, "example-nope", ["feat/x"])
        self.assertIn("Could not resolve to a Repository", str(ctx.exception))

    def test_compare_against_a_missing_ref_is_not_forgiven(self):
        self.use(gh_state(repos={f"{ORG}/example-repo": gh_repo()}))
        with self.assertRaises(audit.GhError) as ctx:
            audit.compare(ORG, "example-repo", ["feat/gone"])
        self.assertIn("Could not resolve head ref 'feat/gone'", str(ctx.exception))

    def test_no_call_sends_a_mutation_and_every_api_call_names_github_com(self):
        self.use(self.state)
        audit.owners([])
        audit.open_prs(ORG)
        audit.repo_branches(ORG, "example-repo", ["feat/x"])
        audit.compare(ORG, "example-repo", ["feat/x"])
        calls = self.fake.calls()
        self.assertGreaterEqual(len(calls), 5)
        for argv in calls:
            self.assertFalse(any("mutation" in word for word in argv), argv)
            self.assertEqual(argv[0], "api")
            self.assertEqual(argv[argv.index("--hostname") + 1], "github.com")


class TestTheQueriesAreTheCapturedOnes(GhCase):
    """Each query the module sends, re-sent verbatim, answers the captured shape."""

    def logged(self, operation: str) -> list[str]:
        for argv in self.fake.calls():
            if argv[:2] == ["api", "graphql"] and any(
                word.startswith("query=") and operation in word for word in argv
            ):
                return argv
        self.fail(f"the module never sent {operation}")

    def test_hs_owner_pull_requests(self):
        repos = {f"example-user/example-repo-{i}": gh_repo() for i in (1, 2, 3)}
        self.use(gh_state(orgs=[], repos=repos), FAKE_GH_PAGE_SIZE=2)
        audit.open_prs("example-user")
        proc = self.rerun(self.logged("HsOwnerPullRequests"), FAKE_GH_PAGE_SIZE=2)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        answer = without_nested_page_info(
            json.loads(proc.stdout), ["data", "repositoryOwner", "repositories", "nodes"]
        )
        self.assertEqual(shape(answer), shape(capture("owner-pull-requests.json")))

    def test_hs_repo_open_pull_requests(self):
        prs = [
            gh_pr(
                101,
                "dependabot/npm_and_yarn/example-dependency-2.0.0",
                author={"login": "dependabot", "__typename": "Bot"},
            ),
            gh_pr(102, "feat/example-branch"),
            gh_pr(103, "feat/example-branch-2"),
        ]
        self.use(gh_state(repos={f"{ORG}/example-repo": gh_repo(prs=prs)}), FAKE_GH_PAGE_SIZE=2)
        audit.open_prs(ORG)
        proc = self.rerun(self.logged("HsRepoOpenPullRequests"), FAKE_GH_PAGE_SIZE=2)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        answer = without_nested_page_info(json.loads(proc.stdout), ["data", "repository"])
        expected = capture("open-pull-requests.json")
        self.assertEqual(shape(answer), shape(expected))

    def test_hs_repo_branches(self):
        merged = gh_pr(2, "example-branch", state="MERGED", repo="example-user/example-repo")
        repo = gh_repo(refs={"feat/kept": ZERO_OID}, prs=[merged])
        self.use(gh_state(repos={"example-user/example-repo": repo}))
        audit.repo_branches("example-user", "example-repo", ["example-branch", "feat/kept"])
        proc = self.rerun(self.logged("HsRepoBranches"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(shape(json.loads(proc.stdout)), shape(capture("repo-branches.json")))

    def test_hs_compare_and_its_missing_ref_error_byte_for_byte(self):
        repo = gh_repo(refs={"main": ZERO_OID}, compare={"main": "IDENTICAL"})
        self.use(gh_state(repos={"example-user/example-repo": repo}))
        audit.compare("example-user", "example-repo", ["main"])
        argv = self.logged("HsCompare")
        proc = self.rerun(argv)
        self.assertEqual(json.loads(proc.stdout), capture("compare.json"))

        missing = [word if word != "h0=main" else "h0=example-branch" for word in argv]
        self.assertNotEqual(missing, argv)
        proc = self.rerun(missing)
        self.assertEqual(proc.stdout, (GH_CAPTURES / "compare-missing-ref.stdout").read_text())
        self.assertEqual(proc.stderr, (GH_CAPTURES / "compare-missing-ref.stderr").read_text())
        expected_exit = int((GH_CAPTURES / "compare-missing-ref.exit").read_text())
        self.assertEqual(proc.returncode, expected_exit)


if __name__ == "__main__":
    unittest.main()
