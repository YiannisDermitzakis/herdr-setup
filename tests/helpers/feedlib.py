"""Shared plumbing for the Python test files.

Not a test file itself: it lives in tests/helpers/ so tests/run.sh's
`test_*.py` glob never picks it up.

`load_feed()` imports lib/feed.py by path. The file has no `.py`-package
home and is a PEP 723 script rather than an installed module, so an ordinary
import statement cannot reach it; importlib by path is the standard-library
way and keeps the test running under the same uv-pinned interpreter as the
script it is testing.

`write_adapter()` writes a tiny inline shell adapter into a directory. Nearly
every probe and resolve test needs one, and building them by hand invites the
one mistake that matters here: an adapter that is not executable, or one that
silently talks to something real. These are three-line shell scripts that
print a fixed string.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import socket
import sys
import threading
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent
HELPERS_DIR = TESTS_DIR / "helpers"
REPO_ROOT = TESTS_DIR.parent
FEED_PATH = REPO_ROOT / "lib" / "feed.py"

# The same variables tests/run.sh clears. Herdr exports the HERDR_* ones into
# every process it starts, so a suite run from inside a Herdr pane inherits
# the operator's live socket; the FAKE_HERDR_* ones would reconfigure the fake
# under every test from a developer's own shell.
LEAKY_VARS = (
    "HERDR_ENV",
    "HERDR_SOCKET_PATH",
    "HERDR_BIN_PATH",
    "HERDR_PANE_ID",
    "HERDR_TAB_ID",
    "HERDR_WORKSPACE_ID",
    "HERDR_CONFIG_DIR",
    "FAKE_HERDR_LOG",
    "FAKE_HERDR_FIXTURES",
    "FAKE_HERDR_PROTOCOL_MISMATCH",
    "FAKE_HERDR_ERROR_CODE",
    "FAKE_HERDR_ERROR_STREAM",
    "FAKE_HERDR_ERROR_EXIT",
    "FAKE_HERDR_FAIL",
    "FAKE_HERDR_STDERR_NOTE",
    "FAKE_HERDR_PROMPT",
)


def isolate_environment() -> None:
    """Make `herdr` resolve to the fake, whoever started this test file.

    tests/run.sh already does this for the whole suite. Doing it again here
    is not redundancy for its own sake: these files are ordinary pytest
    modules as well (pyproject collects `tests/test_*.py`), and a `pytest`
    run outside the runner would otherwise reach the REAL herdr installed on
    the machine -- a test making live calls to the operator's own server.
    A test file that cannot be run wrongly is worth two lines.
    """
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep)
    if not parts or parts[0] != str(HELPERS_DIR):
        os.environ["PATH"] = os.pathsep.join([str(HELPERS_DIR), *parts])
    for name in LEAKY_VARS:
        os.environ.pop(name, None)


def load_feed():
    """Import lib/feed.py as a module object."""
    spec = importlib.util.spec_from_file_location("hs_feed", FEED_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {FEED_PATH}")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec_module, not after: @dataclass resolves its own
    # field annotations through sys.modules[cls.__module__], so a module that
    # is not there yet fails at class-definition time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_adapter(directory: Path, name: str, body: str, *, executable: bool = True) -> Path:
    """Write a shell adapter into `directory` and return its path.

    `body` is the script text after the shebang. Made executable unless the
    test is specifically about a non-executable file being ignored.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    else:
        path.chmod(0o644)
    return path


def probe_adapter(directory: Path, name: str, probe_obj, **kwargs) -> Path:
    """An adapter whose `probe` prints `probe_obj` (a dict, or raw text)."""
    text = probe_obj if isinstance(probe_obj, str) else json.dumps(probe_obj)
    body = (
        'case "$1" in\n'
        f"  probe) cat <<'JSON'\n{text}\nJSON\n"
        "    ;;\n"
        "  *) echo '{\"results\":[]}' ;;\n"
        "esac\n"
    )
    return write_adapter(directory, name, body, **kwargs)


class RecordingServer:
    """A unix socket server that records the JSON lines sent to it.

    Used instead of a mock so the report stage is exercised through a real
    AF_UNIX connect/send, which is what the Herdr integration hooks do. A
    test that asserts nothing was received is therefore evidence that no
    socket was opened, not merely that a stub was not called.
    """

    def __init__(self, path: Path):
        self.path = str(path)
        self.received: list[dict] = []
        self.raw: list[str] = []
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.path)
        self._server.listen(8)
        self._server.settimeout(0.2)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> RecordingServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._server.close()
        with contextlib.suppress(OSError):
            os.unlink(self.path)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except (TimeoutError, OSError):
                continue
            with conn:
                conn.settimeout(1.0)
                buf = b""
                try:
                    while b"\n" not in buf:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        buf += chunk
                except OSError:
                    pass
                line = buf.split(b"\n", 1)[0].decode("utf-8", "replace")
                if line:
                    self.raw.append(line)
                    with contextlib.suppress(ValueError):
                        self.received.append(json.loads(line))
                with contextlib.suppress(OSError):
                    conn.sendall(b'{"result":{"ok":true}}\n')


