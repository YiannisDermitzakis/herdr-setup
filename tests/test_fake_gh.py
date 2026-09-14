#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""The fake `gh` (tests/helpers/fake-gh), tested before anything relies on it.

Every audit test that touches GitHub goes through this fake, so a fake that
answers a shape GitHub never returns would make the whole audit suite agree
with itself and with nothing else. So this file pins it to the captures in
tests/fixtures/gh/: every GraphQL operation is driven with the spec's own
field selection and its answer is compared, key by key and leaf type by leaf
type, with the capture of the same operation.

It also proves each failure switch actually fails, because a fake that can
only succeed can only prove the happy path.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import HELPERS_DIR, LEAKY_VARS, TESTS_DIR, isolate_environment  # noqa: E402

isolate_environment()

GH_CAPTURES = TESTS_DIR / "fixtures" / "gh"
ZERO_OID = "0" * 40

# The spec's own field selections ("GitHub queries"), exactly as the captures
# were taken. HsCompare starts with a newline: the captured NOT_FOUND error
# locates `c0` at line 5, column 7, which is where this text puts it.
OWNER_QUERY = """query HsOwnerPullRequests($login: String!, $after: String) {
  repositoryOwner(login: $login) {
    repositories(first: 50, isArchived: false, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        pullRequests(states: OPEN, first: 100) {
          nodes {
            number title url headRefName isDraft updatedAt
            author { login __typename }
            headRepository { nameWithOwner }
          }
        }
      }
    }
  }
}"""

OPEN_QUERY = """query HsRepoOpenPullRequests($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 100, after: $after) {
      nodes {
        number title url headRefName isDraft updatedAt
        author { login __typename }
        headRepository { nameWithOwner }
      }
    }
  }
}"""

BRANCHES_QUERY = """query HsRepoBranches($owner: String!, $name: String!,
    $h0: String!, $q0: String!, $h1: String!, $q1: String!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
    r0: ref(qualifiedName: $q0) { target { oid } }
    p0: pullRequests(headRefName: $h0, states: [OPEN, MERGED], first: 20) {
      nodes { number state isDraft url headRepository { nameWithOwner } }
    }
    r1: ref(qualifiedName: $q1) { target { oid } }
    p1: pullRequests(headRefName: $h1, states: [OPEN, MERGED], first: 20) {
      nodes { number state isDraft url headRepository { nameWithOwner } }
    }
  }
}"""

ONE_BRANCH_QUERY = """query HsRepoBranches($owner: String!, $name: String!,
    $h0: String!, $q0: String!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
    r0: ref(qualifiedName: $q0) { target { oid } }
    p0: pullRequests(headRefName: $h0, states: [OPEN, MERGED], first: 20) {
      nodes { number state isDraft url headRepository { nameWithOwner } }
    }
  }
}"""

COMPARE_QUERY = """
query HsCompare($owner: String!, $name: String!, $h0: String!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      c0: compare(headRef: $h0) { status }
    }
  }
}"""

# The switches the fakes read. Named here so the environment guard below
# cannot pass vacuously on fakes that do not exist yet.
FAKE_SWITCHES = (
    "FAKE_GH_STATE",
    "FAKE_GH_LOG",
    "FAKE_GH_FAIL",
    "FAKE_GH_UNAUTH",
    "FAKE_GH_GRAPHQL_ERRORS",
    "FAKE_GH_PAGE_SIZE",
    "FAKE_FR_STATUS",
    "FAKE_FR_FAIL",
)


def pr(number, head, *, state="OPEN", repo="example-org/example-repo", **values) -> dict:
    """One pull request in the fake's state file."""
    record = {
        "number": number,
        "title": f"placeholder title {number}",
        "url": f"https://github.com/{repo}/pull/{number}",
        "head": head,
        "head_repo": repo,
        "state": state,
        "draft": False,
        "updated_at": "2026-01-01T00:00:00Z",
        "author": {"login": "example-user", "__typename": "User"},
    }
    record.update(values)
    return record


def repo(**values) -> dict:
    record = {"default": "main", "archived": False, "refs": {}, "compare": {}, "prs": []}
    record.update(values)
    return record


