"""Training examples for breaking a goal down, made from the record of earlier goals.

The episode export (``dataset.py``) teaches a model the whole conversation, turn by turn. This
one teaches the single hardest reply: given a goal and where the player starts, the plan to
set. Each example is short (a few hundred tokens), so a small model can learn it from a few
hundred of them on a free Colab GPU, and the plan it then sets is kept on track by the game's
own checks.

Examples come from every ``experience.json`` under the data folder (the agent's own, and those
of benchmark and collection runs): goals the game confirmed as done, with the plan as it stood
at the end, cleaned of what happened along the way.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..agent.experience import ExperiencePool, situation_text
from ..agent.tools import tool_specs

SYSTEM = ("You are MineGPT, playing Minecraft in survival mode. Given a goal and where the player starts, set "
          "the plan: steps in order, each with a done_when the game can check where there is one, substeps "
          "for big steps, and a goal_check that holds only when the whole goal is done.")
_KEEP = ("step", "done_when", "substeps", "kind")


def clean_step(raw: dict[str, Any]) -> dict[str, Any]:
    """A saved plan step as the model would have written it: no record of what was done or failed."""
    out: dict[str, Any] = {"step": raw.get("step", "")}
    if raw.get("done_when"):
        out["done_when"] = raw["done_when"]
    if raw.get("substeps"):
        out["substeps"] = [clean_step(s) for s in raw["substeps"]]
        if raw.get("kind") == "any":
            out["any"] = True
    return out


def example(record: dict[str, Any]) -> Optional[dict[str, Any]]:
    plan = record.get("plan") or {}
    steps = plan.get("steps") or []
    if not steps or not plan.get("goal_check"):
        return None
    args = {"steps": [clean_step(s) for s in steps], "goal_check": plan["goal_check"]}
    user = f"Goal: {record['goal']}\nThe player {situation_text(record.get('situation', {}))}."
    spec = next(t for t in tool_specs() if t.name == "set_plan")
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "set_plan", "arguments": args}}]},
        ],
        "tools": [{"type": "function", "function": {"name": spec.name, "description": spec.description,
                                                    "parameters": spec.parameters}}],
        "meta": {"goal": record["goal"], "turns": record.get("turns"), "backend": record.get("backend", "")},
    }


def records(data_dir: Path) -> list[dict[str, Any]]:
    """Every goal on record under the data folder, at most three folders down."""
    data_dir = Path(data_dir)
    files = [p for depth in ("", "*/", "*/*/", "*/*/*/") for p in sorted(data_dir.glob(depth + "experience.json"))]
    return [r for path in files for r in ExperiencePool(path).records]


def examples(data_dir: Path, unverified: bool = False) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The plan examples under the data folder, and counts of what was kept and why the rest was not."""
    found = records(data_dir)
    stats = {"records": len(found), "kept": 0, "not a success": 0, "not confirmed by the game": 0,
             "no checkable plan": 0, "repeats": 0}
    seen: set[str] = set()
    lines = []
    for record in found:
        if record.get("outcome") != "success":
            stats["not a success"] += 1
            continue
        if not record.get("verified") and not unverified:
            stats["not confirmed by the game"] += 1
            continue
        made = example(record)
        if made is None:
            stats["no checkable plan"] += 1
            continue
        key = json.dumps(made["messages"][1:], sort_keys=True)
        if key in seen:
            stats["repeats"] += 1
            continue
        seen.add(key)
        lines.append(made)
    stats["kept"] = len(lines)
    return lines, stats


def export(data_dir: Path, out: Path, unverified: bool = False) -> dict[str, int]:
    """Writes ``plans.jsonl`` under ``out``. Returns the counts from ``examples``."""
    rows, stats = examples(data_dir, unverified)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    (out / "plans.jsonl").write_text(text + ("\n" if rows else ""), encoding="utf-8")
    return stats
