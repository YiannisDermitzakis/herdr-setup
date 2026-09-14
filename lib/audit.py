#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""The audit runner: join Herdr panes, agent session history and GitHub
pull requests into one read-only report.

See docs/superpowers/implemented/specs/2026-09-13-session-audit-design.md, "The audit
runner" and "`audit` (read-only)".

Layer by layer, each failing closed:

- **The GitHub layer.** `gh api` calls, each read-only: a non-zero exit, and an
  `errors` array returned with exit 0, both raise GhError. `require_tools`,
  `owners`, `open_prs`, `repo_branches`, `compare`.
- **The git layer.** One read-only git question per function, its exit status
  read explicitly: `repo_for`, `local_ref`, `is_ancestor`, `local_default`.
- **Merge state.** `classify`, the spec's table as a pure function of gathered
  facts, and `resolve_branches`, which gathers them once per (repository,
  branch).
- **The sources and the join.** `panes` (Herdr; HerdrError), `load_adapters`
  and `gather_sessions` (an adapter that cannot answer makes the report
  incomplete), `pane_worktree` and `join`. The audit never runs `fr`.
- **The report.** `build_report` (the spec's JSON object, pure),
  `render_text`, `render_json` and `exit_code`; `run` wires every stage.

Exit statuses: 0 nothing to act on, 1 section 2 or 3 has a row, 2 an error
(nothing printed on stdout) or an incomplete report (printed, still 2). The
entrypoint's preflight owns 3.

Standard library only, and the interpreter comes from uv (see AGENTS.md).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

# lib/feed.py from this script's own directory: discovery, probing, probe
# validation, herdr_json and the agent-list reader have one implementation.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import feed  # noqa: E402

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
      pageInfo { hasNextPage endCursor }
      nodes { number state isDraft url headRepository { nameWithOwner } }
    }
"""

# The follow-up for one branch whose pull requests do not fit in HsRepoBranches'
# page of 20 -- a name like `patch-1` shared by many forks' pull requests can
# otherwise push this repository's own off the list. Paged to the end, so the
# merge state is never decided from a truncated list.
QUERY_BRANCH_PULL_REQUESTS = """
query HsBranchPullRequests($owner: String!, $name: String!, $head: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(headRefName: $head, states: [OPEN, MERGED], first: 20, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { number state isDraft url headRepository { nameWithOwner } }
    }
  }
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