def shape(obj):
    """The key structure of a JSON value, with leaf types, for comparison.

    A list becomes the sorted distinct shapes of its elements, so two nodes
    of the same shape count once and an empty list stays distinguishable.
    """
    if isinstance(obj, dict):
        return {key: shape(value) for key, value in obj.items()}
    if isinstance(obj, list):
        distinct = {json.dumps(shape(item), sort_keys=True) for item in obj}
        return [json.loads(item) for item in sorted(distinct)]
    return type(obj).__name__


def capture(name: str):
    return json.loads((GH_CAPTURES / name).read_text(encoding="utf-8"))


class FakeGhCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state_path = self.tmp / "state.json"
        self.log_path = self.tmp / "gh.log"
        self.write_state({"user": "example-user", "orgs": ["example-org"], "repos": {}})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_state(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def gh(self, *args, **extra_env) -> subprocess.CompletedProcess:
        # Refuse before running anything when `gh` is not the fake. Without
        # this, a missing or broken fake falls through PATH to the host's REAL
        # gh, and this file's first RED run did exactly that.
        found = shutil.which("gh")
        if found is None or Path(found).resolve() != HELPERS_DIR / "fake-gh":
            self.fail(f"gh resolves to {found}, not tests/helpers/fake-gh; refusing to run it")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("FAKE_GH_", "FAKE_FR_"))}
        env["FAKE_GH_STATE"] = str(self.state_path)
        env["FAKE_GH_LOG"] = str(self.log_path)
        env.update({k: str(v) for k, v in extra_env.items()})
        return subprocess.run(  # noqa: S603
            ["gh", *args], capture_output=True, text=True, env=env, timeout=120
        )

    def graphql(self, query: str, raw=(), typed=(), **extra_env) -> subprocess.CompletedProcess:
        args = ["api", "graphql", "-f", f"query={query}"]
        for pair in raw:
            args += ["-f", pair]
        for pair in typed:
            args += ["-F", pair]
        return self.gh(*args, **extra_env)

    def ok_json(self, proc: subprocess.CompletedProcess):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def log_lines(self) -> list[list[str]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]


class TestItIsTheFakeOnPath(unittest.TestCase):
    def test_gh_resolves_to_the_fake(self):
        found = shutil.which("gh")
        self.assertIsNotNone(found)
        self.assertEqual(Path(found).parent, HELPERS_DIR)
        self.assertEqual(Path(found).resolve(), HELPERS_DIR / "fake-gh")

    def test_every_fake_switch_is_cleared_by_the_runner_and_feedlib(self):
        sources = ""
        for name in ("fake-gh", "fake-fr"):
            path = HELPERS_DIR / name
            self.assertTrue(path.is_file(), f"{path} is missing")
            sources += path.read_text(encoding="utf-8")
        read = set(re.findall(r"\bFAKE_(?:GH|FR)_[A-Z_]+\b", sources))
        self.assertTrue(set(FAKE_SWITCHES) <= read, sorted(set(FAKE_SWITCHES) - read))
        run_sh = (TESTS_DIR / "run.sh").read_text(encoding="utf-8")
        for name in sorted(read):
            self.assertIn(f"-u {name} ", run_sh, f"tests/run.sh does not clear {name}")
            self.assertIn(name, LEAKY_VARS, f"feedlib.LEAKY_VARS does not clear {name}")


class TestRest(FakeGhCase):
    def test_user_jq_login_prints_the_login(self):
        proc = self.gh("api", "user", "--jq", ".login")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "example-user\n")

    def test_user_has_the_captured_shape(self):
        answer = self.ok_json(self.gh("api", "user"))
        self.assertEqual(shape(answer), shape(capture("user.json")))
        self.assertEqual(answer["login"], "example-user")

    def test_user_orgs_pages_as_concatenated_arrays(self):
        self.write_state({"user": "example-user", "orgs": ["example-org", "example-org-2"]})
        proc = self.gh("api", "--paginate", "user/orgs", FAKE_GH_PAGE_SIZE=1)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        decoder = json.JSONDecoder()
        pages, index, text = [], 0, proc.stdout
        while index < len(text):
            if text[index].isspace():
                index += 1
                continue
            page, index = decoder.raw_decode(text, index)
            pages.append(page)
        self.assertEqual(len(pages), 2)
        logins = [org["login"] for page in pages for org in page]
        self.assertEqual(logins, ["example-org", "example-org-2"])
        for page in pages:
            self.assertEqual(shape(page), shape(capture("user-orgs.json")))

    def test_auth_status_succeeds_and_fake_gh_unauth_fails_it(self):
        self.assertEqual(self.gh("auth", "status", "--hostname", "github.com").returncode, 0)
        proc = self.gh("auth", "status", "--hostname", "github.com", FAKE_GH_UNAUTH=1)
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(proc.stderr.strip())

    def test_an_unfaked_command_is_refused_loudly(self):
        proc = self.gh("pr", "list")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("fake gh", proc.stderr)


