#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""How many git processes resolving branches costs (lib/audit.py resolve_branches).

docs/superpowers/specs/2026-09-14-audit-live-host-findings-design.md, change 1:
a directory is resolved at most once, a repository's facts are read at most
once however many branches or clones point at it, and its local and
remote-tracking refs come from ONE `for-each-ref`. A live run spent 1,269 git
processes, most of an 8-minute audit, asking the same questions once per
branch directory.

Counted, never timed: every git process goes through
tests/helpers/auditlib.recording_git, and the count must be bounded by the
directories and repositories in the scenario, not by its branches.
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
    FakeGh,
    commit,
    gh_repo,
    gh_state,
    git,
    git_subcommand,
    isolate_audit_environment,
    load_audit,
    make_repo,
    recording_git,
    use_in_process_gh,
    writing_git_calls,
)

isolate_audit_environment()

audit = load_audit()

REMOTE_A = "https://github.com/example-org/example-repo.git"
REMOTE_B = "https://github.com/example-org/example-repo-2.git"

# Per branch: one with commits of its own (unmerged) and one without (contained).
BRANCHES_PER_CHECKOUT = 12

# What one repository may cost, whatever its branch count: the main
# checkout, the chosen remote and its URL (3), and its refs and their
# ancestry (2).
CALLS_PER_TOPLEVEL = 5


class TestGitCallsAreBoundedByRepositories(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.fake = FakeGh(self.root / "gh")
        self.fake.write(
            gh_state(
                repos={
                    "example-org/example-repo": gh_repo(),
                    "example-org/example-repo-2": gh_repo(),
                }
            )
        )
        self.enterContext(self.fake.active())
        use_in_process_gh(self, audit)

        # Repository A in three clones, one of them with a linked worktree;
        # repository B in one clone.
        self.checkouts = [
            make_repo(self.root, "clone-a1", remote=REMOTE_A),
            make_repo(self.root, "clone-a2", remote=REMOTE_A),
            make_repo(self.root, "clone-a3", remote=REMOTE_A),
            make_repo(self.root, "clone-b1", remote=REMOTE_B),
        ]
        for checkout in self.checkouts:
            git(checkout, "checkout", "-q", "-b", "side")
            commit(checkout, f"side work in {checkout.name}")
            git(checkout, "checkout", "-q", "main")
            (checkout / "sub").mkdir()
            for index in range(BRANCHES_PER_CHECKOUT):
                git(checkout, "branch", f"feat/unmerged-{index}", "side")
                git(checkout, "branch", f"feat/fresh-{index}", "main")
        self.worktree = self.root / "worktree-a1"
        git(
            self.checkouts[0], "worktree", "add", "-q", "-b", "feat/in-worktree", str(self.worktree)
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def entries(self, branches: int) -> list[dict]:
        """Every way a session names a branch: its checkout, a subdirectory, a removed worktree."""
        found = []
        for checkout in self.checkouts:
            for index in range(branches):
                for name in (f"feat/unmerged-{index}", f"feat/fresh-{index}"):
                    removed = self.root / "removed" / f"{checkout.name}-{name.replace('/', '__')}"
                    for directory in (checkout, checkout / "sub", removed):
                        found.append({"name": name, "dir": str(directory), "cwd": str(checkout)})
        found.append(
            {"name": "feat/in-worktree", "dir": str(self.worktree), "cwd": str(self.checkouts[0])}
        )
        return found

    def resolve_counting(self, branches: int):
        bin_dir, log = recording_git(self.root / f"record-{branches}")
        with mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]}):
            results = audit.resolve_branches(self.entries(branches))
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        self.assertEqual(writing_git_calls(log), [])
        return results, lines

    def test_git_processes_are_bounded_by_repositories_not_branches(self):
        results, calls = self.resolve_counting(BRANCHES_PER_CHECKOUT)

        # The behaviour is the per-branch layer's.
        for checkout in self.checkouts:
            for index in range(BRANCHES_PER_CHECKOUT):
                with self.subTest(checkout=checkout.name, index=index):
                    unmerged = results[(str(checkout), f"feat/unmerged-{index}")]
                    fresh = results[(str(checkout), f"feat/fresh-{index}")]
                    self.assertEqual(unmerged.state, "unmerged")
                    self.assertEqual(fresh.state, "contained")
        self.assertEqual(results[(str(self.checkouts[0]), "feat/in-worktree")].state, "contained")

        branch_count = len(self.checkouts) * 2 * BRANCHES_PER_CHECKOUT + 1
        existing_directories = 2 * len(self.checkouts) + 1  # each checkout, its sub, the worktree
        toplevels = len(self.checkouts) + 1  # each checkout, and the linked worktree
        bound = existing_directories + CALLS_PER_TOPLEVEL * toplevels
        by_subcommand: dict[str, int] = {}
        for line in calls:
            by_subcommand[git_subcommand(line)] = by_subcommand.get(git_subcommand(line), 0) + 1
        self.assertLessEqual(
            len(calls),
            bound,
            f"{len(calls)} git processes for {branch_count} branches in {toplevels} work trees "
            f"(bound {bound}): {by_subcommand}",
        )

    def test_twice_the_branches_cost_no_more_git_processes(self):
        _, fewer = self.resolve_counting(BRANCHES_PER_CHECKOUT // 2)
        _, more = self.resolve_counting(BRANCHES_PER_CHECKOUT)
        self.assertEqual(len(more), len(fewer), (len(fewer), len(more)))

    def test_one_ref_read_per_repository_names_its_heads_and_its_remote(self):
        _, calls = self.resolve_counting(BRANCHES_PER_CHECKOUT)
        reads = [line for line in calls if line.endswith(" refs/heads refs/remotes/origin")]
        self.assertEqual(len(reads), len(self.checkouts), calls)
        per_branch = [line for line in calls if "refs/heads/feat/" in line]
        self.assertEqual(per_branch, [])


if __name__ == "__main__":
    unittest.main()
