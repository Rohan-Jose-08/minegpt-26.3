"""A map of what has been seen where: ore, trees, water, lava and useful blocks, by area.

``memory.py`` holds the notes the model chooses to write. This map fills itself: every time the
agent looks for blocks (``look_around``, ``find_blocks``, the scan at the start of a goal), what
it found is filed under the 16 by 16 column it is in. Later goals can then ask "where did I see
iron?" and walk straight there instead of searching again. When a search right next to a
remembered spot comes back empty, the spot is forgotten: the ore was mined.

One file holds all worlds, keyed like the memory by the world id the mod reports.
"""

from __future__ import annotations

import fnmatch
import json
import math
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from ..knowledge.graph import short

CELL = 16
#: Cells kept per world; the ones not seen for longest go first.
MAX_CELLS = 4000
#: Blocks not worth a place on the map: they are everywhere.
COMMON = ("stone", "cobblestone", "deepslate", "andesite", "diorite", "granite", "dirt", "grass_block")


def _cell(pos: Iterable[float]) -> tuple[int, int]:
    x, _, z = pos
    return math.floor(x / CELL), math.floor(z / CELL)


class WorldMap:
    def __init__(self, path: Path, world: str):
        self.path = Path(path)
        self.world = world
        try:
            self._data: dict[str, Any] = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}
        if not isinstance(self._data, dict):
            self._data = {}
        self.cells: dict[str, dict[str, Any]] = self._data.setdefault("worlds", {}).setdefault(world, {})
        self._dirty = False

    @staticmethod
    def _key(dimension: Optional[str], cell: tuple[int, int]) -> str:
        return f"{short(dimension or 'overworld')}|{cell[0]}|{cell[1]}"

    def note_found(self, found: Iterable[dict[str, Any]], dimension: Optional[str]) -> None:
        """Files the results of a block search (``pos`` and ``block`` each)."""
        now = int(time.time())
        seen: dict[tuple[str, str], list[list[int]]] = {}
        for entry in found:
            pos, block = entry.get("pos"), short(str(entry.get("block", "")))
            if not pos or not block or block in COMMON:
                continue
            seen.setdefault((self._key(dimension, _cell(pos)), block), []).append([int(v) for v in pos])
        for (key, block), places in seen.items():
            cell = self.cells.setdefault(key, {"seen": {}})
            old = cell["seen"].get(block, {})
            cell["seen"][block] = {"n": max(len(places), int(old.get("n", 0))), "pos": places[0], "t": now}
            cell["t"] = now
            self._dirty = True

    def note_absent(self, blocks: Iterable[str], pos: Iterable[float], radius: float,
                    dimension: Optional[str]) -> None:
        """A search for these blocks from ``pos`` found none: forget the spots well inside its reach."""
        px, py, pz = pos
        patterns = [short(b) for b in blocks]
        prefix = short(dimension or "overworld") + "|"
        for key, cell in self.cells.items():
            if not key.startswith(prefix):
                continue
            for block in [b for b in cell["seen"] if any(fnmatch.fnmatchcase(b, p) for p in patterns)]:
                x, y, z = cell["seen"][block]["pos"]
                if math.dist((x, y, z), (px, py, pz)) <= max(4.0, radius * 0.6):
                    del cell["seen"][block]
                    self._dirty = True

    def nearest(self, patterns: Iterable[str], pos: Iterable[float], dimension: Optional[str],
                limit: int = 3, beyond: float = 0.0) -> list[dict[str, Any]]:
        """Remembered places with a block matching any pattern (``*`` wildcards), nearest first."""
        px, py, pz = pos
        wanted = [short(p) for p in patterns]
        prefix = short(dimension or "overworld") + "|"
        out = []
        for key, cell in self.cells.items():
            if not key.startswith(prefix):
                continue
            for block, info in cell["seen"].items():
                if any(fnmatch.fnmatchcase(block, p) for p in wanted):
                    distance = math.dist(info["pos"], (px, py, pz))
                    if distance >= beyond:
                        out.append({"block": block, "pos": list(info["pos"]), "n": info["n"],
                                    "distance": distance, "t": info["t"]})
        out.sort(key=lambda e: e["distance"])
        return out[:limit]

    def lines(self, pos: Iterable[float], dimension: Optional[str], limit: int = 6,
              patterns: Iterable[str] = ("*_ore", "ancient_debris")) -> list[str]:
        """The nearest remembered ore, one line each, for the model."""
        pos = tuple(pos)
        best: dict[str, dict[str, Any]] = {}
        for entry in self.nearest(patterns, pos, dimension, limit=200, beyond=24.0):
            kind = entry["block"].replace("deepslate_", "")
            best.setdefault(kind, entry)
        out = []
        for kind, e in sorted(best.items(), key=lambda kv: kv[1]["distance"])[:limit]:
            x, y, z = e["pos"]
            out.append(f"{kind} seen at {x} {y} {z} ({e['distance']:.0f} blocks away)")
        return out

    def save(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        if len(self.cells) > MAX_CELLS:
            for key in sorted(self.cells, key=lambda k: self.cells[k].get("t", 0))[: len(self.cells) - MAX_CELLS]:
                del self.cells[key]
        for key in [k for k, c in self.cells.items() if not c.get("seen")]:
            del self.cells[key]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, separators=(",", ":")))
            tmp.replace(self.path)
        except OSError:
            pass