class TestCapturedShapes(FakeGhCase):
    def test_owner_pull_requests(self):
        repos = {f"example-user/example-repo-{i}": repo() for i in (1, 2, 3)}
        self.write_state({"user": "example-user", "orgs": [], "repos": repos})
        answer = self.ok_json(
            self.graphql(OWNER_QUERY, raw=["login=example-user"], FAKE_GH_PAGE_SIZE=2)
        )
        self.assertEqual(shape(answer), shape(capture("owner-pull-requests.json")))

    def test_repo_open_pull_requests(self):
        bot = pr(
            101,
            "dependabot/npm_and_yarn/example-dependency-2.0.0",
            author={"login": "dependabot", "__typename": "Bot"},
        )
        person = pr(102, "feat/example-branch")
        self.write_state({"repos": {"example-org/example-repo": repo(prs=[bot, person])}})
        answer = self.ok_json(
            self.graphql(OPEN_QUERY, raw=["owner=example-org", "name=example-repo"])
        )
        self.assertEqual(shape(answer), shape(capture("open-pull-requests.json")))
        nodes = answer["data"]["repository"]["pullRequests"]["nodes"]
        self.assertEqual([n["number"] for n in nodes], [101, 102])
        self.assertEqual(nodes[0]["author"], {"login": "dependabot", "__typename": "Bot"})

    def test_repo_branches(self):
        merged = pr(2, "example-branch", state="MERGED", repo="example-user/example-repo")
        state = repo(refs={"feat/kept": ZERO_OID}, prs=[merged])
        self.write_state({"repos": {"example-user/example-repo": state}})
        answer = self.ok_json(
            self.graphql(
                BRANCHES_QUERY,
                raw=[
                    "owner=example-user",
                    "name=example-repo",
                    "h0=example-branch",
                    "q0=refs/heads/example-branch",
                    "h1=feat/kept",
                    "q1=refs/heads/feat/kept",
                ],
            )
        )
        self.assertEqual(shape(answer), shape(capture("repo-branches.json")))
        data = answer["data"]["repository"]
        self.assertIsNone(data["r0"])
        self.assertEqual(data["r1"], {"target": {"oid": ZERO_OID}})
        self.assertEqual(data["p0"]["nodes"][0]["state"], "MERGED")

    def test_compare(self):
        state = repo(refs={"main": ZERO_OID}, compare={"main": "IDENTICAL"})
        self.write_state({"repos": {"example-user/example-repo": state}})
        answer = self.ok_json(
            self.graphql(COMPARE_QUERY, raw=["owner=example-user", "name=example-repo", "h0=main"])
        )
        self.assertEqual(answer, capture("compare.json"))

    def test_compare_against_a_missing_ref_reproduces_the_capture_exactly(self):
        self.write_state({"repos": {"example-user/example-repo": repo()}})
        proc = self.graphql(
            COMPARE_QUERY, raw=["owner=example-user", "name=example-repo", "h0=example-branch"]
        )
        self.assertEqual(proc.stdout, (GH_CAPTURES / "compare-missing-ref.stdout").read_text())
        self.assertEqual(proc.stderr, (GH_CAPTURES / "compare-missing-ref.stderr").read_text())
        expected_exit = int((GH_CAPTURES / "compare-missing-ref.exit").read_text())
        self.assertEqual(proc.returncode, expected_exit)


