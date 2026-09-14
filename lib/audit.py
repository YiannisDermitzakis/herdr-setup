#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""The audit runner: join Herdr panes, agent session history and GitHub
pull requests into one read-only report.

See docs/superpowers/specs/2026-09-13-session-audit-design.md, "The audit
runner" and "`audit` (read-only)".

What exists so far is the evidence machinery, layer by layer:

- **The GitHub layer.** `gh` calls, each read-only and each failing closed:
  a non-zero exit, and an `errors` array returned with exit 0, both raise.
- **The git layer** and **merge state** (phase 3, later tasks).

`main()` still does not join or report anything. Until the report arrives
(phase 4) it FAILS CLOSED -- AGENTS.md's own rule -- rather than printing an
empty, clean-looking report a partial run could not back up. An incomplete
report is exit 2 per the design doc's exit-code table ("An incomplete report
is printed and still exits 2"), and this is that incomplete report in its
most extreme form: no sections at all.

Standard library only, and the interpreter comes from uv (see AGENTS.md).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

# Mirrors the design doc's own table (Commands > audit): a positive integer
# up to 3650 (ten years), which is generous enough that no real session
# history bumps into it while still rejecting nonsense like a negative or
# zero window.
SINCE_MIN = 1
SINCE_MAX = 3650


class AuditError(RuntimeError):
    """The audit cannot produce a report it could stand behind. Exit 2."""


class ToolError(AuditError):
    """git or gh is missing, or gh is not authenticated for github.com."""


class GhError(AuditError):
    """A gh call failed, answered with errors, or answered something unreadable."""


class GitError(AuditError):
    """A git command failed, or warned, inside a repository that exists."""


def flatten(text) -> str:
    """Fold text onto one line (lib/feed.py's flatten, for the one-line messages)."""
    return " ".join(str(text).split())


# --------------------------------------------------------------------------
# The GitHub layer (spec: "GitHub queries", "Bots")
# --------------------------------------------------------------------------
#
# Every call is `gh api`, read-only, and names `--hostname github.com`: the
# host queried is the host `gh auth status --hostname github.com` checked,
# whatever GH_HOST says in the operator's environment.
#
# Branch names only ever travel as GraphQL variables sent with `-f` (a raw
# string). `-F` type-infers, so a branch named `123` or `true` would reach
# GitHub as a number or a boolean. No query here declares a non-string
# variable, so nothing here sends `-F` at all; a null cursor is sent by
# omitting the variable, which GraphQL reads as null.

GITHUB_HOST = "github.com"
GH_TIMEOUT = 120.0

# HsRepoBranches and HsCompare ask about this many branches per request.
BRANCH_CHUNK = 20

# A GitHub user or organisation login: alphanumerics and hyphens, not
# starting with a hyphen, at most 39 characters. Checked before a login given
# with --owner is ever sent, so nonsense is refused by name rather than
# reaching GitHub.
LOGIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")

COMPARE_STATUSES = ("AHEAD", "BEHIND", "DIVERGED", "IDENTICAL")

# The spec's field selections, one named operation each, so the fake gh can
# answer by name. tests/test_audit_github.py re-sends each query the module
# builds and compares the answer with the capture of the same operation in
# tests/fixtures/gh/.
#
# One addition to the spec's selection, a named construction: the nested
# `pullRequests` connection also selects `pageInfo { hasNextPage endCursor }`.
# Without it a repository with more than 100 open pull requests cannot be told
# from one with exactly 100, and the spec's own HsRepoOpenPullRequests
# follow-up could never be triggered.
QUERY_OWNER_PULL_REQUESTS = """
query HsOwnerPullRequests($login: String!, $after: String) {
  repositoryOwner(login: $login) {
    repositories(first: 50, isArchived: false, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        pullRequests(states: OPEN, first: 100) {
          pageInfo { hasNextPage endCursor }
          nodes {
            number title url headRefName isDraft updatedAt
            author { login __typename }
            headRepository { nameWithOwner }
          }
        }
      }
    }
  }
}
"""

QUERY_REPO_OPEN_PULL_REQUESTS = """
query HsRepoOpenPullRequests($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url headRefName isDraft updatedAt
        author { login __typename }
        headRepository { nameWithOwner }
      }
    }
  }
}
"""

