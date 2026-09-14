#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""The audit runner's git layer (lib/audit.py), against real repositories.

docs/superpowers/implemented/specs/2026-09-13-session-audit-design.md, "Resolving a
branch" and "Merge state", is the contract. Every repository here is built
by tests/helpers/auditlib.make_repo in a temporary directory; none is ever
fetched from, and the last test proves the module never fetches or writes
either -- by fingerprinting refs and status, and by recording every git
subcommand it runs.
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
    READ_ONLY_SUBCOMMANDS,
    commit,
    fingerprint,
    git,
    isolate_audit_environment,
    load_audit,
    make_repo,
    recording_git,
)

isolate_audit_environment()

audit = load_audit()

SLUG = "example-org/example-repo"


def corrupt_ref(repo: Path, branch: str) -> None:
    """Replace a loose ref's contents with garbage: git can no longer read it."""
    path = repo / ".git" / "refs" / "heads" / branch
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-an-object-id\n", encoding="utf-8")


class GitCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TestRepoFor(GitCase):
    def test_a_subdirectory_resolves_to_its_toplevel_main_checkout_and_slug(self):
        repo = make_repo(self.root)
        (repo / "sub" / "deeper").mkdir(parents=True)
        found = audit.repo_for(str(repo / "sub" / "deeper"), None)
        expected = audit.Repo(
            toplevel=str(repo), main_checkout=str(repo), slug=SLUG, remote="origin"
        )
        self.assertEqual(found, expected)

    def test_every_github_remote_url_form_names_the_slug(self):
        urls = (
            "https://github.com/example-org/example-repo",
            "https://github.com/example-org/example-repo.git",
            "https://github.com/example-org/example-repo.git/",
            "https://GITHUB.COM/example-org/example-repo.git",
            "git@github.com:example-org/example-repo.git",
            "git@GitHub.com:example-org/example-repo.git",
            "github.com:example-org/example-repo",
            # built in two pieces so the hygiene check's email pattern does not
            # read a user@host remote as an address
            "org-123" + "@github.com:example-org/example-repo.git",
            "ssh://git@github.com/example-org/example-repo.git",
            "ssh://github.com/example-org/example-repo",
        )
        for index, url in enumerate(urls):
            with self.subTest(url=url):
                repo = make_repo(self.root, f"repo-{index}", remote=url)
                self.assertEqual(audit.repo_for(str(repo), None).slug, SLUG)

    def test_a_non_github_remote_has_no_slug_but_still_resolves(self):
        urls = (
            "https://gitlab.example.invalid/example-org/example-repo.git",
            "git@gitlab.example.invalid:example-org/example-repo.git",
            "ssh://git@github.com.example.invalid/example-org/example-repo.git",
            # an SSH host alias cannot be recognised without reading ssh config
            "git@github-work:example-org/example-repo.git",
        )
        for index, url in enumerate(urls):
            with self.subTest(url=url):
                repo = make_repo(self.root, f"repo-{index}", remote=url)
                found = audit.repo_for(str(repo), None)
                self.assertEqual(found.toplevel, str(repo))
                self.assertIsNone(found.slug)

    def test_origin_is_preferred_over_another_remote(self):
        repo = make_repo(self.root, remote=None)
        git(repo, "remote", "add", "aaa-upstream", "https://github.com/example-org/other-repo.git")
        git(repo, "remote", "add", "origin", "https://github.com/example-org/example-repo.git")
        self.assertEqual(audit.repo_for(str(repo), None).slug, SLUG)

    def test_the_only_remote_is_used_whatever_its_name(self):
        repo = make_repo(self.root, remote=None)
        git(repo, "remote", "add", "upstream", "https://github.com/example-org/example-repo.git")
        found = audit.repo_for(str(repo), None)
        self.assertEqual((found.slug, found.remote), (SLUG, "upstream"))

    def test_several_remotes_and_no_origin_or_no_remote_at_all_mean_no_slug(self):
        several = make_repo(self.root, "several", remote=None)
        git(several, "remote", "add", "one", "https://github.com/example-org/example-repo.git")
        git(several, "remote", "add", "two", "https://github.com/example-org/other-repo.git")
        self.assertIsNone(audit.repo_for(str(several), None).slug)
        bare = make_repo(self.root, "no-remote", remote=None)
        self.assertIsNone(audit.repo_for(str(bare), None).slug)

    def test_a_linked_worktree_names_its_main_checkout(self):
        repo = make_repo(self.root)
        worktree = self.root / "worktree"
        git(repo, "worktree", "add", "-q", "-b", "feat/wt", str(worktree))
        found = audit.repo_for(str(worktree), None)
        self.assertEqual(found.toplevel, str(worktree))
        self.assertEqual(found.main_checkout, str(repo))
        self.assertEqual(found.slug, SLUG)

    def test_a_missing_directory_falls_back_to_the_fallback(self):
        repo = make_repo(self.root)
        found = audit.repo_for(str(self.root / "removed-worktree"), str(repo))
        self.assertEqual(found.toplevel, str(repo))

    def test_neither_a_work_tree_is_none(self):
        plain = self.root / "plain"
        plain.mkdir()
        self.assertIsNone(audit.repo_for(str(plain), None))
        self.assertIsNone(audit.repo_for(str(self.root / "gone"), str(plain)))
        self.assertIsNone(audit.repo_for(str(self.root / "gone"), str(self.root / "also-gone")))

    def test_a_bare_repository_is_not_a_work_tree(self):
        bare = self.root / "bare.git"
        git(self.root, "init", "-q", "--bare", str(bare))
        self.assertIsNone(audit.repo_for(str(bare), None))

    def test_any_other_git_failure_in_an_existing_directory_is_an_error(self):
        repo = make_repo(self.root)
        git(repo, "config", "core.repositoryformatversion", "99")
        with self.assertRaises(audit.GitError) as ctx:
            audit.repo_for(str(repo), None)
        self.assertIn(str(repo), str(ctx.exception))


