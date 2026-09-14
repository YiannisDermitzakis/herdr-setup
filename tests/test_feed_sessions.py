#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""The `sessions` query's runner-side half: validation and the subprocess call.

docs/adapters.md, "The sessions query" section, is the contract; this file
tests lib/feed.py's implementation of it -- parse_sessions (the tolerant
validator every adapter's answer passes through), branch_name_ok (the name
filter the runner re-applies regardless of what an adapter already did), and
sessions() (the subprocess call itself, the third door beside probe() and
resolve()).

Phase 2's adapters (tests/test_adapter_claude_sessions.py,
tests/test_adapter_codex_sessions.py) exercise the other half: an adapter
actually walking its own session store. Nothing here touches a real store.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from feedlib import (  # noqa: E402
    REAL_BRANCH_NAMES,
    UNREAL_BRANCH_NAMES,
    isolate_environment,
    load_feed,
    probe_adapter,
    write_adapter,
)

isolate_environment()

feed = load_feed()

GOOD_PROBE = {
    "agent": "claude",
    "source": "herdr:claude",
    "available": True,
    "confidence": "exact",
    "sessions": True,
}

# One session, complete and valid, for tests that only need a base to mutate.
GOOD_SESSION = {
    "id": "00000000-0000-4000-8000-000000000001",
    "cwd": "/work/alpha",
    "last_active": "2026-09-12T18:04:11Z",
    "title": "add the health endpoint",
    "branches": [
        {
            "name": "feat/health-endpoint",
            "dir": "/work/alpha",
            "evidence": "command",
            "seen_at": "2026-09-12T17:58:02Z",
        }
    ],
}


def adapter_for(path: Path, *, sessions: bool = True) -> feed.Adapter:
    """An Adapter record pointing at `path`, the way usable_adapters() builds one."""
    return feed.Adapter(
        path=path,
        agent="claude",
        source="herdr:claude",
        confidence="exact",
        command="claude",
        unverified=False,
        sessions=sessions,
        probe=dict(GOOD_PROBE, sessions=sessions),
    )


class TestValidateProbeSessionsKey(unittest.TestCase):
    def test_it_accepts_true_and_false(self):
        for value in (True, False):
            feed.validate_probe(dict(GOOD_PROBE, sessions=value))

    def test_it_accepts_the_key_being_absent(self):
        obj = {k: v for k, v in GOOD_PROBE.items() if k != "sessions"}
        feed.validate_probe(obj)

    def test_it_rejects_a_non_boolean(self):
        for value in ("yes", 1, None, [], {}):
            with self.assertRaises(feed.AdapterError, msg=f"sessions={value!r} must be rejected"):
                feed.validate_probe(dict(GOOD_PROBE, sessions=value))


class TestAdapterSessionsField(unittest.TestCase):
    def test_an_adapter_that_declares_sessions_true_has_it_set(self):
        adapters, _ = self._usable(dict(GOOD_PROBE, sessions=True))
        self.assertTrue(adapters[0].sessions)

    def test_an_adapter_with_no_sessions_key_has_it_false(self):
        obj = {k: v for k, v in GOOD_PROBE.items() if k != "sessions"}
        adapters, _ = self._usable(obj)
        self.assertFalse(adapters[0].sessions)

    def test_an_adapter_that_declares_sessions_false_has_it_false(self):
        adapters, _ = self._usable(dict(GOOD_PROBE, sessions=False))
        self.assertFalse(adapters[0].sessions)

    def _usable(self, probe_obj):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            probe_adapter(directory, "claude", probe_obj)
            warnings: list[str] = []
            adapters = feed.usable_adapters(directory, warn=warnings.append)
            self.assertEqual(len(adapters), 1, warnings)
            return adapters, warnings


