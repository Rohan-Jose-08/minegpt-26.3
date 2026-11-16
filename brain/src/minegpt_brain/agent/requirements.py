"""Works out everything a set of items takes: what to gather, what to craft and smelt, in order.

The game only tells the client about recipes the player has unlocked, so a model planning a
big job (a house, a set of armour) would have to walk the recipe tree a call at a time and do
the sums itself. This module carries the common survival recipes and does it in one go::

    resolve({"oak_door": 1, "glass_pane": 8, "iron_pickaxe": 1}, inventory)

returns the raw materials still to gather (with how to get each and the tool it takes), the
crafting and smelting steps in a workable order, and how much fuel the smelting burns. What the
player already carries is used first, at every level of the tree. It is a planning aid: the
craft skill still uses the game's own recipes, so an item missing from the table here is just
listed as something to find out about with the recipe tool.
"""

from __future__ import annotations

import fnmatch
import math
from dataclasses import dataclass, field
from typing import Mapping, Optional

from .state import full, short

#: item -> (how many one craft makes, ingredients, where: "hand", "table" or "furnace").
Recipe = tuple[int, dict[str, int], str]

WOODS = ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry")
TIERS = ("wooden", "stone", "iron", "diamond", "netherite")
#: What each tier of tool is made from.
_TIER_MATERIAL = {"wooden": "#planks", "stone": "cobblestone", "iron": "iron_ingot", "diamond": "diamond"}

RECIPES: dict[str, Recipe] = {
    "stick": (4, {"#planks": 2}, "hand"),
    "crafting_table": (1, {"#planks": 4}, "hand"),
    "chest": (1, {"#planks": 8}, "table"),
    "barrel": (1, {"#planks": 6, "#wooden_slabs": 2}, "table"),
    "ladder": (3, {"stick": 7}, "table"),
    "torch": (4, {"coal": 1, "stick": 1}, "hand"),
    "furnace": (1, {"cobblestone": 8}, "table"),
    "white_bed": (1, {"white_wool": 3, "#planks": 3}, "table"),
    "bowl": (4, {"#planks": 3}, "table"),
    "shield": (1, {"#planks": 6, "iron_ingot": 1}, "table"),
    "bucket": (1, {"iron_ingot": 3}, "table"),
    "shears": (1, {"iron_ingot": 2}, "hand"),
    "flint_and_steel": (1, {"iron_ingot": 1, "flint": 1}, "hand"),
    "bread": (1, {"wheat": 3}, "table"),
    "bow": (1, {"stick": 3, "string": 3}, "table"),
    "arrow": (4, {"flint": 1, "stick": 1, "feather": 1}, "table"),
    "fishing_rod": (1, {"stick": 3, "string": 2}, "table"),
    "iron_block": (1, {"iron_ingot": 9}, "table"),
    "iron_bars": (16, {"iron_ingot": 6}, "table"),
    "cobblestone_stairs": (4, {"cobblestone": 6}, "table"),
    "cobblestone_slab": (6, {"cobblestone": 3}, "table"),
    "cobblestone_wall": (6, {"cobblestone": 6}, "table"),
    "stone_bricks": (4, {"stone": 4}, "table"),
    "stone_brick_stairs": (4, {"stone_bricks": 6}, "table"),
    "stone_brick_slab": (6, {"stone_bricks": 3}, "table"),
    "stone_slab": (6, {"stone": 3}, "table"),
    "sandstone": (1, {"sand": 4}, "hand"),
    "cut_sandstone": (4, {"sandstone": 4}, "table"),
    "sandstone_stairs": (4, {"sandstone": 6}, "table"),
    "sandstone_slab": (6, {"sandstone": 3}, "table"),
    "glass_pane": (16, {"glass": 6}, "table"),
    "bricks": (1, {"brick": 4}, "hand"),
    # Smelting.
    "stone": (1, {"cobblestone": 1}, "furnace"),
    "smooth_stone": (1, {"stone": 1}, "furnace"),
    "glass": (1, {"sand": 1}, "furnace"),
    "brick": (1, {"clay_ball": 1}, "furnace"),
    "iron_ingot": (1, {"raw_iron": 1}, "furnace"),
    "gold_ingot": (1, {"raw_gold": 1}, "furnace"),
    "copper_ingot": (1, {"raw_copper": 1}, "furnace"),
    "charcoal": (1, {"#logs": 1}, "furnace"),
    "cooked_beef": (1, {"beef": 1}, "furnace"),
    "cooked_porkchop": (1, {"porkchop": 1}, "furnace"),
    "cooked_mutton": (1, {"mutton": 1}, "furnace"),
    "cooked_chicken": (1, {"chicken": 1}, "furnace"),
    "baked_potato": (1, {"potato": 1}, "furnace"),
    # Armour.
    "iron_helmet": (1, {"iron_ingot": 5}, "table"),
    "iron_chestplate": (1, {"iron_ingot": 8}, "table"),
    "iron_leggings": (1, {"iron_ingot": 7}, "table"),
    "iron_boots": (1, {"iron_ingot": 4}, "table"),
    "diamond_helmet": (1, {"diamond": 5}, "table"),
    "diamond_chestplate": (1, {"diamond": 8}, "table"),
    "diamond_leggings": (1, {"diamond": 7}, "table"),
    "diamond_boots": (1, {"diamond": 4}, "table"),
}
for _wood in WOODS:
    _log = "mangrove_log" if _wood == "mangrove" else f"{_wood}_log"
    RECIPES[f"{_wood}_planks"] = (4, {_log: 1}, "hand")
    RECIPES[f"{_wood}_stairs"] = (4, {f"{_wood}_planks": 6}, "table")
    RECIPES[f"{_wood}_slab"] = (6, {f"{_wood}_planks": 3}, "table")
    RECIPES[f"{_wood}_door"] = (3, {f"{_wood}_planks": 6}, "table")
    RECIPES[f"{_wood}_trapdoor"] = (2, {f"{_wood}_planks": 6}, "table")
    RECIPES[f"{_wood}_fence"] = (3, {f"{_wood}_planks": 4, "stick": 2}, "table")
    RECIPES[f"{_wood}_fence_gate"] = (1, {f"{_wood}_planks": 2, "stick": 4}, "table")
    RECIPES[f"{_wood}_boat"] = (1, {f"{_wood}_planks": 5}, "table")
