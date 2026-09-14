#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Sandbox builder and output checker for tests/test_audit_entrypoint.sh.

Not a test file: tests/run.sh's `test_*` glob and pytest's `test_*.py` never
pick it up. The shell test calls it twice, so the whole end-to-end setup costs
one uv start rather than one per file it writes:

    audit_e2e.py setup <work>                   build everything under <work>
    audit_e2e.py check <text> <json> <git-log>  the JSON run agrees with the text
                                                run, and git ran read-only

`setup` writes, from the captures with values edited and never keys:

    <work>/repos/example-repo         a real git repository, origin on github.com,
                                      with feat/open-work, feat/stranded and
                                      feat/squashed each one commit ahead of main
    <work>/stores/findings/claude     CLAUDE_CONFIG_DIR: the open pane's session
                                      (feat/open-work) and a closed one
                                      (feat/stranded, feat/squashed, feat/deleted)
    <work>/stores/findings/codex      CODEX_HOME: a closed session on feat/stranded
    <work>/stores/clean/claude        only the open pane's session
    <work>/stores/clean/codex         an empty store
    <work>/gh-findings.json           feat/squashed MERGED as #7, an open draft #31,
                                      an open bot pull request #40
    <work>/gh-clean.json              the merged #7 and the open bot pull request #40
    <work>/herdr/                     agent list (three panes in the repository:
                                      the open session, an unknown session, and a
                                      codex pane with none) and the captured tab list
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auditlib import (  # noqa: E402
    commit,
    gh_pr,
    gh_repo,
    gh_state,
    git,
    make_repo,
    writing_git_calls,
)
from feedlib import TESTS_DIR, agent_entry, agent_list, captured, write_codex_rollout  # noqa: E402

SLUG = "example-org/example-repo"
OPEN_SESSION = "00000000-0000-4000-8000-0000000000a1"
CLOSED_SESSION = "00000000-0000-4000-8000-0000000000a2"
CODEX_SESSION = "00000000-0000-4000-8000-0000000000a3"
UNKNOWN_SESSION = "00000000-0000-4000-8000-0000000000a4"
CLAUDE_TRANSCRIPT = TESTS_DIR / "fixtures" / "claude" / "transcript.jsonl"


def claude_line(**values) -> dict:
    """The captured first transcript line (a user line with gitBranch), values replaced."""
    line = json.loads(CLAUDE_TRANSCRIPT.read_text(encoding="utf-8").splitlines()[0])
    for key, value in values.items():
        if key not in line:
            raise KeyError(f"{key!r} is not a key the capture has; do not invent one")
        line[key] = value
    return line


