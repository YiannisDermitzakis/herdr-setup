#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Adapter discovery and the probe handshake (lib/feed.py).

The probe is where a broken adapter has to stop being the runner's problem.
An adapter is any executable a host happens to have dropped into adapters/;
one that crashes, prints nothing, prints something that is not JSON, or
answers with a shape the runner cannot read must be SKIPPED with a warning,
never allowed to take the whole feed run down with it.

The other half is the opposite rule, and it is the one with teeth: a skip
must be a skip the operator can see. An adapter dropped silently is an agent
whose live panes never get fed, and the whole tool exists so that a restart
does not strand them.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import isolate_environment, load_feed, probe_adapter, write_adapter  # noqa: E402

isolate_environment()

feed = load_feed()

GOOD_PROBE = {
    "agent": "claude",
    "source": "herdr:claude",
    "available": True,
    "confidence": "exact",
    "unverified": False,
}


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)


class TestDiscover(TempDirCase):
    def test_returns_executables_sorted(self):
        for name in ("zulu", "alpha", "mike"):
            write_adapter(self.dir, name, "exit 0\n")
        found = feed.discover(self.dir)
        self.assertEqual([p.name for p in found], ["alpha", "mike", "zulu"])

    def test_ignores_non_executable_files(self):
        write_adapter(self.dir, "runnable", "exit 0\n")
        write_adapter(self.dir, "notes", "exit 0\n", executable=False)
        self.assertEqual([p.name for p in feed.discover(self.dir)], ["runnable"])

    def test_ignores_dotfiles_and_markdown(self):
        write_adapter(self.dir, "claude", "exit 0\n")
        write_adapter(self.dir, ".hidden", "exit 0\n")
        write_adapter(self.dir, "README.md", "exit 0\n")
        self.assertEqual([p.name for p in feed.discover(self.dir)], ["claude"])

    def test_ignores_directories(self):
        (self.dir / "subdir").mkdir()
        (self.dir / "subdir").chmod(0o755)
        write_adapter(self.dir, "claude", "exit 0\n")
        self.assertEqual([p.name for p in feed.discover(self.dir)], ["claude"])

    def test_missing_directory_is_empty_not_an_error(self):
        self.assertEqual(feed.discover(self.dir / "nope"), [])


class TestProbe(TempDirCase):
    def test_parses_one_object(self):
        path = probe_adapter(self.dir, "claude", GOOD_PROBE)
        self.assertEqual(feed.probe(path), GOOD_PROBE)

    def test_non_zero_exit_raises_adapter_error(self):
        body = f"echo '{json.dumps(GOOD_PROBE)}'; exit 3\n"
        path = write_adapter(self.dir, "claude", body)
        with self.assertRaises(feed.AdapterError):
            feed.probe(path)

    def test_no_output_raises_adapter_error(self):
        path = write_adapter(self.dir, "claude", "exit 0\n")
        with self.assertRaises(feed.AdapterError):
            feed.probe(path)

    def test_non_json_raises_adapter_error(self):
        path = write_adapter(self.dir, "claude", "echo not json at all\n")
        with self.assertRaises(feed.AdapterError):
            feed.probe(path)

    def test_json_that_is_not_an_object_raises_adapter_error(self):
        path = probe_adapter(self.dir, "claude", "[1, 2, 3]")
        with self.assertRaises(feed.AdapterError):
            feed.probe(path)

    def test_missing_required_key_raises_adapter_error(self):
        for key in ("agent", "source", "available", "confidence"):
            obj = dict(GOOD_PROBE)
            del obj[key]
            path = probe_adapter(self.dir, "claude", obj)
            with self.assertRaises(feed.AdapterError, msg=f"omitting {key} must be rejected"):
                feed.probe(path)

    def test_unknown_confidence_raises_adapter_error(self):
        obj = dict(GOOD_PROBE, confidence="probably")
        path = probe_adapter(self.dir, "claude", obj)
        with self.assertRaises(feed.AdapterError):
            feed.probe(path)

    def test_heuristic_confidence_is_accepted(self):
        obj = dict(GOOD_PROBE, confidence="heuristic")
        path = probe_adapter(self.dir, "codex", obj)
        self.assertEqual(feed.probe(path)["confidence"], "heuristic")


class TestUsableAdapters(TempDirCase):
    """The skip policy: which probes are used, which are dropped, and how loudly.

    usable_adapters() is what the run itself calls, so this is where "skipped
    with a warning rather than crashing" is actually asserted.
    """

    def usable(self):
        warnings: list[str] = []
        adapters = feed.usable_adapters(self.dir, warn=warnings.append)
        return adapters, warnings

    def test_a_broken_adapter_is_skipped_with_a_warning_and_the_rest_still_run(self):
        write_adapter(self.dir, "broken", "exit 1\n")
        probe_adapter(self.dir, "claude", GOOD_PROBE)
        adapters, warnings = self.usable()
        self.assertEqual([a.agent for a in adapters], ["claude"])
        self.assertTrue(any("broken" in w for w in warnings), warnings)

    def test_non_json_adapter_is_skipped_with_a_warning(self):
        write_adapter(self.dir, "noisy", "echo hello\n")
        adapters, warnings = self.usable()
        self.assertEqual(adapters, [])
        self.assertTrue(any("noisy" in w for w in warnings), warnings)

    def test_unavailable_adapter_is_skipped_silently(self):
        probe_adapter(self.dir, "codex", dict(GOOD_PROBE, agent="codex", available=False))
        adapters, warnings = self.usable()
        self.assertEqual(adapters, [])
        self.assertEqual(warnings, [])

    def test_unverified_adapter_is_used_and_warned_about_by_name(self):
        probe_adapter(
            self.dir,
            "copilot",
            dict(GOOD_PROBE, agent="copilot", source="herdr:copilot", unverified=True),
        )
        adapters, warnings = self.usable()
        self.assertEqual([a.agent for a in adapters], ["copilot"])
        self.assertTrue(any("copilot" in w for w in warnings), warnings)
        self.assertTrue(any("unverified" in w.lower() for w in warnings), warnings)

    def test_a_probe_that_hangs_is_skipped_rather_than_hanging_the_run(self):
        """An adapter is third-party code; it does not get to stop the tool.

        `probe` is given a timeout, and the timeout is a skip with a warning,
        the same as any other broken probe.
        """
        write_adapter(self.dir, "sleepy", "sleep 30\n")
        warnings: list[str] = []
        adapters = feed.usable_adapters(self.dir, warn=warnings.append, timeout=0.5)
        self.assertEqual(adapters, [])
        self.assertTrue(any("sleepy" in w for w in warnings), warnings)

    def test_adapter_records_carry_agent_source_confidence_and_command(self):
        probe_adapter(self.dir, "claude", GOOD_PROBE)
        adapters, _ = self.usable()
        adapter = adapters[0]
        self.assertEqual(adapter.agent, "claude")
        self.assertEqual(adapter.source, "herdr:claude")
        self.assertEqual(adapter.confidence, "exact")
        # `command` is what a pane's foreground argv0 is matched against. It
        # defaults to the agent name; an adapter may declare its own.
        self.assertEqual(adapter.command, "claude")
        self.assertEqual(adapter.path.name, "claude")

    def test_an_adapter_may_declare_its_own_command(self):
        probe_adapter(self.dir, "hermes", dict(GOOD_PROBE, agent="hermes", command="hermes-cli"))
        adapters, _ = self.usable()
        self.assertEqual(adapters[0].command, "hermes-cli")


if __name__ == "__main__":
    unittest.main(verbosity=1)