class TestParseSessionsTopLevel(unittest.TestCase):
    def test_it_raises_for_a_non_object(self):
        for value in ([], "sessions", 7, None):
            with self.assertRaises(feed.AdapterError):
                feed.parse_sessions(value)

    def test_it_raises_when_sessions_key_is_missing(self):
        with self.assertRaises(feed.AdapterError):
            feed.parse_sessions({})

    def test_it_raises_when_sessions_is_not_a_list(self):
        for value in ("nope", 3, {}, None):
            with self.assertRaises(feed.AdapterError):
                feed.parse_sessions({"sessions": value})

    def test_an_empty_list_is_fine(self):
        sessions, dropped = feed.parse_sessions({"sessions": []})
        self.assertEqual(sessions, [])
        self.assertEqual(dropped, 0)

    def test_the_documented_example_survives_whole(self):
        sessions, dropped = feed.parse_sessions({"sessions": [GOOD_SESSION]})
        self.assertEqual(dropped, 0)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(sessions[0]["branches"]), 1)


class TestParseSessionsDropsBrokenSessions(unittest.TestCase):
    def test_a_non_object_session_is_dropped_and_counted(self):
        sessions, dropped = feed.parse_sessions({"sessions": ["not an object"]})
        self.assertEqual(sessions, [])
        self.assertEqual(dropped, 1)

    def test_missing_id_cwd_or_last_active_is_dropped_and_counted(self):
        for key in ("id", "cwd", "last_active"):
            broken = {k: v for k, v in GOOD_SESSION.items() if k != key}
            sessions, dropped = feed.parse_sessions({"sessions": [broken]})
            self.assertEqual(sessions, [], f"missing {key} must be dropped")
            self.assertEqual(dropped, 1, f"missing {key} must be counted")

    def test_a_non_list_branches_is_dropped_and_counted(self):
        for value in ("nope", {}, None):
            broken = dict(GOOD_SESSION, branches=value)
            sessions, dropped = feed.parse_sessions({"sessions": [broken]})
            self.assertEqual(sessions, [])
            self.assertEqual(dropped, 1)

    def test_a_valid_session_beside_a_broken_one_still_comes_through(self):
        sessions, dropped = feed.parse_sessions({"sessions": [GOOD_SESSION, {"id": "only-an-id"}]})
        self.assertEqual(len(sessions), 1)
        self.assertEqual(dropped, 1)

    def test_missing_branches_key_entirely_is_dropped(self):
        broken = {k: v for k, v in GOOD_SESSION.items() if k != "branches"}
        sessions, dropped = feed.parse_sessions({"sessions": [broken]})
        self.assertEqual(sessions, [])
        self.assertEqual(dropped, 1)

    def test_title_is_omitted_when_absent_rather_than_null(self):
        no_title = {k: v for k, v in GOOD_SESSION.items() if k != "title"}
        sessions, _ = feed.parse_sessions({"sessions": [no_title]})
        self.assertNotIn("title", sessions[0])


class TestParseSessionsDropsBrokenBranches(unittest.TestCase):
    def _branch(self, **overrides):
        branch = dict(GOOD_SESSION["branches"][0])
        branch.update(overrides)
        return dict(GOOD_SESSION, branches=[branch])

    def test_an_evidence_outside_the_documented_four_is_dropped(self):
        for value in ("guess", "", None, "SESSION-META"):
            sessions, dropped = feed.parse_sessions({"sessions": [self._branch(evidence=value)]})
            self.assertEqual(sessions[0]["branches"], [], f"evidence={value!r} must be dropped")
            # The SESSION survives; only the one bad branch is dropped, silently.
            self.assertEqual(dropped, 0)

    def test_every_documented_evidence_value_is_accepted(self):
        for value in feed.EVIDENCE:
            sessions, _ = feed.parse_sessions({"sessions": [self._branch(evidence=value)]})
            self.assertEqual(len(sessions[0]["branches"]), 1, value)

    def test_a_missing_name_dir_or_seen_at_drops_just_the_branch(self):
        for key in ("name", "dir", "seen_at"):
            branch = dict(GOOD_SESSION["branches"][0])
            del branch[key]
            session = dict(GOOD_SESSION, branches=[branch])
            sessions, dropped = feed.parse_sessions({"sessions": [session]})
            self.assertEqual(sessions[0]["branches"], [], f"missing {key} must drop the branch")
            self.assertEqual(dropped, 0)

    def test_a_non_dict_branch_entry_is_ignored(self):
        session = dict(GOOD_SESSION, branches=["nope"])
        sessions, dropped = feed.parse_sessions({"sessions": [session]})
        self.assertEqual(sessions[0]["branches"], [])
        self.assertEqual(dropped, 0)

    def test_parse_sessions_re_applies_branch_name_ok(self):
        """An adapter that forgot its own filter must not inject noise through here."""
        sessions, _ = feed.parse_sessions({"sessions": [self._branch(name="main")]})
        self.assertEqual(sessions[0]["branches"], [])