def write_claude(config: Path, session_id: str, cwd: Path, branches) -> None:
    (config / "sessions").mkdir(parents=True, exist_ok=True)
    project = config / "projects" / "example-project"
    project.mkdir(parents=True, exist_ok=True)
    lines = [
        claude_line(
            sessionId=session_id,
            cwd=str(cwd),
            gitBranch=branch,
            timestamp=f"2026-09-12T18:0{index}:00.000Z",
        )
        for index, branch in enumerate(branches)
    ]
    (project / f"{session_id}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )


def write_codex(config: Path, session_id: str | None, cwd: Path, branch: str | None) -> None:
    (config / "sessions").mkdir(parents=True, exist_ok=True)
    if session_id is None:
        return
    git_block = dict(captured_codex_git(), branch=branch)
    write_codex_rollout(
        config,
        2026,
        9,
        12,
        "rollout-e2e.jsonl",
        id=session_id,
        session_id=session_id,
        cwd=str(cwd),
        git=git_block,
    )


def captured_codex_git() -> dict:
    text = (TESTS_DIR / "fixtures" / "codex" / "session-meta.json").read_text(encoding="utf-8")
    return json.loads(text)["payload"]["git"]


def pane(index: int, cwd: Path, *, agent: str | None = None, session: str | None) -> dict:
    captured_session = agent_entry(index)["agent_session"]
    values = {"cwd": str(cwd), "foreground_cwd": str(cwd)}
    values["agent_session"] = None if session is None else dict(captured_session, value=session)
    if agent is not None:
        values["agent"] = agent
    return agent_entry(index, **values)


def setup(work: Path) -> None:
    repo = make_repo(work / "repos", "example-repo")
    for name in ("feat/open-work", "feat/stranded", "feat/squashed"):
        git(repo, "checkout", "-q", "-b", name, "main")
        commit(repo, f"work on {name}")
    git(repo, "checkout", "-q", "main")

    findings = work / "stores" / "findings"
    write_claude(findings / "claude", OPEN_SESSION, repo, ["feat/open-work"])
    write_claude(
        findings / "claude",
        CLOSED_SESSION,
        repo,
        ["feat/stranded", "feat/squashed", "feat/deleted"],
    )
    write_codex(findings / "codex", CODEX_SESSION, repo, "feat/stranded")
    clean = work / "stores" / "clean"
    write_claude(clean / "claude", OPEN_SESSION, repo, ["feat/open-work"])
    write_codex(clean / "codex", None, repo, None)

    merged = gh_pr(7, "feat/squashed", state="MERGED")
    bot_pr = gh_pr(40, "deps/bump", author={"login": "dependabot[bot]", "__typename": "Bot"})
    states = {
        "findings": [merged, gh_pr(31, "chore/bump", draft=True), bot_pr],
        "clean": [merged, bot_pr],
    }
    for name, prs in states.items():
        state = gh_state(repos={SLUG: gh_repo(prs=prs)})
        (work / f"gh-{name}.json").write_text(json.dumps(state), encoding="utf-8")

    herdr = work / "herdr"
    herdr.mkdir()
    agents = agent_list(
        [
            pane(0, repo, session=OPEN_SESSION),
            pane(1, repo, session=UNKNOWN_SESSION),
            pane(2, repo, agent="codex", session=None),
        ]
    )
    (herdr / "agent->list.json").write_text(json.dumps(agents), encoding="utf-8")
    (herdr / "tab->list.json").write_text(json.dumps(captured("tab-list")), encoding="utf-8")


HEADINGS = ("open in Herdr", "closed, with unmerged branches", "open PRs no session is working on")
FOOTER = re.compile(
    r"^not listed: (\d+) branch(?:es)? gone, (\d+) unresolved, (\d+) bot PRs? hidden\.$", re.M
)


def section_rows(text: str, heading: str) -> int:
    """Rows under `heading`, not counting its column line or an `(none)`."""
    lines = text.splitlines()
    at = lines.index(heading)
    rows = []
    for line in lines[at + 1 :]:
        if not line.startswith("  "):
            break
        rows.append(line)
    return 0 if rows == ["  (none)"] else len(rows) - 1


def check(text_path: Path, json_path: Path, git_log: Path) -> int:
    text = text_path.read_text(encoding="utf-8")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    problems = []
    footer = FOOTER.search(text)
    counts = data["counts"]
    if footer is None:
        problems.append("the text run has no footer")
    elif tuple(int(n) for n in footer.groups()) != (
        counts["gone"],
        counts["unresolved"],
        counts["bots_hidden"],
    ):
        problems.append(f"footer {footer.group(0)!r} disagrees with JSON counts {counts}")
    open_rows = sum(max(1, len(p["branches"])) for p in data["open"])
    for heading, expected in zip(
        HEADINGS, (open_rows, len(data["closed_unmerged"]), len(data["unmatched_prs"])), strict=True
    ):
        found = section_rows(text, heading)
        if found != expected:
            problems.append(f"{heading!r}: text has {found} rows, JSON {expected}")
    if not git_log.exists() or not git_log.read_text(encoding="utf-8").strip():
        problems.append(f"{git_log} recorded no git call: the shim was not on the audit's PATH")
    for line in writing_git_calls(git_log):
        problems.append(f"git ran a subcommand outside the read-only set: {line}")
    for problem in problems:
        print(problem)
    return 1 if problems else 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "setup":
        setup(Path(argv[1]))
        return 0
    if len(argv) == 4 and argv[0] == "check":
        return check(Path(argv[1]), Path(argv[2]), Path(argv[3]))
    print("usage: audit_e2e.py setup <work> | check <text> <json> <git-log>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
