"""Turns "I want these items" into the whole job: what to gather, craft and smelt, in a workable order.

This is a search over the recipe graph (``graph.py``). For every item it picks the cheapest way
to come by it, uses up what the player carries first, adds the tools the mining needs (a stone
pickaxe before iron ore), a crafting table and furnace where recipes call for them, and fuel for
the smelting. The result is a list of steps that can be run as skill calls one after another,
each with a check ("carries 3 iron ingots") the game can make::

    plan = plan_items({"iron_pickaxe": 1}, inventory, load())
    for call in plan.calls(): ...

Wood is kept loose: a plan says "any planks" and "any logs", so the agent uses whatever trees are
about, unless a recipe really needs one kind (an oak door).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .graph import ANY_LOGS, ANY_PLANKS, Knowledge, Recipe, Source, full, short, tool_tier

#: Items one unit of fuel smelts.
FUELS = (("coal", 8.0), ("charcoal", 8.0), (ANY_PLANKS, 1.5), (ANY_LOGS, 1.5))
_PATTERNS = {ANY_PLANKS: "*_planks", ANY_LOGS: "*_log"}
_NAMES = {ANY_PLANKS: "planks (any wood)", ANY_LOGS: "logs (any wood)"}


def name(item: str) -> str:
    return _NAMES.get(item, item)


@dataclass
class Step:
    how: str  # "mine", "kill", "craft" or "smelt"
    item: str
    #: Items this step adds.
    count: int = 0
    #: Blocks to mine, mobs to fight, or items to put in the furnace.
    units: int = 0
    #: mine: block ids; kill: the mob; smelt: the item that goes in.
    targets: tuple[str, ...] = ()
    tag: Optional[str] = None
    table: bool = False
    tier: int = -1
    #: How many of the item the player carries once the step is done.
    have: int = 0
    seconds: float = 0.0
    #: Items this step needs first (ingredients, a tool, a table), for ordering.
    needs: set[str] = field(default_factory=set)

    def pattern(self) -> str:
        return _PATTERNS.get(self.item, self.item)

    def text(self) -> str:
        if self.how == "mine":
            what = self.tag or ", ".join(self.targets[:3])
            return f"mine {self.units} {what} for {self.count} {name(self.item)}"
        if self.how == "kill":
            return f"fight {self.units} {self.targets[0]} for {self.count} {name(self.item)}"
        if self.how == "smelt":
            return f"smelt {self.units} {name(self.targets[0])} into {name(self.item)}"
        return f"craft {self.count} {name(self.item)}" + (" (crafting table)" if self.table else "")

    def calls(self) -> list[dict[str, Any]]:
        """The step as skill calls, in the form the agent's tools take."""
        if self.how == "mine":
            block: Any = self.tag or [full(t) for t in self.targets]
            if isinstance(block, list) and len(block) == 1:
                block = block[0]
            return [{"tool": "mine", "args": {"block": block, "count": self.units}}]
        if self.how == "kill":
            return [{"tool": "fight", "args": {"entity": full(self.targets[0])}}]
        if self.how == "smelt":
            source = self.targets[0]
            item = "#minecraft:logs" if source == ANY_LOGS else full(source)
            return [{"tool": "smelt", "args": {"item": item, "count": n}}
                    for n in _chunks(self.units, 64)]
        item = "#minecraft:planks" if self.item == ANY_PLANKS else full(self.item)
        # The craft skill's count is how many more to make, not how many to end up with.
        return [{"tool": "craft", "args": {"item": item, "count": n}} for n in _chunks(self.count, 576)]

    def done_when(self) -> dict[str, Any]:
        return {"have": {self.pattern(): self.have}}


def _chunks(total: int, size: int) -> list[int]:
    return [min(size, total - i) for i in range(0, total, size)]