class TestLocalRef(GitCase):
    def test_true_for_a_branch_false_for_none_and_a_numeric_name_works(self):
        repo = make_repo(self.root)
        git(repo, "branch", "feat/x")
        git(repo, "branch", "123")
        found = audit.repo_for(str(repo), None)
        self.assertTrue(audit.local_ref(found, "feat/x"))
        self.assertTrue(audit.local_ref(found, "123"))
        self.assertFalse(audit.local_ref(found, "feat/missing"))
        self.assertFalse(audit.local_ref(found, "feat"))

    def test_a_ref_git_cannot_read_is_an_error_not_an_absence(self):
        repo = make_repo(self.root)
        corrupt_ref(repo, "feat/broken")
        with self.assertRaises(audit.GitError) as ctx:
            audit.local_ref(audit.repo_for(str(repo), None), "feat/broken")
        self.assertIn(str(repo), str(ctx.exception))


class TestIsAncestor(GitCase):
    def test_fresh_merged_and_unmerged_branches_against_the_local_default(self):
        repo = make_repo(self.root)
        git(repo, "branch", "feat/fresh")
        git(repo, "checkout", "-q", "-b", "feat/merged")
        commit(repo, "merged work")
        git(repo, "checkout", "-q", "main")
        git(repo, "merge", "-q", "--no-ff", "-m", "merge feat/merged", "feat/merged")
        git(repo, "checkout", "-q", "-b", "feat/unmerged")
        commit(repo, "unmerged work")
        git(repo, "checkout", "-q", "main")
        found = audit.repo_for(str(repo), None)
        self.assertTrue(audit.is_ancestor(found, "feat/fresh", "main"))
        self.assertTrue(audit.is_ancestor(found, "feat/merged", "main"))
        self.assertFalse(audit.is_ancestor(found, "feat/unmerged", "main"))

    def test_the_remote_tracking_default_wins_over_the_local_one_when_it_exists(self):
        repo = make_repo(self.root)
        git(repo, "update-ref", "refs/remotes/origin/main", "main")
        git(repo, "checkout", "-q", "-b", "feat/local-merge")
        commit(repo, "work merged only locally")
        git(repo, "checkout", "-q", "main")
        git(repo, "merge", "-q", "--no-ff", "-m", "local merge", "feat/local-merge")
        found = audit.repo_for(str(repo), None)
        # refs/heads/main contains the branch; refs/remotes/origin/main does not.
        self.assertFalse(audit.is_ancestor(found, "feat/local-merge", "main"))

    def test_any_exit_but_zero_or_one_is_an_error_naming_the_repository(self):
        repo = make_repo(self.root)
        corrupt_ref(repo, "feat/broken")
        with self.assertRaises(audit.GitError) as ctx:
            audit.is_ancestor(audit.repo_for(str(repo), None), "feat/broken", "main")
        self.assertIn(str(repo), str(ctx.exception))

    def test_a_default_present_neither_locally_nor_on_the_remote_is_none_not_an_error(self):
        repo = make_repo(self.root)
        git(repo, "branch", "-m", "main", "master")
        git(repo, "branch", "feat/x")
        self.assertIsNone(audit.is_ancestor(audit.repo_for(str(repo), None), "feat/x", "main"))


