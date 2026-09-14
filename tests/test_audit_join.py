#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""The audit's sources and the join (spec: "The audit runner", "Sources";
"Text output", section 1's notes).

- `panes()` reads `herdr agent list` and `herdr tab list` through the fake
  herdr, answered from the CAPTURES with values edited, never keys.
- `load_adapters()` and `gather_sessions()` run tiny inline shell adapters, so
  no uv cold start is spent on an adapter here.
- `join()` is pure: panes, adapters and gathered sessions in, one record per
  pane out, with the branch entries the resolver will be handed and, when a
  pane has none, the note that says why.

No test here and no code path under it runs `fr`: a recording `fr` shim sits
first on PATH and its log must stay empty.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from auditlib import isolate_audit_environment, load_audit  # noqa: E402
from feedlib import agent_entry, agent_list, captured, probe_adapter, write_adapter  # noqa: E402

isolate_audit_environment()

audit = load_audit()

# lib/feed.py by its own name, `feed`: the very module object lib/audit.py
# imports, so feed.HerdrError and feed.Adapter here ARE the audit's.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import feed  # noqa: E402

SESSION_A = "00000000-0000-4000-8000-00000000000a"
SESSION_B = "00000000-0000-4000-8000-00000000000b"


def tab_list(entries) -> dict:
    """A captured `tab list` answer carrying exactly `entries`."""
    answer = captured("tab-list")
    answer["result"]["tabs"] = list(entries)
    return answer


def tab_entry(index: int = 0, **values) -> dict:
    entry = dict(captured("tab-list")["result"]["tabs"][index])
    for key, value in values.items():
        if key not in entry:
            raise KeyError(f"{key!r} is not a key the capture has; do not invent one")
        entry[key] = value
    return entry


