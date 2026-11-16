"""How the skills have actually done: how often each kind of call works and how long it takes.

Every skill run is counted under a key that says what it was for (``mine:iron_ore``,
``kill:cow``, ``craft``, ``goto``). The averages replace the planner's rough guesses of how long
things take, so its choice between ways of getting an item follows what happens in this player's
worlds, and they give the time estimates shown with a plan.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from ..knowledge.graph import short

#: Runs needed before an average is trusted.
MIN_RUNS = 3


def _first_block(spec: Any) -> str:
    one = spec[0] if isinstance(spec, list) and spec else spec
    name = short(str(one or ""))
    return "#logs" if name in ("#logs", "#logs_that_burn") else name


def key_for(tool: str, args: Mapping[str, Any]) -> str:
    if tool == "mine":
        return f"mine:{_first_block(args.get('block'))}"
    if tool == "fight":
        return f"kill:{short(str(args.get('entity') or 'hostile'))}"
    return tool


class SkillStats:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.data: dict[str, dict[str, float]] = json.loads(self.path.read_text()).get("skills", {})
        except (FileNotFoundError, json.JSONDecodeError, AttributeError):
            self.data = {}
        self._unsaved = 0

    def record(self, tool: str, args: Mapping[str, Any], success: bool, seconds: float,
               data: Optional[Mapping[str, Any]] = None) -> None:
        units = 1.0
        if tool == "mine":
            units = float((data or {}).get("broken") or args.get("count") or 1)
        elif tool == "smelt":
            units = float(args.get("count") or 1)
        keys = [key_for(tool, args)]
        if tool == "mine" and isinstance(args.get("block"), list):
            keys = [f"mine:{short(str(b))}" for b in args["block"]]
        for key in dict.fromkeys(keys):
            entry = self.data.setdefault(key, {"runs": 0, "ok": 0, "seconds": 0.0, "units": 0.0})
            entry["runs"] += 1
            if success:
                entry["ok"] += 1
                entry["seconds"] = round(entry["seconds"] + max(0.0, seconds), 1)
                entry["units"] += max(1.0, units)
        self._unsaved += 1
        if self._unsaved >= 5:
            self.save()

    def rate(self, key: str) -> Optional[float]:
        """The share of runs that worked, or None while there are too few to say."""
        entry = self.data.get(key)
        if not entry or entry["runs"] < MIN_RUNS:
            return None
        return entry["ok"] / entry["runs"]

    def measured(self) -> dict[str, float]:
        """Seconds per unit (a block mined, a mob killed, an item smelted, a call) by key."""
        out = {}
        for key, entry in self.data.items():
            if entry["ok"] >= MIN_RUNS and entry["units"] > 0:
                out[key] = round(entry["seconds"] / entry["units"], 2)
        return out

    def save(self) -> None:
        self._unsaved = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"skills": self.data}, indent=1, sort_keys=True))
            tmp.replace(self.path)
        except OSError:
            pass
