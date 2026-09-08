#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""The feed runner: adapter discovery, pane gathering, resolution, reporting.

`feed` asks Herdr which panes are running a coding agent, asks each agent's
adapter which session that pane is in, and sends Herdr one
`pane.report_agent_session` call per pane it is sure about. A pane with a
reported session comes back running its agent's resume command after a
server restart; a pane without one comes back as a bare shell.

Two rules shape everything below, and both are about being wrong rather than
being slow.

**The runner decides, the adapter never reports.** An adapter answers a
question and nothing more: it is handed a list of panes and prints
candidates. It never opens the Herdr socket, and its confidence is an input
to the decision, not the decision. Reporting without asking happens in
exactly one case -- one candidate, at `exact` confidence. Anything else is a
question for the operator, because feeding Herdr a wrong session id makes a
pane resume the wrong conversation, which is the worst thing this subsystem
can do.

**A failed Herdr call raises; it never becomes an empty list.** A blocked
Herdr answers with a JSON error object rather than an empty result, and it
may do so on stderr, and it may do so while exiting 0. A parser that reads
`result.agents` and shrugs at a missing key sees no panes and prints a
cheerful summary having fed nothing -- which is indistinguishable, to the
operator, from a host that genuinely had no agent panes. That failure mode
is the reason the whole tool exists, so every unreadable answer below is an
exception, never a zero.

Standard library only, and the interpreter comes from uv (see AGENTS.md).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Keys every probe must declare. `unverified` is deliberately not required:
# absent means false, so an adapter that is fine says nothing.
REQUIRED_PROBE_KEYS = ("agent", "source", "available", "confidence")
VALID_CONFIDENCE = ("exact", "heuristic")

# An adapter is a small local script. These bound a broken one; they are not
# a performance budget.
PROBE_TIMEOUT = 10.0
RESOLVE_TIMEOUT = 30.0

REPORT_METHOD = "pane.report_agent_session"


class HerdrError(RuntimeError):
    """A Herdr call failed, or answered something this tool cannot read.

    Never caught in order to carry on with an empty list. The whole point of
    the type is that the run stops rather than reporting success having done
    nothing.
    """


class AdapterError(RuntimeError):
    """One adapter misbehaved. The run skips it and keeps going."""


def warn(message: str) -> None:
    sys.stderr.write(f"herdr-setup: feed: {message}\n")


def flatten(text: str) -> str:
    """Fold text onto one line, matching lib/common.sh's hs_flatten."""
    return " ".join(str(text).split())


# --------------------------------------------------------------------------
# Adapter discovery and probe
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Adapter:
    """One usable adapter: its executable plus what its probe declared."""

    path: Path
    agent: str
    source: str
    confidence: str
    command: str
    unverified: bool
    probe: dict

    @property
    def name(self) -> str:
        return self.path.name


def discover(adapter_dir) -> list[Path]:
    """Executable files in `adapter_dir`, sorted by name.

    Skips directories, names beginning with a dot, and names ending in `.md`,
    so a README and an editor's backup file can sit beside the adapters. A
    missing directory is an empty list rather than an error: a checkout may
    legitimately carry no adapters, and the run says so in its summary.

    Executability is `os.access(X_OK)` -- can *this* process run it -- not the
    mode bits. A file nobody here can execute is not an adapter, whatever its
    permissions claim.
    """
    directory = Path(adapter_dir)
    if not directory.is_dir():
        return []
    found = []
    for entry in sorted(directory.iterdir(), key=lambda p: p.name):
        if entry.name.startswith(".") or entry.name.endswith(".md"):
            continue
        if not entry.is_file():
            continue
        if not os.access(entry, os.X_OK):
            continue
        found.append(entry)
    return found


