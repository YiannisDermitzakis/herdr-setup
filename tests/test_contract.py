#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""The adapter contract, checked against the document that states it.

docs/adapters.md is what somebody adding a harness reads, and a contract
document that has drifted from the runner is worse than none: it tells them
their adapter is correct while the runner rejects it. So this file does not
restate the contract, it EXTRACTS it. Every example is pulled out of the
document by its marker and run through the real code -- the probe example
through validate_probe, and the worked minimal adapter through probe() and
resolve() as an actual executable.

That makes the document executable. If the runner tightens a rule and the
document is not updated, this file fails; if the document gains an example
that does not work, this file fails.

Phase 8 extends this file with a conformance pass over every real adapter in
adapters/.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import REPO_ROOT, isolate_environment, load_feed, write_opencode_db  # noqa: E402

isolate_environment()

feed = load_feed()

DOC = REPO_ROOT / "docs" / "adapters.md"

# A block is claimed by an HTML comment on the line before its fence:
#     <!-- contract: probe -->
#     ```json
#     ...
#     ```
BLOCK = re.compile(
    r"<!--\s*contract:\s*(?P<name>[a-z0-9-]+)\s*-->\s*\n```[a-z]*\n(?P<body>.*?)\n```",
    re.DOTALL,
)


def blocks() -> dict[str, str]:
    if not DOC.exists():
        raise AssertionError(f"{DOC} does not exist")
    return {m.group("name"): m.group("body") for m in BLOCK.finditer(DOC.read_text("utf-8"))}


class TestTheDocumentIsPresent(unittest.TestCase):
    def test_the_document_exists(self):
        self.assertTrue(DOC.exists(), f"{DOC} is the adapter contract and must exist")

    def test_it_carries_every_marked_example(self):
        self.assertEqual(
            sorted(blocks()),
            ["minimal-adapter", "probe", "resolve-request", "resolve-response"],
        )

    def test_it_states_the_rule_that_the_adapter_never_reports(self):
        text = DOC.read_text("utf-8").lower()
        self.assertIn("never", text)
        self.assertIn("socket", text)
        for word in ("exact", "heuristic", "unverified", "available"):
            self.assertIn(word, text, f"the contract must explain {word}")

    def test_it_states_which_time_frame_pid_start_epoch_uses(self):
        """Phases 7 and 8 compare an agent's own recorded start against this.

        Claude Code writes `procStart` in UTC while `ps -o lstart=` is local,
        so an adapter that guesses is wrong by the host's offset -- and right
        on a host running in UTC, which is how such a bug survives testing.
        The contract has to say the frame out loud.
        """
        text = DOC.read_text("utf-8")
        self.assertIn("pid_start_epoch", text)
        self.assertIn("epoch seconds", text.lower())
        self.assertIn("UTC", text)
        self.assertIn("procStart", text)


class TestValidateProbe(unittest.TestCase):
    def setUp(self) -> None:
        self.example = json.loads(blocks()["probe"])

    def test_it_accepts_the_documented_example(self):
        self.assertEqual(feed.validate_probe(self.example), self.example)

    def test_it_rejects_every_single_key_omission(self):
        for key in feed.REQUIRED_PROBE_KEYS:
            partial = {k: v for k, v in self.example.items() if k != key}
            with self.assertRaises(feed.AdapterError, msg=f"omitting {key} must be rejected"):
                feed.validate_probe(partial)

    def test_the_example_declares_every_required_key(self):
        for key in feed.REQUIRED_PROBE_KEYS:
            self.assertIn(key, self.example)

    def test_it_rejects_an_unknown_confidence(self):
        with self.assertRaises(feed.AdapterError):
            feed.validate_probe(dict(self.example, confidence="fairly-sure"))

    def test_it_accepts_both_documented_confidences(self):
        for value in ("exact", "heuristic"):
            self.assertEqual(
                feed.validate_probe(dict(self.example, confidence=value)),
                dict(self.example, confidence=value),
            )

    def test_it_rejects_a_non_object(self):
        for value in ([], "probe", 7, None):
            with self.assertRaises(feed.AdapterError):
                feed.validate_probe(value)

    def test_it_rejects_wrongly_typed_values(self):
        for key, value in (
            ("agent", ""),
            ("agent", 3),
            ("source", ""),
            ("available", "true"),
            ("unverified", "yes"),
            ("command", ""),
        ):
            with self.assertRaises(feed.AdapterError, msg=f"{key}={value!r} must be rejected"):
                feed.validate_probe(dict(self.example, **{key: value}))


