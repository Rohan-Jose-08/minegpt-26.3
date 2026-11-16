"""Turning episodes into a fine-tuning dataset.

Only episodes that reached their goal, as checked by the game, are kept by default: the
benchmark's own check for benchmark and collection runs, the agent's goal check otherwise.
Each kept episode becomes one or more training samples in the OpenAI chat format with tools,
which Hugging Face chat templates accept as they are::

    {"messages": [...], "tools": [...], "meta": {...}}

An assistant message with ``"train": false`` is context only: the fine-tune script does not
learn from it. Long episodes are cut into windows with the same rule the local backend uses to
fit its context (:func:`..llm.history.fit_history`), so every reply is learnt once, from the
history the model would have had in front of it.
"""

from __future__ import annotations

import hashlib
import json
import random
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Optional

from ..agent.tools import tool_specs
from ..llm.history import DEFAULT_BUDGET, DROP_STEP, TRIMMED_NOTE, dropped_turns, turn_groups
from .episodes import Episode


@dataclass
class Filters:
    #: Keep episodes the game did not confirm, as long as the model said it succeeded.
    unverified: bool = False
    #: Keep episodes in which the player died.
    deaths: bool = True
    #: Only episodes from backends whose name starts with one of these (e.g. ``claude``).
    backends: tuple[str, ...] = ()
    #: Leave out episodes with more turns than this (0: no limit).
    max_turns: int = 0


@dataclass
class Selection:
    kept: list[Episode] = field(default_factory=list)
    #: Why the rest were left out, with counts.
    skipped: Counter = field(default_factory=Counter)


def select(episodes: Iterable[Episode], filters: Optional[Filters] = None) -> Selection:
    filters = filters or Filters()
    selection = Selection()
    seen: set[str] = set()
    for episode in episodes:
        reason = _reject(episode, filters)
        if reason is None:
            key = _fingerprint(episode)
            if key in seen:
                reason = "duplicate"
            seen.add(key)
        if reason:
            selection.skipped[reason] += 1
        else:
            selection.kept.append(episode)
    return selection


def _reject(episode: Episode, filters: Filters) -> Optional[str]:
    if len(episode.messages) < 3 or episode.turns == 0:
        return "no model turns"
    if episode.outcome != "success":
        return f"outcome {episode.outcome}"
    if episode.passed is False:
        return "benchmark check failed"
    if episode.passed is None and not episode.verified and not filters.unverified:
        return "not confirmed by the game"
    if episode.deaths and not filters.deaths:
        return "died"
    if filters.backends and not episode.backend.startswith(filters.backends):
        return f"backend {episode.backend.split(':')[0] or 'unknown'}"
    if filters.max_turns and episode.turns > filters.max_turns:
        return "too many turns"
    if any(c["function"]["name"] == "review_build" for m in episode.messages for c in m.get("tool_calls") or ()):
        # The pictures are not in the log, and a text-only model cannot be shown them anyway.
        return "used pictures"
    return None


def _fingerprint(episode: Episode) -> str:
    # Call ids differ from run to run; what was called, and with what, is the content.
    replies = [[m.get("content")] + [[c["function"]["name"], c["function"]["arguments"]] for c in m.get("tool_calls") or ()]
               for m in episode.messages if m["role"] == "assistant"]
    return hashlib.sha1(json.dumps([episode.goal, replies], sort_keys=True, default=str).encode()).hexdigest()


