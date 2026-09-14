#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Merge state (lib/audit.py resolve_branches), real repositories plus the fake gh.

docs/superpowers/specs/2026-09-13-session-audit-design.md, "Merge state" is
the table, first match wins:

    merged     a MERGED pull request whose head is the branch and whose head
               repository is this repository
    open-pr    an OPEN pull request by the same rule
    contained  at least one ref exists, and every ref that exists is
               reachable from the default branch
    unmerged   a local or GitHub ref exists that is not contained
    gone       no local ref, no GitHub ref, no open or merged pull request

plus `unresolved` for a branch with no work tree, or no default branch to
compare against. The pull request check comes first because a squash merge
is never an ancestor of anything.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from auditlib import (  # noqa: E402
    GITHUB_REMOTE,
    REPO_SLUG,
    ZERO_OID,
    FakeGh,
    commit,
    fingerprint,
    gh_pr,
    gh_repo,
    gh_state,
    git,
    isolate_audit_environment,
    load_audit,
    make_repo,
    recording_git,
    use_in_process_gh,
)

isolate_audit_environment()

audit = load_audit()

FORK = "example-user/example-repo"
NOT_GITHUB = "https://gitlab.example.invalid/example-org/example-repo.git"


def entry(name: str, directory, cwd=None) -> dict:
    return {"name": name, "dir": str(directory), "cwd": None if cwd is None else str(cwd)}


class MergeStateCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.fake = FakeGh(self.root / "gh")
        self.enterContext(self.fake.active())
        use_in_process_gh(self, audit)
        self.repo = make_repo(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def github(self, *, refs=None, compare=None, prs=(), default="main") -> None:
        repo = gh_repo(default=default, refs=dict(refs or {}), compare=dict(compare or {}))
        repo["prs"] = list(prs)
        self.fake.write(gh_state(repos={REPO_SLUG: repo}))

    def branch_with_commit(self, name: str, repo=None) -> None:
        repo = repo or self.repo
        git(repo, "checkout", "-q", "-b", name)
        commit(repo, f"work on {name}")
        git(repo, "checkout", "-q", "main")

    def resolve(self, *names: str, repo=None) -> dict:
        repo = repo or self.repo
        return audit.resolve_branches([entry(name, repo, repo) for name in names])

    def state(self, results: dict, name: str, repo=None) -> str:
        return results[(str(repo or self.repo), name)].state


class TestTheTable(MergeStateCase):
    def test_squash_merged_is_merged_though_its_commits_are_not_ancestors(self):
        self.branch_with_commit("feat/squash")
        self.github(prs=[gh_pr(7, "feat/squash", state="MERGED")])
        resolution = self.resolve("feat/squash")[(str(self.repo), "feat/squash")]
        self.assertEqual(resolution.state, "merged")
        self.assertEqual(
            resolution.pr,
            {"number": 7, "draft": False, "url": f"https://github.com/{REPO_SLUG}/pull/7"},
        )
        self.assertEqual(resolution.repo_slug, REPO_SLUG)

    def test_merged_by_a_merge_commit_without_a_pull_request_is_contained(self):
        self.branch_with_commit("feat/merged")
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge feat/merged", "feat/merged")
        self.github()
        self.assertEqual(self.state(self.resolve("feat/merged"), "feat/merged"), "contained")

    def test_a_fresh_branch_with_no_commits_of_its_own_is_contained(self):
        git(self.repo, "branch", "feat/fresh")
        self.github()
        self.assertEqual(self.state(self.resolve("feat/fresh"), "feat/fresh"), "contained")

    def test_local_only_unpushed_commits_are_unmerged(self):
        self.branch_with_commit("feat/unpushed")
        self.github()
        self.assertEqual(self.state(self.resolve("feat/unpushed"), "feat/unpushed"), "unmerged")

    def test_a_github_only_ref_is_decided_by_compare(self):
        refs = {"feat/ahead": ZERO_OID, "feat/behind": ZERO_OID, "feat/identical": ZERO_OID}
        compare = {"feat/ahead": "AHEAD", "feat/behind": "BEHIND", "feat/identical": "IDENTICAL"}
        self.github(refs=refs, compare=compare)
        results = self.resolve("feat/ahead", "feat/behind", "feat/identical")
        self.assertEqual(self.state(results, "feat/ahead"), "unmerged")
        self.assertEqual(self.state(results, "feat/behind"), "contained")
        self.assertEqual(self.state(results, "feat/identical"), "contained")

    def test_a_contained_local_ref_with_a_github_ref_ahead_is_unmerged(self):
        git(self.repo, "branch", "feat/both")
        self.github(refs={"feat/both": ZERO_OID}, compare={"feat/both": "AHEAD"})
        self.assertEqual(self.state(self.resolve("feat/both"), "feat/both"), "unmerged")

    def test_an_open_pull_request_is_open_pr_with_its_number_and_draft(self):
        self.branch_with_commit("feat/open")
        self.github(
            refs={"feat/open": ZERO_OID},
            compare={"feat/open": "AHEAD"},
            prs=[gh_pr(12, "feat/open", draft=True)],
        )
        resolution = self.resolve("feat/open")[(str(self.repo), "feat/open")]
        self.assertEqual(resolution.state, "open-pr")
        self.assertEqual(
            resolution.pr,
            {"number": 12, "draft": True, "url": f"https://github.com/{REPO_SLUG}/pull/12"},
        )

    def test_no_ref_anywhere_and_only_a_closed_or_no_pull_request_is_gone(self):
        self.github(prs=[gh_pr(3, "feat/closed", state="CLOSED")])
        results = self.resolve("feat/closed", "feat/never-existed")
        self.assertEqual(self.state(results, "feat/closed"), "gone")
        self.assertEqual(self.state(results, "feat/never-existed"), "gone")
        self.assertIsNone(results[(str(self.repo), "feat/closed")].pr)

    def test_a_forks_same_named_pull_request_is_not_this_repositorys(self):
        for name in ("feat/fork-merged", "feat/fork-open", "feat/deleted-fork"):
            self.branch_with_commit(name)
        self.github(
            prs=[
                gh_pr(20, "feat/fork-merged", state="MERGED", head_repo=FORK),
                gh_pr(21, "feat/fork-open", state="OPEN", head_repo=FORK),
                gh_pr(22, "feat/deleted-fork", state="MERGED", head_repo=None),
            ]
        )
        results = self.resolve("feat/fork-merged", "feat/fork-open", "feat/deleted-fork")
        for name in ("feat/fork-merged", "feat/fork-open", "feat/deleted-fork"):
            with self.subTest(branch=name):
                self.assertEqual(self.state(results, name), "unmerged")
                self.assertIsNone(results[(str(self.repo), name)].pr)

    def test_a_branch_named_123_resolves(self):
        self.branch_with_commit("123")
        self.github(prs=[gh_pr(30, "123", state="MERGED")])
        self.assertEqual(self.state(self.resolve("123"), "123"), "merged")

    def test_a_forks_merged_pull_request_on_page_one_does_not_hide_ours_on_page_two(self):
        self.branch_with_commit("patch-1")
        self.github(
            prs=[
                gh_pr(1, "patch-1", state="MERGED", head_repo=FORK),
                gh_pr(2, "patch-1", state="MERGED"),
            ]
        )
        with mock.patch.dict(os.environ, {"FAKE_GH_PAGE_SIZE": "1"}):
            resolution = self.resolve("patch-1")[(str(self.repo), "patch-1")]
        self.assertEqual((resolution.state, resolution.pr["number"]), ("merged", 2))


class TestFindingTheRepository(MergeStateCase):
    def test_a_missing_directory_resolves_through_the_session_cwd(self):
        git(self.repo, "branch", "feat/fresh")
        self.github()
        results = audit.resolve_branches(
            [entry("feat/fresh", self.root / "removed-worktree", self.repo)]
        )
        self.assertEqual(results[(str(self.repo), "feat/fresh")].state, "contained")

    def test_no_work_tree_at_all_is_unresolved_with_a_reason(self):
        plain = self.root / "plain"
        plain.mkdir()
        self.github()
        results = audit.resolve_branches([entry("feat/x", plain, self.root / "gone")])
        self.assertEqual(len(results), 1)
        (resolution,) = results.values()
        self.assertEqual(resolution.state, "unresolved")
        self.assertTrue(resolution.reason)
        self.assertIsNone(resolution.repo_slug)
        self.assertEqual(self.fake.calls(), [])

    def test_a_non_github_repository_without_origin_head_is_unresolved(self):
        other = make_repo(self.root, "elsewhere", remote=NOT_GITHUB)
        self.branch_with_commit("feat/work", repo=other)
        results = self.resolve("feat/work", repo=other)
        resolution = results[(str(other), "feat/work")]
        self.assertEqual(resolution.state, "unresolved")
        self.assertTrue(resolution.reason)
        self.assertEqual(self.fake.calls(), [])

    def test_a_non_github_repository_with_origin_head_uses_ancestry_only(self):
        other = make_repo(self.root, "elsewhere", remote=NOT_GITHUB)
        git(other, "update-ref", "refs/remotes/origin/main", "main")
        git(other, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        git(other, "branch", "feat/fresh")
        self.branch_with_commit("feat/work", repo=other)
        results = self.resolve("feat/fresh", "feat/work", "feat/nothing", repo=other)
        self.assertEqual(self.state(results, "feat/fresh", repo=other), "contained")
        self.assertEqual(self.state(results, "feat/work", repo=other), "unmerged")
        self.assertEqual(self.state(results, "feat/nothing", repo=other), "gone")
        self.assertIsNone(results[(str(other), "feat/work")].repo_slug)
        self.assertEqual(self.fake.calls(), [])


class TestTheChosenRemoteAndItsDefault(MergeStateCase):
    def test_a_default_branch_absent_locally_is_unresolved_and_the_run_continues(self):
        # A clone from before a default-branch rename: only master, while
        # GitHub's default is main.
        git(self.repo, "branch", "-m", "main", "master")
        git(self.repo, "checkout", "-q", "-b", "feat/stale")
        commit(self.repo, "work on a pre-rename clone")
        git(self.repo, "checkout", "-q", "master")
        self.github(prs=[gh_pr(8, "feat/squashed", state="MERGED")])
        results = self.resolve("feat/stale", "feat/squashed")
        stale = results[(str(self.repo), "feat/stale")]
        self.assertEqual(stale.state, "unresolved")
        self.assertEqual(stale.reason, "default branch main not present locally")
        self.assertEqual(self.state(results, "feat/squashed"), "merged")

    def upstream_clone(self) -> Path:
        """A clone whose only remote is `upstream`, with upstream/main containing feat/up."""
        clone = make_repo(self.root, "upstream-clone", remote=None)
        git(clone, "remote", "add", "upstream", GITHUB_REMOTE)
        git(clone, "checkout", "-q", "-b", "feat/up")
        commit(clone, "work merged upstream")
        git(clone, "update-ref", "refs/remotes/upstream/main", "refs/heads/feat/up")
        return clone

    def test_the_sole_remotes_default_decides_ancestry_over_a_stale_local_main(self):
        clone = self.upstream_clone()
        git(clone, "checkout", "-q", "main")
        self.github()
        results = self.resolve("feat/up", repo=clone)
        self.assertEqual(self.state(results, "feat/up", repo=clone), "contained")

    def test_with_no_local_main_the_sole_remotes_default_is_used(self):
        clone = self.upstream_clone()
        git(clone, "branch", "-D", "main")
        self.github()
        results = self.resolve("feat/up", repo=clone)
        self.assertEqual(self.state(results, "feat/up", repo=clone), "contained")


class TestWhatIsAsked(MergeStateCase):
    def test_compare_is_asked_only_for_existing_github_refs_no_pull_request_decided(self):
        self.branch_with_commit("feat/local-only")
        refs = {"feat/merged": ZERO_OID, "feat/undecided": ZERO_OID, "feat/open": ZERO_OID}
        compare = {"feat/merged": "AHEAD", "feat/undecided": "AHEAD", "feat/open": "AHEAD"}
        prs = [gh_pr(1, "feat/merged", state="MERGED"), gh_pr(2, "feat/open")]
        self.github(refs=refs, compare=compare, prs=prs)
        self.resolve("feat/merged", "feat/undecided", "feat/open", "feat/local-only")
        calls = self.fake.graphql_calls("HsCompare")
        self.assertEqual(len(calls), 1)
        asked = {key: value for key, value in calls[0]["raw"].items() if key.startswith("h")}
        self.assertEqual(asked, {"h0": "feat/undecided"})

    def test_one_resolution_per_repository_and_branch_however_many_name_it(self):
        worktree = self.root / "worktree"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/elsewhere", str(worktree))
        (self.repo / "sub").mkdir()
        self.branch_with_commit("feat/shared")
        self.github()
        before = (fingerprint(self.repo), fingerprint(worktree))
        entries = [
            entry("feat/shared", self.repo, self.repo),
            entry("feat/shared", self.repo / "sub", self.repo),
            entry("feat/shared", worktree, self.root / "other-cwd"),
            entry("feat/shared", self.root / "removed", self.repo),
        ]
        bin_dir, git_log = recording_git(self.root / "record")
        with mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]}):
            results = audit.resolve_branches(entries)
        # "Resolve once" means the work is done once, not only that one answer
        # comes back: the local ref is looked up, and its ancestry asked,
        # exactly one time for the four entries.
        recorded = git_log.read_text(encoding="utf-8").splitlines()
        lookups = [
            line
            for line in recorded
            if line.endswith("for-each-ref " + "--format=%(refname) refs/heads/feat/shared")
        ]
        ancestry = [
            line for line in recorded if " merge-base " in line and "refs/heads/feat/shared" in line
        ]
        self.assertEqual(len(lookups), 1, recorded)
        self.assertEqual(len(ancestry), 1, recorded)
        self.assertEqual(list(results), [(str(self.repo), "feat/shared")])
        self.assertEqual(results[(str(self.repo), "feat/shared")].state, "unmerged")
        calls = self.fake.graphql_calls("HsRepoBranches")
        self.assertEqual(len(calls), 1)
        heads = [key for key in calls[0]["raw"] if key.startswith("h")]
        self.assertEqual(heads, ["h0"])
        self.assertEqual(
            sorted(results[(str(self.repo), "feat/shared")].sources),
            sorted((e["dir"], e["cwd"]) for e in entries),
        )
        self.assertEqual((fingerprint(self.repo), fingerprint(worktree)), before)
        for argv in self.fake.calls():
            self.assertFalse(any("mutation" in word for word in argv))

    def test_every_entry_maps_back_to_its_key_whatever_path_type_it_used(self):
        git(self.repo, "branch", "feat/fresh")
        self.github()
        missing = self.root / "never-a-work-tree"
        entries = [
            {"name": "feat/fresh", "dir": self.repo, "cwd": self.repo},
            {"name": "feat/fresh", "dir": str(self.repo) + "/", "cwd": str(self.repo)},
            {"name": "feat/x", "dir": missing, "cwd": None},
        ]
        results = audit.resolve_branches(entries)
        key = (str(self.repo), "feat/fresh")
        self.assertEqual(results.index[("feat/fresh", str(self.repo), str(self.repo))], key)
        self.assertEqual(results.key_for("feat/fresh", self.repo, str(self.repo) + "/"), key)
        self.assertEqual(results.key_for("feat/fresh", str(self.repo) + "/", self.repo), key)
        # the Path and the trailing-slash str spell one entry
        self.assertEqual(len(results.index), 2)
        self.assertEqual(results[results.key_for("feat/x", missing, None)].state, "unresolved")

    def test_a_git_failure_inside_the_repository_raises(self):
        broken = self.repo / ".git" / "refs" / "heads" / "feat" / "broken"
        broken.parent.mkdir(parents=True)
        broken.write_text("not-an-object-id\n", encoding="utf-8")
        self.github()
        with self.assertRaises(audit.GitError):
            self.resolve("feat/broken")


