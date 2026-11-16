"""The recipe and tech-tree graph: every way to come by an item, and what each way takes.

Nodes are items (and tags such as ``#planks``). Edges are the ways to get one:

- ``craft`` and ``smelt``: a recipe from the game's own data, with its ingredients;
- ``mine``: a block found in the world drops it, perhaps needing a pickaxe of some tier;
- ``kill``: a mob drops it.

Each way has a cost in seconds, so the planner can pick the cheapest. The costs start as rough
guesses (below) and are replaced by what MineGPT measures as it plays (``agent/skillstats.py``).
The data comes from ``knowledge/build.py``; a file for the current game version ships with the brain.
"""

from __future__ import annotations

import fnmatch
import gzip
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

DATA_DIR = Path(__file__).parent / "data"
TIERS = ("wooden", "stone", "iron", "diamond", "netherite")

#: Blocks worth going out to mine, with seconds to find and break one. Anything else that drops
#: an item (a placed furnace, a block of iron) is not a source: someone has to have made it.
#: Patterns are matched in order; the first hit wins.
NATURAL: tuple[tuple[str, float], ...] = (
    ("*_log", 5), ("*_stem", 8), ("stone", 3), ("cobblestone", 3), ("deepslate", 5), ("cobbled_deepslate", 5),
    ("dirt", 2), ("grass_block", 2), ("sand", 2), ("red_sand", 10), ("gravel", 3), ("clay", 15),
    ("andesite", 6), ("diorite", 6), ("granite", 6), ("tuff", 8), ("calcite", 30), ("sandstone", 8),
    ("short_grass", 2), ("tall_grass", 2), ("fern", 3), ("*_leaves", 2), ("cobweb", 200),
    ("coal_ore", 15), ("deepslate_coal_ore", 40), ("copper_ore", 20), ("deepslate_copper_ore", 40),
    ("iron_ore", 30), ("deepslate_iron_ore", 30), ("gold_ore", 90), ("deepslate_gold_ore", 90),
    ("redstone_ore", 60), ("deepslate_redstone_ore", 40), ("lapis_ore", 120), ("deepslate_lapis_ore", 100),
    ("diamond_ore", 240), ("deepslate_diamond_ore", 180), ("emerald_ore", 400), ("deepslate_emerald_ore", 400),
    ("nether_quartz_ore", 20), ("nether_gold_ore", 30), ("ancient_debris", 600),
    ("obsidian", 120), ("netherrack", 2), ("soul_sand", 10), ("glowstone", 40), ("basalt", 8),
    ("blackstone", 8), ("end_stone", 3), ("ice", 10), ("snow_block", 6), ("moss_block", 20), ("mud", 15),
    ("terracotta", 20), ("sugar_cane", 15), ("cactus", 15), ("bamboo", 15), ("pumpkin", 40), ("melon", 40),
    ("kelp", 15), ("dandelion", 10), ("poppy", 10), ("brown_mushroom", 30), ("red_mushroom", 30),
    ("wheat", 20), ("carrots", 30), ("potatoes", 30), ("beetroots", 30),
)
#: Mobs worth hunting, with seconds to find and kill one.
HUNTED: dict[str, float] = {
    "cow": 30, "pig": 30, "sheep": 30, "chicken": 25, "rabbit": 60, "spider": 90, "skeleton": 90,
    "zombie": 60, "creeper": 120, "enderman": 240, "blaze": 300, "slime": 240, "squid": 60, "cod": 40,
    "salmon": 40, "witch": 400, "ghast": 400, "magma_cube": 200, "wither_skeleton": 400,
}
CRAFT_SECONDS = 2.0
SMELT_SECONDS = 10.5
#: Tags that stand for "any wood will do". Plans keep these as tags, so the agent uses the wood it finds.
ANY_PLANKS = "#planks"
ANY_LOGS = "#logs"


def short(item: str) -> str:
    if item.startswith("#"):
        return "#" + short(item[1:])
    return item[len("minecraft:"):] if item.startswith("minecraft:") else item


def full(item: str) -> str:
    if item.startswith("#"):
        return "#" + full(item[1:])
    return item if ":" in item else "minecraft:" + item


def tool_tier(item: str, kind: str = "pickaxe") -> int:
    """The tier of a tool item as a number (wooden 0 .. netherite 4), or -1 if it is not that tool."""
    name = short(item)
    if not name.endswith("_" + kind):
        return -1
    return {"wooden": 0, "golden": 0, "stone": 1, "copper": 1, "iron": 2, "diamond": 3,
            "netherite": 4}.get(name[: -len(kind) - 1], -1)