def openai_tools(tools: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The tool list in the format the local backend sends. Old logs get today's tools."""
    if tools is None:
        tools = [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tool_specs()]
    return [{"type": "function", "function": t} for t in tools]


def samples(episode: Episode, budget: int = DEFAULT_BUDGET, step: int = DROP_STEP,
            thinking: bool = False) -> list[dict[str, Any]]:
    """One episode as training samples: one per context window (usually just one)."""
    messages = [dict(m) for m in episode.messages]
    if thinking:
        replies = [m for m in messages if m["role"] == "assistant"]
        for message, thought in zip(replies, episode.thinking):
            if thought:
                message["reasoning_content"] = thought
    # Drop anything after the last reply: nothing learns from it.
    while messages and messages[-1]["role"] != "assistant":
        messages.pop()
    replies = [i for i, m in enumerate(messages) if m["role"] == "assistant"]
    if not replies:
        return []
    # How many old turns the backend left out when it asked for each reply.
    windows: list[tuple[int, list[int]]] = []
    for index in replies:
        dropped = dropped_turns(messages[:index], budget, step)
        if windows and windows[-1][0] == dropped:
            windows[-1][1].append(index)
        else:
            windows.append((dropped, [index]))

    tools = openai_tools(episode.tools)
    meta = {"goal": episode.goal, "task": episode.task, "backend": episode.backend, "episode": episode.path.name,
            "turns": episode.turns, "exact": episode.exact}
    out = []
    for n, (dropped, learn) in enumerate(windows):
        last = learn[-1]
        prefix = messages[:last]
        window = list(prefix[:2])
        if dropped:
            window.append({"role": "user", "content": TRIMMED_NOTE.format(n=dropped)})
        # Message positions grouped by turn, exactly as turn_groups splits them.
        groups = turn_groups([{"role": m["role"], "i": i} for i, m in enumerate(prefix)])
        for i in [m["i"] for group in groups[dropped:] for m in group]:
            message = dict(messages[i])
            if message["role"] == "assistant" and i not in learn:
                message["train"] = False  # learnt in an earlier window
            window.append(message)
        window.append(dict(messages[last]))
        out.append({"messages": window, "tools": tools, "meta": {**meta, "window": n}})
    return out


@dataclass
class ExportInfo:
    episodes: int
    kept: int
    skipped: dict[str, int]
    train_samples: int
    val_samples: int
    train_episodes: int
    val_episodes: int
    replies: int
    approx_tokens: int
    longest_tokens: int
    inexact: int
    budget: int
    files: dict[str, str]
    #: Goal-to-plan examples added to the training file (see ``plans.py``).
    plan_samples: int = 0


def export(episodes: list[Episode], out_dir: Path, filters: Optional[Filters] = None, *, val_fraction: float = 0.1,
           budget: int = DEFAULT_BUDGET, thinking: bool = False, seed: int = 0, bundle: bool = True,
           plan_rows: Optional[list[dict[str, Any]]] = None) -> ExportInfo:
    """Writes ``train.jsonl``, ``val.jsonl``, ``info.json`` and, with ``bundle``, a zip to upload to Colab.

    ``plan_rows`` are goal-to-plan examples (``plans.examples``), added to the training file."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    selection = select(episodes, filters)
    kept = list(selection.kept)
    # Split by episode, so windows of one episode stay together. Stable for a given seed and set.
    random.Random(seed).shuffle(kept)
    n_val = 0
    if len(kept) >= 5 and val_fraction > 0:
        n_val = max(1, round(len(kept) * val_fraction))
    val, train = kept[:n_val], kept[n_val:]

    stats = {"replies": 0, "chars": 0, "longest": 0}

    def write(name: str, chosen: list[Episode]) -> int:
        count = 0
        with (out_dir / name).open("w", encoding="utf-8") as f:
            for episode in chosen:
                for sample in samples(episode, budget, thinking=thinking):
                    f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                    count += 1
                    chars = len(json.dumps(sample["messages"], ensure_ascii=False)) + len(json.dumps(sample["tools"]))
                    stats["chars"] += chars
                    stats["longest"] = max(stats["longest"], chars)
                    stats["replies"] += sum(1 for m in sample["messages"]
                                            if m["role"] == "assistant" and m.get("train", True))
        return count

    n_train = write("train.jsonl", train)
    n_val_samples = write("val.jsonl", val)
    plan_rows = plan_rows or []
    if plan_rows:
        with (out_dir / "train.jsonl").open("a", encoding="utf-8") as f:
            for row in plan_rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                chars = len(json.dumps(row["messages"], ensure_ascii=False)) + len(json.dumps(row["tools"]))
                stats["chars"] += chars
                stats["replies"] += 1
        n_train += len(plan_rows)
    files = {"train": str(out_dir / "train.jsonl"), "val": str(out_dir / "val.jsonl")}
    info = ExportInfo(
        episodes=len(selection.kept) + sum(selection.skipped.values()), kept=len(kept),
        skipped=dict(selection.skipped.most_common()), train_samples=n_train, val_samples=n_val_samples,
        train_episodes=len(train), val_episodes=len(val), replies=stats["replies"],
        approx_tokens=stats["chars"] // 4, longest_tokens=stats["longest"] // 4,
        inexact=sum(1 for e in kept if not e.exact), budget=budget, files=files, plan_samples=len(plan_rows))
    (out_dir / "info.json").write_text(json.dumps(info.__dict__, indent=2), encoding="utf-8")
    files["info"] = str(out_dir / "info.json")
    if bundle:
        script = resources.files(__package__).joinpath("finetune.py").read_text(encoding="utf-8")
        zip_path = out_dir / "minegpt-data.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("train.jsonl", "val.jsonl", "info.json"):
                z.write(out_dir / name, name)
            z.writestr("finetune.py", script)
        files["zip"] = str(zip_path)
    return info