class TestTheDocumentedShapes(unittest.TestCase):
    def test_the_resolve_request_is_the_shape_the_runner_sends(self):
        request = json.loads(blocks()["resolve-request"])
        self.assertIn("panes", request)
        for pane in request["panes"]:
            self.assertEqual(sorted(pane), ["cwd", "pane_id", "pid", "pid_start_epoch"])

    def test_the_resolve_response_survives_the_runner_s_own_parser(self):
        response = blocks()["resolve-response"]
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "documented"
            adapter.write_text(f"#!/bin/sh\ncat >/dev/null\ncat <<'JSON'\n{response}\nJSON\n")
            adapter.chmod(0o755)
            results = feed.resolve(adapter, [])
        by_pane = feed.candidates_by_pane(results)
        self.assertTrue(by_pane, "the documented response must yield at least one pane")
        for candidates in by_pane.values():
            for candidate in candidates:
                self.assertIn(candidate.get("confidence"), feed.VALID_CONFIDENCE)


class TestTheWorkedAdapter(unittest.TestCase):
    """The minimal adapter in the document is run, not read.

    A worked example that does not work is the fastest way to make somebody
    believe a broken adapter is correct.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.dir = root / "adapters"
        self.dir.mkdir()
        self.adapter = self.dir / "example"
        self.adapter.write_text(blocks()["minimal-adapter"] + "\n", encoding="utf-8")
        self.adapter.chmod(0o755)

        self.panes = json.loads(blocks()["resolve-request"])["panes"]
        self.store = root / "sessions"
        self.store.mkdir()
        (self.store / "s-here.txt").write_text(self.panes[0]["cwd"] + "\n")
        (self.store / "s-elsewhere.txt").write_text("/somewhere/else\n")

        self._saved = dict(os.environ)
        self.addCleanup(self._restore_env)
        os.environ["EXAMPLE_SESSION_DIR"] = str(self.store)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved)

    def test_its_probe_satisfies_the_contract(self):
        feed.validate_probe(feed.probe(self.adapter))

    def test_the_runner_discovers_and_accepts_it(self):
        warnings: list[str] = []
        adapters = feed.usable_adapters(self.dir, warn=warnings.append)
        self.assertEqual(len(adapters), 1, warnings)
        self.assertEqual(adapters[0].path.name, "example")
        self.assertEqual(adapters[0].confidence, "heuristic")

    def test_with_no_session_store_it_says_unavailable_and_is_skipped_silently(self):
        """The checklist's first item, run rather than read.

        Every adapter has to behave on a host where its agent is simply not
        installed, and behaving means `available: false`, not an error.
        """
        os.environ["EXAMPLE_SESSION_DIR"] = str(self.store / "nowhere")
        self.assertIs(feed.probe(self.adapter)["available"], False)
        warnings: list[str] = []
        self.assertEqual(feed.usable_adapters(self.dir, warn=warnings.append), [])
        self.assertEqual(warnings, [])

    def test_its_resolve_answers_the_documented_request(self):
        results = feed.resolve(self.adapter, self.panes)
        by_pane = feed.candidates_by_pane(results)
        pane_id = self.panes[0]["pane_id"]
        self.assertIn(pane_id, by_pane)
        self.assertEqual([c["session_id"] for c in by_pane[pane_id]], ["s-here"])
        self.assertEqual(by_pane[pane_id][0]["confidence"], "heuristic")

    def test_it_answers_about_several_panes_at_once(self):
        """Panes arrive in one batch, so a worked example must handle a batch.

        An example that only ever emits one entry, or forgets the separator
        between them, teaches its reader to write an adapter that breaks the
        first time two panes run the same agent -- which is the ordinary case.
        """
        (self.store / "s-other.txt").write_text("/other/checkout\n")
        panes = [
            self.panes[0],
            {"pane_id": "w9:p9", "cwd": "/other/checkout", "pid": 2, "pid_start_epoch": 3},
        ]
        by_pane = feed.candidates_by_pane(feed.resolve(self.adapter, panes))
        self.assertEqual([c["session_id"] for c in by_pane[panes[0]["pane_id"]]], ["s-here"])
        self.assertEqual([c["session_id"] for c in by_pane["w9:p9"]], ["s-other"])

    def test_it_never_opens_the_herdr_socket(self):
        """Stated in prose in the contract; asserted here on the example.

        An adapter that reported would defeat the one rule the whole seam is
        built on -- one place decides -- so the example must not so much as
        mention the socket.
        """
        text = blocks()["minimal-adapter"]
        for forbidden in ("herdr.sock", "HERDR_SOCKET_PATH", "report_agent_session"):
            self.assertNotIn(forbidden, text)

    def test_it_answers_an_empty_pane_list_without_falling_over(self):
        proc = subprocess.run(
            [str(self.adapter), "resolve"],
            input='{"panes":[]}',
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("results", json.loads(proc.stdout))


# --------------------------------------------------------------------------
# Conformance across every real adapter (phase 8)
# --------------------------------------------------------------------------
#
# Every other test in this file exercises the DOCUMENT: the probe example,
# the resolve shapes, the worked minimal adapter. This section instead walks
# adapters/ itself, the same way lib/feed.py's own discover() does, and
# probes each one twice -- once in a fixture home where its agent is
# ABSENT, once where it is PRESENT -- so a fifth adapter that passes its own
# tests/test_adapter_<name>.py in isolation but breaks the shared discovery
# contract does not slip through unnoticed.
#
# A new adapter that omits its own entry from ADAPTER_HOME_LAYOUT fails the
# "present" pass with a message naming exactly what to add, rather than
# silently skipping itself out of the loop.

ADAPTER_HOME_LAYOUT = {
    "claude": lambda home: (home / ".claude" / "sessions").mkdir(parents=True),
    "codex": lambda home: (home / ".codex" / "sessions").mkdir(parents=True),
    "opencode": lambda home: write_opencode_db(
        home / ".local" / "share" / "opencode" / "opencode.db", []
    ),
    "copilot": lambda home: (home / ".copilot" / "session-state").mkdir(parents=True),
}

# Every env var an adapter uses to override its own fallback-under-$HOME
# lookup (adapters/claude's $CLAUDE_CONFIG_DIR, .../codex's $CODEX_HOME,
# .../opencode's $XDG_DATA_HOME, .../copilot's $COPILOT_HOME). Cleared so
# this test's fixture $HOME is what every adapter actually looks under,
# regardless of what the developer's own shell happens to have set.
ADAPTER_OVERRIDE_VARS = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_DATA_HOME", "COPILOT_HOME")


class TestConformanceAcrossAllAdapters(unittest.TestCase):
    def setUp(self) -> None:
        self._env_saved = dict(os.environ)
        self.addCleanup(self._restore_env)
        for var in ADAPTER_OVERRIDE_VARS:
            os.environ.pop(var, None)
        self.adapters = feed.discover(REPO_ROOT / "adapters")
        self.assertTrue(self.adapters, "adapters/ must not be empty for this test to mean anything")

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_saved)

    def test_every_adapter_is_unavailable_and_error_free_when_its_agent_is_absent(self):
        for path in self.adapters:
            with self.subTest(adapter=path.name), tempfile.TemporaryDirectory() as tmp:
                os.environ["HOME"] = tmp
                obj = feed.probe(path)
                feed.validate_probe(obj)
                self.assertIs(
                    obj["available"], False, f"{path.name} must report unavailable on an empty home"
                )

    def test_every_adapter_is_available_when_its_agent_is_present(self):
        for path in self.adapters:
            self.assertIn(
                path.name,
                ADAPTER_HOME_LAYOUT,
                f"add a fixture-home layout for adapters/{path.name} to "
                "ADAPTER_HOME_LAYOUT in tests/test_contract.py",
            )
        for path in self.adapters:
            with self.subTest(adapter=path.name), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                os.environ["HOME"] = str(home)
                ADAPTER_HOME_LAYOUT[path.name](home)
                obj = feed.probe(path)
                feed.validate_probe(obj)
                self.assertIs(obj["available"], True, f"{path.name} must report available")


if __name__ == "__main__":
    unittest.main(verbosity=1)