class TestQuerySemantics(FakeGhCase):
    def test_pagination_follows_real_cursors_at_the_page_size(self):
        repos = {f"example-org/example-repo-{i}": repo() for i in (1, 2, 3)}
        self.write_state({"user": "example-user", "orgs": ["example-org"], "repos": repos})
        names, after, pages = [], None, 0
        while True:
            raw = ["login=example-org"] + ([f"after={after}"] if after else [])
            answer = self.ok_json(self.graphql(OWNER_QUERY, raw=raw, FAKE_GH_PAGE_SIZE=1))
            connection = answer["data"]["repositoryOwner"]["repositories"]
            pages += 1
            self.assertEqual(len(connection["nodes"]), 1)
            names += [node["name"] for node in connection["nodes"]]
            if not connection["pageInfo"]["hasNextPage"]:
                break
            after = connection["pageInfo"]["endCursor"]
            self.assertIsInstance(after, str)
            self.assertLess(pages, 10)
        self.assertEqual(pages, 3)
        self.assertEqual(names, ["example-repo-1", "example-repo-2", "example-repo-3"])

    def test_a_cursor_from_another_connection_or_garbage_is_an_error(self):
        repos = {f"example-org/example-repo-{i}": repo(prs=[pr(i, "feat/x")]) for i in (1, 2)}
        self.write_state({"repos": repos})
        answer = self.ok_json(
            self.graphql(OWNER_QUERY, raw=["login=example-org"], FAKE_GH_PAGE_SIZE=1)
        )
        cursor = answer["data"]["repositoryOwner"]["repositories"]["pageInfo"]["endCursor"]
        for bad in (cursor, base64.b64encode(b"nonsense").decode()):
            with self.subTest(cursor=bad):
                proc = self.graphql(
                    OPEN_QUERY,
                    raw=["owner=example-org", "name=example-repo-1", f"after={bad}"],
                )
                self.assertEqual(proc.returncode, 1)
                self.assertTrue(json.loads(proc.stdout)["errors"])

    def test_archived_repositories_are_filtered_only_when_the_query_asks(self):
        repos = {
            "example-org/example-repo-1": repo(),
            "example-org/example-repo-2": repo(archived=True),
        }
        self.write_state({"repos": repos})
        asked = self.ok_json(self.graphql(OWNER_QUERY, raw=["login=example-org"]))
        names = [n["name"] for n in asked["data"]["repositoryOwner"]["repositories"]["nodes"]]
        self.assertEqual(names, ["example-repo-1"])
        unfiltered = OWNER_QUERY.replace(", isArchived: false", "")
        both = self.ok_json(self.graphql(unfiltered, raw=["login=example-org"]))
        names = [n["name"] for n in both["data"]["repositoryOwner"]["repositories"]["nodes"]]
        self.assertEqual(names, ["example-repo-1", "example-repo-2"])

    def test_pull_request_states_and_head_filters_are_honoured(self):
        prs = [
            pr(1, "feat/x", state="OPEN"),
            pr(2, "feat/x", state="MERGED"),
            pr(3, "feat/x", state="CLOSED"),
            pr(4, "feat/other", state="OPEN"),
        ]
        self.write_state({"repos": {"example-org/example-repo": repo(prs=prs)}})
        opened = self.ok_json(
            self.graphql(OPEN_QUERY, raw=["owner=example-org", "name=example-repo"])
        )
        numbers = [n["number"] for n in opened["data"]["repository"]["pullRequests"]["nodes"]]
        self.assertEqual(numbers, [1, 4])
        branches = self.ok_json(
            self.graphql(
                ONE_BRANCH_QUERY,
                raw=["owner=example-org", "name=example-repo", "h0=feat/x", "q0=refs/heads/feat/x"],
            )
        )
        nodes = branches["data"]["repository"]["p0"]["nodes"]
        self.assertEqual([(n["number"], n["state"]) for n in nodes], [(1, "OPEN"), (2, "MERGED")])

    def test_an_unknown_owner_is_null_and_an_unknown_repository_is_not_found(self):
        self.write_state({"user": "example-user", "orgs": [], "repos": {}})
        owner = self.ok_json(self.graphql(OWNER_QUERY, raw=["login=example-nobody"]))
        self.assertEqual(owner, {"data": {"repositoryOwner": None}})
        proc = self.graphql(OPEN_QUERY, raw=["owner=example-org", "name=example-nope"])
        self.assertEqual(proc.returncode, 1)
        body = json.loads(proc.stdout)
        self.assertEqual(body["data"], {"repository": None})
        self.assertEqual(body["errors"][0]["type"], "NOT_FOUND")
        self.assertIn("example-org/example-nope", proc.stderr)


