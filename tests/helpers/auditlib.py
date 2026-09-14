"""Shared plumbing for the audit test files.

Not a test file itself: it lives in tests/helpers/ so tests/run.sh's
`test_*.py` glob never picks it up.

Three things every audit test needs, done once so each test stays about its
own case:

- **The fakes, and only the fakes.** `isolate_audit_environment()` puts
  tests/helpers first on PATH (feedlib's rule) and then REFUSES to continue
  unless `gh`, `fr` and `herdr` all resolve to the fakes there. A missing fake
  otherwise falls through PATH to the host's real command, and that has
  already happened once in this repository (journal p3-red-reached-real-fr):
  a RED run of the fake-fr tests ran the real `fr isolation up`.
- **A fake gh state.** `gh_state()`, `gh_repo()` and `gh_pr()` build the
  state file tests/helpers/fake-gh answers from, with every key it reads, and
  `FakeGh` writes it, points the environment at it, and reads its call log
  back as parsed argv.
- **Real git repositories.** `make_repo()` builds one in a temporary
  directory with a fixed identity and no ambient configuration: no global
  hooks, no signing, no `init.defaultBranch` surprise. `git()` checks every
  exit status, because a test whose setup silently failed proves nothing
  (phase 1's vacuous guard-on-the-guard).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from feedlib import HELPERS_DIR, REPO_ROOT, TESTS_DIR, isolate_environment

AUDIT_PATH = REPO_ROOT / "lib" / "audit.py"
GH_CAPTURES = TESTS_DIR / "fixtures" / "gh"
ZERO_OID = "0" * 40
GITHUB_REMOTE = "https://github.com/example-org/example-repo.git"
REPO_SLUG = "example-org/example-repo"

# A fixed identity and no ambient configuration, for the tests' own git calls
# AND for the module under test (which inherits os.environ). Under tests/run.sh
# HOME is already a throwaway; under a bare `pytest` it is the operator's, and
# their global hooks or signing config must not leak into a sandbox repo.
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Example User",
    "GIT_AUTHOR_EMAIL": "example-user@example.invalid",
    "GIT_COMMITTER_NAME": "Example User",
    "GIT_COMMITTER_EMAIL": "example-user@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
}

FAKES = (("gh", "fake-gh"), ("fr", "fake-fr"), ("herdr", "fake-herdr"))


def require_fakes() -> None:
    """Raise unless every command the audit spawns resolves to its fake."""
    for command, fake in FAKES:
        found = shutil.which(command)
        if found is None or Path(found).resolve() != HELPERS_DIR / fake:
            raise RuntimeError(
                f"{command} resolves to {found}, not tests/helpers/{fake}: "
                "refusing to run tests that would reach the real one"
            )


def isolate_audit_environment() -> None:
    isolate_environment()
    os.environ.update(GIT_ENV)
    require_fakes()


def load_audit():
    """Import lib/audit.py as a module object (see feedlib.load_feed)."""
    spec = importlib.util.spec_from_file_location("hs_audit", AUDIT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {AUDIT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Fake gh state
# --------------------------------------------------------------------------


def gh_pr(number: int, head: str, *, state: str = "OPEN", repo: str = REPO_SLUG, **values) -> dict:
    """One pull request in fake-gh's state. `values` overrides any key."""
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
    for key in values:
        if key not in record:
            raise KeyError(f"{key!r} is not a pull request key fake-gh reads")
    record.update(values)
    return record


def gh_repo(**values) -> dict:
    """One repository in fake-gh's state. `values` overrides any key."""
    record = {"default": "main", "archived": False, "refs": {}, "compare": {}, "prs": []}
    for key in values:
        if key not in record:
            raise KeyError(f"{key!r} is not a repository key fake-gh reads")
    record.update(values)
    return record


def gh_state(*, user: str = "example-user", orgs=("example-org",), repos=None) -> dict:
    return {"user": user, "orgs": list(orgs), "repos": dict(repos or {})}


def capture(name: str):
    """A captured gh response from tests/fixtures/gh/, parsed."""
    return json.loads((GH_CAPTURES / name).read_text(encoding="utf-8"))


def shape(obj):
    """The key structure of a JSON value, with leaf types, for comparison.

    A list becomes the sorted distinct shapes of its elements, so two nodes
    of the same shape count once and an empty list stays distinguishable
    from a list of nodes.
    """
    if isinstance(obj, dict):
        return {key: shape(value) for key, value in obj.items()}
    if isinstance(obj, list):
        distinct = {json.dumps(shape(item), sort_keys=True) for item in obj}
        return [json.loads(item) for item in sorted(distinct)]
    return type(obj).__name__