# The two chunked operations are templates: `@VARIABLES@` takes the per-branch
# variable declarations and `@FIELDS@` one copy of the per-branch fields for
# each branch, with `@I@` replaced by its index (see chunk_query). The HsCompare
# text starts with a newline and indents its field six spaces so that `c0`
# sits at line 5, column 7 -- exactly where the captured NOT_FOUND error
# locates it, which is how the test proves this is the captured query.
QUERY_REPO_BRANCHES = """
query HsRepoBranches($owner: String!, $name: String!@VARIABLES@) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
@FIELDS@
  }
}
"""
FIELDS_REPO_BRANCHES = """\
    r@I@: ref(qualifiedName: $q@I@) { target { oid } }
    p@I@: pullRequests(headRefName: $h@I@, states: [OPEN, MERGED], first: 20) {
      nodes { number state isDraft url headRepository { nameWithOwner } }
    }
"""

QUERY_COMPARE = """
query HsCompare($owner: String!, $name: String!@VARIABLES@) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
@FIELDS@
    }
  }
}
"""
FIELDS_COMPARE = """\
      c@I@: compare(headRef: $h@I@) { status }
"""

# What each per-branch variable carries: `h` the branch name itself, `q` its
# fully qualified ref (a short name is ambiguous with a tag on GitHub).
BRANCH_VARIABLES = {
    "h": lambda branch: branch,
    "q": lambda branch: f"refs/heads/{branch}",
}


def chunk_query(template: str, fields: str, prefixes: tuple[str, ...], branches: list[str]):
    """Build one chunked query and its variables: the ONE place aliases are made.

    For branch number I, every prefix P declares `$PI: String!` and binds it
    to BRANCH_VARIABLES[P](branch), and `fields` is repeated with `@I@` -> I.
    Returns (query_text, {variable: value}); a caller maps an answer's alias
    back to branches[I].
    """
    declarations, values, parts = [], {}, []
    for index, branch in enumerate(branches):
        for prefix in prefixes:
            declarations.append(f", ${prefix}{index}: String!")
            values[f"{prefix}{index}"] = BRANCH_VARIABLES[prefix](branch)
        parts.append(fields.replace("@I@", str(index)))
    query = template.replace("@VARIABLES@", "".join(declarations))
    return query.replace("@FIELDS@", "".join(parts).rstrip("\n")), values


def _chunks(items: list, size: int = BRANCH_CHUNK) -> list[list]:
    return [items[start : start + size] for start in range(0, len(items), size)]


def _gh_label(args: list[str]) -> str:
    """How a gh call is named in a message: the operation, never the query text."""
    if args[:2] == ["api", "graphql"]:
        for word in args:
            if word.startswith("query="):
                named = re.match(r"\s*query\s+(\w+)", word[len("query=") :])
                return f"gh api graphql {named.group(1) if named else '(unnamed query)'}"
    return "gh " + " ".join(args)


def _error_messages(errors) -> str:
    if isinstance(errors, list):
        return "; ".join(
            flatten(entry.get("message", entry) if isinstance(entry, dict) else entry)
            for entry in errors
        )
    return flatten(errors)