for _tier, _material in _TIER_MATERIAL.items():
    for _tool, (_heads, _sticks) in {"pickaxe": (3, 2), "axe": (3, 2), "shovel": (1, 2), "hoe": (2, 2),
                                     "sword": (2, 1)}.items():
        RECIPES[f"{_tier}_{_tool}"] = (1, {_material: _heads, "stick": _sticks}, "table")

#: Tags used as ingredients: the patterns that count towards them, and what to make when short.
TAGS: dict[str, tuple[str, str]] = {
    "#planks": ("*_planks", "oak_planks"),
    "#logs": ("*_log", "oak_log"),
    "#wooden_slabs": ("*_slab", "oak_slab"),
}

#: Things that are gathered, not made: how to get them, and the pickaxe tier it takes (or None).
SOURCES: dict[str, tuple[str, Optional[str]]] = {
    "cobblestone": ("mine stone (drops cobblestone) or cobblestone", "wooden"),
    "coal": ("mine #minecraft:coal_ores, often exposed on hillsides and in caves; or smelt logs into charcoal",
             "wooden"),
    "raw_iron": ("mine #minecraft:iron_ores, in caves and cliffs from y=0 to y=60", "stone"),
    "raw_copper": ("mine #minecraft:copper_ores", "stone"),
    "raw_gold": ("mine #minecraft:gold_ores, below y=32", "iron"),
    "diamond": ("mine #minecraft:diamond_ores, deep down around y=-58", "iron"),
    "redstone": ("mine #minecraft:redstone_ores, deep down", "iron"),
    "sand": ("mine sand, by water and in deserts", None),
    "gravel": ("mine gravel", None),
    "flint": ("mine gravel; about one block in ten drops flint", None),
    "clay_ball": ("mine clay, under shallow water", None),
    "dirt": ("mine dirt or grass_block", None),
    "white_wool": ("fight minecraft:sheep (1 wool each), or shear them with shears for more", None),
    "string": ("fight minecraft:spider, or break cobwebs", None),
    "feather": ("fight minecraft:chicken", None),
    "leather": ("fight minecraft:cow", None),
    "beef": ("fight minecraft:cow", None),
    "porkchop": ("fight minecraft:pig", None),
    "mutton": ("fight minecraft:sheep", None),
    "chicken": ("fight minecraft:chicken", None),
    "wheat": ("farm it: wheat_seeds from breaking grass, planted on tilled soil near water", None),
    "potato": ("farm it, or find it in a village", None),
    "wheat_seeds": ("mine short_grass or tall_grass until seeds drop", None),
}
for _wood in WOODS:
    SOURCES["mangrove_log" if _wood == "mangrove" else f"{_wood}_log"] = (
        f"mine {_wood} trees (or any logs with #minecraft:logs, and use that wood instead)", None)

#: Items one unit of fuel smelts.
FUELS = {"coal": 8.0, "charcoal": 8.0, "*_planks": 1.5, "*_log": 1.5, "stick": 0.5}


@dataclass
class Step:
    how: str  # "craft" or "smelt"
    item: str
    count: int
    table: bool = False


