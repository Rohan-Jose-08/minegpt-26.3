"""Convenient access to the observation JSON described in docs/protocol.md."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

#: Player inventory slot numbers, as used in observations.
HOTBAR_SLOTS = range(0, 9)
MAIN_SLOTS = range(9, 36)
ARMOR_SLOTS = range(36, 40)
OFFHAND_SLOT = 40


@dataclass
class Observation:
    """One observation from the mod. The raw JSON is in :attr:`data`."""

    data: dict[str, Any]
    tick: int = 0
    mode: str = "idle"
    #: False when the actions sent with this step were ignored because the agent was paused.
    applied: bool = True

    # Player ---------------------------------------------------------------------------

    @property
    def player(self) -> dict[str, Any]:
        return self.data["player"]

    @property
    def pos(self) -> tuple[float, float, float]:
        x, y, z = self.player["pos"]
        return (x, y, z)

    @property
    def health(self) -> float:
        return self.player["health"]

    @property
    def food(self) -> int:
        return self.player["food"]

    def distance_to(self, x: float, y: float, z: float) -> float:
        px, py, pz = self.pos
        return math.dist((px, py, pz), (x, y, z))

    # Inventory ------------------------------------------------------------------------

    @property
    def inventory(self) -> list[dict[str, Any]]:
        return self.data["inventory"]["slots"]

    def count(self, item_id: str) -> int:
        """How many of an item the player carries, e.g. ``count("minecraft:oak_log")``."""
        item_id = _namespaced(item_id)
        return sum(s["count"] for s in self.inventory if s["id"] == item_id)

    def find_slot(self, item_id: str) -> Optional[int]:
        """The first inventory slot holding an item, or None."""
        item_id = _namespaced(item_id)
        return next((s["slot"] for s in self.inventory if s["id"] == item_id), None)

    # World ----------------------------------------------------------------------------

    @property
    def entities(self) -> list[dict[str, Any]]:
        return self.data["entities"]

    @property
    def target(self) -> dict[str, Any]:
        return self.data["target"]

    @property
    def has_grid(self) -> bool:
        return "grid" in self.data

    def block_at(self, dx: int, dy: int, dz: int) -> Optional[str]:
        """Block id at an offset from the player's feet, or None if outside the grid or unloaded."""
        grid = self.data.get("grid")
        if grid is None:
            raise ValueError("this observation has no block grid; request one with grid=True")
        r, below, above = grid["radius"], grid["below"], grid["above"]
        if not (-r <= dx <= r and -r <= dz <= r and -below <= dy <= above):
            return None
        side = 2 * r + 1
        index = (dy + below) * side * side + (dz + r) * side + (dx + r)
        cell = grid["cells"][index]
        return grid["palette"][cell] if cell >= 0 else None

    def find_blocks(self, block_id: str) -> list[tuple[int, int, int]]:
        """World positions of every grid block with this id, nearest first."""
        grid = self.data.get("grid")
        if grid is None:
            raise ValueError("this observation has no block grid; request one with grid=True")
        block_id = _namespaced(block_id)
        try:
            wanted = grid["palette"].index(block_id)
        except ValueError:
            return []
        r, below = grid["radius"], grid["below"]
        side = 2 * r + 1
        ox, oy, oz = grid["origin"]
        found = []
        for index, cell in enumerate(grid["cells"]):
            if cell == wanted:
                dy, rest = divmod(index, side * side)
                dz, dx = divmod(rest, side)
                found.append((ox + dx - r, oy + dy - below, oz + dz - r))
        px, py, pz = self.pos
        found.sort(key=lambda p: (p[0] + 0.5 - px) ** 2 + (p[1] + 0.5 - py) ** 2 + (p[2] + 0.5 - pz) ** 2)
        return found


def _namespaced(item_id: str) -> str:
    return item_id if ":" in item_id else f"minecraft:{item_id}"
