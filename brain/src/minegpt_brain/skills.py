"""The mod's skill library, one blocking call per skill.

Skills run inside the game, a tick at a time, pressing the same keys a player would. Each
call returns once the skill has finished, failed or been cancelled (for example by the user
pressing the pause key or typing ``/minegpt stop``). Arguments are described in
docs/protocol.md.

Example::

    from minegpt_brain.bridge import Bridge
    from minegpt_brain.skills import Skills

    with Bridge.connect() as mc:
        skills = Skills(mc)
        skills.mine("#minecraft:logs", count=4)
        skills.craft("#minecraft:planks", count=8)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .bridge import Bridge

#: Seconds to wait beyond a skill's own time limit before assuming the reply was lost.
_MARGIN = 30.0


@dataclass
class SkillResult:
    skill: str
    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.success


class SkillFailed(RuntimeError):
    """Raised by :class:`Skills` with ``raise_on_failure=True`` when a skill fails."""

    def __init__(self, result: SkillResult):
        super().__init__(f"{result.skill} failed: {result.message}")
        self.result = result


class Skills:
    """Typed wrappers around :meth:`Bridge.skill`.

    With ``raise_on_failure`` set, a failed skill raises :class:`SkillFailed` instead of
    returning a false result, which keeps scripted sequences short.
    """

    def __init__(self, bridge: Bridge, raise_on_failure: bool = False):
        self.bridge = bridge
        self.raise_on_failure = raise_on_failure

    def run(self, name: str, timeout: Optional[float] = None, tuning: Optional[dict[str, float]] = None,
            **args: Any) -> SkillResult:
        """Runs any skill by name. ``None`` arguments are left out so the mod's defaults apply.

        ``tuning`` carries skill settings to the mod (see :mod:`minegpt_brain.practice.tuner`)."""
        args = {key: value for key, value in args.items() if value is not None}
        if timeout is not None:
            args["timeout"] = timeout
        extra = {} if tuning is None else {"tuning": tuning}
        reply = self.bridge.skill(name, args, timeout=None if timeout is None else timeout + _MARGIN, **extra)
        result = SkillResult(reply.get("skill", name), bool(reply["success"]), reply.get("message", ""),
                             reply.get("data") or {})
        if not result and self.raise_on_failure:
            raise SkillFailed(result)
        return result

    def goto(self, x: Optional[int] = None, y: Optional[int] = None, z: Optional[int] = None, *,
             radius: Optional[float] = None, allow_break: Optional[bool] = None, allow_place: Optional[bool] = None,
             sprint: Optional[bool] = None, max_nodes: Optional[int] = None, parkour: Optional[bool] = None,
             timeout: Optional[float] = None) -> SkillResult:
        """Walks to x y z (within ``radius``), to an x z column, or with only ``y`` digs down to that height.

        By default the path digs through natural terrain (stone, dirt, sand, ores, leaves) only
        when there is no walking route; ``allow_break=True`` digs wherever that is quicker and
        ``False`` never digs. It never breaks anything that might be someone's build.
        ``allow_place`` (default on) lets it pillar and bridge with spare dirt or cobblestone,
        and ``sprint`` (default on) sprints on long straight stretches.
        """
        return self.run("goto", timeout, x=x, y=y, z=z, radius=radius, allowBreak=allow_break,
                        allowPlace=allow_place, sprint=sprint, maxNodes=max_nodes, parkour=parkour)

    def mine(self, block: str | list[str], count: int = 1, *, radius: Optional[int] = None,
             hidden: bool = False, explore: Optional[int] = None, timeout: Optional[float] = None) -> SkillResult:
        """Mines ``count`` blocks (an id, a ``#tag``, or a list) and picks up the drops.

        Only blocks a player could see are targeted unless ``hidden`` is set, since finding
        buried ores amounts to x-ray on a server. When nothing is in range it tunnels in one
        direction up to ``explore`` times, which exposes new blocks as it goes.
        """
        return self.run("mine", timeout, block=block, count=count, radius=radius, hidden=hidden, explore=explore)

    def collect(self, item: Optional[str] = None, *, radius: Optional[float] = None,
                timeout: Optional[float] = None) -> SkillResult:
        """Picks up dropped items nearby."""
        return self.run("collect", timeout, item=item, radius=radius)

    def craft(self, item: str, count: int = 1, *, timeout: Optional[float] = None) -> SkillResult:
        """Crafts at least ``count`` of an item (or ``#tag``) through the recipe book.

        Uses a nearby crafting table when the recipe needs one, placing or crafting a table
        if there is none. Only recipes the player has unlocked can be used.
        """
        return self.run("craft", timeout, item=item, count=count)

    def smelt(self, item: str, count: int = 1, *, fuel: Optional[str] = None,
              timeout: Optional[float] = None) -> SkillResult:
        """Smelts ``count`` of an input item in a nearby furnace, placing or crafting one if needed."""
        return self.run("smelt", timeout, item=item, count=count, fuel=fuel)

    def place(self, item: str, x: Optional[int] = None, y: Optional[int] = None, z: Optional[int] = None, *,
              timeout: Optional[float] = None) -> SkillResult:
        """Places a block at x y z, or anywhere next to the player when no position is given."""
        return self.run("place", timeout, item=item, x=x, y=y, z=z)

    def equip(self, item: Optional[str] = None, *, tool: Optional[str] = None, block: Optional[str] = None,
              armor: bool = False) -> SkillResult:
        """Holds an item, the best ``tool`` of a kind (pickaxe, axe, shovel, hoe, sword), or the best
        tool for a ``block``; or with ``armor`` puts on the best armour carried."""
        return self.run("equip", None, item=item, tool=tool, block=block, armor=armor or None)

    def eat(self, item: Optional[str] = None) -> SkillResult:
        """Eats the most filling safe food carried, or ``item``."""
        return self.run("eat", None, item=item)

    def fight(self, entity: Optional[str] = None, *, radius: Optional[float] = None,
              min_health: Optional[float] = None, timeout: Optional[float] = None) -> SkillResult:
        """Fights hostile mobs nearby (or only ``entity``), stopping when health drops to ``min_health``."""
        return self.run("fight", timeout, entity=entity, radius=radius, minHealth=min_health)

    def build(self, blocks: list[list[Any]], *, clear: Optional[bool] = None, partial: Optional[bool] = None,
              timeout: Optional[float] = None) -> SkillResult:
        """Places blocks, each ``[x, y, z, "item"]`` in world coordinates, bottom layer first.

        An entry may carry a fifth value saying how the block faces (``"north"``, ``"top"``,
        ``"on:down"``, or several joined with commas), and the item ``air`` marks a spot to dig out.

        Blocks already in place are skipped. ``clear`` digs natural terrain out of the way first;
        ``partial`` starts even without enough blocks for all of it. See
        :mod:`minegpt_brain.agent.blueprints` for building from a drawing.
        """
        return self.run("build", timeout, blocks=blocks, clear=clear, partial=partial)

    def farm(self, *, radius: Optional[int] = None, replant: Optional[bool] = None, plant: Optional[bool] = None,
             till: Optional[bool] = None, max: Optional[int] = None, timeout: Optional[float] = None) -> SkillResult:
        """Harvests ripe crops within ``radius`` and replants them; plants empty farmland, and with
        ``till`` hoes dirt near water into new farmland."""
        return self.run("farm", timeout, radius=radius, replant=replant, plant=plant, till=till, max=max)

    def store(self, items: Optional[list[str]] = None, *, keep: Optional[list[str]] = None,
              x: Optional[int] = None, y: Optional[int] = None, z: Optional[int] = None,
              timeout: Optional[float] = None) -> SkillResult:
        """Puts ``items`` (or everything but tools, armour, food and some scaffold) into the nearest
        chest or barrel, or the one at x y z. The result lists the chest's contents."""
        return self.run("store", timeout, items=items, keep=keep, x=x, y=y, z=z)

    def take(self, item: str, count: Optional[int] = None, *, x: Optional[int] = None, y: Optional[int] = None,
             z: Optional[int] = None, timeout: Optional[float] = None) -> SkillResult:
        """Takes up to ``count`` (default a stack) of an item from the nearest chest or barrel, or the one at x y z."""
        return self.run("take", timeout, item=item, count=count, x=x, y=y, z=z)

    def sleep(self, *, x: Optional[int] = None, y: Optional[int] = None, z: Optional[int] = None,
              timeout: Optional[float] = None) -> SkillResult:
        """Sleeps in the nearest bed (or the one at x y z) until morning, setting the respawn point."""
        return self.run("sleep", timeout, x=x, y=y, z=z)

    def cancel(self) -> None:
        """Stops the running skill."""
        self.bridge.cancel_skill()

    def recipes(self, item: Optional[str] = None, craftable: bool = False) -> list[dict[str, Any]]:
        """Unlocked crafting recipes, optionally only for ``item`` or only those craftable now."""
        return self.bridge.recipes(item, craftable)
