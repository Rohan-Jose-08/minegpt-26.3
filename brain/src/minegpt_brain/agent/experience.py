"""What happened on earlier goals: the plan that was used, how it went, and what got in the way.

After every goal a short record is kept: the goal, the situation it started from (tools carried,
where), the plan as it stood at the end, the outcome, and the trouble met on the way. When a new
goal comes in, the records most like it, by wording and by starting situation, are shown to the
model, successes and failures both. A plan that worked from the same start is the best guide
there is; a failure says what to avoid.

The same records feed training: ``python -m minegpt_brain.train plans`` turns the verified
successes into examples of breaking a goal down.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from ..knowledge.graph import short, tool_tier
from .routines import tokens

MAX_RECORDS = 600
_TIERS = ("no pickaxe", "a wooden pickaxe", "a stone pickaxe", "an iron pickaxe", "a diamond pickaxe",
          "a netherite pickaxe")


def situation(inventory: Mapping[str, int], y: float, dimension: Optional[str]) -> dict[str, Any]:
    """The start of a goal in a few words that decide what plan fits."""
    tier = max((tool_tier(item) for item, n in inventory.items() if n > 0), default=-1)
    top = sorted(((short(item), n) for item, n in inventory.items() if n > 0), key=lambda kv: -kv[1])[:8]
    return {"tier": tier, "dimension": short(dimension or "overworld"),
            "depth": "deep" if y < 0 else "underground" if y < 50 else "surface",
            "carrying": dict(top), "empty": not inventory}


def situation_text(s: Mapping[str, Any]) -> str:
    tier = _TIERS[max(-1, min(int(s.get("tier", -1)), 4)) + 1]
    carrying = ", ".join(f"{n} {item}" for item, n in list(s.get("carrying", {}).items())[:5])
    where = s.get("depth", "surface") + ("" if s.get("dimension") in (None, "overworld") else f" in {s['dimension']}")
    return f"started on the {where}" .replace("on the deep", "deep down").replace("on the underground", "underground") \
        + f" with {tier}" + (f", carrying {carrying}" if carrying else ", carrying nothing")


class ExperiencePool:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.records: list[dict[str, Any]] = json.loads(self.path.read_text()).get("records", [])
        except (FileNotFoundError, json.JSONDecodeError, AttributeError):
            self.records = []

    def add(self, goal: str, world: str, start: Mapping[str, Any], outcome: str, verified: bool, turns: int,
            seconds: float, plan: Optional[dict[str, Any]], outline: list[str], problems: list[str],
            summary: str, backend: str = "") -> dict[str, Any]:
        record = {"goal": goal, "world": world, "time": time.strftime("%Y-%m-%d %H:%M"), "situation": dict(start),
                  "outcome": outcome, "verified": verified, "turns": turns, "seconds": round(seconds),
                  "plan": plan, "outline": outline[:30], "problems": problems[:6], "summary": summary[:400],
                  "backend": backend}
        self.records.append(record)
        del self.records[:-MAX_RECORDS]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"records": self.records}, indent=1))
            tmp.replace(self.path)
        except OSError:
            pass
        return record

    def similar(self, goal: str, start: Mapping[str, Any], k: int = 2,
                min_score: float = 0.34) -> list[dict[str, Any]]:
        """The records most like this goal and start: the best success and, if there is one, a failure."""
        words = set(tokens(goal))
        if not words:
            return []
        scored = []
        for record in self.records:
            if not record.get("outline") and not record.get("problems"):
                continue
            other = set(tokens(record.get("goal", "")))
            if not other:
                continue
            score = len(words & other) / len(words | other)
            if score < min_score:
                continue
            theirs = record.get("situation", {})
            if theirs.get("tier") == start.get("tier"):
                score += 0.15
            if theirs.get("dimension") == start.get("dimension"):
                score += 0.05
            scored.append((score, record))
        scored.sort(key=lambda s: -s[0])
        wins = [r for _, r in scored if r["outcome"] == "success"]
        losses = [r for _, r in scored if r["outcome"] in ("failed", "turn_limit")]
        picked = wins[:1] + losses[:1] + wins[1:]
        return picked[:k]

    @staticmethod
    def describe(record: Mapping[str, Any]) -> list[str]:
        outcome = {"success": "worked", "failed": "was given up", "turn_limit": "ran out of turns"}.get(
            record.get("outcome", ""), record.get("outcome", ""))
        lines = [f"{record.get('goal')!r} {outcome} after {record.get('turns', '?')} turns; "
                 f"{situation_text(record.get('situation', {}))}."]
        outline = record.get("outline") or []
        if outline:
            lines.append("  Plan at the end: " + " | ".join(line.strip() for line in outline[:12]))
        if record.get("problems"):
            lines.append("  Trouble on the way: " + "; ".join(record["problems"][:3]))
        if record.get("outcome") != "success" and record.get("summary"):
            lines.append("  Why it stopped: " + record["summary"])
        return lines