def _run_gh(args: list[str]) -> subprocess.CompletedProcess:
    """Run `gh <args>`; raise GhError on a non-zero exit, quoting what gh said.

    stdout and stderr are captured separately. On a failed GraphQL call gh
    prints the JSON answer on stdout and the message on stderr, so stderr is
    the quote; only when it is empty is anything read from stdout.
    """
    label = _gh_label(args)
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"}
    try:
        proc = subprocess.run(  # noqa: S603
            ["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT, env=env
        )
    except subprocess.TimeoutExpired as exc:
        raise GhError(f"{label}: did not answer within {GH_TIMEOUT:g}s") from exc
    except OSError as exc:
        raise GhError(f"{label}: {exc}") from exc
    if proc.returncode != 0:
        detail = flatten(proc.stderr)
        if not detail:
            try:
                detail = _error_messages(json.loads(proc.stdout).get("errors"))
            except (ValueError, AttributeError):
                detail = flatten(proc.stdout)
        raise GhError(f"{label}: exited {proc.returncode}: {detail or 'no output'}")
    return proc


def gh_text(args: list[str]) -> str:
    """Run gh and return its stdout as text (for `--jq` answers)."""
    return _run_gh(args).stdout


def gh(args: list[str]):
    """Run gh and return the one JSON document it printed.

    In lib/feed.py's herdr_json order: a non-zero exit status is a failure
    whatever was printed; then anything unparseable is a failure; then an
    `errors` entry is a failure EVEN WITH EXIT 0 -- the shape a caller that
    trusts the exit status alone reads as an answer. No error is forgiven,
    NOT_FOUND included: the audit never asks about a ref it has not seen
    exist, so any error means something is actually wrong.
    """
    label = _gh_label(args)
    text = _run_gh(args).stdout
    if not text.strip():
        raise GhError(f"{label}: printed nothing to parse")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise GhError(f"{label}: answer is not JSON: {exc}") from exc
    if isinstance(data, dict) and data.get("errors") not in (None, []):
        raise GhError(f"{label}: {_error_messages(data['errors'])}")
    return data


def gh_pages(args: list[str]) -> list:
    """Run `gh api --paginate` on a REST list and return every item of every page.

    gh prints each page as its own JSON array, one after another, so the
    output is a sequence of documents rather than one; each must be a list.
    """
    label = _gh_label(args)
    text = _run_gh(args).stdout
    decoder = json.JSONDecoder()
    items: list = []
    pages = 0
    index = 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        try:
            page, index = decoder.raw_decode(text, index)
        except ValueError as exc:
            raise GhError(f"{label}: answer is not JSON: {exc}") from exc
        if not isinstance(page, list):
            raise GhError(f"{label}: a page is not a JSON list")
        items.extend(page)
        pages += 1
    if pages == 0:
        raise GhError(f"{label}: printed nothing to parse")
    return items


def graphql(operation: str, query: str, variables: dict[str, str | None]) -> dict:
    """Send one named, read-only GraphQL query; return its `data` object.

    Every variable is a string sent with `-f`; a None variable is omitted.
    """
    if not re.match(rf"\s*query {operation}\b", query):
        raise ValueError(f"the query text is not the {operation} operation")
    args = ["api", "graphql", "--hostname", GITHUB_HOST, "-f", f"query={query}"]
    for name, value in variables.items():
        if value is None:
            continue
        if not isinstance(value, str):
            raise TypeError(f"GraphQL variable ${name} must be a string, got {value!r}")
        args += ["-f", f"{name}={value}"]
    data = gh(args)
    payload = data.get("data") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        raise GhError(f"gh api graphql {operation}: answer carries no data object")
    return payload


def require_tools() -> None:
    """git and gh on PATH, and gh authenticated for github.com; else ToolError.

    `--hostname github.com` matters: the unscoped `gh auth status` checks
    every configured host, so a stale login somewhere else fails it.
    """
    missing = [tool for tool in ("git", "gh") if shutil.which(tool) is None]
    if missing:
        verb = "is" if len(missing) == 1 else "are"
        raise ToolError(f"{' and '.join(missing)} {verb} not on PATH")
    try:
        proc = subprocess.run(  # noqa: S603
            ["gh", "auth", "status", "--hostname", GITHUB_HOST],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT,
            env={**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"gh auth status could not be run: {exc}") from exc
    if proc.returncode != 0:
        detail = flatten(proc.stderr) or flatten(proc.stdout) or "no output"
        raise ToolError(f"gh is not authenticated for {GITHUB_HOST}: {detail}")


def _valid_login(login) -> bool:
    return isinstance(login, str) and LOGIN_RE.fullmatch(login) is not None


def owners(given: list[str]) -> list[str]:
    """The owner list: `--owner` logins as given, else the gh user plus its orgs.

    Given logins make no gh call at all. They are checked for shape and
    deduplicated ignoring case (GitHub logins are case-insensitive), keeping
    the first spelling. Whether each one resolves is open_prs()'s question.
    """
    result: list[str] = []
    seen: set[str] = set()

    def add(login: str) -> None:
        if login.lower() not in seen:
            seen.add(login.lower())
            result.append(login)

    if given:
        for login in given:
            if not _valid_login(login):
                raise GhError(f"--owner {login!r} is not a GitHub login")
            add(login)
        return result

    user = gh_text(["api", "user", "--hostname", GITHUB_HOST, "--jq", ".login"]).strip()
    if not _valid_login(user):
        raise GhError(f"gh api user: the answer is not a login: {user!r}")
    add(user)
    for org in gh_pages(["api", "--paginate", "user/orgs", "--hostname", GITHUB_HOST]):
        login = org.get("login") if isinstance(org, dict) else None
        if not _valid_login(login):
            raise GhError("gh api user/orgs: an organisation entry carries no readable login")
        add(login)
    return result


def _connection(obj, label: str) -> tuple[list, bool, str | None]:
    """(nodes, has_next_page, end_cursor) from a connection, or GhError."""
    if not isinstance(obj, dict) or not isinstance(obj.get("nodes"), list):
        raise GhError(f"{label}: answer carries no readable connection")
    info = obj.get("pageInfo")
    if not isinstance(info, dict) or not isinstance(info.get("hasNextPage"), bool):
        raise GhError(f"{label}: answer carries no readable pageInfo")
    cursor = info.get("endCursor")
    if info["hasNextPage"] and not isinstance(cursor, str):
        raise GhError(f"{label}: answer says there is another page but gives no cursor")
    return obj["nodes"], info["hasNextPage"], cursor


def _typed(node: dict, key: str, kind, label: str, *, nullable: bool = False):
    value = node.get(key) if isinstance(node, dict) else None
    if value is None and nullable and isinstance(node, dict) and key in node:
        return None
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise GhError(f"{label}: a pull request carries no readable '{key}'")
    return value


def _head_repo(node: dict, label: str) -> str | None:
    """`headRepository.nameWithOwner`, or None when the head repository is gone."""
    head = _typed(node, "headRepository", dict, label, nullable=True)
    return None if head is None else _typed(head, "nameWithOwner", str, label)


def _open_pr(repo: str, node: dict, label: str) -> dict:
    author = _typed(node, "author", dict, label, nullable=True)
    login = None if author is None else _typed(author, "login", str, label)
    typename = None if author is None else _typed(author, "__typename", str, label)
    return {
        "repo": repo,
        "number": _typed(node, "number", int, label),
        "title": _typed(node, "title", str, label),
        "url": _typed(node, "url", str, label),
        "head": _typed(node, "headRefName", str, label),
        "head_repo": _head_repo(node, label),
        "draft": _typed(node, "isDraft", bool, label),
        "updated_at": _typed(node, "updatedAt", str, label),
        "author": login,
        # A deleted author (null) is not a bot: nothing says it was one.
        "bot": author is not None and (typename == "Bot" or login.endswith("[bot]")),
    }


def open_prs(login: str) -> list[dict]:
    """Every open pull request in the non-archived repositories of `login`.

    Pages the owner's repositories 50 at a time, and any repository whose
    open pull requests do not fit in the first 100 through
    HsRepoOpenPullRequests. An owner GitHub does not know raises, naming it.
    """
    label = f"gh api graphql HsOwnerPullRequests {login}"
    records: list[dict] = []
    after: str | None = None
    while True:
        data = graphql(
            "HsOwnerPullRequests", QUERY_OWNER_PULL_REQUESTS, {"login": login, "after": after}
        )
        if "repositoryOwner" not in data:
            raise GhError(f"{label}: answer carries no repositoryOwner")
        owner = data["repositoryOwner"]
        if owner is None:
            raise GhError(f"owner {login!r} does not resolve to a GitHub user or organisation")
        repositories, more_repositories, repositories_cursor = _connection(
            owner.get("repositories") if isinstance(owner, dict) else None, label
        )
        for repository in repositories:
            name = repository.get("name") if isinstance(repository, dict) else None
            if not isinstance(name, str) or not name:
                raise GhError(f"{label}: a repository carries no readable name")
            nodes, more_prs, prs_cursor = _connection(repository.get("pullRequests"), label)
            nodes = list(nodes)
            while more_prs:
                page_label = f"gh api graphql HsRepoOpenPullRequests {login}/{name}"
                page = graphql(
                    "HsRepoOpenPullRequests",
                    QUERY_REPO_OPEN_PULL_REQUESTS,
                    {"owner": login, "name": name, "after": prs_cursor},
                )
                found = page.get("repository")
                if not isinstance(found, dict):
                    raise GhError(f"{page_label}: answer carries no repository")
                previous = prs_cursor
                more_nodes, more_prs, prs_cursor = _connection(
                    found.get("pullRequests"), page_label
                )
                if more_prs and prs_cursor == previous:
                    raise GhError(f"{page_label}: pagination did not advance")
                nodes.extend(more_nodes)
            records.extend(_open_pr(f"{login}/{name}", node, label) for node in nodes)
        if not more_repositories:
            return records
        if repositories_cursor == after:
            raise GhError(f"{label}: pagination did not advance")
        after = repositories_cursor


@dataclass(frozen=True)
class RepoBranches:
    """What GitHub says about some branches of one repository."""

    default: str | None  # defaultBranchRef.name; None when GitHub reports none
    refs: dict[str, bool]  # branch -> whether refs/heads/<branch> exists on GitHub
    prs: dict[str, list[dict]]  # branch -> its OPEN and MERGED pull requests by head name


def _branch_pr(node: dict, label: str) -> dict:
    state = _typed(node, "state", str, label)
    if state not in ("OPEN", "MERGED"):
        raise GhError(f"{label}: a pull request asked for as OPEN or MERGED is {state!r}")
    return {
        "number": _typed(node, "number", int, label),
        "state": state,
        "draft": _typed(node, "isDraft", bool, label),
        "url": _typed(node, "url", str, label),
        "head_repo": _head_repo(node, label),
    }


def _repository(data: dict, label: str) -> dict:
    repository = data.get("repository")
    if not isinstance(repository, dict):
        raise GhError(f"{label}: answer carries no repository")
    return repository


def repo_branches(owner: str, name: str, branches: list[str]) -> RepoBranches:
    """Default branch, per-branch ref existence and OPEN/MERGED pull requests.

    Asked in chunks of BRANCH_CHUNK branches per request. With no branches at
    all it still asks once, for the default branch.
    """
    label = f"gh api graphql HsRepoBranches {owner}/{name}"
    branches = list(dict.fromkeys(branches))
    default: str | None = None
    refs: dict[str, bool] = {}
    prs: dict[str, list[dict]] = {}
    for chunk in _chunks(branches) or [[]]:
        query, variables = chunk_query(QUERY_REPO_BRANCHES, FIELDS_REPO_BRANCHES, ("h", "q"), chunk)
        repository = _repository(
            graphql("HsRepoBranches", query, {"owner": owner, "name": name, **variables}), label
        )
        default_ref = repository.get("defaultBranchRef")
        if default_ref is None:
            default = None
        elif isinstance(default_ref, dict) and isinstance(default_ref.get("name"), str):
            default = default_ref["name"]
        else:
            raise GhError(f"{label}: answer carries no readable defaultBranchRef")
        for index, branch in enumerate(chunk):
            if f"r{index}" not in repository or f"p{index}" not in repository:
                raise GhError(f"{label}: answer is missing branch {branch!r}")
            ref = repository[f"r{index}"]
            if ref is not None and not isinstance(ref, dict):
                raise GhError(f"{label}: answer carries an unreadable ref for {branch!r}")
            refs[branch] = ref is not None
            nodes, _, _ = _connection(
                {"pageInfo": {"hasNextPage": False}, **(repository[f"p{index}"] or {})}, label
            )
            prs[branch] = [_branch_pr(node, label) for node in nodes]
    return RepoBranches(default=default, refs=refs, prs=prs)


def compare(owner: str, name: str, branches: list[str]) -> dict[str, str]:
    """GitHub's compare of the default branch against each branch: its status.

    Only ever called with branches whose GitHub ref is known to exist. A
    missing one is GitHub's partial-data-plus-NOT_FOUND answer, which gh()
    raises on like any other error.
    """
    label = f"gh api graphql HsCompare {owner}/{name}"
    result: dict[str, str] = {}
    for chunk in _chunks(list(dict.fromkeys(branches))):
        query, variables = chunk_query(QUERY_COMPARE, FIELDS_COMPARE, ("h",), chunk)
        repository = _repository(
            graphql("HsCompare", query, {"owner": owner, "name": name, **variables}), label
        )
        default_ref = repository.get("defaultBranchRef")
        if not isinstance(default_ref, dict):
            raise GhError(f"{label}: no default branch to compare against")
        for index, branch in enumerate(chunk):
            entry = default_ref.get(f"c{index}")
            status = entry.get("status") if isinstance(entry, dict) else None
            if status not in COMPARE_STATUSES:
                raise GhError(f"{label}: answer carries no readable status for {branch!r}")
            result[branch] = status
    return result


# --------------------------------------------------------------------------
# The git layer (spec: "Resolving a branch", "Merge state")
# --------------------------------------------------------------------------
#
# One function per git question, each a single `git -C <dir> <read-only
# subcommand>` whose exit status is read explicitly. Nothing here fetches:
# remote state comes from GitHub, and refs/remotes/origin/<b> is as stale as
# the last fetch. GIT_OPTIONAL_LOCKS=0 keeps even git's opportunistic index
# refresh from writing.

GIT_TIMEOUT = 60.0

# `owner/name` from a remote URL whose host is github.com: https (with or
# without credentials and `.git`), scp-like `git@github.com:`, and ssh://.
# Matched whole, so a lookalike host such as github.com.example.invalid is not
# GitHub.
_GITHUB_REMOTE_RES = tuple(
    re.compile(prefix + r"(?P<owner>[A-Za-z0-9-]+)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?/?", re.I)
    for prefix in (
        r"https://(?:[^@/]+@)?github\.com/",
        r"git@github\.com:",
        r"ssh://git@github\.com(?::\d+)?/",
    )
)


@dataclass(frozen=True)
class Repo:
    """A repository a branch lives in, as git names it."""

    toplevel: str  # the work tree the directory is in (a linked worktree's own root)
    main_checkout: str  # the main worktree: the same for every linked worktree of it
    slug: str | None  # `owner/name` when the chosen remote is on github.com


def _git(directory: str, args: tuple[str, ...]) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    try:
        return subprocess.run(  # noqa: S603
            ["git", "-C", directory, *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git in {directory}: {' '.join(args)} did not answer") from exc
    except OSError as exc:
        raise GitError(f"git in {directory}: {' '.join(args)}: {exc}") from exc


def _git_failure(directory: str, args: tuple[str, ...], proc) -> GitError:
    what = f"exited {proc.returncode}" if proc.returncode else "warned"
    detail = flatten(proc.stderr) or flatten(proc.stdout) or "no output"
    return GitError(f"git in {directory}: {' '.join(args)} {what}: {detail}")


def github_slug(url: str) -> str | None:
    """`owner/name` for a github.com remote URL, else None."""
    for pattern in _GITHUB_REMOTE_RES:
        match = pattern.fullmatch(url.strip())
        if match:
            return f"{match['owner']}/{match['name']}"
    return None


def _toplevel(directory: str | None) -> str | None:
    """The work tree `directory` is in, or None when it is not in one.

    A directory that no longer exists is simply not a work tree: that is the
    ordinary fate of a removed worktree, not an error.
    """
    if not directory or not os.path.isdir(directory):
        return None
    proc = _git(directory, ("rev-parse", "--show-toplevel"))
    toplevel = proc.stdout.strip()
    return toplevel if proc.returncode == 0 and toplevel else None


def _main_checkout(toplevel: str) -> str:
    args = ("worktree", "list", "--porcelain")
    proc = _git(toplevel, args)
    if proc.returncode != 0:
        raise _git_failure(toplevel, args, proc)
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            return line[len("worktree ") :]
    raise GitError(f"git in {toplevel}: {' '.join(args)} named no main worktree")


def _slug(toplevel: str) -> str | None:
    """The slug of `origin`, else of the only remote; None with neither."""
    args = ("remote",)
    proc = _git(toplevel, args)
    if proc.returncode != 0:
        raise _git_failure(toplevel, args, proc)
    remotes = proc.stdout.split()
    if "origin" in remotes:
        remote = "origin"
    elif len(remotes) == 1:
        remote = remotes[0]
    else:
        return None
    args = ("remote", "get-url", remote)
    proc = _git(toplevel, args)
    if proc.returncode != 0:
        raise _git_failure(toplevel, args, proc)
    return github_slug(proc.stdout)


def repo_for(directory: str | None, fallback: str | None) -> Repo | None:
    """The repository `directory` is in, else the one `fallback` is in, else None."""
    for candidate in (directory, fallback):
        toplevel = _toplevel(candidate)
        if toplevel is not None:
            return Repo(toplevel, _main_checkout(toplevel), _slug(toplevel))
    return None


def _ref_exists(repo: Repo, ref: str) -> bool:
    """Whether the fully qualified `ref` exists, telling broken from absent.

    `show-ref --verify --quiet` and `rev-parse --verify --quiet` both exit 1
    for a ref git cannot read, exactly as for one that is not there.
    `for-each-ref` also exits 0 for it, but says `ignoring broken ref` on
    stderr -- so anything on stderr is an error, never an absence.
    """
    args = ("for-each-ref", "--format=%(refname)", ref)
    proc = _git(repo.toplevel, args)
    if proc.returncode != 0 or proc.stderr.strip():
        raise _git_failure(repo.toplevel, args, proc)
    return ref in proc.stdout.splitlines()


def local_ref(repo: Repo, name: str) -> bool:
    """Whether the local branch refs/heads/<name> exists."""
    return _ref_exists(repo, f"refs/heads/{name}")


def is_ancestor(repo: Repo, name: str, default: str) -> bool:
    """Whether refs/heads/<name> is reachable from the default branch.

    The default branch is refs/remotes/origin/<default> when that exists,
    else refs/heads/<default>. Exit 0 is contained, exit 1 is not, and any
    other exit is a GitError naming the repository.
    """
    remote_default = f"refs/remotes/origin/{default}"
    target = remote_default if _ref_exists(repo, remote_default) else f"refs/heads/{default}"
    args = ("merge-base", "--is-ancestor", f"refs/heads/{name}", target)
    proc = _git(repo.toplevel, args)
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise _git_failure(repo.toplevel, args, proc)


def local_default(repo: Repo) -> str | None:
    """The branch refs/remotes/origin/HEAD points at, or None when it is not set."""
    args = ("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    proc = _git(repo.toplevel, args)
    if proc.returncode == 1:
        return None
    if proc.returncode != 0:
        raise _git_failure(repo.toplevel, args, proc)
    target = proc.stdout.strip()
    prefix = "refs/remotes/origin/"
    if not target.startswith(prefix) or target == prefix:
        raise GitError(f"git in {repo.toplevel}: refs/remotes/origin/HEAD points at {target!r}")
    return target[len(prefix) :]


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def _since_days(value: str) -> int:
    """argparse `type=` for --since: an integer in [SINCE_MIN, SINCE_MAX].

    Raising ArgumentTypeError (rather than returning something argparse has
    to double-check) is what makes argparse itself print the usage line
    naming `--since` and exit 2 -- the exact shape the design doc's "A
    positive integer up to 3650" needs, with no hand-rolled validation here.
    """
    try:
        days = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be an integer, got {value!r}") from None
    if not SINCE_MIN <= days <= SINCE_MAX:
        raise argparse.ArgumentTypeError(f"must be between {SINCE_MIN} and {SINCE_MAX}, got {days}")
    return days


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit.py",
        description="Report open sessions, stranded branches and unmatched pull requests.",
    )
    parser.add_argument(
        "--since",
        type=_since_days,
        default=30,
        help="session history window in days, 1-3650 (default: 30)",
    )
    parser.add_argument(
        "--owner",
        dest="owners",
        action="append",
        default=None,
        help="a GitHub owner login; repeatable, replaces the default owner list",
    )
    parser.add_argument(
        "--include-sdk",
        action="store_true",
        help="include sessions an automated caller drove",
    )
    parser.add_argument(
        "--include-bots",
        action="store_true",
        help="list bot-authored pull requests in section 3",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one JSON object instead of the text tables",
    )
    parser.add_argument(
        "--adapters",
        dest="adapter_dir",
        default=None,
        help="directory of adapter executables (default: adapters/ beside this script)",
    )
    parser.add_argument(
        "--socket",
        dest="socket_path",
        default=None,
        help="Herdr socket path (default: $HERDR_SOCKET_PATH, else ~/.config/herdr/herdr.sock)",
    )
    # audit makes no writes and asks no questions -- both are accepted and
    # change nothing (design doc: "The global --dry-run and --yes are
    # accepted and change nothing, since audit makes no writes and asks no
    # questions"), so a caller that always passes them (as herdr-setup's own
    # entrypoint does for feed) can do the same here without a special case.
    parser.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)

    # Still the walking skeleton's body: say so, and fail closed. Sections 1
    # through 3 (panes, stranded branches, unmatched pull requests) are not
    # joined or printed yet, and printing an empty report here would read as
    # "nothing to act on" -- the one thing this run does not know.
    print("incomplete: the audit runner is not implemented yet")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