class HerdrCase(unittest.TestCase):
    """A fake-herdr fixture directory and call log, plus a recording `fr` shim."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.fixtures = self.root / "herdr"
        self.fixtures.mkdir()
        self.herdr_log = self.root / "herdr.log"
        shim_dir = self.root / "fr-shim"
        shim_dir.mkdir()
        self.fr_log = self.root / "fr.log"
        shim = shim_dir / "fr"
        shim.write_text(f'#!/bin/sh\necho "$*" >> "{self.fr_log}"\nexit 97\n', encoding="utf-8")
        shim.chmod(0o755)
        path = os.environ["PATH"].split(os.pathsep)
        path.insert(1, str(shim_dir))  # after tests/helpers, before everything else
        self.enterContext(
            mock.patch.dict(
                os.environ,
                {
                    "PATH": os.pathsep.join(path),
                    "FAKE_HERDR_FIXTURES": str(self.fixtures),
                    "FAKE_HERDR_LOG": str(self.herdr_log),
                },
            )
        )
        self.addCleanup(self.assert_fr_never_ran)

    def assert_fr_never_ran(self) -> None:
        self.assertFalse(self.fr_log.exists(), "the audit must never run fr")

    def herdr_answers(self, agents: dict, tabs: dict) -> None:
        (self.fixtures / "agent->list.json").write_text(json.dumps(agents), encoding="utf-8")
        (self.fixtures / "tab->list.json").write_text(json.dumps(tabs), encoding="utf-8")

    def herdr_calls(self) -> list[str]:
        if not self.herdr_log.exists():
            return []
        return self.herdr_log.read_text(encoding="utf-8").splitlines()


class TestPanes(HerdrCase):
    def test_every_pane_with_its_agent_session_cwd_and_tab_label(self):
        self.herdr_answers(captured("agent-list"), captured("tab-list"))
        found = audit.panes()
        agents = captured("agent-list")["result"]["agents"]
        tabs = {t["tab_id"]: t["label"] for t in captured("tab-list")["result"]["tabs"]}
        self.assertEqual(
            found,
            [
                {
                    "pane_id": entry["pane_id"],
                    "agent": entry["agent"],
                    "session": entry["agent_session"]["value"],
                    "cwd": entry["cwd"],
                    "tab": tabs[entry["tab_id"]],
                }
                for entry in agents
            ],
        )
        self.assertEqual(self.herdr_calls(), ["agent list", "tab list"])

    def test_a_pane_herdr_has_no_session_for_has_none(self):
        self.herdr_answers(agent_list([agent_entry(0, agent_session=None)]), captured("tab-list"))
        self.assertIsNone(audit.panes()[0]["session"])

    def test_a_pane_whose_tab_is_not_listed_has_no_tab(self):
        self.herdr_answers(agent_list([agent_entry(0)]), tab_list([tab_entry(1)]))
        self.assertIsNone(audit.panes()[0]["tab"])

    def test_no_panes_is_an_empty_list_only_when_herdr_says_so(self):
        self.herdr_answers(agent_list([]), tab_list([]))
        self.assertEqual(audit.panes(), [])

    def test_a_failing_herdr_call_raises(self):
        self.herdr_answers(captured("agent-list"), captured("tab-list"))
        for switches in (
            {"FAKE_HERDR_FAIL": "agent list"},
            {"FAKE_HERDR_FAIL": "tab list"},
            {"FAKE_HERDR_ERROR_CODE": "socket_closed", "FAKE_HERDR_ERROR_EXIT": "0"},
        ):
            with (
                self.subTest(**switches),
                mock.patch.dict(os.environ, switches),
                self.assertRaises(feed.HerdrError),
            ):
                audit.panes()

    def test_an_unreadable_answer_raises(self):
        for agents, tabs in (
            ({"result": {}}, captured("tab-list")),
            (captured("agent-list"), {"result": {}}),
            (agent_list(["not an entry"]), captured("tab-list")),
            (agent_list([agent_entry(0, pane_id="")]), captured("tab-list")),
        ):
            with self.subTest(agents=agents, tabs=tabs):
                self.herdr_answers(agents, tabs)
                with self.assertRaises(feed.HerdrError):
                    audit.panes()


def sessions_adapter(directory: Path, name: str, agent: str, answer, *, log: Path, code=0):
    """An adapter declaring `sessions`, logging its argv, answering `answer`."""
    probe = json.dumps(
        {
            "agent": agent,
            "source": f"herdr:{agent}",
            "available": True,
            "confidence": "heuristic",
            "sessions": True,
        }
    )
    text = answer if isinstance(answer, str) else json.dumps(answer)
    body = (
        f'echo "{name} $*" >> "{log}"\n'
        'case "$1" in\n'
        f"  probe) echo '{probe}' ;;\n"
        f"  sessions) cat <<'JSON'\n{text}\nJSON\n    exit {code} ;;\n"
        "  *) echo '{\"results\":[]}' ;;\n"
        "esac\n"
    )
    return write_adapter(directory, name, body)


def session(session_id: str, cwd: str = "/work/alpha", branches=(), **extra) -> dict:
    record = {
        "id": session_id,
        "cwd": cwd,
        "last_active": "2026-09-12T18:04:11Z",
        "branches": list(branches),
    }
    record.update(extra)
    return record


def branch(name: str, directory: str = "/work/alpha", evidence: str = "command") -> dict:
    return {"name": name, "dir": directory, "evidence": evidence, "seen_at": "2026-09-12T18:00:00Z"}


class AdapterCase(HerdrCase):
    def setUp(self) -> None:
        super().setUp()
        self.adapter_dir = self.root / "adapters"
        self.adapter_log = self.root / "adapters.log"
        self.warnings: list[str] = []

    def adapter_calls(self) -> list[str]:
        if not self.adapter_log.exists():
            return []
        return self.adapter_log.read_text(encoding="utf-8").splitlines()


class TestGatherSessions(AdapterCase):
    def test_only_adapters_declaring_sessions_are_asked_with_since(self):
        sessions_adapter(
            self.adapter_dir,
            "claude",
            "claude",
            {"sessions": [session(SESSION_A)]},
            log=self.adapter_log,
        )
        probe_adapter(
            self.adapter_dir,
            "opencode",
            {
                "agent": "opencode",
                "source": "herdr:opencode",
                "available": True,
                "confidence": "heuristic",
            },
        )
        adapters, incomplete = audit.load_adapters(self.adapter_dir, warn=self.warnings.append)
        self.assertEqual(incomplete, [])
        gathered = audit.gather_sessions(adapters, 7, warn=self.warnings.append)
        self.assertEqual(
            [c for c in self.adapter_calls() if " sessions" in c], ["claude sessions --since 7"]
        )
        self.assertEqual(
            [(s["agent"], s["id"]) for s in gathered.sessions], [("claude", SESSION_A)]
        )
        self.assertEqual(gathered.incomplete, [])
        self.assertEqual(gathered.failed, {})

    def test_include_sdk_is_passed_only_when_asked(self):
        sessions_adapter(
            self.adapter_dir, "claude", "claude", {"sessions": []}, log=self.adapter_log
        )
        adapters, _ = audit.load_adapters(self.adapter_dir, warn=self.warnings.append)
        audit.gather_sessions(adapters, 30, include_sdk=True, warn=self.warnings.append)
        self.assertIn("claude sessions --since 30 --include-sdk", self.adapter_calls())

    def test_a_failing_adapter_is_warned_by_name_and_makes_the_run_incomplete(self):
        sessions_adapter(
            self.adapter_dir,
            "claude",
            "claude",
            {"sessions": [session(SESSION_A)]},
            log=self.adapter_log,
        )
        sessions_adapter(self.adapter_dir, "codex", "codex", "boom", log=self.adapter_log, code=1)
        adapters, _ = audit.load_adapters(self.adapter_dir, warn=self.warnings.append)
        gathered = audit.gather_sessions(adapters, 30, warn=self.warnings.append)
        self.assertEqual([s["agent"] for s in gathered.sessions], ["claude"])
        self.assertEqual(list(gathered.failed), ["codex"])
        self.assertEqual(len(gathered.incomplete), 1)
        self.assertIn("codex", gathered.incomplete[0])
        self.assertTrue(any("codex" in w for w in self.warnings), self.warnings)

    def test_an_adapter_whose_every_session_was_dropped_makes_the_run_incomplete(self):
        sessions_adapter(
            self.adapter_dir,
            "claude",
            "claude",
            {"sessions": [{"id": "x"}]},
            log=self.adapter_log,
        )
        adapters, _ = audit.load_adapters(self.adapter_dir, warn=self.warnings.append)
        gathered = audit.gather_sessions(adapters, 30, warn=self.warnings.append)
        self.assertEqual(gathered.sessions, [])
        self.assertEqual(list(gathered.failed), ["claude"])
        self.assertIn("claude", gathered.incomplete[0])

    def test_an_adapter_whose_probe_fails_makes_the_run_incomplete(self):
        write_adapter(self.adapter_dir, "codex", 'echo "probe broke" >&2\nexit 3\n')
        adapters, incomplete = audit.load_adapters(self.adapter_dir, warn=self.warnings.append)
        self.assertEqual(adapters, [])
        self.assertEqual(len(incomplete), 1)
        self.assertIn("codex", incomplete[0])


def adapter(agent: str, *, sessions: bool = True):
    return feed.Adapter(
        path=Path(f"/adapters/{agent}"),
        agent=agent,
        source=f"herdr:{agent}",
        confidence="heuristic",
        command=agent,
        unverified=False,
        sessions=sessions,
        probe={},
    )


def pane(pane_id: str, agent: str = "claude", session_value=SESSION_A, cwd="/work/alpha"):
    return {"pane_id": pane_id, "agent": agent, "session": session_value, "cwd": cwd, "tab": "t"}


def gathered(*sessions_by_agent, failed=None):
    records = [dict(s, agent=agent) for agent, s in sessions_by_agent]
    reasons = [f"adapter {a}: failed" for a in (failed or {})]
    return audit.Gathered(sessions=records, failed=dict(failed or {}), incomplete=reasons)


WORKTREE = "/home/placeholder-user/.cache/fr/worktrees/example-repo/feat__from-pane"


class TestWorktreePath(unittest.TestCase):
    def test_a_path_inside_an_fr_worktree_names_its_branch_and_worktree(self):
        for cwd in (WORKTREE, WORKTREE + "/", WORKTREE + "/src/deep"):
            with self.subTest(cwd=cwd):
                self.assertEqual(
                    audit.pane_worktree(cwd), {"name": "feat/from-pane", "dir": WORKTREE}
                )

    def test_anything_else_names_nothing(self):
        for cwd in (
            None,
            "",
            "/work/alpha",
            "/home/placeholder-user/.cache/fr/worktrees/example-repo",
            "/home/placeholder-user/.cache/fr/worktrees/example-repo/main",
            "/home/placeholder-user/.cache/fr/worktrees/example-repo/a$b",
            "/home/placeholder-user/cache/fr/worktrees/example-repo/feat__x",
        ):
            with self.subTest(cwd=cwd):
                self.assertIsNone(audit.pane_worktree(cwd))


class TestJoin(HerdrCase):
    """Pure; HerdrCase only for the fr shim, which must stay silent."""

    def one(self, panes, adapters, found) -> dict:
        joined = audit.join(panes, adapters, found)
        self.assertEqual(len(joined), 1)
        return joined[0]

    def test_a_session_found_in_history_gives_its_branches(self):
        found = gathered(("claude", session(SESSION_A, branches=[branch("feat/x")])))
        record = self.one([pane("w2:p9")], [adapter("claude")], found)
        self.assertEqual(
            (record["pane_id"], record["agent"], record["session_id"], record["note"]),
            ("w2:p9", "claude", SESSION_A, None),
        )
        self.assertEqual(
            record["entries"],
            [{"name": "feat/x", "dir": "/work/alpha", "cwd": "/work/alpha", "evidence": "command"}],
        )

    def test_a_pane_cwd_inside_an_fr_worktree_adds_a_worktree_path_branch(self):
        found = gathered(("claude", session(SESSION_A, branches=[branch("feat/x")])))
        record = self.one([pane("w2:p9", cwd=WORKTREE + "/src")], [adapter("claude")], found)
        self.assertIn(
            {
                "name": "feat/from-pane",
                "dir": WORKTREE,
                "cwd": WORKTREE + "/src",
                "evidence": "worktree-path",
            },
            record["entries"],
        )
        self.assertEqual(len(record["entries"]), 2)
        self.assertIsNone(record["note"])

    def test_the_worktree_path_shows_what_an_unsupported_agents_pane_works_on(self):
        record = self.one(
            [pane("w2:p1", agent="opencode", session_value=None, cwd=WORKTREE)],
            [adapter("opencode", sessions=False)],
            gathered(),
        )
        self.assertIsNone(record["note"])
        self.assertEqual([e["name"] for e in record["entries"]], ["feat/from-pane"])

    def test_every_section_one_note(self):
        claude = adapter("claude")
        cases = (
            (
                "no branch evidence",
                pane("w2:p1"),
                [claude],
                gathered(("claude", session(SESSION_A))),
            ),
            (
                "session not reported to Herdr",
                pane("w2:p1", session_value=None),
                [claude],
                gathered(("claude", session(SESSION_A, branches=[branch("feat/x")]))),
            ),
            (
                "session not found in history (outside --since, or SDK-driven)",
                pane("w2:p1", session_value=SESSION_B),
                [claude],
                gathered(("claude", session(SESSION_A, branches=[branch("feat/x")]))),
            ),
            (
                "no session history (unsupported)",
                pane("w2:p1", agent="opencode"),
                [claude, adapter("opencode", sessions=False)],
                gathered(),
            ),
            ("no adapter", pane("w2:p1", agent="aider"), [claude], gathered()),
        )
        for note, the_pane, adapters, found in cases:
            with self.subTest(note=note):
                record = self.one([the_pane], adapters, found)
                self.assertEqual(record["note"], note)
                self.assertEqual(record["entries"], [])

    def test_a_session_is_looked_up_under_its_own_agent_only(self):
        found = gathered(("codex", session(SESSION_A, branches=[branch("feat/x")])))
        record = self.one([pane("w2:p1")], [adapter("claude"), adapter("codex")], found)
        self.assertEqual(
            record["note"], "session not found in history (outside --since, or SDK-driven)"
        )

    def test_a_pane_whose_adapter_failed_says_so_rather_than_not_found(self):
        found = gathered(failed={"claude": "adapter claude: sessions exited 1"})
        record = self.one([pane("w2:p1")], [adapter("claude")], found)
        self.assertEqual(record["note"], "session history unavailable (adapter failed)")

    def test_entries_for_resolution_cover_every_session_and_every_pane_worktree(self):
        found = gathered(
            ("claude", session(SESSION_A, branches=[branch("feat/open")])),
            (
                "claude",
                session(
                    SESSION_B, cwd="/work/beta", branches=[branch("feat/closed", "/work/beta")]
                ),
            ),
        )
        joined = audit.join([pane("w2:p1", cwd=WORKTREE)], [adapter("claude")], found)
        entries = audit.resolution_entries(joined, found)
        self.assertEqual(
            sorted((e["name"], e["dir"], e["cwd"]) for e in entries),
            [
                ("feat/closed", "/work/beta", "/work/beta"),
                ("feat/from-pane", WORKTREE, WORKTREE),
                ("feat/open", "/work/alpha", "/work/alpha"),
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=1)