class TestFieldTyping(FakeGhCase):
    def setUp(self) -> None:
        super().setUp()
        state = repo(refs={"123": ZERO_OID, "true": ZERO_OID, "feat": ZERO_OID})
        self.write_state({"repos": {"example-org/example-repo": state}})

    def branch(self, name, *, typed: bool):
        head = f"h0={name}"
        raw = ["owner=example-org", "name=example-repo", f"q0=refs/heads/{name}"]
        if typed:
            return self.graphql(ONE_BRANCH_QUERY, raw=raw, typed=[head])
        return self.graphql(ONE_BRANCH_QUERY, raw=[*raw, head])

    def test_dash_f_sends_a_numeric_looking_name_as_a_string(self):
        answer = self.ok_json(self.branch("123", typed=False))
        self.assertEqual(answer["data"]["repository"]["r0"], {"target": {"oid": ZERO_OID}})

    def test_dash_capital_f_type_infers_and_the_string_variable_rejects_it(self):
        for literal in ("123", "true", "false", "null"):
            with self.subTest(literal=literal):
                proc = self.branch(literal, typed=True)
                self.assertEqual(proc.returncode, 1)
                message = "Variable $h0 of type String! was provided invalid value"
                self.assertEqual(json.loads(proc.stdout)["errors"][0]["message"], message)
                self.assertEqual(proc.stderr, f"gh: {message}\n")

    def test_dash_capital_f_leaves_a_plain_word_a_string(self):
        answer = self.ok_json(self.branch("feat", typed=True))
        self.assertEqual(answer["data"]["repository"]["r0"], {"target": {"oid": ZERO_OID}})


class TestFailureSwitches(FakeGhCase):
    def setUp(self) -> None:
        super().setUp()
        state = repo(refs={"main": ZERO_OID}, compare={"main": "IDENTICAL"})
        self.write_state(
            {"user": "example-user", "orgs": [], "repos": {"example-org/example-repo": state}}
        )
        self.compare_raw = ["owner=example-org", "name=example-repo", "h0=main"]
        self.branch_raw = [
            "owner=example-org",
            "name=example-repo",
            "h0=main",
            "q0=refs/heads/main",
        ]

    def test_fake_gh_fail_matches_an_argv_prefix_with_a_plain_stderr_line(self):
        proc = self.graphql(COMPARE_QUERY, raw=self.compare_raw, FAKE_GH_FAIL="api graphql")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(len(proc.stderr.splitlines()), 1)
        self.assertNotIn("{", proc.stderr)

    def test_fake_gh_fail_can_name_one_graphql_operation(self):
        env = {"FAKE_GH_FAIL": "api graphql HsCompare"}
        self.assertEqual(self.graphql(COMPARE_QUERY, raw=self.compare_raw, **env).returncode, 1)
        self.assertEqual(self.graphql(ONE_BRANCH_QUERY, raw=self.branch_raw, **env).returncode, 0)

    def test_fake_gh_fail_prefix_is_word_bounded(self):
        proc = self.gh("api", "--paginate", "user/orgs", FAKE_GH_FAIL="api user")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self.gh("api", "user", "--jq", ".login", FAKE_GH_FAIL="api user").returncode, 1
        )

    def test_fake_gh_graphql_errors_returns_data_and_errors_with_exit_zero(self):
        proc = self.graphql(COMPARE_QUERY, raw=self.compare_raw, FAKE_GH_GRAPHQL_ERRORS=1)
        self.assertEqual(proc.returncode, 0)
        body = json.loads(proc.stdout)
        self.assertEqual(body["data"], capture("compare.json")["data"])
        self.assertTrue(body["errors"])
        self.assertTrue(body["errors"][0]["message"])

    def test_fake_gh_log_records_each_argv_as_one_json_line(self):
        self.gh("api", "user", "--jq", ".login")
        self.graphql(COMPARE_QUERY, raw=self.compare_raw)
        lines = self.log_lines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], ["api", "user", "--jq", ".login"])
        self.assertEqual(lines[1][:4], ["api", "graphql", "-f", f"query={COMPARE_QUERY}"])

    def test_a_query_text_containing_mutation_is_refused(self):
        texts = (
            'mutation HsStar { addStar(input: {starrableId: "x"}) { clientMutationId } }',
            "query HsCompare { viewer { login } }\nmutation HsX { x }",
        )
        for text in texts:
            with self.subTest(text=text):
                proc = self.graphql(text)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("mutation", proc.stderr)
                self.assertEqual(proc.stdout, "")

    def test_graphql_without_a_state_file_is_refused_loudly(self):
        proc = self.graphql(COMPARE_QUERY, raw=self.compare_raw, FAKE_GH_STATE="")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("FAKE_GH_STATE", proc.stderr)


if __name__ == "__main__":
    unittest.main()
