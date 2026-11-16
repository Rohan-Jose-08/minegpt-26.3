"""What each skill needs and what it changes, and a dry run of a list of skill calls.

This is MineGPT's world model: not a picture of the game, but its rules. Crafting and smelting
follow recipes exactly, a block needs a certain pickaxe and drops certain items, a mob drops
certain items. Given what the player carries and a list of skill calls, ``simulate`` plays the
calls forward on paper and reports the first one that cannot work ("craft stone_pickaxe: 3
cobblestone short"), what the player would carry at the end, and about how long it takes.

It is used before a saved routine is replayed, when routines are offered for a goal, and to
check plans. Mining and hunting are luck in the game; here they give their average yield, and
the game's own checks stay the last word.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from ..knowledge.graph import ANY_LOGS, ANY_PLANKS, Knowledge, short, tool_tier

_TIER_NAMES = ("a wooden", "a stone", "an iron", "a diamond", "a netherite")
#: Seconds a call takes when nothing has been measured: (fixed, per unit).
DEFAULT_SECONDS: dict[str, tuple[float, float]] = {
    "mine": (5.0, 8.0), "craft": (2.0, 0.0), "smelt": (6.0, 10.5), "fight": (25.0, 0.0), "goto": (20.0, 0.0),
    "collect": (6.0, 0.0), "place": (3.0, 0.0), "equip": (1.0, 0.0), "eat": (2.0, 0.0), "build": (60.0, 0.0),
    "farm": (40.0, 0.0), "store": (8.0, 0.0), "take": (8.0, 0.0), "sleep": (15.0, 0.0),
}


@dataclass
class Simulation:
    #: Problems found, as (number of the call from 1, what is wrong).
    problems: list[tuple[int, str]] = field(default_factory=list)
    inventory: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    #: Calls whose outcome is luck or cannot be worked out here (walking, fighting, building).
    uncertain: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems

    def first(self) -> Optional[str]:
        if not self.problems:
            return None
        n, text = self.problems[0]
        return f"step {n}: {text}"


def _option(spec: str) -> str:
    """A skill's item argument as the knowledge names it."""
    name = short(spec)
    if name == "#planks":
        return ANY_PLANKS
    if name in ("#logs", "#logs_that_burn"):
        return ANY_LOGS
    return name


def _carried(k: Knowledge, stock: Mapping[str, int], option: str) -> int:
    return sum(n for item, n in stock.items() if n > 0 and k.matches(item, k.generic(option)))


def _take(k: Knowledge, stock: dict[str, int], option: str, n: int) -> int:
    option = k.generic(option)
    for item in [i for i, have in stock.items() if have > 0 and k.matches(i, option)]:
        used = min(n, stock[item])
        stock[item] -= used
        n -= used
        if n == 0:
            break
    return n


def _blocks(k: Knowledge, spec: Any) -> list[str]:
    """The block ids a mine call's block argument covers."""
    out: list[str] = []
    for one in (spec if isinstance(spec, list) else [spec]):
        if not isinstance(one, str):
            continue
        name = short(one)
        if name.startswith("#"):
            out += k.block_tags.get(name[1:], [])
        else:
            out.append(name)
    return out


def _mine(k: Knowledge, stock: dict[str, int], args: Mapping[str, Any]) -> Optional[str]:
    blocks = _blocks(k, args.get("block"))
    count = int(args.get("count", 1) or 1)
    best = max((tool_tier(item) for item, n in stock.items() if n > 0), default=-1)
    usable = []
    lowest = 99
    for block in blocks:
        need = k.tools.get(block, {})
        tier = int(need.get("tier", 0)) if need.get("tool") == "pickaxe" else -1
        lowest = min(lowest, tier)
        if tier <= best:
            usable.append(block)
    if blocks and not usable:
        return f"mining {blocks[0]} needs {_TIER_NAMES[min(lowest, 4)]} pickaxe or better, and none is carried by then"
    for block in usable[:1]:
        for item, each in k.blocks.get(block, {}).items():
            got = math.floor(each * count)
            if got:
                stock[item] = stock.get(item, 0) + got
    return None


