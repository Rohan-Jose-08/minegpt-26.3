"""Example designs to start from, and the designs that were built and checked in this world.

A model designs better from a worked example of about the right size and style than from a
blank page. The examples here all compile and pass the design review (the tests see to that).
Designs the agent builds itself are added to ``designs.json`` in the data folder once the
check against the plan passes, so what worked once can be found again.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import design


@dataclass(frozen=True)
class Example:
    name: str
    #: Themes and styles it suits.
    styles: tuple[str, ...]
    #: The pattern it shows, in a few words.
    pattern: str
    program: str
    #: True for one this agent built and checked in the game.
    built: bool = False


EXAMPLES: tuple[Example, ...] = (
    Example("cottage_with_wing", ("cottage", "oak", "birch_lodge", "birch", "cherry_house", "cherry"),
            "L shape: a hall with a lower wing set back, a porch and a chimney",
            """build cottage_with_wing
mass hall box(9,4,7)
mass wing box(5,3,5) attach hall.west align=back
roof hall gable(overhang=1) ; roof wing gable(overhang=1)
facade hall.south -> [window, door_porch, window]
detail hall chimney at east"""),
    Example("small_cabin", ("cabin", "spruce", "cottage", "oak", "savanna_hut", "acacia"),
            "one low hall with a lean-to shed on a short side",
            """build small_cabin
mass hall box(9,4,6)
mass shed box(4,3,5) attach hall.east align=back
roof hall gable(overhang=1) ; roof shed lean(to=west)
facade hall.south -> [window, door_porch, window]
facade shed.south -> [opening]
detail hall chimney at west
detail shed furnish [chest, chest, barrel]"""),
    Example("first_house", ("cottage", "oak", "spruce", "birch", "jungle_lodge", "jungle", "swamp_house", "mangrove"),
            "the smallest house that is not a box: a 7x6 hall with a porch and a hip roof",
            """build first_house
mass hall box(7,3,6)
roof hall hip(overhang=1)
facade hall.south -> [window, door_porch, window]
detail hall chimney at north"""),
    Example("t_house", ("cottage", "oak", "dark_oak", "manor", "cherry_house"),
            "T shape: a long hall with a gabled wing coming forward from the middle, the door in the wing",
            """build t_house
mass hall box(11,4,6)
mass front box(5,3,5) attach hall.south align=center
roof hall gable(overhang=1) ; roof front gable(overhang=1, ridge=z)
facade front.south -> [door_porch]
facade hall.north -> [window, window, window]
detail hall chimney at east"""),
    Example("manor", ("manor", "dark_oak", "stone_brick", "cobblestone"),
            "a tall two-floor hall with two matching wings, symmetric about the door",
            """build manor
mass hall box(11,7,9) floors=2
mass west_wing box(6,4,7) attach hall.west align=center
mirror west_wing as east_wing
roof hall hip(overhang=1) ; roof west_wing gable(overhang=1, ridge=x) ; roof east_wing gable(overhang=1, ridge=x)
facade hall.south -> [tall_window, door_porch, tall_window]
facade hall.north -> [window, wall, window]
detail hall chimney at north
detail west_wing furnish [chest, chest, chest, chest]
detail east_wing furnish [furnace, furnace, crafting_table]"""),
    Example("desert_court", ("desert_house", "sandstone"),
            "flat-roofed blocks of different heights pushed together, with parapets",
            """build desert_court style=desert_house
mass hall box(9,5,7)
mass side box(5,4,5) attach hall.east align=front
mass store box(5,3,4) attach hall.north align=west
roof hall flat(parapet=1) ; roof side flat(parapet=1) ; roof store flat
facade hall.south -> [window, door, window]
facade side.east -> [window]
detail store furnish [chest, chest]"""),
    Example("keep", ("keep", "stone_brick", "cobblestone"),
            "a square stone hall with a taller two-floor tower on a corner, buttresses on the long wall",
            """build keep style=keep
mass hall box(9,5,9)
mass tower box(5,9,5) attach hall.east align=back floors=2
roof hall hip(overhang=1) ; roof tower flat(parapet=1)
facade hall.south -> [tall_window, door, tall_window]
facade hall.west -> [buttress, tall_window, buttress]
facade tower.east -> [tall_window]
detail hall chimney at north"""),
    Example("barn", ("cottage", "oak", "spruce", "cabin", "savanna_hut", "acacia"),
            "a tall barn with an open doorway and a low lean-to along one side, for storage and animals",
            """build barn
mass barn box(7,5,9)
mass stall box(4,3,7) attach barn.west align=center
roof barn gable(overhang=1, ridge=z) ; roof stall lean(to=east)
facade barn.south -> [opening]
facade barn.north -> [wide_window]
facade stall.south -> [opening]
detail barn furnish [chest, chest, chest, chest, barrel, barrel]"""),
)


class DesignLibrary:
    """Designs built and checked in the game, kept in ``designs.json``."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.saved: list[dict] = []
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.saved = [d for d in data if isinstance(d, dict) and d.get("program")]
            except (ValueError, OSError):
                self.saved = []

    def add(self, name: str, program: str, style: Optional[str], width: int, depth: int) -> bool:
        """Keeps a design that was built. Returns False if the same program is already kept."""
        text = "\n".join(line.strip() for line in program.strip().splitlines() if line.strip())
        if any(d["program"] == text for d in self.saved) or any(e.program == text for e in EXAMPLES):
            return False
        self.saved = [d for d in self.saved if d.get("name") != name][-39:]
        self.saved.append({"name": name, "program": text, "style": style, "width": width, "depth": depth,
                           "time": int(time.time())})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.saved, indent=1))
        return True

    def examples(self) -> list[Example]:
        return [Example(d.get("name", "design"), tuple(s for s in [d.get("style")] if s),
                        "built and checked in this world", d["program"], built=True) for d in self.saved]


def find(width: Optional[int] = None, depth: Optional[int] = None, style: Optional[str] = None,
         library: Optional[DesignLibrary] = None, limit: int = 3) -> list[tuple[Example, tuple[int, int], int]]:
    """The examples nearest a size and style, best first, each with its footprint and block count.
    One that fits inside the size asked for beats one that does not; then the style, then how close."""
    pool = list(EXAMPLES) + (library.examples() if library else [])
    scored = []
    style = (style or "").strip().lower()
    for example in pool:
        try:
            made = design.compile_design(example.program).structure
        except design.DesignError:
            continue
        w, d = made.footprint()
        fits = [(w, d), (d, w)]       # it can be turned
        too_big = min(max(0, a - (width or 99)) + max(0, b - (depth or 99)) for a, b in fits)
        gap = min(abs(a - (width or a)) + abs(b - (depth or b)) for a, b in fits)
        matches = bool(style) and style in example.styles
        scored.append(((too_big > 0, not matches, gap, not example.built), example, (w, d),
                       sum(made.materials().values())))
    scored.sort(key=lambda s: s[0])
    return [(example, size, blocks) for _, example, size, blocks in scored[:limit]]