class TestLocalDefault(GitCase):
    def test_none_without_origin_head_and_the_target_with_it(self):
        repo = make_repo(self.root)
        found = audit.repo_for(str(repo), None)
        self.assertIsNone(audit.local_default(found))
        git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        self.assertEqual(audit.local_default(found), "trunk")

    def test_the_head_of_the_chosen_remote_not_origin(self):
        repo = make_repo(self.root, remote=None)
        git(repo, "remote", "add", "upstream", "https://gitlab.example.invalid/example-org/r.git")
        git(repo, "symbolic-ref", "refs/remotes/upstream/HEAD", "refs/remotes/upstream/trunk")
        self.assertEqual(audit.local_default(audit.repo_for(str(repo), None)), "trunk")


class TestNothingIsWrittenOrFetched(GitCase):
    def test_refs_and_status_are_identical_and_only_read_only_subcommands_ran(self):
        repo = make_repo(self.root)
        git(repo, "update-ref", "refs/remotes/origin/main", "main")
        git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        git(repo, "branch", "feat/fresh")
        git(repo, "checkout", "-q", "-b", "feat/work")
        commit(repo, "work")
        git(repo, "checkout", "-q", "main")
        worktree = self.root / "worktree"
        git(repo, "worktree", "add", "-q", "-b", "feat/wt", str(worktree))
        (repo / "untracked.txt").write_text("left alone\n", encoding="utf-8")
        before = (fingerprint(repo), fingerprint(worktree))

        bin_dir, log = recording_git(self.root / "record")
        with mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]}):
            for directory in (repo, worktree, self.root / "gone"):
                found = audit.repo_for(str(directory), str(repo))
                audit.local_default(found)
                for branch in ("feat/fresh", "feat/work", "feat/wt", "feat/missing"):
                    if audit.local_ref(found, branch):
                        audit.is_ancestor(found, branch, "main")

        self.assertEqual((fingerprint(repo), fingerprint(worktree)), before)
        lines = log.read_text(encoding="utf-8").splitlines()
        self.assertGreater(len(lines), 10)
        for line in lines:
            words = line.split()
            while words and words[0] in ("-C", "-c"):
                words = words[2:]
            subcommand = words[0] if words else ""
            if subcommand == "worktree":
                subcommand = " ".join(words[:2])
            self.assertIn(subcommand, READ_ONLY_SUBCOMMANDS, line)


if __name__ == "__main__":
    unittest.main()