@dataclass
class ItemPlan:
    wanted: dict[str, int] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)
    #: Items with no known recipe or source.
    unknown: list[str] = field(default_factory=list)
    #: Tools added because the mining needs them.
    tools: list[str] = field(default_factory=list)
    smelts: int = 0
    fuel: str = ""

    def empty(self) -> bool:
        return not (self.steps or self.unknown)

    def seconds(self) -> float:
        return sum(s.seconds for s in self.steps)

    def calls(self) -> list[dict[str, Any]]:
        return [call for step in self.steps for call in step.calls()]

    def goal_check(self) -> dict[str, Any]:
        return {"have": {_PATTERNS.get(item, item): n for item, n in self.wanted.items()}}

    def describe(self, inventory: Optional[Mapping[str, int]] = None) -> str:
        if self.empty():
            return "You already carry everything for that."
        lines = []
        if self.tools:
            lines.append("Tools made on the way, because the mining needs them: " + ", ".join(self.tools) + ".")
        gather = [s for s in self.steps if s.how in ("mine", "kill")]
        if gather:
            lines.append("Gather:")
            for s in gather:
                tier = ("a wooden", "a stone", "an iron", "a diamond")[min(s.tier, 3)]
                tool = f" (needs {tier} pickaxe or better)" if s.tier >= 0 else ""
                lines.append(f"- {s.count} {name(s.item)}: {s.text()}{tool}")
        lines.append("All steps, in order:")
        lines += [f"{i}. {s.text()}" for i, s in enumerate(self.steps, 1)]
        if self.smelts:
            lines.append(f"Smelting {self.smelts} items; fuel: {self.fuel}.")
        if self.unknown:
            lines.append("No recipe or source known for: " + ", ".join(self.unknown) + ". Check them with the "
                         "recipe tool, or find them in the world; they are left out of the steps.")
        minutes = self.seconds() / 60
        lines.append(f"Rough time: {max(1, round(minutes))} minute{'s' if round(minutes) > 1 else ''}. "
                     "run_plan(items=...) carries out these steps one after another without further planning.")
        return "\n".join(lines)