# --------------------------------------------------------------------------
# Captured Herdr responses
# --------------------------------------------------------------------------
#
# Everything below builds a Herdr answer by loading a CAPTURE from
# tests/fixtures/herdr/ and editing values inside it. Nothing here writes a
# response shape by hand, and nothing may. See that directory's README for
# why: phase 6's first cut invented three fields that Herdr does not return
# and its hand-built fixtures invented the same three, so the tests agreed
# with the code and neither agreed with reality.
#
# Editing a VALUE is fine. Adding, renaming or removing a KEY is not -- at
# that point the fixture stops being evidence, which is the whole failure
# this indirection exists to prevent.

CAPTURES = TESTS_DIR / "fixtures" / "herdr"


def captured(name: str) -> dict:
    """Load a captured Herdr response by file stem."""
    return json.loads((CAPTURES / f"{name}.json").read_text(encoding="utf-8"))


def agent_entry(index: int = 0, **values) -> dict:
    """One captured `agent.list` entry, with the named values replaced."""
    entries = captured("agent-list")["result"]["agents"]
    entry = dict(entries[index % len(entries)])
    for key, value in values.items():
        if key not in entry:
            raise KeyError(f"{key!r} is not a key the capture has; do not invent one")
        entry[key] = value
    return entry


def agent_list(entries) -> dict:
    """A captured `agent.list` answer carrying exactly `entries`."""
    answer = captured("agent-list")
    answer["result"]["agents"] = list(entries)
    return answer


def process_entry(index: int = -1, **values) -> dict:
    """One captured foreground process, with the named values replaced.

    Index -1 is the agent itself; 0 is the `node` child that sits beside it
    in the capture, which is what makes "several foreground processes, one of
    them the agent" the default case rather than a special one.
    """
    processes = captured("pane-process-info")["result"]["process_info"]["foreground_processes"]
    entry = dict(processes[index])
    for key, value in values.items():
        if key not in entry:
            raise KeyError(f"{key!r} is not a key the capture has; do not invent one")
        entry[key] = value
    return entry


def process_info(pane_id=None, processes=None, group_id=None) -> dict:
    """A captured `pane.process_info` answer, with values replaced."""
    answer = captured("pane-process-info")
    info = answer["result"]["process_info"]
    if pane_id is not None:
        info["pane_id"] = pane_id
    if processes is not None:
        info["foreground_processes"] = list(processes)
    if group_id is not None:
        info["foreground_process_group_id"] = group_id
    return answer


# --------------------------------------------------------------------------
# Captured Claude Code and Codex session state (phase 7)
#
# Same discipline as the Herdr captures above, and the same reason: phase 6's
# own postmortem (tests/fixtures/herdr/README.md) is what this indirection
# exists to not repeat. Editing a VALUE is fine. Adding, renaming or removing
# a KEY is not.

CLAUDE_CAPTURES = TESTS_DIR / "fixtures" / "claude"
CODEX_CAPTURES = TESTS_DIR / "fixtures" / "codex"


def claude_session(**values) -> dict:
    """The captured `~/.claude/sessions/<pid>.json` shape, values replaced.

    See tests/fixtures/claude/README.md for provenance and the time-frame
    trap this capture exists to test against.
    """
    session = json.loads((CLAUDE_CAPTURES / "session.json").read_text(encoding="utf-8"))
    for key, value in values.items():
        if key not in session:
            raise KeyError(f"{key!r} is not a key the capture has; do not invent one")
        session[key] = value
    return session


def write_claude_session(config_dir: Path, pid, **values) -> Path:
    """Write a (possibly edited) captured session as `<config_dir>/sessions/<pid>.json`.

    `pid` sets both the filename and the session's own `pid` field, which
    always agree in a real capture.
    """
    session = claude_session(pid=pid, **values)
    sessions_dir = Path(config_dir) / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{pid}.json"
    path.write_text(json.dumps(session), encoding="utf-8")
    return path


def codex_session_meta(**values) -> dict:
    """The captured first line of a Codex rollout file, `payload` values replaced.

    See tests/fixtures/codex/README.md for provenance. `values` are applied
    to `payload`, since that is the object every real edit in this suite
    needs to reach.
    """
    line = json.loads((CODEX_CAPTURES / "session-meta.json").read_text(encoding="utf-8"))
    payload = line["payload"]
    for key, value in values.items():
        if key not in payload:
            raise KeyError(f"{key!r} is not a key the capture has; do not invent one")
        payload[key] = value
    return line


def write_codex_rollout(
    config_dir: Path, year, month, day, filename, *, extra_lines=(), **values
) -> Path:
    """Write a (possibly edited) captured session_meta as one rollout file's first line.

    `extra_lines` are appended verbatim after it, unread by the adapter but
    useful for asserting that only the first line is ever opened.
    """
    day_dir = Path(config_dir) / "sessions" / f"{year:04d}" / f"{month:02d}" / f"{day:02d}"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / filename
    lines = [json.dumps(codex_session_meta(**values))]
    lines.extend(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