class FakeGh:
    """fake-gh's state file and call log for one test."""

    def __init__(self, directory: Path, state: dict | None = None):
        self.state_path = Path(directory) / "gh-state.json"
        self.log_path = Path(directory) / "gh.log"
        self.write(state if state is not None else gh_state())

    def write(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def env(self, **switches) -> dict:
        values = {"FAKE_GH_STATE": str(self.state_path), "FAKE_GH_LOG": str(self.log_path)}
        values.update({key: str(value) for key, value in switches.items()})
        return values

    @contextmanager
    def active(self, **switches):
        """Point os.environ at this state for the duration, with switches set."""
        with mock.patch.dict(os.environ, self.env(**switches)):
            yield self

    def calls(self) -> list[list[str]]:
        if not self.log_path.exists():
            return []
        text = self.log_path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]

    def graphql_calls(self, operation: str | None = None) -> list[dict]:
        """Each GraphQL call as {operation, query, raw: {name: value}, typed: {...}}."""
        found = []
        for argv in self.calls():
            if argv[:2] != ["api", "graphql"]:
                continue
            call = {"operation": None, "query": None, "raw": {}, "typed": {}}
            for flag, pair in zip(argv, argv[1:], strict=False):
                if flag not in ("-f", "-F") or "=" not in pair:
                    continue
                name, value = pair.split("=", 1)
                if flag == "-f" and name == "query":
                    call["query"] = value
                    words = value.split()
                    call["operation"] = words[1].split("(")[0] if len(words) > 1 else None
                else:
                    call["raw" if flag == "-f" else "typed"][name] = value
            if operation is None or call["operation"] == operation:
                found.append(call)
        return found


# --------------------------------------------------------------------------
# Real git repositories
# --------------------------------------------------------------------------


def git(repo, *args, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run git in `repo` with hooks and signing off; raise on failure unless check=False."""
    proc = subprocess.run(  # noqa: S603
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV, **(env or {})},
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"test setup: git {' '.join(args)} failed in {repo} (rc={proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    return proc


def make_repo(root, name: str = "repo", *, remote: str | None = GITHUB_REMOTE) -> Path:
    """A new repository at root/name with one commit on main, and origin set."""
    path = Path(root) / name
    path.mkdir(parents=True)
    proc = subprocess.run(  # noqa: S603
        ["git", "-c", "init.defaultBranch=main", "init", "-q", str(path)],
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
    )
    if proc.returncode != 0:
        raise AssertionError(f"test setup: git init failed (rc={proc.returncode}): {proc.stderr}")
    commit(path, "initial")
    if remote is not None:
        git(path, "remote", "add", "origin", remote)
    return path.resolve()


def commit(repo, message: str) -> str:
    """Commit a new file named after the message; return the commit id."""
    repo = Path(repo)
    slug = "".join(c if c.isalnum() else "-" for c in message)
    (repo / f"{slug}.txt").write_text(message + "\n", encoding="utf-8")
    git(repo, "add", f"{slug}.txt")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def fingerprint(repo) -> tuple[str, str]:
    """Every ref and the porcelain status: what a read-only caller must not change.

    GIT_OPTIONAL_LOCKS=0 so taking the fingerprint does not itself refresh the
    index and change what it is measuring.
    """
    refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname) %(symref)").stdout
    status = git(repo, "status", "--porcelain", env={"GIT_OPTIONAL_LOCKS": "0"}).stdout
    return refs, status


def recording_git(directory) -> tuple[str, Path]:
    """A PATH entry whose `git` logs its argv and then runs the real git.

    Returns (bin_dir, log_path). Prepend bin_dir to PATH to count exactly which
    git subcommands the module under test runs -- the only way to prove it
    never runs `fetch` or writes a ref, rather than hoping.
    """
    real = shutil.which("git")
    if real is None:
        raise RuntimeError("git is not on PATH")
    bin_dir = Path(directory) / "recording-git"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path(directory) / "git.log"
    shim = bin_dir / "git"
    shim.write_text(
        f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{log_path}"\nexec "{real}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return str(bin_dir), log_path


def restricted_path(directory, commands) -> str:
    """A PATH holding only symlinks to `commands` (as resolved right now)."""
    bin_dir = Path(directory) / "restricted-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for command in commands:
        found = shutil.which(command)
        if found is None:
            raise RuntimeError(f"{command} is not on PATH to link")
        (bin_dir / command).symlink_to(found)
    return str(bin_dir)