@dataclass
class Requirements:
    #: Raw items still to gather: short id -> count.
    gather: dict[str, int] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)
    smelts: int = 0
    #: Items with no recipe here and no known source.
    unknown: list[str] = field(default_factory=list)
    #: Tools added because gathering needs them.
    tools: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.gather or self.steps or self.unknown)

    def describe(self, inventory: Optional[Mapping[str, int]] = None) -> str:
        if self.empty():
            return "You already carry everything for that."
        lines = []
        if self.tools:
            lines.append("Tools to make first, because the gathering needs them: " + ", ".join(self.tools) + ".")
        if self.gather:
            lines.append("Gather:")
            for item, n in self.gather.items():
                source = SOURCES.get(item)
                how = f": {source[0]}" if source else ""
                tool = f" (needs a {source[1]} pickaxe or better)" if source and source[1] else ""
                lines.append(f"- {n} {item}{how}{tool}")
        if self.steps:
            lines.append("Then, in this order:")
            for step in self.steps:
                where = " (crafting table)" if step.table else ""
                lines.append(f"- {step.how} {step.count} {step.item}{where}")
        if any(step.table for step in self.steps) or self.smelts:
            lines.append("craft and smelt use a crafting table or furnace within 24 blocks, or make and place "
                         "one themselves if you carry 4 planks or 8 cobblestone more.")
        if self.smelts:
            fuel = fuel_available(inventory or {})
            lines.append(f"Smelting {self.smelts} items burns {math.ceil(self.smelts / 8)} coal or charcoal, or "
                         f"{math.ceil(self.smelts / 1.5)} planks or logs"
                         + (f"; the fuel you carry smelts about {int(fuel)}." if inventory is not None else "."))
        if self.unknown:
            lines.append("No recipe known here for: " + ", ".join(self.unknown) + ". Check them with the recipe "
                         "tool, or find them in the world.")
        return "\n".join(lines)


def fuel_available(inventory: Mapping[str, int]) -> float:
    total = 0.0
    for item, n in inventory.items():
        for pattern, each in FUELS.items():
            if fnmatch.fnmatchcase(short(item), pattern):
                total += n * each
                break
    return total


def _best_pickaxe(stock: Mapping[str, int]) -> int:
    """The tier index of the best pickaxe in stock, or -1."""
    best = -1
    for i, tier in enumerate(TIERS):
        if stock.get(f"{tier}_pickaxe", 0) > 0:
            best = i
    return best


def resolve(wanted: Mapping[str, int], inventory: Mapping[str, int]) -> Requirements:
    """What it takes to end up with ``wanted`` (item -> count), given what is carried.

    Item ids may be short (``oak_planks``) or full (``minecraft:oak_planks``). The inventory is
    used up as the tree is walked, so 10 planks carried cover a chest and a bit, not every recipe
    that asks for planks.
    """
    stock: dict[str, int] = {}
    for item, n in inventory.items():
        stock[short(item)] = stock.get(short(item), 0) + n
    out = Requirements()
    steps: dict[tuple[str, str], Step] = {}
    # One kind of wood for everything that takes "any planks": the one being asked for, else one carried.
    names = [short(full(item)) for item in wanted] + list(stock)
    wood = next((w for name in names for w in sorted(WOODS, key=len, reverse=True) if name.startswith(w + "_")), "oak")

    def take(item: str, n: int) -> int:
        """Uses up to n of an item (or of a tag) from stock; returns how many are still missing."""
        if item in TAGS:
            pattern = TAGS[item][0]
            for name in [k for k in stock if fnmatch.fnmatchcase(k, pattern)]:
                used = min(n, stock[name])
                stock[name] -= used
                n -= used
                if n == 0:
                    break
            return n
        used = min(n, stock.get(item, 0))
        stock[item] = stock.get(item, 0) - used
        return n - used

    def concrete(tag: str) -> str:
        """The item to make for a tag, in the wood this plan uses."""
        return TAGS[tag][1].replace("oak", wood)

    def need(item: str, n: int, depth: int = 0) -> None:
        n = take(item, n)
        if n <= 0:
            return
        if item in TAGS:
            item = concrete(item)
        recipe = RECIPES.get(item)
        if recipe is None or depth > 12:
            source = SOURCES.get(item)
            if source is None:
                if item not in out.unknown:
                    out.unknown.append(item)
            elif source[1] is not None and _best_pickaxe(stock) < TIERS.index(source[1]):
                tool = f"{source[1]}_pickaxe"
                if tool not in out.tools:
                    out.tools.append(tool)
                need(tool, 1, depth + 1)
                # Made by the time the gathering starts; keep it in stock so it is only made once.
                stock[tool] = stock.get(tool, 0) + 1
            out.gather[item] = out.gather.get(item, 0) + n
            return
        makes, ingredients, where = recipe
        crafts = math.ceil(n / makes)
        for ingredient, each in ingredients.items():
            need(ingredient, each * crafts, depth + 1)
        how = "smelt" if where == "furnace" else "craft"
        if how == "smelt":
            out.smelts += crafts
        key = (how, item)
        if key in steps:
            steps[key].count += crafts * makes
        else:
            steps[key] = Step(how, item, crafts * makes, table=where == "table")
        # What one craft makes beyond what was asked for is there for the next recipe.
        stock[item] = stock.get(item, 0) + crafts * makes - n

    for item, n in wanted.items():
        if isinstance(n, int) and n > 0:
            need(short(full(item)), n)
    out.steps = list(steps.values())
    return out