def validate_probe(obj) -> dict:
    """Check a probe object against the contract in docs/adapters.md.

    Returns the object on success; raises AdapterError naming the specific
    violation otherwise. Callers turn that message into the warning the
    operator sees, so it has to say which key or which value was wrong.
    """
    if not isinstance(obj, dict):
        raise AdapterError(f"probe must print a JSON object, got {type(obj).__name__}")
    for key in REQUIRED_PROBE_KEYS:
        if key not in obj:
            raise AdapterError(f"probe is missing the required key '{key}'")
    if not isinstance(obj["agent"], str) or not obj["agent"]:
        raise AdapterError("probe key 'agent' must be a non-empty string")
    if not isinstance(obj["source"], str) or not obj["source"]:
        raise AdapterError("probe key 'source' must be a non-empty string")
    if not isinstance(obj["available"], bool):
        raise AdapterError("probe key 'available' must be true or false")
    if obj["confidence"] not in VALID_CONFIDENCE:
        raise AdapterError(
            f"probe key 'confidence' must be one of {' or '.join(VALID_CONFIDENCE)}, "
            f"got {obj['confidence']!r}"
        )
    if "unverified" in obj and not isinstance(obj["unverified"], bool):
        raise AdapterError("probe key 'unverified' must be true or false")
    if "command" in obj and (not isinstance(obj["command"], str) or not obj["command"]):
        raise AdapterError("probe key 'command' must be a non-empty string")
    return obj