def _gh_env() -> dict[str, str]:
    return {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"}


def run_gh_process(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run `gh <argv>` as found on PATH, stdout and stderr captured apart.

    Raises OSError or subprocess.TimeoutExpired exactly as subprocess.run does;
    the callers turn those into their own errors.
    """
    return subprocess.run(  # noqa: S603
        ["gh", *argv], capture_output=True, text=True, timeout=GH_TIMEOUT, env=env
    )


# The one door to gh: every gh call in this module goes through whatever this
# names. Only tests replace it -- with tests/helpers/fake-gh run in-process,
# after they have already refused to run unless `gh` on PATH is that fake.
gh_runner = run_gh_process


def _run_gh(args: list[str]) -> subprocess.CompletedProcess:
    """Run `gh <args>`; raise GhError on a non-zero exit, quoting what gh said.

    stdout and stderr are captured separately. On a failed GraphQL call gh
    prints the JSON answer on stdout and the message on stderr, so stderr is
    the quote; only when it is empty is anything read from stdout.
    """
    label = _gh_label(args)
    try:
        proc = gh_runner(list(args), _gh_env())
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
    `--active` matters for the same reason within github.com: it checks only
    the account gh would use, so a stale, inactive second account does not.
    """
    missing = [tool for tool in ("git", "gh") if shutil.which(tool) is None]
    if missing:
        verb = "is" if len(missing) == 1 else "are"
        raise ToolError(f"{' and '.join(missing)} {verb} not on PATH")
    try:
        proc = gh_runner(["auth", "status", "--hostname", GITHUB_HOST, "--active"], _gh_env())
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

    Each record's `repo` is `<login>/<name>` with the login spelled as given,
    so a caller matching it against a remote's slug compares ignoring case,
    as GitHub does.
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
    all it still asks once, for the default branch. A branch whose pull
    requests run past one page is paged to the end with HsBranchPullRequests.
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
            nodes, more, cursor = _connection(repository[f"p{index}"], label)
            nodes = list(nodes)
            while more:
                page_label = f"gh api graphql HsBranchPullRequests {owner}/{name}"
                page = _repository(
                    graphql(
                        "HsBranchPullRequests",
                        QUERY_BRANCH_PULL_REQUESTS,
                        {"owner": owner, "name": name, "head": branch, "after": cursor},
                    ),
                    page_label,
                )
                previous = cursor
                more_nodes, more, cursor = _connection(page.get("pullRequests"), page_label)
                if more and cursor == previous:
                    raise GhError(f"{page_label}: pagination did not advance")
                nodes.extend(more_nodes)
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
# remote state comes from GitHub, and refs/remotes/<remote>/<b> is as stale as
# the last fetch. GIT_OPTIONAL_LOCKS=0 keeps even git's opportunistic index
# refresh from writing.

GIT_TIMEOUT = 60.0

# `owner/name` from a remote URL whose host is github.com, in any letter case:
# https (with or without credentials), ssh:// (with or without a user and a
# port), and scp-like `[user@]github.com:`, each with or without `.git` and a
# trailing slash. Matched whole, so a lookalike host such as
# github.com.example.invalid is not GitHub. An SSH host alias
# (`git@github-work:o/r`, which ~/.ssh/config maps to github.com) is NOT
# recognised: knowing would mean reading ssh configuration, so such a remote
# resolves by ancestry only -- a known limit.
_GITHUB_REMOTE_RES = tuple(
    re.compile(prefix + r"(?P<owner>[A-Za-z0-9-]+)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?/?", re.I)
    for prefix in (
        r"https://(?:[^@/]+@)?github\.com/",
        r"ssh://(?:[^@/]+@)?github\.com(?::\d+)?/",
        r"(?:[^@/:]+@)?github\.com:",
    )
)

# What git says about a directory that simply is not in a work tree: in no
# repository at all, or in a bare one. Any other failure in a directory that
# exists -- a safe.directory refusal, a repository this git cannot read -- is
# an error, never "not a work tree". _git runs with LC_ALL=C so these stay
# the words git prints.
_NOT_A_WORK_TREE = ("not a git repository", "must be run in a work tree")


@dataclass(frozen=True)
class Repo:
    """A repository a branch lives in, as git names it."""

    toplevel: str  # the work tree the directory is in (a linked worktree's own root)
    main_checkout: str  # the main worktree: the same for every linked worktree of it
    slug: str | None  # `owner/name` when the chosen remote is on github.com
    # The chosen remote: `origin`, else the only remote, else None. Every
    # remote-tracking ref this module reads is this remote's, so the slug and
    # the ancestry answer always describe the same remote.
    remote: str | None = None


def _git(directory: str, args: tuple[str, ...]) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
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
    ordinary fate of a removed worktree, not an error. Nor is one git reports
    as outside any repository, or inside a bare one. Any other git failure in
    a directory that exists raises GitError naming it.
    """
    if not directory or not os.path.isdir(directory):
        return None
    args = ("rev-parse", "--show-toplevel")
    proc = _git(directory, args)
    toplevel = proc.stdout.strip()
    if proc.returncode == 0 and toplevel:
        return toplevel
    if proc.returncode == 128 and any(text in proc.stderr for text in _NOT_A_WORK_TREE):
        return None
    raise _git_failure(directory, args, proc)


def _main_checkout(toplevel: str) -> str:
    args = ("worktree", "list", "--porcelain")
    proc = _git(toplevel, args)
    if proc.returncode != 0:
        raise _git_failure(toplevel, args, proc)
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            return line[len("worktree ") :]
    raise GitError(f"git in {toplevel}: {' '.join(args)} named no main worktree")


def _remote_and_slug(toplevel: str) -> tuple[str | None, str | None]:
    """(the chosen remote, its github.com slug): `origin`, else the only remote."""
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
        return None, None
    args = ("remote", "get-url", remote)
    proc = _git(toplevel, args)
    if proc.returncode != 0:
        raise _git_failure(toplevel, args, proc)
    return remote, github_slug(proc.stdout)


def repo_for(directory: str | None, fallback: str | None) -> Repo | None:
    """The repository `directory` is in, else the one `fallback` is in, else None."""
    for candidate in (directory, fallback):
        toplevel = _toplevel(candidate)
        if toplevel is not None:
            remote, slug = _remote_and_slug(toplevel)
            return Repo(toplevel, _main_checkout(toplevel), slug, remote)
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


def default_ref(repo: Repo, default: str) -> str | None:
    """The local ref standing for the default branch, or None when there is none.

    refs/remotes/<remote>/<default> for the chosen remote when that exists,
    else refs/heads/<default>. Neither is an ordinary state -- a clone from
    before a default-branch rename, a --single-branch clone, a fork clone --
    not an error.
    """
    candidates = [f"refs/heads/{default}"]
    if repo.remote is not None:
        candidates.insert(0, f"refs/remotes/{repo.remote}/{default}")
    for ref in candidates:
        if _ref_exists(repo, ref):
            return ref
    return None


def is_ancestor(repo: Repo, name: str, default: str) -> bool | None:
    """Whether refs/heads/<name> is reachable from the default branch.

    None when the default branch is not present locally at all (default_ref),
    which makes the branch `unresolved` rather than stopping the run. Otherwise
    exit 0 is contained, exit 1 is not, and any other exit on refs that do
    exist is a GitError naming the repository.
    """
    target = default_ref(repo, default)
    if target is None:
        return None
    args = ("merge-base", "--is-ancestor", f"refs/heads/{name}", target)
    proc = _git(repo.toplevel, args)
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise _git_failure(repo.toplevel, args, proc)


def local_default(repo: Repo) -> str | None:
    """The branch the chosen remote's HEAD points at, or None when it is not set."""
    if repo.remote is None:
        return None
    head = f"refs/remotes/{repo.remote}/HEAD"
    args = ("symbolic-ref", "--quiet", head)
    proc = _git(repo.toplevel, args)
    if proc.returncode == 1:
        return None
    if proc.returncode != 0:
        raise _git_failure(repo.toplevel, args, proc)
    target = proc.stdout.strip()
    prefix = f"refs/remotes/{repo.remote}/"
    if not target.startswith(prefix) or target == prefix:
        raise GitError(f"git in {repo.toplevel}: {head} points at {target!r}")
    return target[len(prefix) :]


# --------------------------------------------------------------------------
# Merge state (spec: "Resolving a branch", "Merge state")
# --------------------------------------------------------------------------
#
# First match wins:
#
#   merged     a MERGED pull request whose head is the branch and whose head
#              repository is this repository
#   open-pr    an OPEN pull request by the same rule
#   contained  at least one ref exists, and every ref that exists is
#              reachable from the default branch
#   unmerged   a local or GitHub ref exists that is not contained
#   gone       no local ref, no GitHub ref, no open or merged pull request
#
# The pull request check comes first because a squash merge is never an
# ancestor of anything. The head repository check stops a fork's same-named
# branch counting as this repository's, and a pull request whose head
# repository is null (its fork was deleted) cannot show that, so it is not
# used. `unresolved` is the branch this cannot decide at all: no work tree,
# or no default branch to measure against, which is never guessed.

MERGED = "merged"
OPEN_PR = "open-pr"
CONTAINED = "contained"
UNMERGED = "unmerged"
GONE = "gone"
UNRESOLVED = "unresolved"

# GitHub's compare of the default branch against a branch: the branch has
# nothing the default branch lacks.
CONTAINED_COMPARE = frozenset({"IDENTICAL", "BEHIND"})


@dataclass(frozen=True)
class Facts:
    """Everything one branch's merge state is decided from, already gathered.

    None means "not asked". resolve_branches asks only what the table still
    needs -- nothing about refs once a pull request has decided, nothing at
    all without a default branch -- and classify() refuses to decide from a
    fact that was needed and never gathered.
    """

    repo_slug: str | None  # `owner/name` on github.com, or None
    default: str | None  # GitHub's default branch, else <remote>/HEAD's, else None
    prs: tuple[dict, ...] = ()  # OPEN/MERGED pull requests by head name (RepoBranches.prs)
    local_ref: bool | None = None  # refs/heads/<b> exists locally
    local_contained: bool | None = None  # is_ancestor(), asked only when local_ref
    # is_ancestor() found no local ref for the default branch at all
    local_default_missing: bool = False
    remote_ref: bool | None = None  # refs/heads/<b> exists on GitHub
    remote_status: str | None = None  # compare() status, asked only when remote_ref


@dataclass(frozen=True)
class Resolution:
    """One branch's merge state."""

    state: str
    pr: dict | None = None  # {number, draft, url} for merged and open-pr
    repo_slug: str | None = None
    reason: str | None = None  # why, for unresolved
    # The distinct (dir, cwd) pairs that named this branch: how a caller maps
    # its own entries back to the one resolution they share.
    sources: tuple[tuple[str | None, str | None], ...] = ()


def pr_decision(repo_slug: str | None, prs) -> tuple[str, dict] | None:
    """(merged|open-pr, pr) when a pull request of THIS repository decides, else None."""
    if repo_slug is None:
        return None
    own = [
        pr
        for pr in prs
        if isinstance(pr.get("head_repo"), str) and pr["head_repo"].lower() == repo_slug.lower()
    ]
    for state, wanted in ((MERGED, "MERGED"), (OPEN_PR, "OPEN")):
        matching = [pr for pr in own if pr.get("state") == wanted]
        if matching:
            chosen = max(matching, key=lambda pr: pr["number"])
            return state, {
                "number": chosen["number"],
                "draft": chosen["draft"],
                "url": chosen["url"],
            }
    return None


def classify(facts: Facts) -> Resolution:
    """The merge state table, as a pure function of already-gathered facts."""
    decided = pr_decision(facts.repo_slug, facts.prs)
    if decided is not None:
        return Resolution(decided[0], decided[1], facts.repo_slug)
    if facts.default is None:
        if facts.repo_slug is not None:
            reason = f"GitHub reports no default branch for {facts.repo_slug}"
        else:
            reason = "no GitHub remote, and no <remote>/HEAD to name a default branch"
        return Resolution(UNRESOLVED, None, facts.repo_slug, reason)

    contained: list[bool] = []
    if facts.local_ref is None:
        raise ValueError("classify: whether the local ref exists was never asked")
    if facts.local_ref and facts.local_default_missing:
        reason = f"default branch {facts.default} not present locally"
        return Resolution(UNRESOLVED, None, facts.repo_slug, reason)
    if facts.local_ref:
        if facts.local_contained is None:
            raise ValueError("classify: local ancestry was never asked")
        contained.append(facts.local_contained)
    if facts.repo_slug is not None:
        if facts.remote_ref is None:
            raise ValueError("classify: whether the GitHub ref exists was never asked")
        if facts.remote_ref:
            if facts.remote_status is None:
                raise ValueError("classify: GitHub's compare was never asked")
            contained.append(facts.remote_status in CONTAINED_COMPARE)
    if not contained:
        return Resolution(GONE, None, facts.repo_slug)
    return Resolution(CONTAINED if all(contained) else UNMERGED, None, facts.repo_slug)


def _resolve_repository(repo: Repo, names: list[str]) -> dict[str, Resolution]:
    """Gather the facts for every branch of one repository, batched, and classify.

    GitHub first (one HsRepoBranches per chunk): the pull requests may decide
    a branch outright. Then, for the undecided only, local ancestry, and
    GitHub's compare for those whose GitHub ref exists -- compare is never
    asked about a ref not known to exist.
    """
    github: RepoBranches | None = None
    if repo.slug is not None:
        owner, name = repo.slug.split("/", 1)
        github = repo_branches(owner, name, names)
        default = github.default
    else:
        default = local_default(repo)

    prs = {branch: tuple(github.prs[branch]) if github else () for branch in names}
    undecided = []
    if default is not None:
        undecided = [branch for branch in names if pr_decision(repo.slug, prs[branch]) is None]
    local = {branch: local_ref(repo, branch) for branch in undecided}
    contained = {
        branch: is_ancestor(repo, branch, default) for branch in undecided if local[branch]
    }
    statuses: dict[str, str] = {}
    if github is not None:
        asked = [branch for branch in undecided if github.refs[branch]]
        if asked:
            statuses = compare(owner, name, asked)

    return {
        branch: classify(
            Facts(
                repo_slug=repo.slug,
                default=default,
                prs=prs[branch],
                local_ref=local.get(branch),
                local_contained=contained.get(branch),
                local_default_missing=bool(local.get(branch)) and contained[branch] is None,
                remote_ref=github.refs[branch] if github else None,
                remote_status=statuses.get(branch),
            )
        )
        for branch in names
    }


def _path_text(value) -> str | None:
    """A dir or cwd as path text, so a `Path` and a `str` naming it compare equal."""
    if value is None or value == "":
        return None
    return str(Path(value))


class Resolutions(dict):
    """{(repo_key, name): Resolution}, plus the way back from every entry.

    `index` maps each entry's (name, dir, cwd) -- dir and cwd as path text,
    so `Path("/work/a")`, `"/work/a"` and `"/work/a/"` are one entry -- to its
    key, and `key_for()` normalises its arguments the same way. A caller
    looks a session's branch up directly instead of searching `sources`.
    """

    def __init__(self, results, index: dict):
        super().__init__(results)
        self.index = index

    def key_for(self, name: str, directory, cwd) -> tuple[str, str]:
        return self.index[(name, _path_text(directory), _path_text(cwd))]


def resolve_branches(entries) -> Resolutions:
    """Merge state for each distinct (repository, branch) the entries name.

    Each entry is a mapping with `name`, `dir` and `cwd` (the session's
    working directory, the fallback when `dir` is not a work tree); `dir` and
    `cwd` may be `str` or `Path`. The key is (repo_key, name), where repo_key
    is the repository's main checkout, so a branch named from several
    sessions, subdirectories and linked worktrees of one repository resolves
    once. An entry whose `dir` and `cwd` are both outside any work tree has no
    repository to key it by: it is `unresolved` under its own `dir`. Every
    Resolution lists the (dir, cwd) pairs that named it in `sources`, and the
    returned mapping's `index` maps every entry back to its key.

    GhError and GitError propagate: a failed call stops the run, it never
    becomes a state.
    """
    located: dict[tuple, Repo | None] = {}
    repositories: dict[str, tuple[Repo, list[str]]] = {}
    sources: dict[tuple[str, str], list] = {}
    results: dict[tuple[str, str], Resolution] = {}
    index: dict[tuple[str, str | None, str | None], tuple[str, str]] = {}

    for item in entries:
        name = item["name"]
        directory, cwd = _path_text(item.get("dir")), _path_text(item.get("cwd"))
        spot = (directory, cwd)
        if spot not in located:
            located[spot] = repo_for(directory, cwd)
        repo = located[spot]
        if repo is None:
            key = (str(directory if directory else cwd), name)
            reason = f"neither {directory} nor the session cwd {cwd} is in a git work tree"
            results.setdefault(key, Resolution(UNRESOLVED, reason=reason))
        else:
            key = (repo.main_checkout, name)
            _, names = repositories.setdefault(repo.main_checkout, (repo, []))
            if name not in names:
                names.append(name)
        named_by = sources.setdefault(key, [])
        if spot not in named_by:
            named_by.append(spot)
        index[(name, directory, cwd)] = key

    for repo_key, (repo, names) in repositories.items():
        for name, resolution in _resolve_repository(repo, names).items():
            results[(repo_key, name)] = resolution

    resolved = {key: replace(value, sources=tuple(sources[key])) for key, value in results.items()}
    return Resolutions(resolved, index)


# --------------------------------------------------------------------------
# The sources and the join (spec: "The audit runner", "Sources")
# --------------------------------------------------------------------------
#
# Three sources, each failing closed in its own way. Herdr (panes and tab
# labels) raises feed.HerdrError: without it there is no report. An adapter's
# session history that cannot be read is warned about by name and makes the
# report incomplete, because one broken adapter must not hide the others.
# A pane's own directory is text: an fr worktree path names its branch, which
# is how an agent with no session history still shows what it works on. The
# audit never runs `fr`.

# Why a pane has no branch rows, in the order they are decided.
NOTE_NO_ADAPTER = "no adapter"
NOTE_UNSUPPORTED = "no session history (unsupported)"
NOTE_ADAPTER_FAILED = "session history unavailable (adapter failed)"
NOTE_NOT_REPORTED = "session not reported to Herdr"
NOTE_NOT_FOUND = "session not found in history (outside --since, or SDK-driven)"
NOTE_NO_EVIDENCE = "no branch evidence"

# An fr worktree: `.../.cache/fr/worktrees/<repo>/<slug>`, where the slug is
# the branch with `/` written as `__` -- adapters/claude's worktree-path rule,
# read here from a pane's absolute cwd, so no `~` or `$HOME` forms arise.
_PANE_WORKTREE_RE = re.compile(r"(?P<dir>/.*?/\.cache/fr/worktrees/[^/]+/(?P<slug>[^/]+))(?:/.*)?")


def warn(message: str) -> None:
    sys.stderr.write(f"herdr-setup: audit: {message}\n")


def panes() -> list[dict]:
    """Every agent pane Herdr lists: pane_id, agent, session, cwd and tab label.

    `herdr agent list` then `herdr tab list`, both through feed.herdr_json, so
    a failed or unreadable call raises feed.HerdrError. `session` is
    `agent_session.value`, None when Herdr has none; `tab` is the label of the
    pane's tab, None when the tab list does not name it.
    """
    agents = feed._agent_entries(feed.herdr_json(["agent", "list"]))
    label = "herdr tab list"
    tabs = feed._result(feed.herdr_json(["tab", "list"]), label).get("tabs")
    if not isinstance(tabs, list):
        raise feed.HerdrError(f"{label}: answer carries no 'tabs' list")
    labels: dict[str, str | None] = {}
    for tab in tabs:
        if not isinstance(tab, dict) or not isinstance(tab.get("tab_id"), str):
            raise feed.HerdrError(f"{label}: a tab carries no readable tab_id")
        text = tab.get("label")
        labels[tab["tab_id"]] = text if isinstance(text, str) and text else None

    found: list[dict] = []
    for entry in agents:
        pane_id = entry.get("pane_id") if isinstance(entry, dict) else None
        agent = entry.get("agent") if isinstance(entry, dict) else None
        if not (isinstance(pane_id, str) and pane_id and isinstance(agent, str) and agent):
            raise feed.HerdrError(
                "herdr agent list: an entry carries no readable pane_id and agent"
            )
        session = entry.get("agent_session")
        value = session.get("value") if isinstance(session, dict) else None
        # The agent process's own directory first, then the pane's: lib/feed.py's order.
        cwd = entry.get("foreground_cwd") or entry.get("cwd")
        found.append(
            {
                "pane_id": pane_id,
                "agent": agent,
                "session": value if isinstance(value, str) and value else None,
                "cwd": cwd if isinstance(cwd, str) and cwd else None,
                "tab": labels.get(entry.get("tab_id")),
            }
        )
    return found


def load_adapters(adapter_dir, warn=warn) -> tuple[list, list[str], set[str]]:
    """The usable adapters, an incomplete reason per failed probe, and those adapters' names.

    feed.usable_adapters is the one discovery and probe policy. A failed probe
    is also a source this run could not read -- its agent's sessions are
    missing -- so unlike `feed`, the audit marks the report incomplete for it,
    and join() tells its panes so rather than calling them "no adapter".
    """
    skipped: list[tuple[str, str]] = []
    adapters = feed.usable_adapters(adapter_dir, warn=warn, skipped=skipped)
    reasons = [f"adapter {name}: probe failed: {reason}" for name, reason in skipped]
    return adapters, reasons, {name for name, _ in skipped}


@dataclass(frozen=True)
class Gathered:
    """Session history from every adapter that declared `sessions`."""

    sessions: list[dict]  # feed.parse_sessions' records, each with its `agent`
    failed: dict[str, str]  # agent -> why its adapter could not answer
    incomplete: list[str]  # one reason per failed adapter


def gather_sessions(
    adapters,
    since: int,
    *,
    include_sdk: bool = False,
    warn=warn,
    timeout: float = feed.SESSIONS_TIMEOUT,
) -> Gathered:
    """Ask every adapter declaring `sessions` once; a failure is incomplete, not fatal."""
    sessions: list[dict] = []
    failed: dict[str, str] = {}
    incomplete: list[str] = []
    for adapter in adapters:
        if not adapter.sessions:
            continue
        try:
            found, _, _ = feed.sessions(
                adapter, since, include_sdk=include_sdk, timeout=timeout, warn=warn
            )
        except feed.AdapterError as exc:
            reason = f"adapter {adapter.name}: {exc}"
            warn(f"{reason}; its {adapter.agent} sessions are missing from this report")
            failed[adapter.agent] = reason
            incomplete.append(reason)
            continue
        sessions.extend({**session, "agent": adapter.agent} for session in found)
    return Gathered(sessions=sessions, failed=failed, incomplete=incomplete)


def pane_worktree(cwd) -> dict | None:
    """{name, dir} when `cwd` is in an fr worktree whose slug is a plausible branch."""
    if not isinstance(cwd, str) or not cwd:
        return None
    match = _PANE_WORKTREE_RE.fullmatch(cwd)
    if match is None:
        return None
    name = match["slug"].replace("__", "/")
    if not feed.branch_name_ok(name):
        return None
    return {"name": name, "dir": match["dir"]}


def join(
    pane_list: list[dict], adapters, gathered: Gathered, *, failed_probes=frozenset()
) -> list[dict]:
    """One record per pane: its branch entries, or the note saying why it has none.

    A pane's session is looked up under its own agent. Its entries are that
    session's branches (with the session's cwd, the resolver's fallback) plus
    a worktree-path branch when the pane's own cwd is in an fr worktree.
    `failed_probes` names the adapters whose probe failed; a failed probe says
    nothing about its agent, so a pane is matched to it by the adapter's own
    name, which is the agent it covers (adapters/claude, adapters/codex, ...).
    """
    by_agent: dict[str, object] = {}
    for adapter in adapters:
        by_agent.setdefault(adapter.agent, adapter)
    history = {(session["agent"], session["id"]): session for session in gathered.sessions}

    joined: list[dict] = []
    for pane in pane_list:
        adapter = by_agent.get(pane["agent"])
        found = history.get((pane["agent"], pane["session"])) if pane["session"] else None
        entries = []
        if found is not None:
            entries.extend(
                {
                    "name": branch["name"],
                    "dir": branch["dir"],
                    "cwd": found["cwd"],
                    "evidence": branch["evidence"],
                }
                for branch in found["branches"]
            )
        worktree = pane_worktree(pane["cwd"])
        if worktree is not None:
            entries.append({**worktree, "cwd": pane["cwd"], "evidence": "worktree-path"})

        note = None
        if not entries:
            if adapter is None:
                note = NOTE_ADAPTER_FAILED if pane["agent"] in failed_probes else NOTE_NO_ADAPTER
            elif not adapter.sessions:
                note = NOTE_UNSUPPORTED
            elif pane["agent"] in gathered.failed:
                note = NOTE_ADAPTER_FAILED
            elif pane["session"] is None:
                note = NOTE_NOT_REPORTED
            elif found is None:
                note = NOTE_NOT_FOUND
            else:
                note = NOTE_NO_EVIDENCE
        joined.append(
            {
                "pane_id": pane["pane_id"],
                "tab": pane["tab"],
                "agent": pane["agent"],
                "session_id": pane["session"],
                "note": note,
                "entries": entries,
            }
        )
    return joined


def resolution_entries(joined: list[dict], gathered: Gathered) -> list[dict]:
    """Every (name, dir, cwd) to resolve: each session's branches, each pane's, once."""
    seen: set[tuple] = set()
    entries: list[dict] = []
    candidates = [
        (branch["name"], branch["dir"], session["cwd"])
        for session in gathered.sessions
        for branch in session["branches"]
    ] + [
        (entry["name"], entry["dir"], entry["cwd"]) for pane in joined for entry in pane["entries"]
    ]
    for name, directory, cwd in candidates:
        if (name, directory, cwd) not in seen:
            seen.add((name, directory, cwd))
            entries.append({"name": name, "dir": directory, "cwd": cwd})
    return entries


# --------------------------------------------------------------------------
# The report (spec: "`audit` (read-only)": Text output, JSON output, Exit codes)
# --------------------------------------------------------------------------
#
# build_report() is pure: it turns joined panes, gathered sessions, resolutions
# and open pull requests into the JSON object the spec describes, and both
# renderers read only that object, so the text and the JSON cannot disagree.

# States section 2 lists: work that exists and is not in the default branch.
STRANDED = frozenset({UNMERGED, OPEN_PR})

HEADING_OPEN = "open in Herdr"
HEADING_CLOSED = "closed, with unmerged branches"
HEADING_PRS = "open PRs no session is working on"

# A pane with branch evidence whose every branch is `gone` has no row to show a
# branch in; its one row says so, so the pane is never missing from section 1.
STATE_ONLY_GONE = "only gone branches (counted)"

SESSION_ID_TEXT = 8


# Clones of one repository can disagree on a branch's local half of merge state
# (one holds a commit another lacks). The report keeps the most actionable.
STATE_PRIORITY = (UNMERGED, OPEN_PR, UNRESOLVED, CONTAINED, MERGED, GONE)


def _identity(key: tuple[str, str], resolution: Resolution) -> tuple[str, str]:
    """(repository, branch): the GitHub slug ignoring case, else the checkout path.

    Resolutions are keyed by main checkout, so two clones of one GitHub
    repository are two keys; the report must count them as one repository.
    """
    repo = resolution.repo_slug.lower() if resolution.repo_slug is not None else key[0]
    return repo, key[1]


def _reconcile(resolutions: Resolutions) -> dict[tuple[str, str], tuple]:
    """{(repository, branch): (key, Resolution)}, the most actionable state among clones."""
    chosen: dict[tuple[str, str], tuple] = {}
    for key, resolution in resolutions.items():
        identity = _identity(key, resolution)
        held = chosen.get(identity)
        rank = STATE_PRIORITY.index(resolution.state)
        if held is None or rank < STATE_PRIORITY.index(held[1].state):
            chosen[identity] = (key, resolution)
    return chosen


def _repo_name(key: tuple[str, str], resolution: Resolution) -> str:
    """`owner/name` when the branch resolved on github.com, else its repository path."""
    return resolution.repo_slug if resolution.repo_slug is not None else key[0]


def _session_record(session: dict) -> dict:
    return {
        "agent": session["agent"],
        "id": session["id"],
        "cwd": session["cwd"],
        "last_active": session["last_active"],
        "title": session.get("title"),
    }


def build_report(
    *,
    generated_at: str,
    since_days: int,
    owners: list[str],
    joined: list[dict],
    sessions: list[dict],
    resolutions: Resolutions,
    prs: list[dict],
    include_bots: bool,
    incomplete: list[str],
) -> dict:
    """The report as the spec's JSON object; see render_text and render_json.

    Every section and count works on (repository, branch), where the
    repository is the GitHub repository when there is one (_identity), with
    the one merge state _reconcile chose for it.
    """
    chosen = _reconcile(resolutions)

    def identity_for(name: str, directory, cwd) -> tuple[str, str]:
        key = resolutions.key_for(name, directory, cwd)
        return _identity(key, resolutions[key])

    open_panes: list[dict] = []
    open_identities: set[tuple[str, str]] = set()
    for pane in joined:
        branches: dict[tuple[str, str], dict] = {}
        for entry in pane["entries"]:
            identity = identity_for(entry["name"], entry["dir"], entry["cwd"])
            open_identities.add(identity)
            key, resolution = chosen[identity]
            if resolution.state == GONE:
                continue
            record = branches.setdefault(
                identity,
                {
                    "repo": _repo_name(key, resolution),
                    "name": key[1],
                    "state": resolution.state,
                    "pr": resolution.pr,
                    "evidence": [],
                    "reason": resolution.reason,
                },
            )
            if entry["evidence"] not in record["evidence"]:
                record["evidence"].append(entry["evidence"])
        for record in branches.values():
            record["evidence"].sort()
        open_panes.append(
            {
                "pane_id": pane["pane_id"],
                "tab": pane["tab"],
                "agent": pane["agent"],
                "session_id": pane["session_id"],
                "note": pane["note"],
                "branches": list(branches.values()),
            }
        )

    touched: dict[tuple[str, str], dict[tuple[str, str], dict]] = {}
    for session in sessions:
        for branch in session["branches"]:
            identity = identity_for(branch["name"], branch["dir"], session["cwd"])
            touched.setdefault(identity, {})[(session["agent"], session["id"])] = session
    closed: list[dict] = []
    for identity, (key, resolution) in chosen.items():
        stranded = resolution.state in STRANDED and identity in touched
        if not stranded or identity in open_identities:
            continue
        by_age = sorted(
            touched[identity].values(), key=lambda s: (s["last_active"], s["id"]), reverse=True
        )
        closed.append(
            {
                "repo": _repo_name(key, resolution),
                "name": key[1],
                "state": resolution.state,
                "pr": resolution.pr,
                "session": _session_record(by_age[0]),
                "older_sessions": len(by_age) - 1,
            }
        )
    closed.sort(key=lambda row: row["repo"].lower() + "\0" + row["name"])
    closed.sort(key=lambda row: row["session"]["last_active"], reverse=True)

    # Every resolved branch -- from session history and from pane directories
    # alike -- in the same (repository, branch) identity, slug ignoring case.
    session_branches = {
        identity for identity, (_, resolution) in chosen.items() if resolution.repo_slug is not None
    }
    unmatched: list[dict] = []
    bots_hidden = 0
    for pr in prs:
        heads = {(pr["repo"].lower(), pr["head"])}
        if pr.get("head_repo"):
            heads.add((pr["head_repo"].lower(), pr["head"]))
        if heads & session_branches:
            continue
        if pr["bot"] and not include_bots:
            bots_hidden += 1
            continue
        unmatched.append(
            {
                key: pr[key]
                for key in (
                    "repo",
                    "number",
                    "title",
                    "head",
                    "author",
                    "bot",
                    "draft",
                    "updated_at",
                    "url",
                )
            }
        )
    unmatched.sort(key=lambda row: (row["repo"].lower(), row["number"]))

    states = [resolution.state for _, resolution in chosen.values()]
    return {
        "generated_at": generated_at,
        "since_days": since_days,
        "owners": list(owners),
        "open": open_panes,
        "closed_unmerged": closed,
        "unmatched_prs": unmatched,
        "counts": {
            "gone": states.count(GONE),
            "unresolved": states.count(UNRESOLVED),
            "bots_hidden": bots_hidden,
        },
        "incomplete": list(incomplete),
    }


def exit_code(report: dict) -> int:
    """2 when incomplete, whatever the findings; else 1 when section 2 or 3 has a row."""
    if report["incomplete"]:
        return 2
    return 1 if report["closed_unmerged"] or report["unmatched_prs"] else 0


def render_json(report: dict) -> str:
    return json.dumps(report, indent=2) + "\n"


def _plural(count: int, word: str, plural: str | None = None) -> str:
    return f"{count} {word if count == 1 else (plural or word + 's')}"


def _state_text(state: str, pr: dict | None) -> str:
    if state == OPEN_PR and pr is not None:
        return f"open PR #{pr['number']}" + (" (draft)" if pr["draft"] else "")
    if state == MERGED and pr is not None:
        return f"merged PR #{pr['number']}"
    return state


def _short(session_id: str | None) -> str:
    return session_id[:SESSION_ID_TEXT] if session_id else "-"


def _table(columns: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return ["  (none)"]
    widths = [max(len(row[i]) for row in [columns, *rows]) for i in range(len(columns))]
    return [
        (
            "  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        ).rstrip()
        for row in [columns, *rows]
    ]


def render_text(report: dict, *, session_count: int) -> str:
    """The spec's text shape: a header line, three sections, the footer."""
    generated = datetime.strptime(report["generated_at"], "%Y-%m-%dT%H:%M:%SZ")
    since = (generated - timedelta(days=report["since_days"])).strftime("%Y-%m-%d")
    lines = [
        f"audit: {_plural(len(report['open']), 'pane')}, {_plural(session_count, 'session')} "
        f"since {since}, owners: {', '.join(report['owners'])}",
        "",
        HEADING_OPEN,
    ]

    rows = []
    for pane in report["open"]:
        lead = [pane["pane_id"], pane["tab"] or "-", pane["agent"], _short(pane["session_id"])]
        if pane["note"] is not None:
            rows.append([*lead, "-", "-", pane["note"]])
        elif not pane["branches"]:
            rows.append([*lead, "-", "-", STATE_ONLY_GONE])
        for branch in pane["branches"]:
            rows.append(
                [*lead, branch["repo"], branch["name"], _state_text(branch["state"], branch["pr"])]
            )
    lines += _table(["PANE", "TAB", "AGENT", "SESSION", "REPO", "BRANCH", "STATE"], rows)

    lines += ["", HEADING_CLOSED]
    rows = []
    for row in report["closed_unmerged"]:
        session = row["session"]
        older = f" +{row['older_sessions']}" if row["older_sessions"] else ""
        rows.append(
            [
                session["agent"],
                _short(session["id"]) + older,
                session["last_active"][:10],
                row["repo"],
                row["name"],
                _state_text(row["state"], row["pr"]),
            ]
        )
    lines += _table(["AGENT", "SESSION", "LAST ACTIVE", "REPO", "BRANCH", "STATE"], rows)

    lines += ["", HEADING_PRS]
    rows = []
    for pr in report["unmatched_prs"]:
        note = ", ".join(word for word, on in (("draft", pr["draft"]), ("bot", pr["bot"])) if on)
        rows.append(
            [
                pr["repo"],
                f"#{pr['number']}",
                pr["head"],
                pr["author"] or "-",
                pr["updated_at"][:10],
                note,
            ]
        )
    lines += _table(["REPO", "PR", "BRANCH", "AUTHOR", "UPDATED", "NOTE"], rows)

    counts = report["counts"]
    lines += [
        "",
        f"not listed: {_plural(counts['gone'], 'branch', 'branches')} gone, "
        f"{counts['unresolved']} unresolved, {_plural(counts['bots_hidden'], 'bot PR')} hidden.",
    ]
    if report["incomplete"]:
        lines.append(f"incomplete: {'; '.join(report['incomplete'])}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(args, *, out, err, now=_now) -> int:
    """One audit: require_tools -> owners -> panes -> sessions -> resolve -> open PRs -> report.

    Any AuditError (ToolError, GhError, GitError) or feed.HerdrError stops the
    run before anything is printed on `out`: one line on `err`, exit 2. An
    adapter that could not answer does not stop it: the report prints, marked
    incomplete, and exits 2.
    """

    def say(message: str) -> None:
        err.write(f"herdr-setup: audit: {message}\n")

    adapter_dir = feed.default_adapter_dir() if args.adapter_dir is None else Path(args.adapter_dir)
    try:
        require_tools()
        owner_list = owners(args.owners or [])
        pane_list = panes()
        adapters, probe_failures, failed_probes = load_adapters(adapter_dir, warn=say)
        gathered = gather_sessions(adapters, args.since, include_sdk=args.include_sdk, warn=say)
        joined = join(pane_list, adapters, gathered, failed_probes=failed_probes)
        resolutions = resolve_branches(resolution_entries(joined, gathered))
        prs = [pr for login in owner_list for pr in open_prs(login)]
    except (AuditError, feed.HerdrError) as exc:
        say(str(exc))
        return 2

    report = build_report(
        generated_at=now(),
        since_days=args.since,
        owners=owner_list,
        joined=joined,
        sessions=gathered.sessions,
        resolutions=resolutions,
        prs=prs,
        include_bots=args.include_bots,
        incomplete=probe_failures + gathered.incomplete,
    )
    if args.json:
        out.write(render_json(report))
    else:
        out.write(render_text(report, session_count=len(gathered.sessions)))
    return exit_code(report)


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


def main(argv: list[str] | None = None, *, out=None, err=None, now=None) -> int:
    """Parse the command line and run one audit; see run() for the exit statuses.

    Anything run() did not anticipate still exits 2, with its traceback on
    stderr: Python's own exit status for an uncaught exception is 1, which
    this command's table reads as "findings to act on" -- the one answer a
    crash must never give.
    """
    args = build_parser().parse_args(argv)
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    try:
        return run(args, out=out, err=err, now=_now if now is None else now)
    except Exception as exc:  # see the docstring
        traceback.print_exc(file=err)
        err.write(f"herdr-setup: audit: unexpected error: {type(exc).__name__}: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
