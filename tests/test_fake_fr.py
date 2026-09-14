#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""The fake `fr` (tests/helpers/fake-fr), tested before anything relies on it.

The audit's fr enrichment is optional and warning-only, which is exactly the
kind of code path that quietly never runs. A fake that can fail is what lets
a later test prove the warning happens.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from auditlib import isolate_audit_environment  # noqa: E402
from feedlib import HELPERS_DIR  # noqa: E402

isolate_audit_environment()

# The key structure `fr isolation status --format json` prints, observed on a
# host (keys only; every value here is a placeholder).
STATUS = [
    {
        "repo": "/work/alpha",
        "branch": "feat/x",
        "profile": "default",
        "worktree": "/work/alpha-worktrees/feat__x",
        "worktree_exists": True,
        "container": "",
        "pr": None,
        "sessions": [
            {
                "session_id": "00000000-0000-4000-8000-000000000001",
                "harness": "claude",
                "attached_at": "2026-01-01T00:00:00Z",
            }
        ],
    }
]


class TestFakeFr(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.status_path = Path(self._tmp.name) / "status.json"
        self.status_path.write_text(json.dumps(STATUS), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def fr(self, *args, **extra_env) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("FAKE_GH_", "FAKE_FR_"))}
        env["FAKE_FR_STATUS"] = str(self.status_path)
        env.update({k: str(v) for k, v in extra_env.items()})
        return subprocess.run(  # noqa: S603
            ["fr", *args], capture_output=True, text=True, env=env, timeout=120
        )

    def test_fr_resolves_to_the_fake(self):
        found = shutil.which("fr")
        self.assertIsNotNone(found)
        self.assertEqual(Path(found).parent, HELPERS_DIR)
        self.assertEqual(Path(found).resolve(), HELPERS_DIR / "fake-fr")

    def test_isolation_status_answers_from_fake_fr_status(self):
        proc = self.fr("isolation", "status", "--format", "json", "--repo", "/work/alpha")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), STATUS)

    def test_fake_fr_fail_exits_one_with_nothing_on_stdout(self):
        proc = self.fr(
            "isolation", "status", "--format", "json", "--repo", "/work/alpha", FAKE_FR_FAIL=1
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.strip())

    def test_an_unset_status_is_refused_loudly_not_answered_empty(self):
        proc = self.fr(
            "isolation", "status", "--format", "json", "--repo", "/work/alpha", FAKE_FR_STATUS=""
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("FAKE_FR_STATUS", proc.stderr)

    def test_a_status_that_is_not_a_json_list_is_refused(self):
        self.status_path.write_text('{"not": "a list"}', encoding="utf-8")
        proc = self.fr("isolation", "status", "--format", "json", "--repo", "/work/alpha")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")

    def test_anything_else_is_refused(self):
        for args in (
            ("isolation", "up", "--branch", "feat/x"),
            ("isolation", "status", "--repo", "/work/alpha"),
            ("isolation", "status", "--format", "json"),
            ("isolation", "status", "--format", "text", "--repo", "/work/alpha"),
        ):
            with self.subTest(args=args):
                proc = self.fr(*args)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