def probe(path, timeout: float = PROBE_TIMEOUT) -> dict:
    """Run `<path> probe` and return the object it printed.

    Raises AdapterError on every way this can go wrong -- a non-zero exit, no
    output, output that is not JSON, output that is not an object, a missing
    required key, an unknown confidence. The caller decides what to do with
    that; nothing here takes the run down.
    """
    path = Path(path)
    try:
        proc = subprocess.run(  # noqa: S603
            [str(path), "probe"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(f"probe did not answer within {timeout:g}s") from exc
    except OSError as exc:
        raise AdapterError(f"probe could not be run: {exc}") from exc

    if proc.returncode != 0:
        detail = flatten(proc.stderr) or flatten(proc.stdout) or "no output"
        raise AdapterError(f"probe exited {proc.returncode}: {detail}")
    if not proc.stdout.strip():
        raise AdapterError("probe printed nothing")
    try:
        obj = json.loads(proc.stdout)
    except ValueError as exc:
        raise AdapterError(f"probe printed something that is not JSON: {exc}") from exc
    return validate_probe(obj)


def usable_adapters(adapter_dir, warn=warn, timeout: float = PROBE_TIMEOUT) -> list[Adapter]:
    """Discover and probe every adapter, and return the ones the run will use.

    The skip policy, in the order it matters:

    - A probe that fails in any way is skipped **with a warning naming the
      adapter**. It is a broken adapter, not a broken run, but it is also an
      agent whose panes will not be fed, so it is never silent.
    - `available: false` is skipped **silently**. That is the adapter working
      correctly: the agent leaves no readable session state on this host, and
      there is nothing to say about it.
    - `unverified: true` is used, with a warning naming the adapter. It ships
      untested against a real installation and the operator should know
      before a session id it produced is reported.
    """
    adapters: list[Adapter] = []
    for path in discover(adapter_dir):
        try:
            obj = probe(path, timeout=timeout)
        except AdapterError as exc:
            warn(f"adapter {path.name}: skipped: {exc}")
            continue
        if not obj["available"]:
            continue
        unverified = bool(obj.get("unverified", False))
        if unverified:
            warn(
                f"adapter {path.name}: ships unverified -- it has not been confirmed "
                f"against a real {obj['agent']} installation."
            )
        adapters.append(
            Adapter(
                path=path,
                agent=obj["agent"],
                source=obj["source"],
                confidence=obj["confidence"],
                command=obj.get("command") or obj["agent"],
                unverified=unverified,
                probe=obj,
            )
        )
    return adapters


# --------------------------------------------------------------------------
# Talking to Herdr
# --------------------------------------------------------------------------


def herdr_json(args: list[str]) -> dict:
    """Run `herdr <args>` and return the object it printed.

    The Python counterpart of lib/common.sh's hs_herdr_json, and it fails in
    the same three-part shape, in this order:

    1. A non-zero exit status is a failure, whatever was printed.
    2. A top-level `error` key is a failure **even when the exit status is
       zero**. Herdr answers a blocked call with an error object; a caller
       that trusts the status alone parses that object as its answer, finds
       no result, and calls it an empty one.
    3. Anything unparseable is a failure. No output, output that is not JSON,
       output that is not an object -- none of those is an empty result.

    stdout and stderr are captured SEPARATELY and never merged: a deprecation
    notice glued to the front of the JSON breaks a parse of an answer that
    was fine. On success the notice is passed through to our own stderr,
    because it is still worth seeing.
    """
    label = "herdr " + " ".join(args)
    try:
        proc = subprocess.run(  # noqa: S603
            ["herdr", *args],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise HerdrError(f"{label}: {exc}") from exc

    if proc.returncode != 0:
        detail = flatten(proc.stdout) or flatten(proc.stderr) or "no output"
        raise HerdrError(f"{label}: exited {proc.returncode}: {detail}")

    if proc.stderr.strip():
        sys.stderr.write(proc.stderr if proc.stderr.endswith("\n") else proc.stderr + "\n")

    if not proc.stdout.strip():
        raise HerdrError(f"{label}: printed nothing to parse")
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise HerdrError(f"{label}: answer is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HerdrError(f"{label}: answer is not a JSON object")

    error = data.get("error")
    if error is not None:
        message = error.get("message", error) if isinstance(error, dict) else error
        raise HerdrError(f"{label}: {flatten(message)}")
    return data


def _result(data: dict, label: str) -> dict:
    result = data.get("result")
    if not isinstance(result, dict):
        raise HerdrError(f"{label}: answer carries no readable result object")
    return result


def _agent_entries(data: dict) -> list:
    """The agent list out of `herdr agent list`.

    An answer whose shape this cannot read raises. That is the whole rule of
    this module written once: an empty list must come from Herdr saying there
    are no agent panes, never from this function failing to find where they
    would have been.
    """
    label = "herdr agent list"
    result = _result(data, label)
    agents = result.get("agents")
    if not isinstance(agents, list):
        raise HerdrError(f"{label}: answer carries no 'agents' list")
    return agents


def _foreground_match(result: dict, command: str, label: str):
    """The foreground process in a pane whose argv0 is `command`, or None.

    Matches the argv0 whole and by basename, so a pane running
    `/usr/local/bin/claude` counts. Returns None when the pane genuinely runs
    something else -- that is a real answer and the caller drops the pane with
    a note. A missing process list is NOT that answer, and raises.
    """
    processes = result.get("processes")
    if not isinstance(processes, list):
        raise HerdrError(f"{label}: answer carries no 'processes' list")
    for entry in processes:
        if not isinstance(entry, dict):
            continue
        if not entry.get("foreground"):
            continue
        argv0 = entry.get("argv0") or ""
        if argv0 != command and os.path.basename(str(argv0)) != command:
            continue
        return entry
    return None


def panes_for(agent: str, command: str | None = None, warn=warn) -> list[dict]:
    """Every live pane running `agent`, as the payload an adapter is given.

    Asks `herdr agent list`, keeps the entries whose `agent` field matches,
    and asks `herdr pane process-info --pane <id>` about each one. The record
    is `{pane_id, cwd, pid, pid_start_epoch}`: `pid` is the pane's foreground
    process, which is what lets an exact adapter tie the pane to a session,
    and `pid_start_epoch` is what stops a recycled pid from matching a stale
    session file.

    A pane whose foreground process is not the agent is dropped with a note --
    the operator ran something else in it, and there is nothing to feed. Any
    Herdr call that fails raises, including one that fails only on the second
    round of calls.
    """
    command = command or agent
    entries = _agent_entries(herdr_json(["agent", "list"]))

    panes: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("agent") != agent:
            continue
        pane_id = entry.get("pane_id") or entry.get("id")
        if not pane_id:
            warn(f"agent list entry for {agent} carries no pane id; skipping it")
            continue

        label = f"herdr pane process-info --pane {pane_id}"
        result = _result(herdr_json(["pane", "process-info", "--pane", str(pane_id)]), label)
        match = _foreground_match(result, command, label)
        if match is None:
            warn(f"pane {pane_id}: no foreground '{command}' process; skipping it")
            continue

        cwd = result.get("cwd") or match.get("cwd")
        start = match.get("pid_start_epoch")
        if start is None:
            start = match.get("start_epoch")
        panes.append(
            {
                "pane_id": str(pane_id),
                "cwd": cwd,
                "pid": match.get("pid"),
                "pid_start_epoch": start,
            }
        )
    return panes


# --------------------------------------------------------------------------
# Asking the adapter
# --------------------------------------------------------------------------


def resolve(adapter_path, panes: list[dict], timeout: float = RESOLVE_TIMEOUT) -> list:
    """Hand `panes` to `<adapter> resolve` on stdin and return its results.

    Raises AdapterError for every unreadable answer. The caller treats that as
    "this adapter contributed nothing", which is safe in a way the Herdr side
    is not: an adapter that cannot answer means panes go unfed and get said so
    in the summary, whereas Herdr failing means the tool does not know what is
    out there at all.
    """
    adapter_path = Path(adapter_path)
    payload = json.dumps({"panes": panes})
    try:
        proc = subprocess.run(  # noqa: S603
            [str(adapter_path), "resolve"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(f"resolve did not answer within {timeout:g}s") from exc
    except OSError as exc:
        raise AdapterError(f"resolve could not be run: {exc}") from exc

    if proc.returncode != 0:
        detail = flatten(proc.stderr) or flatten(proc.stdout) or "no output"
        raise AdapterError(f"resolve exited {proc.returncode}: {detail}")
    if not proc.stdout.strip():
        raise AdapterError("resolve printed nothing")
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise AdapterError(f"resolve printed something that is not JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise AdapterError("resolve must print an object with a 'results' list")
    return data["results"]


def candidates_by_pane(results: list) -> dict[str, list]:
    """Index an adapter's results by pane id, dropping what cannot be reported.

    An adapter may answer about fewer panes than it was given -- the runner
    treats a pane it said nothing about the same as one it had no candidates
    for. A candidate with no `session_id` is dropped here rather than
    downstream: it cannot be reported, so as a prompt option it does nothing,
    and as the lone `exact` candidate it would be reported unasked.
    """
    by_pane: dict[str, list] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        pane_id = item.get("pane_id")
        if not pane_id:
            continue
        candidates = item.get("candidates")
        if not isinstance(candidates, list):
            candidates = []
        kept = [
            c
            for c in candidates
            if isinstance(c, dict) and isinstance(c.get("session_id"), str) and c["session_id"]
        ]
        by_pane[str(pane_id)] = kept
    return by_pane


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------


def _confidence(candidate: dict) -> str:
    value = candidate.get("confidence")
    return value if value in VALID_CONFIDENCE else ""


def describe(candidate: dict) -> str:
    """One line naming a candidate, for the prompt and for a dry run."""
    bits = [str(candidate.get("label") or candidate["session_id"])]
    if candidate.get("updated"):
        bits.append(str(candidate["updated"]))
    bits.append(_confidence(candidate) or "no confidence declared")
    return f"{candidate['session_id']}  ({', '.join(bits)})"


def prompt_for_candidate(pane_id: str, candidates: list[dict]):
    """Show the candidates on stderr and read a choice from the terminal.

    stderr, not stdout, so the report lines and the summary stay pipeable.
    An empty answer, an out-of-range number and anything that is not a number
    all mean skip: this is the question whose wrong answer resumes the wrong
    conversation, so the only accepted answer is an unambiguous one.
    """
    sys.stderr.write(f"herdr-setup: feed: pane {pane_id} has more than one possible session:\n")
    for index, candidate in enumerate(candidates, start=1):
        sys.stderr.write(f"  {index}) {describe(candidate)}\n")
    sys.stderr.write(f"Which session is pane {pane_id} in? [1-{len(candidates)}, blank to skip] ")
    sys.stderr.flush()
    try:
        answer = sys.stdin.readline()
    except (OSError, KeyboardInterrupt):
        return None
    answer = answer.strip()
    if not answer.isdigit():
        return None
    choice = int(answer)
    if not 1 <= choice <= len(candidates):
        return None
    return candidates[choice - 1]


def decide(pane_id: str, candidates: list[dict], *, assume_yes: bool, interactive: bool, ask):
    """Choose the session to report for one pane, or choose not to.

    Returns `(candidate, note)`. A note is present exactly when nothing is
    being reported, and it is what the operator is told.

    The one case that reports unasked is a single candidate at `exact`
    confidence -- one session, and an adapter that tied it to the pane's own
    process rather than to a directory two panes might share. Everything else
    asks, and `--yes` is the operator waiving the question in advance, taking
    the adapter's best candidate. With neither an answer nor a terminal there
    is nobody to ask, so the pane is skipped: silence is not consent
    (AGENTS.md), and a pane left unfed can be fed by the next run, whereas a
    pane fed the wrong id resumes the wrong conversation.
    """
    if not candidates:
        return None, "no session found"
    if len(candidates) == 1 and _confidence(candidates[0]) == "exact":
        return candidates[0], ""
    if assume_yes:
        return candidates[0], ""
    if interactive:
        chosen = ask(pane_id, candidates)
        if chosen is None:
            return None, "not confirmed at the prompt"
        return chosen, ""
    reason = (
        "more than one possible session"
        if len(candidates) > 1
        else "the adapter is not certain of this match"
    )
    return None, f"{reason}; re-run at a terminal to choose, or with --yes to take the best"


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------

_LAST_SEQ = 0


def next_seq() -> int:
    """A strictly increasing sequence number for a report.

    Herdr orders reports for a pane by `seq`, so two reports made inside the
    same nanosecond tick must still be ordered. The clock supplies the value;
    the counter only guarantees it never repeats or goes backwards within a
    run.
    """
    global _LAST_SEQ
    seq = max(time.time_ns(), _LAST_SEQ + 1)
    _LAST_SEQ = seq
    return seq


def build_request(pane: dict, adapter: Adapter, candidate: dict) -> dict:
    """The `pane.report_agent_session` request, shaped as Herdr's own hooks make it.

    `agent_session_path` is present only when the adapter supplied one: an
    adapter that cannot find a transcript says nothing rather than sending a
    path that is not there.
    """
    params = {
        "pane_id": pane["pane_id"],
        "source": adapter.source,
        "agent": adapter.agent,
        "seq": next_seq(),
        "agent_session_id": candidate["session_id"],
    }
    session_path = candidate.get("session_path")
    if isinstance(session_path, str) and session_path:
        params["agent_session_path"] = session_path
    request_id = f"{adapter.source}:{int(time.time() * 1000)}:{random.randrange(1_000_000):06d}"
    return {"id": request_id, "method": REPORT_METHOD, "params": params}


def send(socket_path, request: dict, timeout: float = 2.0) -> None:
    """Send one request as a single JSON line on the Herdr socket.

    Raises HerdrError if it cannot be delivered. Herdr's own hooks swallow
    every failure here, which is right for a hook that must never disturb the
    agent it is attached to, and wrong for this tool: a pane that was not fed
    is the exact thing feed exists to prevent, so it is reported and it
    changes the exit status.
    """
    line = (json.dumps(request) + "\n").encode("utf-8")
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(timeout)
        try:
            client.connect(str(socket_path))
            client.sendall(line)
            # Herdr answers, and the answer is not needed: the send is the
            # report. Draining it keeps the peer from seeing a reset.
            with contextlib.suppress(OSError):
                client.recv(65536)
        finally:
            client.close()
    except OSError as exc:
        raise HerdrError(f"could not report on {socket_path}: {exc}") from exc


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


def default_socket_path() -> Path:
    """The same rule as lib/common.sh's hs_socket_path."""
    override = os.environ.get("HERDR_SOCKET_PATH")
    if override:
        return Path(override)
    return Path.home() / ".config" / "herdr" / "herdr.sock"


def default_adapter_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "adapters"


def _count(n: int, word: str = "pane") -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def run(
    *,
    adapter_dir=None,
    socket_path=None,
    dry_run: bool = False,
    assume_yes: bool = False,
    interactive=None,
    ask=prompt_for_candidate,
    warn=warn,
    out=None,
) -> int:
    """One whole feed run. Returns the process exit status.

    0  every pane the run was sure about was reported.
    1  something did not get through -- a send failed, or an adapter could
       not answer. Panes deliberately skipped are NOT this: a skip is a
       decision the run made and told the operator about.
    2  Herdr could not be read. The run does not know what is out there, so
       it says so instead of printing a reassuring zero.
    """
    adapter_dir = default_adapter_dir() if adapter_dir is None else Path(adapter_dir)
    socket_path = default_socket_path() if socket_path is None else Path(socket_path)
    if interactive is None:
        interactive = sys.stdin.isatty()
    out = sys.stdout if out is None else out

    adapters = usable_adapters(adapter_dir, warn=warn)
    if not adapters:
        warn(f"no usable adapters in {adapter_dir}; nothing can be fed")

    reported = 0
    skipped = 0
    failed = 0

    for adapter in adapters:
        try:
            panes = panes_for(adapter.agent, command=adapter.command, warn=warn)
        except HerdrError as exc:
            warn(str(exc))
            return 2
        if not panes:
            continue

        try:
            results = resolve(adapter.path, panes)
        except AdapterError as exc:
            warn(f"adapter {adapter.name}: {exc}; its {_count(len(panes))} went unfed")
            failed += 1
            continue
        by_pane = candidates_by_pane(results)

        for pane in panes:
            pane_id = pane["pane_id"]
            candidate, note = decide(
                pane_id,
                by_pane.get(pane_id, []),
                assume_yes=assume_yes,
                interactive=interactive,
                ask=ask,
            )
            if candidate is None:
                warn(f"pane {pane_id}: skipped: {note}")
                skipped += 1
                continue

            request = build_request(pane, adapter, candidate)
            if dry_run:
                out.write(f"+ {REPORT_METHOD} {json.dumps(request['params'])}\n")
                reported += 1
                continue
            try:
                send(socket_path, request)
            except HerdrError as exc:
                warn(f"pane {pane_id}: {exc}")
                failed += 1
                continue
            out.write(f"reported {pane_id}: {adapter.agent} session {candidate['session_id']}\n")
            reported += 1

    summary = f"feed: reported {_count(reported)}, skipped {skipped}"
    if failed:
        summary += f", {failed} failed"
    summary += "."
    if dry_run:
        summary += " (dry run: nothing was sent)"
    out.write(summary + "\n")

    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="feed.py",
        description="Report live agent sessions to Herdr so a restart can resume them.",
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
    parser.add_argument("--dry-run", action="store_true", help="print the calls, make none")
    parser.add_argument(
        "--yes",
        dest="assume_yes",
        action="store_true",
        help="do not ask; take each adapter's best candidate",
    )
    args = parser.parse_args(argv)
    return run(
        adapter_dir=args.adapter_dir,
        socket_path=args.socket_path,
        dry_run=args.dry_run,
        assume_yes=args.assume_yes,
    )


if __name__ == "__main__":
    raise SystemExit(main())