def _craft(k: Knowledge, stock: dict[str, int], args: Mapping[str, Any]) -> Optional[str]:
    item = _option(str(args.get("item", "")))
    # The skill makes this many more, whatever is carried already.
    missing = int(args.get("count", 1) or 1)
    recipes = [r for r in k.recipes_for(item) if r.how == "craft"]
    if not recipes and item.startswith("#"):
        members = k.members(item)
        recipes = [r for m in members[:1] for r in k.recipes_for(m) if r.how == "craft"]
    if not recipes:
        return None  # nothing known about it: leave it to the game
    shortfalls = []
    for recipe in recipes:
        crafts = math.ceil(missing / recipe.n)
        lacking = []
        for options, n in recipe.inputs:
            have = sum(_carried(k, stock, o) for o in options)
            if have < n * crafts:
                lacking.append(f"{n * crafts - have} {k.cheapest(options)}")
        if not lacking:
            for options, n in recipe.inputs:
                left = n * crafts
                for option in options:
                    left = _take(k, stock, option, left)
                    if left == 0:
                        break
            made = recipe.out if not recipe.out.startswith("#") else "oak_planks"
            stock[made] = stock.get(made, 0) + crafts * recipe.n
            return None
        shortfalls.append(lacking)
    best = min(shortfalls, key=len)
    return f"craft {item}: " + ", ".join(best) + " short"


def _smelt(k: Knowledge, stock: dict[str, int], args: Mapping[str, Any]) -> Optional[str]:
    source = _option(str(args.get("item", "")))
    count = int(args.get("count", 1) or 1)
    have = _carried(k, stock, source)
    if have <= 0:
        return f"smelt {source}: none carried by then"
    count = min(count, have)
    result = None
    for recipes in k.recipes.values():
        for recipe in recipes:
            if recipe.how == "smelt" and any(k.generic(o) == k.generic(source) or k.matches(source, k.generic(o))
                                             for o in recipe.inputs[0][0]):
                result = recipe.out
                break
        if result:
            break
    _take(k, stock, source, count)
    if result:
        stock[result] = stock.get(result, 0) + count
    return None


def _fight(k: Knowledge, stock: dict[str, int], args: Mapping[str, Any]) -> None:
    mob = short(str(args.get("entity", "")))
    for item, each in k.mobs.get(mob, {}).items():
        if each >= 0.5:
            stock[item] = stock.get(item, 0) + max(1, math.floor(each))


def seconds_for(tool: str, args: Mapping[str, Any], measured: Optional[Mapping[str, float]] = None) -> float:
    """About how long a call takes: measured where there is a figure, else a rough default."""
    fixed, per = DEFAULT_SECONDS.get(tool, (10.0, 0.0))
    units = int(args.get("count", 1) or 1) if tool in ("mine", "smelt") else 1
    if measured:
        from .skillstats import key_for

        key = key_for(tool, args)
        if key in measured:
            return measured[key] * units
    return fixed + per * units


def simulate(calls: list[Mapping[str, Any]], inventory: Mapping[str, int], knowledge: Knowledge,
             measured: Optional[Mapping[str, float]] = None) -> Simulation:
    """Plays ``calls`` (``{"tool", "args"}``) forward from ``inventory`` without touching the game."""
    sim = Simulation()
    stock: dict[str, int] = {}
    for item, n in inventory.items():
        stock[short(item)] = stock.get(short(item), 0) + int(n)
    for i, call in enumerate(calls, 1):
        tool = call.get("tool", "")
        args = call.get("args") or {}
        problem: Optional[str] = None
        if tool == "mine":
            problem = _mine(knowledge, stock, args)
            sim.uncertain += 1
        elif tool == "craft":
            problem = _craft(knowledge, stock, args)
        elif tool == "smelt":
            problem = _smelt(knowledge, stock, args)
        elif tool == "fight":
            _fight(knowledge, stock, args)
            sim.uncertain += 1
        elif tool == "place":
            item = _option(str(args.get("item", "")))
            if _take(knowledge, stock, item, 1):
                problem = f"place {item}: none carried by then"
        else:
            sim.uncertain += 1
        if problem:
            sim.problems.append((i, problem))
        sim.seconds += seconds_for(tool, args, measured)
    sim.inventory = {item: n for item, n in stock.items() if n > 0}
    return sim
