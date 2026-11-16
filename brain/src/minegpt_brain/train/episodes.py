"""Reading episode logs back into the conversations the model had.

An episode log (``agent/trace.py``) records every message of a goal's conversation along with
what happened in the game. :func:`load` rebuilds the conversation in the OpenAI chat format the
local backend sends, so a model trained on it sees exactly what it will see when it plays.

Logs from brain 0.3 on hold everything needed. Older logs lack the tool list, the "carry on"
nudge and the results of calls that were not run; those are filled in the way the agent of the
time wrote them, and the episode is marked ``exact = False``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ..agent.loop import NUDGE
from ..agent.tools import SKILLS

#: Tool results sent for calls the agent did not run, as old logs did not record them.
_LENGTH_LIMIT = "not run: your reply hit the length limit. Think less per turn and make fewer calls."


@dataclass
class Episode:
    path: Path
    goal: str = ""
    world: str = ""
    backend: str = ""
    #: ``success``, ``failed``, ``turn_limit``, ``stopped``, ``replaced``, ``error``,
    #: ``disconnected``, or ``unfinished`` when the log has no end.
    outcome: str = "unfinished"
    summary: str = ""
    #: The agent's own game check (goal_check) held when the model said it was done.
    verified: bool = False
    #: The benchmark's check passed, if the episode was a benchmark or collection run.
    passed: Optional[bool] = None
    task: Optional[str] = None
    turns: int = 0
    deaths: int = 0
    reflexes: int = 0
    system: str = ""
    #: The tools offered, as name, description and parameters. None in logs before 0.3.
    tools: Optional[list[dict[str, Any]]] = None
    #: The conversation: system prompt, goal message, then replies, tool results and notes.
    messages: list[dict[str, Any]] = field(default_factory=list)
    #: The model's (summarized) thinking for each reply, in order, where the backend logged any.
    thinking: list[str] = field(default_factory=list)
    #: False when parts of the conversation had to be filled in (logs before brain 0.3).
    exact: bool = True

    @property
    def good(self) -> bool:
        """Reached its goal, as checked by the game."""
        if self.outcome != "success":
            return False
        return self.passed if self.passed is not None else self.verified


def load(path: Path, bench_results: Optional[dict[str, dict[str, Any]]] = None) -> Episode:
    """Reads one episode log. ``bench_results`` maps log file names to their benchmark record."""
    path = Path(path)
    episode = Episode(path)
    pending: Optional[dict[str, Any]] = None  # the last reply, until its results are in
    tool_events: dict[str, dict[str, Any]] = {}

    def flush_old_results() -> None:
        """For logs without a ``results`` event: rebuild the results of the last reply."""
        nonlocal pending
        if pending is None or not pending["calls"]:
            pending = None
            return
        episode.exact = False
        skipped_because = None
        for call in pending["calls"]:
            event = tool_events.get(call["id"])
            if pending["stop"] == "max_tokens":
                content, error = _LENGTH_LIMIT, True
            elif event is not None:
                content, error = event.get("result", ""), not event.get("ok", True)
                if error and call["name"] in SKILLS + ("run_routine",) and skipped_because is None:
                    skipped_because = call["name"]
            elif skipped_because:
                content, error = f"not run: skipped because {skipped_because} failed", True
            else:
                content, error = "not run: the goal has already ended", True
            episode.messages.append(_tool_message(call["id"], content, error))
        pending = None

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # a line cut off by a crash
        event = record.get("event")
        if event == "start":
            episode.goal = record.get("goal", "")
            episode.world = record.get("world", "")
            episode.backend = record.get("backend", "")
        elif event == "prompt":
            episode.system = record.get("system", "")
            episode.tools = record.get("tools")
            episode.messages = [{"role": "system", "content": episode.system},
                                {"role": "user", "content": record.get("text", "")}]
        elif event == "turn":
            if pending is not None and pending["calls"]:
                flush_old_results()
            elif pending is not None:
                # A reply without tool calls, followed by another reply: the nudge was sent between.
                if episode.messages[-1]["role"] == "assistant":
                    episode.exact = False
                    episode.messages.append({"role": "user", "content": NUDGE})
                pending = None
            calls = record.get("calls") or []
            message: dict[str, Any] = {"role": "assistant", "content": record.get("text") or ""}
            if calls:
                message["tool_calls"] = [{"id": c["id"], "type": "function",
                                          "function": {"name": c["name"], "arguments": c.get("args") or {}}}
                                         for c in calls]
            episode.messages.append(message)
            episode.thinking.append(_thinking(record.get("raw")))
            episode.turns += 1
            pending = {"calls": calls, "stop": record.get("stop")}
            tool_events = {}
        elif event == "tool":
            tool_events[record.get("id", "")] = record
        elif event == "results":
            for result in record.get("results", []):
                episode.messages.append(_tool_message(result["id"], result["content"], result.get("error", False)))
            pending = None
        elif event == "note":
            flush_old_results()
            episode.messages.append({"role": "user", "content": record.get("text", "")})
        elif event == "user":
            pending = None
            episode.messages.append({"role": "user", "content": record.get("text", "")})
        elif event == "death":
            episode.deaths += 1
        elif event == "reflex":
            episode.reflexes += 1
        elif event == "end":
            flush_old_results()
            episode.outcome = record.get("outcome", "unfinished")
            episode.summary = record.get("summary", "")
            episode.verified = bool(record.get("verified"))
    record = (bench_results or {}).get(path.name)
    if record is not None:
        episode.passed = bool(record.get("passed"))
        episode.task = record.get("task")
    return episode


def _tool_message(call_id: str, content: str, error: bool) -> dict[str, Any]:
    # The same text the local backend sends.
    return {"role": "tool", "tool_call_id": call_id, "content": f"Error: {content}" if error else content}


def _thinking(raw: Any) -> str:
    if not isinstance(raw, list):
        return ""
    return "\n".join(block.get("thinking", "") for block in raw
                     if isinstance(block, dict) and block.get("type") == "thinking").strip()


def bench_index(results_files: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """Benchmark and collection records by episode log file name."""
    index: dict[str, dict[str, Any]] = {}
    for results in results_files:
        try:
            lines = Path(results).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            log = record.get("log")
            if log:
                # Paths may come from Windows; only the file name is needed.
                index[log.replace("\\", "/").rsplit("/", 1)[-1]] = record
    return index


def find(data_dir: Path) -> list[Path]:
    """Every episode log under a data folder: the agent's, the benchmark's and collection runs'."""
    data_dir = Path(data_dir)
    return sorted(p for p in data_dir.rglob("*.jsonl") if p.parent.name == "episodes")


def load_all(data_dir: Path, extra: Iterable[Path] = ()) -> list[Episode]:
    data_dir = Path(data_dir)
    index = bench_index([*data_dir.rglob("results.jsonl")])
    paths = find(data_dir) + [Path(p) for p in extra]
    return [load(p, index) for p in dict.fromkeys(paths)]