class TestBranchNameOk(unittest.TestCase):
    """The exact list the phase brief pins, run through the runner's own copy.

    tests/test_adapter_claude_sessions.py runs the SAME two lists
    (UNREAL_BRANCH_NAMES, REAL_BRANCH_NAMES, both in tests/helpers/feedlib.py)
    through the adapter's own copy, so the two filters cannot silently drift
    apart from each other.
    """

    def test_every_unreal_name_is_rejected(self):
        for name in UNREAL_BRANCH_NAMES:
            self.assertFalse(feed.branch_name_ok(name), name)

    def test_every_real_name_is_accepted(self):
        for name in REAL_BRANCH_NAMES:
            self.assertTrue(feed.branch_name_ok(name), name)


class TestSessionsCall(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_it_runs_sessions_with_since_and_parses_the_answer(self):
        path = write_adapter(
            self.dir,
            "claude",
            'echo "sessions $*" >&2\nprintf \'{"sessions":[]}\'\n',
        )
        result = feed.sessions(adapter_for(path), 30)
        self.assertEqual(result, [])

    def test_it_forwards_since_as_an_argument(self):
        log = self.dir / "log"
        path = write_adapter(
            self.dir, "logger", f'echo "$@" > "{log}"\nprintf \'{{"sessions":[]}}\'\n'
        )
        feed.sessions(adapter_for(path), 30)
        self.assertEqual(log.read_text().split(), ["sessions", "--since", "30"])

    def test_include_sdk_is_added_only_when_asked(self):
        log = self.dir / "log"
        path = write_adapter(
            self.dir, "claude", f'echo "$@" > "{log}"\nprintf \'{{"sessions":[]}}\'\n'
        )
        feed.sessions(adapter_for(path), 7)
        self.assertNotIn("--include-sdk", log.read_text().split())
        feed.sessions(adapter_for(path), 7, include_sdk=True)
        self.assertIn("--include-sdk", log.read_text().split())

    def test_non_zero_exit_raises_adapter_error(self):
        path = write_adapter(self.dir, "claude", "printf '{\"sessions\":[]}'\nexit 1\n")
        with self.assertRaises(feed.AdapterError):
            feed.sessions(adapter_for(path), 30)

    def test_empty_output_raises_adapter_error(self):
        path = write_adapter(self.dir, "claude", "exit 0\n")
        with self.assertRaises(feed.AdapterError):
            feed.sessions(adapter_for(path), 30)

    def test_non_json_output_raises_adapter_error(self):
        path = write_adapter(self.dir, "claude", "echo not json at all\n")
        with self.assertRaises(feed.AdapterError):
            feed.sessions(adapter_for(path), 30)

    def test_a_timeout_raises_adapter_error(self):
        path = write_adapter(self.dir, "claude", "sleep 5\n")
        with self.assertRaises(feed.AdapterError):
            feed.sessions(adapter_for(path), 30, timeout=0.2)

    def test_the_result_has_already_passed_parse_sessions(self):
        payload = json.dumps({"sessions": [GOOD_SESSION, {"id": "broken-only"}]})
        path = write_adapter(self.dir, "real", f"cat <<'JSON'\n{payload}\nJSON\n")
        result = feed.sessions(adapter_for(path), 30)
        self.assertEqual(len(result), 1, "the broken second session must already be dropped")
        self.assertEqual(result[0]["id"], GOOD_SESSION["id"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
