"""Drills: single skill calls on set ground, run without the model, so practice costs no tokens.

A drill resets the player like a benchmark task (fresh land, empty inventory, a kit), may build
a little trouble with commands (a pit, a pool), then runs one skill straight through the bridge
and checks the result in the game: items gained, or how close the player got to a spot. They are
what the tuner learns from most, since a night can hold hundreds of them, and the fixed-seed set
of :data:`GATE` is the quick check that a night of tuning did not make things worse.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from ..agent import state
from ..bench import Task
from ..bench.__main__ import reset

if TYPE_CHECKING:  # pragma: no cover
    from ..agent.loop import Agent

Start = tuple[int, int, int]


@dataclass
class Drill:
    name: str
    skill: str
    #: The skill's arguments from the start position and a random source (for the direction of a walk).
    args: Callable[[Start, random.Random], dict[str, Any]]
    kit: dict[str, int] = field(default_factory=dict)
    #: Commands run after the reset, with ~ relative to the player.
    setup: list[str] = field(default_factory=list)
    #: Items that must be gained: patterns like ``*_log``.
    gain: dict[str, int] = field(default_factory=dict)
    #: Within this many blocks (on x and z) of the goto target at the end.
    near: Optional[float] = None
    #: Seconds the brain waits for the result at most (the mod has its own limits).
    timeout: float = 400.0
    #: What the drill is about, for the report.
    about: str = ""


def _walk(distance: tuple[int, int]) -> Callable[[Start, random.Random], dict[str, Any]]:
    def args(start: Start, rng: random.Random) -> dict[str, Any]:
        angle = rng.uniform(0, 2 * math.pi)
        d = rng.randint(*distance)
        return {"x": start[0] + round(d * math.cos(angle)), "z": start[2] + round(d * math.sin(angle))}
    return args


def _fixed(**kwargs: Any) -> Callable[[Start, random.Random], dict[str, Any]]:
    return lambda start, rng: dict(kwargs)


#: A pit three deep with stone walls, the player at its bottom.
_PIT = ["fill ~-2 ~-4 ~-2 ~2 ~-1 ~2 minecraft:stone", "fill ~ ~-3 ~ ~ ~-1 ~ minecraft:air", "tp @s ~ ~-3 ~"]
#: A pool five across and three deep with stone round it, the player in the middle.
_POOL = ["fill ~-4 ~-4 ~-4 ~4 ~-1 ~4 minecraft:stone", "fill ~-2 ~-3 ~-2 ~2 ~-1 ~2 minecraft:water",
         "tp @s ~ ~-2 ~"]
#: Tall grass and torches round the player: the soft blocks that used to cause jump loops.
_CLUTTER = ["fill ~-3 ~-1 ~-3 ~3 ~-1 ~3 minecraft:grass_block", "fill ~-3 ~ ~-3 ~3 ~2 ~3 minecraft:air",
            "fill ~-3 ~ ~-3 ~3 ~ ~3 minecraft:short_grass", "setblock ~1 ~ ~ minecraft:torch",
            "setblock ~ ~ ~1 minecraft:torch", "setblock ~-1 ~ ~ minecraft:poppy"]

DRILLS: dict[str, Drill] = {d.name: d for d in [
    Drill("walk", "goto", _walk((40, 80)), near=3, timeout=200, about="walk 40 to 80 blocks over open land"),
    Drill("climb_out", "goto", lambda s, r: {"x": s[0] + 8, "z": s[2]}, kit={"dirt": 16, "stone_pickaxe": 1},
          setup=_PIT, near=3, timeout=200, about="get out of a 3-deep pit and walk off"),
    Drill("swim_out", "goto", lambda s, r: {"x": s[0] + 9, "z": s[2]}, kit={"dirt": 16, "stone_pickaxe": 1},
          setup=_POOL, near=3, timeout=200, about="get out of a walled pool"),
    Drill("pillar_up", "goto", lambda s, r: {"x": s[0], "y": s[1] + 6, "z": s[2]}, kit={"dirt": 16},
          setup=_CLUTTER, near=1, timeout=200, about="pillar 6 up from grass and torches"),
    Drill("logs", "mine", _fixed(block="#minecraft:logs", count=6), gain={"*_log": 6}, about="chop 6 logs"),
    Drill("stone", "mine", _fixed(block="minecraft:stone", count=12), kit={"wooden_pickaxe": 1},
          gain={"cobblestone": 12}, about="mine 12 stone with a wooden pickaxe"),
    Drill("coal", "mine", _fixed(block=["minecraft:coal_ore", "minecraft:deepslate_coal_ore"], count=3),
          kit={"stone_pickaxe": 1, "torch": 16}, gain={"coal": 3}, timeout=500, about="find and mine 3 coal"),
    Drill("iron", "mine", _fixed(block=["minecraft:iron_ore", "minecraft:deepslate_iron_ore"], count=3, tunnel=True),
          kit={"stone_pickaxe": 1, "torch": 16, "dirt": 16}, gain={"raw_iron": 3}, timeout=700,
          about="find and mine 3 iron, tunnelling"),
]}

#: The quick check run at the start and end of a night: every drill twice, same starting spots each time.
GATE = ["walk", "climb_out", "swim_out", "pillar_up", "logs", "stone"] * 2
GATE_SEED = 2026


def run_drill(agent: "Agent", drill: Drill, rng: random.Random, do_reset: bool = True) -> dict[str, Any]:
    """Runs one drill and says how it went: passed, seconds, the skill's message."""
    bridge = agent.bridge
    if do_reset:
        reset(bridge, agent, Task(drill.name, "drill", drill.about, 1, kit=drill.kit, setup=drill.setup), rng)
        time.sleep(0.5)
    before = agent.observe()
    start = state.block_pos(before)
    args = drill.args(start, rng)
    began = time.monotonic()
    result = agent._skill(drill.skill, {**args, "timeout": drill.timeout} if drill.skill != "goto" else dict(args))
    seconds = time.monotonic() - began
    agent.poll_events()
    after = agent.observe()
    passed, detail = check(drill, args, before, after)
    return {"kind": "drill", "task": drill.name, "skill": drill.skill, "passed": passed, "detail": detail,
            "message": result.message[:200], "seconds": round(seconds, 1), "time": time.strftime("%Y-%m-%d %H:%M:%S")}


def check(drill: Drill, args: dict[str, Any], before: Any, after: Any) -> tuple[bool, str]:
    problems = []
    gained = state.delta(state.counts(before), state.counts(after))
    for pattern, wanted in drill.gain.items():
        got = state.count_matching(gained, pattern)
        if got < wanted:
            problems.append(f"gained {got} of {wanted} {pattern}")
    if drill.near is not None:
        x, y, z = after.pos
        dx, dz = x - (args["x"] + 0.5), z - (args["z"] + 0.5)
        off = math.hypot(dx, dz)
        if off > drill.near or ("y" in args and y < args["y"] - 0.5):
            problems.append(f"ended {off:.1f} blocks off")
    return not problems, "; ".join(problems) or "ok"
