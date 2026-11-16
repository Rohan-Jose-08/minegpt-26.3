"""The benchmark suite: a fixed set of tasks, from punching trees to diamonds, farms and builds.

Each task is a goal for the agent plus a check the benchmark runs itself at the end, so a
task passes only when the game agrees, whatever the agent claims. In singleplayer (or on a
server that allows it) the runner resets the player between tasks with commands: survival
mode, empty inventory, a fresh spot far from the last one, the task's starting kit, and any
fixture the task needs (a chest, a field of ripe wheat, a bed).

Results go to ``bench/results.jsonl`` in the data folder, one line per run, labelled with the
brain version and model, so runs from different versions can be compared with
``python -m minegpt_brain.bench --report``.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Optional

from ..agent import critic
from ..agent.state import full
from ..observation import Observation


@dataclass
class Task:
    name: str
    tier: str
    #: The goal given to the agent. ``{x} {y} {z}`` become the start position, ``{x+2}`` and the like offsets.
    goal: str
    turns: int = 40
    #: A critic condition (have, wearing, near) that must hold at the end.
    check: Optional[dict[str, Any]] = None
    #: Items that must be down to at most this many (for storing tasks).
    at_most: dict[str, int] = field(default_factory=dict)
    #: Blocks that must be in place: [dx, dy, dz, item pattern] from the start position.
    blocks: list[tuple[int, int, int, str]] = field(default_factory=list)
    #: Share of ``blocks`` that must match.
    min_fraction: float = 0.9
    #: The time of day must be morning at the end (for sleeping).
    morning: bool = False
    #: Items given at the start.
    kit: dict[str, int] = field(default_factory=dict)
    #: Commands run after moving the player, with ~ relative to where it stands.
    setup: list[str] = field(default_factory=list)
    #: Move to untouched land before starting.
    fresh: bool = True
    #: Time of day to set at the start, or None to leave it.
    time: Optional[int] = 1000

    def goal_text(self, start: tuple[int, int, int]) -> str:
        return format_goal(self.goal, start)


def format_goal(text: str, start: tuple[int, int, int]) -> str:
    """Fills in ``{x}``, ``{y+1}``, ``{z-3}`` and the like."""
    import re

    def sub(match: "re.Match[str]") -> str:
        axis, sign, n = match.group(1), match.group(2), match.group(3)
        value = start["xyz".index(axis)]
        if sign:
            value += int(n) if sign == "+" else -int(n)
        return str(value)

    return re.sub(r"\{([xyz])(?:([+-])(\d+))?\}", sub, text)


def _square(dx: int, dz: int, size: int, dy: int, item: str) -> list[tuple[int, int, int, str]]:
    return [(dx + i, dy, dz + j, item) for i in range(size) for j in range(size)]


def _ring(dx: int, dz: int, size: int, dy: int, item: str) -> list[tuple[int, int, int, str]]:
    return [(dx + i, dy, dz + j, item) for i in range(size) for j in range(size)
            if i in (0, size - 1) or j in (0, size - 1)]


#: Levels the ground for builds: grass one below the player's feet, air above, from 2 blocks behind
#: to 9 blocks ahead on x and z, so a build task tests building rather than the local terrain.
_FLAT_SITE = ["fill ~-2 ~-2 ~-2 ~9 ~-2 ~9 minecraft:dirt", "fill ~-2 ~-1 ~-2 ~9 ~-1 ~9 minecraft:grass_block",
              "fill ~-2 ~ ~-2 ~9 ~6 ~9 minecraft:air"]
#: Room round the chest next to the player, so it can be reached and opened wherever the last task ended.
_CHEST_ROOM = ["fill ~1 ~ ~-1 ~3 ~2 ~1 minecraft:air", "fill ~1 ~-1 ~-1 ~3 ~-1 ~1 minecraft:cobblestone replace #minecraft:replaceable"]

_WHEAT_FIELD = ["fill ~2 ~-1 ~2 ~6 ~-1 ~6 minecraft:farmland", "setblock ~4 ~-1 ~4 minecraft:water",
                "fill ~2 ~ ~2 ~6 ~ ~6 minecraft:wheat[age=7]", "setblock ~4 ~ ~4 minecraft:air"]

TASKS: list[Task] = [
    # Wood ------------------------------------------------------------------------------------
    Task("logs", "wood", "Collect 8 logs.", 20, {"have": {"*_log": 8}}),
    Task("planks", "wood", "Make 16 planks.", 20, {"have": {"*_planks": 16}}),
    Task("crafting_table", "wood", "Make a crafting table and keep it in the inventory.", 20,
         {"have": {"crafting_table": 1}}),
    Task("wooden_pickaxe", "wood", "Make a wooden pickaxe.", 25, {"have": {"wooden_pickaxe": 1}}),
    Task("torches", "wood", "Make 8 torches.", 40, {"have": {"torch": 8}}),
    # Stone -----------------------------------------------------------------------------------
    Task("cobblestone", "stone", "Collect 16 cobblestone.", 35, {"have": {"cobblestone": 16}}),
    Task("stone_pickaxe", "stone", "Make a stone pickaxe.", 40, {"have": {"stone_pickaxe": 1}}),
    Task("furnace", "stone", "Make a furnace and keep it in the inventory.", 40, {"have": {"furnace": 1}}),
    Task("coal", "stone", "Collect 5 coal.", 50, {"have": {"coal": 5}}),
    # Iron ------------------------------------------------------------------------------------
    Task("raw_iron", "iron", "Collect 3 raw iron.", 70, {"have": {"raw_iron": 3}}),
    Task("smelt_iron", "iron", "Smelt the raw iron you carry into 8 iron ingots.", 25,
         {"have": {"iron_ingot": 8}}, kit={"raw_iron": 8, "coal": 2, "cobblestone": 8}),
    Task("iron_pickaxe", "iron", "Make an iron pickaxe.", 90, {"have": {"iron_pickaxe": 1}}),
    Task("shield", "iron", "Make a shield.", 90, {"have": {"shield": 1}}),
    Task("iron_armor", "iron", "Get a full set of iron armour and wear it.", 150,
         {"wearing": ["iron_helmet", "iron_chestplate", "iron_leggings", "iron_boots"]}),
    Task("diamond", "iron", "Mine a diamond.", 150, {"have": {"diamond": 1}}),
    # Long jobs: many steps that depend on each other ----------------------------------------------
    Task("iron_kit", "long", "Get an iron pickaxe, an iron sword and a shield.", 120,
         {"have": {"iron_pickaxe": 1, "iron_sword": 1, "shield": 1}}),
    Task("diamond_pickaxe", "long", "Make a diamond pickaxe.", 200, {"have": {"diamond_pickaxe": 1}}),
    Task("bed_and_bucket", "long", "Make a bed and a bucket.", 120, {"have": {"*_bed": 1, "bucket": 1}}),
    # Food ------------------------------------------------------------------------------------
    Task("cooked_meat", "food", "Hunt animals and cook 3 pieces of meat.", 50, {"have": {"cooked_*": 3}}),
    Task("bread", "food", "Make 2 bread from the wheat you carry.", 15, {"have": {"bread": 2}},
         kit={"wheat": 6}, fresh=False),
    # Farming ---------------------------------------------------------------------------------
    Task("harvest", "farm", "Harvest the ripe wheat next to you and replant it.", 15, {"have": {"wheat": 12}},
         blocks=[b for b in _square(2, 2, 5, 0, "wheat") if b[:3] != (4, 0, 4)], min_fraction=0.8,
         setup=_WHEAT_FIELD),
    Task("new_farm", "farm", "Make new farmland with the hoe next to the water east of you, and plant 8 wheat "
         "seeds on it.", 25, at_most={"wheat_seeds": 8}, kit={"wooden_hoe": 1, "wheat_seeds": 16},
         setup=["fill ~2 ~-1 ~-2 ~6 ~-1 ~2 minecraft:grass_block", "fill ~2 ~ ~-2 ~6 ~3 ~2 minecraft:air",
                "setblock ~4 ~-1 ~ minecraft:water"]),
    # Storage ---------------------------------------------------------------------------------
    Task("store", "storage", "Put the cobblestone and dirt you carry into the chest next to you.", 10,
         at_most={"cobblestone": 0, "dirt": 0}, kit={"cobblestone": 64, "dirt": 48, "stone_pickaxe": 1},
         setup=_CHEST_ROOM + ["setblock ~2 ~ ~ minecraft:chest"], fresh=False),
    Task("take", "storage", "Take 10 iron ingots from the chest next to you.", 10, {"have": {"iron_ingot": 10}},
         setup=_CHEST_ROOM + ['setblock ~2 ~ ~ minecraft:chest{Items:[{Slot:0b,id:"minecraft:iron_ingot",count:16}]}'],
         fresh=False),
    # Building --------------------------------------------------------------------------------
    Task("platform", "build", "Build a 5 by 5 floor of cobblestone at height {y}, from {x+2} {z+2} to "
         "{x+6} {z+6}.", 15, blocks=_square(2, 2, 5, 0, "cobblestone"), kit={"cobblestone": 30}, setup=_FLAT_SITE),
    Task("tower", "build", "Build a pillar of dirt 6 blocks tall at {x+3} {y} {z}.", 12,
         blocks=[(3, dy, 0, "dirt") for dy in range(6)], kit={"dirt": 12}, setup=_FLAT_SITE),
    Task("hut", "build", "Build a small hut with its north-west corner at {x+3} {y} {z+3}: 5 by 5 cobblestone "
         "walls 3 high with a doorway, and a flat oak plank roof on top.", 30,
         blocks=_ring(3, 3, 5, 0, "cobblestone") + _ring(3, 3, 5, 1, "cobblestone") + _ring(3, 3, 5, 2, "cobblestone")
         + _square(3, 3, 5, 3, "oak_planks"), min_fraction=0.85,
         kit={"cobblestone": 64, "oak_planks": 32, "dirt": 32}, setup=_FLAT_SITE),
    # Survival --------------------------------------------------------------------------------
    Task("sleep", "survival", "Sleep in the bed next to you until morning.", 8, morning=True,
         setup=["setblock ~2 ~ ~ minecraft:red_bed[facing=east,part=foot]",
                "setblock ~3 ~ ~ minecraft:red_bed[facing=east,part=head]"], time=13000, fresh=False),
    Task("bed", "survival", "Make a bed.", 60, {"have": {"*_bed": 1}}),
]


def get(names: list[str]) -> list[Task]:
    """Tasks by name or tier, in suite order; ``all`` for everything."""
    if not names or names == ["all"]:
        return list(TASKS)
    wanted = set(names)
    known = {t.name for t in TASKS} | {t.tier for t in TASKS}
    unknown = wanted - known
    if unknown:
        raise ValueError(f"unknown tasks or tiers: {', '.join(sorted(unknown))}")
    return [t for t in TASKS if t.name in wanted or t.tier in wanted]


def check(task: Task, start: tuple[int, int, int], end: Observation,
          blocks: Optional[list[Optional[str]]] = None) -> tuple[bool, str]:
    """Whether the task is done in the final observation (and the blocks at :func:`block_positions`)."""
    problems = critic.unmet(task.check, end) if task.check else []
    totals: dict[str, int] = {}
    for slot in end.inventory:
        if slot["slot"] < 36:
            totals[slot["id"]] = totals.get(slot["id"], 0) + slot["count"]
    for pattern, most in task.at_most.items():
        have = sum(n for item, n in totals.items() if fnmatch.fnmatchcase(item, full(pattern)))
        if have > most:
            problems.append(f"still carrying {have} {pattern} (at most {most})")
    if task.blocks:
        wanted = [full(b[3]) for b in task.blocks]
        found = blocks or []
        matched = sum(1 for want, got in zip(wanted, found) if got and fnmatch.fnmatchcase(got, want))
        if matched < task.min_fraction * len(wanted):
            problems.append(f"{matched} of {len(wanted)} blocks in place (need {task.min_fraction:.0%})")
    if task.morning:
        tod = int(end.data.get("dayTime") or 0) % 24000
        if not (tod < 2000 or tod > 23000):
            problems.append(f"it is not morning (time {tod})")
    return not problems, "; ".join(problems) or "ok"


def block_positions(task: Task, start: tuple[int, int, int]) -> list[list[int]]:
    return [[start[0] + dx, start[1] + dy, start[2] + dz] for dx, dy, dz, _ in task.blocks]