def pull(number: int, state: str, *, head_repo=REPO_SLUG, draft: bool = False) -> dict:
    """A pull request as RepoBranches.prs carries it."""
    return {
        "number": number,
        "state": state,
        "draft": draft,
        "url": f"https://github.com/{REPO_SLUG}/pull/{number}",
        "head_repo": head_repo,
    }


class TestClassifyIsAPureTable(unittest.TestCase):
    """The same table, from facts alone: no repository, no gh, no subprocess.

    Every row names only the facts that row needs; anything the table would
    have to consult and was not given is a ValueError, not a guess.
    """

    ROWS = (
        # (label, Facts, expected state)
        (
            "merged beats an open pull request",
            audit.Facts(REPO_SLUG, "main", (pull(4, "MERGED"), pull(5, "OPEN"))),
            "merged",
        ),
        (
            "merged needs no ref at all",
            audit.Facts(REPO_SLUG, "main", (pull(4, "MERGED"),)),
            "merged",
        ),
        ("open-pr", audit.Facts(REPO_SLUG, "main", (pull(5, "OPEN"),)), "open-pr"),
        (
            "the head repository matches ignoring case",
            audit.Facts(REPO_SLUG, "main", (pull(4, "MERGED", head_repo=REPO_SLUG.upper()),)),
            "merged",
        ),
        (
            "a fork's merged pull request is not used",
            audit.Facts(
                REPO_SLUG,
                "main",
                (pull(4, "MERGED", head_repo=FORK),),
                local_ref=True,
                local_contained=False,
                remote_ref=False,
            ),
            "unmerged",
        ),
        (
            "a fork's open pull request is not used",
            audit.Facts(
                REPO_SLUG,
                "main",
                (pull(5, "OPEN", head_repo=FORK),),
                local_ref=False,
                remote_ref=False,
            ),
            "gone",
        ),
        (
            "a null head repository is not used",
            audit.Facts(
                REPO_SLUG,
                "main",
                (pull(4, "MERGED", head_repo=None),),
                local_ref=False,
                remote_ref=False,
            ),
            "gone",
        ),
        ("no GitHub default branch", audit.Facts(REPO_SLUG, None), "unresolved"),
        ("no GitHub remote and no origin/HEAD", audit.Facts(None, None), "unresolved"),
        (
            "a contained local ref alone",
            audit.Facts(REPO_SLUG, "main", local_ref=True, local_contained=True, remote_ref=False),
            "contained",
        ),
        (
            "an uncontained local ref alone",
            audit.Facts(REPO_SLUG, "main", local_ref=True, local_contained=False, remote_ref=False),
            "unmerged",
        ),
        (
            "a GitHub ref IDENTICAL",
            audit.Facts(
                REPO_SLUG, "main", local_ref=False, remote_ref=True, remote_status="IDENTICAL"
            ),
            "contained",
        ),
        (
            "a GitHub ref BEHIND",
            audit.Facts(
                REPO_SLUG, "main", local_ref=False, remote_ref=True, remote_status="BEHIND"
            ),
            "contained",
        ),
        (
            "a GitHub ref AHEAD",
            audit.Facts(REPO_SLUG, "main", local_ref=False, remote_ref=True, remote_status="AHEAD"),
            "unmerged",
        ),
        (
            "a GitHub ref DIVERGED",
            audit.Facts(
                REPO_SLUG, "main", local_ref=False, remote_ref=True, remote_status="DIVERGED"
            ),
            "unmerged",
        ),
        (
            "contained locally but AHEAD on GitHub",
            audit.Facts(
                REPO_SLUG,
                "main",
                local_ref=True,
                local_contained=True,
                remote_ref=True,
                remote_status="AHEAD",
            ),
            "unmerged",
        ),
        (
            "uncontained locally but IDENTICAL on GitHub",
            audit.Facts(
                REPO_SLUG,
                "main",
                local_ref=True,
                local_contained=False,
                remote_ref=True,
                remote_status="IDENTICAL",
            ),
            "unmerged",
        ),
        (
            "no ref anywhere and no pull request",
            audit.Facts(REPO_SLUG, "main", local_ref=False, remote_ref=False),
            "gone",
        ),
        (
            "not on GitHub, contained locally",
            audit.Facts(None, "main", local_ref=True, local_contained=True),
            "contained",
        ),
        ("not on GitHub, no local ref", audit.Facts(None, "main", local_ref=False), "gone"),
        (
            "not on GitHub, pull requests are never consulted",
            audit.Facts(None, "main", (pull(4, "MERGED"),), local_ref=False),
            "gone",
        ),
    )

    def test_every_row_of_the_table(self):
        forbidden = AssertionError("classify ran a subprocess")
        with mock.patch.object(audit.subprocess, "run", side_effect=forbidden):
            for label, facts, expected in self.ROWS:
                with self.subTest(row=label):
                    self.assertEqual(audit.classify(facts).state, expected)

    def test_the_chosen_pull_request_is_the_highest_numbered_of_its_state(self):
        facts = audit.Facts(REPO_SLUG, "main", (pull(4, "MERGED"), pull(9, "MERGED")))
        self.assertEqual(
            audit.classify(facts).pr,
            {"number": 9, "draft": False, "url": f"https://github.com/{REPO_SLUG}/pull/9"},
        )
        drafted = audit.Facts(REPO_SLUG, "main", (pull(5, "OPEN", draft=True),))
        self.assertTrue(audit.classify(drafted).pr["draft"])

    def test_a_fact_the_table_needs_and_was_never_gathered_is_refused(self):
        missing = (
            audit.Facts(REPO_SLUG, "main", remote_ref=False),
            audit.Facts(REPO_SLUG, "main", local_ref=True, remote_ref=False),
            audit.Facts(REPO_SLUG, "main", local_ref=False),
            audit.Facts(REPO_SLUG, "main", local_ref=False, remote_ref=True),
        )
        for facts in missing:
            with self.subTest(facts=facts), self.assertRaises(ValueError):
                audit.classify(facts)


if __name__ == "__main__":
    unittest.main()