@dataclass(frozen=True)
class Recipe:
    id: str
    out: str
    n: int
    how: str  # "craft" or "smelt"
    #: Ingredient groups: (the items and #tags any of which will do, how many).
    inputs: tuple[tuple[tuple[str, ...], int], ...]
    table: bool = False


@dataclass(frozen=True)
class Source:
    how: str  # "mine" or "kill"
    #: The blocks to mine or the mob to fight, as short ids.
    targets: tuple[str, ...]
    #: Items one block or one mob gives on average.
    each: float
    seconds: float
    #: The pickaxe tier needed (0 wooden .. 3 diamond), or -1 for none.
    tier: int = -1
    #: Set for the "any wood" source: the tag to hand to the mine skill.
    tag: Optional[str] = None


@dataclass
class Knowledge:
    version: str = "unknown"
    recipes: dict[str, list[Recipe]] = field(default_factory=dict)
    item_tags: dict[str, list[str]] = field(default_factory=dict)
    block_tags: dict[str, list[str]] = field(default_factory=dict)
    blocks: dict[str, dict[str, float]] = field(default_factory=dict)
    mobs: dict[str, dict[str, float]] = field(default_factory=dict)
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Measured seconds, by key (``mine:iron_ore``, ``kill:cow``, ``craft``, ``smelt``); see with_costs.
    measured: dict[str, float] = field(default_factory=dict)
    _sources: dict[str, list[Source]] = field(default_factory=dict, repr=False)
    _costs: Optional[dict[str, float]] = field(default=None, repr=False)

    # Building ---------------------------------------------------------------------------------

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> "Knowledge":
        k = cls(version=str(data.get("version", "unknown")))
        k.item_tags = {name: list(v) for name, v in data.get("item_tags", {}).items()}
        k.block_tags = {name: list(v) for name, v in data.get("block_tags", {}).items()}
        k.blocks = {name: dict(v) for name, v in data.get("blocks", {}).items()}
        k.mobs = {name: dict(v) for name, v in data.get("mobs", {}).items()}
        k.tools = {name: dict(v) for name, v in data.get("tools", {}).items()}
        for raw in data.get("recipes", []):
            recipe = Recipe(raw["id"], raw["out"], int(raw.get("n", 1)), raw.get("how", "craft"),
                            tuple((tuple(g["any"]), int(g["n"])) for g in raw["in"]), bool(raw.get("table")))
            k.recipes.setdefault(recipe.out, []).append(recipe)
        k._index_sources()
        return k

    def with_costs(self, measured: Mapping[str, float]) -> "Knowledge":
        """A copy that uses measured seconds where there are any."""
        other = Knowledge(self.version, self.recipes, self.item_tags, self.block_tags, self.blocks, self.mobs,
                          self.tools, dict(measured))
        other._index_sources()
        return other

    def _natural_seconds(self, block: str) -> Optional[float]:
        for pattern, seconds in NATURAL:
            if fnmatch.fnmatchcase(block, pattern):
                return self.measured.get(f"mine:{block}", seconds)
        return None

    def _index_sources(self) -> None:
        self._sources = {}
        self._costs = None
        by_item: dict[tuple[str, int, float], list[tuple[str, float]]] = {}
        for block, drops in self.blocks.items():
            seconds = self._natural_seconds(block)
            if seconds is None:
                continue
            need = self.tools.get(block, {})
            tier = -1
            if need.get("tool") == "pickaxe":
                tier = int(need.get("tier", 0))
            for item, each in drops.items():
                if each < 0.02:
                    continue  # too rare to plan on (an apple from leaves)
                by_item.setdefault((item, tier, each), []).append((block, seconds))
        for (item, tier, each), found in by_item.items():
            found.sort(key=lambda b: b[1])
            self._sources.setdefault(item, []).append(
                Source("mine", tuple(b for b, _ in found), each, found[0][1], tier))
        for mob, drops in self.mobs.items():
            if mob not in HUNTED:
                continue
            seconds = self.measured.get(f"kill:{mob}", HUNTED[mob])
            for item, each in drops.items():
                if each >= 0.25:
                    self._sources.setdefault(item, []).append(Source("kill", (mob,), each, seconds))
        log_seconds = self.measured.get("mine:#logs", 5.0)
        self._sources[ANY_LOGS] = [Source("mine", ("oak_log",), 1.0, log_seconds, tag="#minecraft:logs")]

    # Lookups ----------------------------------------------------------------------------------

    def members(self, tag: str) -> list[str]:
        return self.item_tags.get(short(tag).lstrip("#"), [])

    def generic(self, option: str) -> str:
        """``#planks`` or ``#logs`` when a tag means any wood will do; otherwise the option as given."""
        if not option.startswith("#"):
            return option
        members = self.members(option)
        if "oak_planks" in members and "spruce_planks" in members:
            return ANY_PLANKS
        if "oak_log" in members and "spruce_log" in members:
            return ANY_LOGS
        return option

    def matches(self, item: str, option: str) -> bool:
        """Whether a carried item counts towards an ingredient option."""
        item = short(item)
        if item == option:
            return True
        if option == ANY_PLANKS:
            return item in self.members("planks")
        if option == ANY_LOGS:
            return item in self.members("logs")
        if option.startswith("#"):
            return item in self.members(option)
        return item == option

    def recipes_for(self, item: str) -> list[Recipe]:
        if item == ANY_PLANKS:
            return [Recipe("planks", ANY_PLANKS, 4, "craft", (((ANY_LOGS,), 1),))]
        return self.recipes.get(item, [])

    def sources(self, item: str) -> list[Source]:
        return self._sources.get(item, [])

    def known(self, item: str) -> bool:
        return bool(self.recipes_for(item) or self.sources(item))

    # Costs ------------------------------------------------------------------------------------

    def _pickaxe(self, tier: int) -> str:
        return f"{TIERS[max(0, min(tier, len(TIERS) - 1))]}_pickaxe"

    def costs(self) -> dict[str, float]:
        """Seconds to come by one of each item from nothing, by the cheapest way."""
        if self._costs is not None:
            return self._costs
        cost: dict[str, float] = {}
        craft = self.measured.get("craft", CRAFT_SECONDS)
        smelt = self.measured.get("smelt", SMELT_SECONDS)

        def of(option: str) -> float:
            option = self.generic(option)
            if option in cost:
                return cost[option]
            if option.startswith("#") and option not in (ANY_PLANKS, ANY_LOGS):
                return min((cost.get(m, math.inf) for m in self.members(option)), default=math.inf)
            return math.inf

        for item, sources in self._sources.items():
            cost[item] = min(s.seconds / s.each for s in sources)
        recipes = [r for rs in self.recipes.values() for r in rs] + self.recipes_for(ANY_PLANKS)
        for _ in range(40):
            changed = False
            for r in recipes:
                total = craft if r.how == "craft" else smelt * r.n
                for options, n in r.inputs:
                    total += n * min(of(o) for o in options)
                    if total == math.inf:
                        break
                if total == math.inf:
                    continue
                each = total / r.n
                if each < cost.get(r.out, math.inf) - 1e-9:
                    cost[r.out] = each
                    changed = True
            if not changed:
                break
        # A tool is used many times, so its price is spread thin: add a small share to what it mines.
        self._costs = cost
        return cost

    def cost(self, option: str) -> float:
        option = self.generic(option)
        costs = self.costs()
        if option in costs:
            return costs[option]
        if option.startswith("#"):
            return min((costs.get(m, math.inf) for m in self.members(option)), default=math.inf)
        return math.inf

    def cheapest(self, options: tuple[str, ...] | list[str]) -> str:
        """The option that is cheapest to get, with a plain item standing in for a tag where needed."""
        best, best_cost = self.generic(options[0]), math.inf
        for option in options:
            option = self.generic(option)
            if option.startswith("#") and option not in (ANY_PLANKS, ANY_LOGS):
                for member in self.members(option):
                    c = self.costs().get(member, math.inf)
                    if c < best_cost:
                        best, best_cost = member, c
            else:
                c = self.cost(option)
                if c < best_cost:
                    best, best_cost = option, c
        return best


@lru_cache(maxsize=4)
def load(version: Optional[str] = None, path: Optional[Path] = None) -> Knowledge:
    """The knowledge for a game version (the newest file shipped, when none is named)."""
    if path is None:
        files = sorted(DATA_DIR.glob("minecraft-*.json.gz"))
        if version is not None:
            named = DATA_DIR / f"minecraft-{version}.json.gz"
            files = [named] if named.exists() else files
        if not files:
            raise FileNotFoundError(f"no game data in {DATA_DIR}; build it with python -m minegpt_brain.knowledge build")
        path = files[-1]
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return Knowledge.from_data(json.load(f))
