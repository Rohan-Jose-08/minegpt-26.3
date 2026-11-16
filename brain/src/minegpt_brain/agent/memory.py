"""What the agent remembers about each world: its base, ore spots, chests, deaths and notes.

Everything lives in one JSON file, keyed by the world id the mod reports (the save folder in
singleplayer, the server address in multiplayer), so memories from one world never leak into
another.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Optional

KINDS = ("base", "ore", "chest", "landmark", "danger", "death", "note")
#: Entries kept per world; the oldest go first, except the base.
MAX_ENTRIES = 300


class WorldMemory:
    def __init__(self, path: Path, world: str):
        self.path = Path(path)
        self.world = world
        self._data = _load(self.path)
        self.entries: list[dict[str, Any]] = self._data.setdefault("worlds", {}).setdefault(world, {}).setdefault(
            "entries", [])

    def add(self, kind: str, note: str, pos: Optional[tuple[int, int, int]] = None,
            dimension: Optional[str] = None) -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        entry: dict[str, Any] = {"kind": kind, "note": note, "time": time.strftime("%Y-%m-%d %H:%M")}
        if pos is not None:
            entry["pos"] = [int(v) for v in pos]
        if dimension:
            entry["dimension"] = dimension
        if kind == "base":
            # One base per world: a new one replaces the old.
            self.entries[:] = [e for e in self.entries if e["kind"] != "base"]
        elif pos is not None:
            # A second note on the same spot replaces the first.
            self.entries[:] = [e for e in self.entries
                               if not (e["kind"] == kind and e.get("pos") and _dist(e["pos"], pos) < 4)]
        self.entries.append(entry)
        while len(self.entries) > MAX_ENTRIES:
            oldest = next(i for i, e in enumerate(self.entries) if e["kind"] != "base")
            del self.entries[oldest]
        self.save()
        return entry

    def relevant(self, pos: Optional[tuple[float, float, float]] = None, dimension: Optional[str] = None,
                 limit: int = 15) -> list[dict[str, Any]]:
        """The base, the last few deaths, then the nearest other entries in this dimension."""
        def here(e: dict[str, Any]) -> bool:
            return dimension is None or e.get("dimension") in (None, dimension)

        base = [e for e in self.entries if e["kind"] == "base"]
        deaths = [e for e in self.entries if e["kind"] == "death"][-3:]
        others = [e for e in self.entries if e["kind"] not in ("base", "death") and here(e)]
        if pos is not None:
            others.sort(key=lambda e: _dist(e["pos"], pos) if e.get("pos") else 1e9)
        else:
            others.reverse()
        return (base + deaths + others)[:limit]

    def describe(self, pos: Optional[tuple[float, float, float]] = None, dimension: Optional[str] = None,
                 limit: int = 15) -> str:
        entries = self.relevant(pos, dimension, limit)
        if not entries:
            return "Nothing yet: this is a world you have not played before."
        lines = []
        for e in entries:
            where = ""
            if e.get("pos"):
                x, y, z = e["pos"]
                where = f" at {x} {y} {z}"
                if pos is not None:
                    where += f" ({_dist(e['pos'], pos):.0f}m away)"
            lines.append(f"- {e['kind']}{where}: {e['note']}")
        return "\n".join(lines)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1))
        tmp.replace(self.path)


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        # Keep the damaged file for a human to look at, and start over.
        path.replace(path.with_suffix(".broken"))
        return {}


def _dist(a: Any, b: Any) -> float:
    return math.dist(tuple(a)[:3], tuple(b)[:3])