class _Planner:
    def __init__(self, knowledge: Knowledge, inventory: Mapping[str, int]):
        self.k = knowledge
        self.stock: dict[str, int] = {}
        for item, n in inventory.items():
            self.stock[short(item)] = self.stock.get(short(item), 0) + int(n)
        self.start = dict(self.stock)
        #: Wanted items already provided for, which later steps may use as tools but not use up.
        self.owned: dict[str, int] = {}
        self.steps: dict[str, Step] = {}
        self.unknown: list[str] = []
        self.tools: list[str] = []
        self.smelts = 0

    # Stock ------------------------------------------------------------------------------------

    def carried(self, option: str, stock: Optional[Mapping[str, int]] = None) -> int:
        stock = self.stock if stock is None else stock
        return sum(n for item, n in stock.items() if n > 0 and self.k.matches(item, option))

    def take(self, option: str, n: int) -> int:
        """Uses up to n of an item or tag from stock; returns how many are still missing."""
        for item in [i for i, have in self.stock.items() if have > 0 and self.k.matches(i, option)]:
            used = min(n, self.stock[item])
            self.stock[item] -= used
            n -= used
            if n == 0:
                break
        return n

    # Choosing a way ---------------------------------------------------------------------------

    def _recipe_cost(self, recipe: Recipe, chain: tuple[str, ...]) -> float:
        total = 2.0 if recipe.how == "craft" else 10.5 * recipe.n
        for options, n in recipe.inputs:
            choice = self.k.cheapest(options)
            if choice in chain:
                return math.inf
            have = max(self.carried(o) for o in options)
            total += max(0, n - have) * self.k.cost(choice)
        return total / recipe.n

    def choose(self, item: str, chain: tuple[str, ...]) -> tuple[Optional[Recipe], Optional[Source]]:
        best: tuple[float, Optional[Recipe], Optional[Source]] = (math.inf, None, None)
        for source in self.k.sources(item):
            cost = source.seconds / source.each
            if cost < best[0]:
                best = (cost, None, source)
        for recipe in self.k.recipes_for(item):
            cost = self._recipe_cost(recipe, chain)
            if cost < best[0]:
                best = (cost, recipe, None)
        return best[1], best[2]

    # The search -------------------------------------------------------------------------------

    def step(self, how: str, item: str) -> Step:
        if item not in self.steps:
            self.steps[item] = Step(how, item)
        return self.steps[item]

    def tool(self, tier: int) -> str:
        """Makes sure a pickaxe of at least this tier is carried; returns the one the plan relies on."""
        for source in (self.stock, self.owned):
            for item, n in source.items():
                if n > 0 and tool_tier(item) >= tier:
                    return item
        pickaxe = self.k._pickaxe(tier)
        self.need(pickaxe, 1, ())
        self.stock[pickaxe] = self.stock.get(pickaxe, 0) + 1  # a tool is used, not used up
        if pickaxe not in self.tools:
            self.tools.append(pickaxe)
        return pickaxe

    def station(self, item: str) -> None:
        """A crafting table or furnace: made once, then kept."""
        if self.carried(item) or self.owned.get(item):
            return
        self.need(item, 1, ())
        self.stock[item] = self.stock.get(item, 0) + 1

    def need(self, item: str, n: int, chain: tuple[str, ...]) -> None:
        item = self.k.generic(short(item))
        n = self.take(item, n)
        if n <= 0:
            return
        if item.startswith("#") and item not in (ANY_PLANKS, ANY_LOGS):
            item = self.k.cheapest([item])
        recipe, source = self.choose(item, chain)
        if recipe is None and source is None:
            if item not in self.unknown:
                self.unknown.append(item)
            return
        if source is not None:
            step = self.step(source.how, item)
            if source.tier >= 0:
                step.needs.add(self.tool(source.tier))
                step.tier = source.tier
            units = math.ceil(n / source.each)
            got = max(n, math.floor(units * source.each))
            step.units += units
            step.count += got
            step.targets, step.tag = source.targets, source.tag
            step.seconds += units * source.seconds
            self.stock[item] = self.stock.get(item, 0) + got - n
            return
        assert recipe is not None
        crafts = math.ceil(n / recipe.n)
        step = self.step(recipe.how, item)
        if recipe.table:
            self.station("crafting_table")
            step.needs.add("crafting_table")
            step.table = True
        if recipe.how == "smelt":
            self.station("furnace")
            step.needs.add("furnace")
        for options, each in recipe.inputs:
            wanted = each * crafts
            for option in options:
                wanted = self.take(self.k.generic(option), wanted)
                if wanted == 0:
                    break
            choice = self.k.cheapest(options)
            step.needs.add(choice)
            if recipe.how == "smelt":
                step.targets = (choice,)
            if wanted:
                self.need(choice, wanted, chain + (item,))
        made = crafts * recipe.n
        step.count += made
        step.units += crafts
        step.seconds += crafts * (2.0 if recipe.how == "craft" else 10.5)
        if recipe.how == "smelt":
            self.smelts += crafts
        self.stock[item] = self.stock.get(item, 0) + made - n

    def fuel(self) -> str:
        """Sets fuel aside for the smelting, adding logs to the plan when what is carried falls short."""
        left = float(self.smelts)
        used = []
        for fuel, smelts in FUELS:
            if left <= 0:
                break
            have = self.carried(fuel)
            if have:
                take = min(have, math.ceil(left / smelts))
                self.take(fuel, take)
                left -= take * smelts
                used.append(f"{take} {name(fuel)} carried")
        if left > 0:
            planks = math.ceil(left / 1.5)
            self.need(ANY_PLANKS, planks, ())
            used.append(f"{planks} planks (in the plan)")
            for step in self.steps.values():
                if step.how == "smelt":
                    step.needs.add(ANY_PLANKS)
        return ", ".join(used)

    def ordered(self) -> list[Step]:
        depth: dict[str, int] = {}

        def of(item: str, seen: frozenset[str]) -> int:
            if item in depth:
                return depth[item]
            step = self.steps.get(item)
            if step is None or item in seen:
                return -1
            depth[item] = 1 + max((of(n, seen | {item}) for n in step.needs), default=-1)
            return depth[item]

        for item in self.steps:
            of(item, frozenset())
        order = sorted(self.steps.values(), key=lambda s: depth[s.item])
        for step in order:
            step.have = self.carried(step.item, self.start) + step.count
        return order


def plan_items(wanted: Mapping[str, int], inventory: Mapping[str, int],
               knowledge: Optional[Knowledge] = None) -> ItemPlan:
    """The steps to end up with ``wanted`` (item -> count) on top of nothing but ``inventory``."""
    if knowledge is None:
        from .graph import load

        knowledge = load()
    planner = _Planner(knowledge, inventory)
    plan = ItemPlan()
    for item, n in wanted.items():
        item = knowledge.generic(short(item))
        plan.wanted[item] = plan.wanted.get(item, 0) + int(n)
    # Tools first, so that a wanted pickaxe is also the one the mining uses.
    for item, n in sorted(plan.wanted.items(), key=lambda kv: -tool_tier(kv[0])):
        planner.need(item, n, ())
        planner.owned[item] = planner.owned.get(item, 0) + n
    if planner.smelts:
        plan.fuel = planner.fuel()
    plan.steps = planner.ordered()
    plan.unknown = planner.unknown
    plan.tools = planner.tools
    plan.smelts = planner.smelts
    return plan
