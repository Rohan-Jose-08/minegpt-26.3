"""Checks the model's claims against the game.

Plan steps and the goal can carry a condition the agent checks itself from observations, so
a step counts as done when the inventory says so, not when the model says so, and
``goal_done`` is refused while the goal's condition is unmet. A condition looks like::

    {"have": {"iron_ingot": 24, "*_planks": 4},       # item counts; * matches any characters
     "wearing": ["iron_helmet", "iron_chestplate"],   # armour being worn
     "near": {"x": 10, "y": 64, "z": -3, "radius": 4}, # standing within radius blocks
     "built": ["house"],                               # structures build_structure finished
     "sees": {"block": "chest", "count": 2, "radius": 16},  # blocks in sight nearby
     "height": {"max": 16}}                            # the player's y, with min and/or max

Failed skills get a hint about what usually fixes that failure, and the same call failing the
same way again gets a nudge to try something else. Places goto could not get to are kept for a
couple of minutes (:class:`Unreachable`), so the same trip is not set off on again at once.
"""

from __future__ import annotations

import fnmatch
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..observation import Observation
from .state import count_matching, full, short, worn

CONDITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "What must be true, checked from the game itself. Use any of the keys; all given must hold.",
    "properties": {
        "have": {"type": "object", "additionalProperties": {"type": "integer", "minimum": 1},
                 "description": "Item id (or pattern with *, like *_planks) to the least count carried or worn."},
        "wearing": {"type": "array", "items": {"type": "string"}, "description": "Armour item ids being worn."},
        "near": {"type": "object", "description": "Standing within radius (default 3) of x y z.",
                 "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"},
                                "radius": {"type": "number"}},
                 "required": ["x", "y", "z"]},
        "built": {"type": "array", "items": {"type": "string"},
                  "description": "Structures that build_structure has finished in this goal: a kind like house, "
                  "or kind:part like base:wall."},
        "sees": {"type": "object", "description": "At least count (default 1) blocks of a kind in sight within "
                 "radius (default 16) of the player, e.g. a placed chest or bed.",
                 "properties": {"block": {"type": "string"}, "count": {"type": "integer", "minimum": 1},
                                "radius": {"type": "integer"}},
                 "required": ["block"]},
        "height": {"type": "object", "description": "The player's y is at least min and at most max.",
                   "properties": {"min": {"type": "number"}, "max": {"type": "number"}}},
    },
    "additionalProperties": False,
}

_KEYS = ("have", "wearing", "near", "built", "sees", "height")


@dataclass
class World:
    """What conditions can look up beyond the observation."""

    #: ``find(block, radius, limit)``: blocks in sight, as the bridge's find returns them.
    find: Optional[Callable[[str, int, int], list]] = None
    #: Structures finished in this goal: ``kind`` and ``kind:part``.
    built: set[str] = field(default_factory=set)


def validate(condition: Any) -> Optional[dict[str, Any]]:
    """The condition if it is well formed, None if absent. Raises ValueError otherwise."""
    if condition in (None, {}):
        return None
    if not isinstance(condition, dict):
        raise ValueError(f"a condition is an object with any of {', '.join(_KEYS)}")
    unknown = set(condition) - set(_KEYS)
    if unknown:
        raise ValueError(f"unknown condition keys: {', '.join(sorted(unknown))}; use {', '.join(_KEYS)}")
    have = condition.get("have", {})
    if not isinstance(have, dict) or not all(isinstance(k, str) and isinstance(v, int) for k, v in have.items()):
        raise ValueError('have maps item ids to counts, like {"iron_ingot": 3}')
    wearing = condition.get("wearing", [])
    if not isinstance(wearing, list) or not all(isinstance(w, str) for w in wearing):
        raise ValueError('wearing is a list of item ids, like ["iron_helmet"]')
    near = condition.get("near")
    if near is not None and not (isinstance(near, dict) and all(isinstance(near.get(k), (int, float))
                                                                for k in "xyz")):
        raise ValueError("near needs numbers x, y and z")
    built = condition.get("built", [])
    if not isinstance(built, list) or not all(isinstance(b, str) for b in built):
        raise ValueError('built is a list of structure kinds, like ["house"]')
    sees = condition.get("sees")
    if sees is not None and not (isinstance(sees, dict) and isinstance(sees.get("block"), str)):
        raise ValueError('sees needs a block, like {"block": "chest"}')
    height = condition.get("height")
    if height is not None and not (isinstance(height, dict) and height and all(
            k in ("min", "max") and isinstance(v, (int, float)) for k, v in height.items())):
        raise ValueError('height needs min and/or max, like {"max": 16}')
    return condition


def unmet(condition: Optional[dict[str, Any]], obs: Observation, world: Optional[World] = None) -> list[str]:
    """What is still missing; an empty list means the condition holds."""
    if not condition:
        return []
    missing = []
    totals: dict[str, int] = {}
    for slot in obs.inventory:
        totals[slot["id"]] = totals.get(slot["id"], 0) + slot["count"]
    for pattern, wanted in condition.get("have", {}).items():
        got = count_matching(totals, pattern)
        if got < wanted:
            missing.append(f"have {got} of {wanted} {short(full(pattern))}")
    wearing = list(worn(obs).values())
    for pattern in condition.get("wearing", []):
        if not any(fnmatch.fnmatchcase(item, full(pattern)) for item in wearing):
            missing.append(f"not wearing {short(full(pattern))}")
    near = condition.get("near")
    if near:
        radius = float(near.get("radius", 3))
        distance = obs.distance_to(near["x"] + 0.5, near["y"], near["z"] + 0.5)
        if distance > radius + 0.5:
            missing.append(f"{distance:.0f} blocks from {near['x']} {near['y']} {near['z']}, not within {radius:g}")
    for name in condition.get("built", []):
        if world is None or name.lower() not in world.built:
            missing.append(f"{name} not built yet (build_structure reports when it is)")
    sees = condition.get("sees")
    if sees:
        wanted = int(sees.get("count", 1))
        radius = int(sees.get("radius", 16))
        found = None
        if world is not None and world.find is not None:
            try:
                found = len(world.find(full(sees["block"]), radius, max(wanted, 1)))
            except Exception:  # noqa: BLE001 - a failed lookup just leaves the condition unmet
                found = None
        if found is None:
            missing.append(f"could not look for {short(full(sees['block']))}")
        elif found < wanted:
            missing.append(f"see {found} of {wanted} {short(full(sees['block']))} within {radius} blocks")
    height = condition.get("height")
    if height:
        y = obs.pos[1]
        if "min" in height and y < height["min"] - 0.5:
            missing.append(f"at y={y:.0f}, not up at {height['min']:g} or above")
        if "max" in height and y > height["max"] + 0.5:
            missing.append(f"at y={y:.0f}, not down at {height['max']:g} or below")
    return missing


_HINTS = [
    (r"no unlocked recipe makes", "Recipes unlock when you pick up one of their ingredients, so gather one first. "
                                  "The recipe tool lists what is unlocked."),
    (r"missing ingredients", "Gather what is missing first; the recipe tool shows the ingredients."),
    (r"need a (\w+) that can mine", "Craft a better {0} first: wood mines stone and coal, stone mines iron, "
                                    "iron mines gold and diamond."),
    (r"found no visible", "None in sight from here. Try find_blocks with a bigger radius, walk somewhere new "
                          "(open ground, a cave mouth, a cliff face), or raise explore to tunnel further. Ores "
                          "show up in caves and in tunnel walls: dig down with goto(y=..., allow_break=true) "
                          "first, then mine with explore."),
    (r"looks flooded|went under water", "The way there is under water. Pick a target somewhere dry, or "
                                         "carry dirt or cobblestone so goto can bridge."),
    (r"made no headway", "The player kept moving without getting anywhere, so goto stopped instead of going "
                         "round in circles. Go to a different spot first, come at it from another side, or "
                         "set allow_break=true to dig through."),
    (r"no path to|cannot get closer|gave up going", "Pick a nearer or different target. goto already digs when "
                                                     "there is no walking route, unless allow_break was false; "
                                                     "carrying dirt or cobblestone lets it pillar and bridge."),
    (r"cannot see the|could not open", "Something hides the block. Walk to its other side, or let craft or "
                                       "smelt place a new one."),
    (r"no fuel|went out", "Bring fuel: coal, charcoal, planks, logs or sticks."),
    (r"not enough blocks: need", "Gather or craft the missing blocks first (the message lists them), or set "
                                 "partial=true to build what you have."),
    (r"protected region", "That spot is inside an area the player protected. Build, dig or place somewhere else."),
    (r"is in the way", "Something that is not natural terrain is in the way. Move the origin, or remove it on "
                       "purpose if it is yours; clear=true only digs natural blocks."),
    (r"missed the jump|too hungry to sprint", "goto picked another way round; if it keeps failing, eat, or pass "
                                              "parkour=false."),
    (r"not enough for .*: missing", "Gather or craft what is missing (plan_materials gives the full list), or "
                                    "pass partial=true to build what you carry. Blocks already placed are kept."),
    (r"no flat site", "Try a bigger radius, a smaller footprint, or walk somewhere more open and look again."),
    (r"nothing to place it against", "Those blocks float: the lowest layer must rest on the ground or touch "
                                     "existing blocks."),
    (r"nothing to do: no ripe crops", "Nothing is ripe yet: crops take one to three in-game days. Do something "
                                      "else and come back."),
    (r"no chest or barrel", "Craft a chest (8 planks) and place it, or give the chest's x y z."),
    (r"is full", "Use another chest, or place a new chest next to it (two side by side make a double chest)."),
    (r"no bed within", "Craft a bed from 3 wool (from sheep: fight them, or shear them) and 3 planks, and place it."),
    (r"could not sleep", "Beds work only at night or in a thunderstorm with no monsters close by. Try after "
                         "dusk, or deal with the mobs first."),
    (r"sleep timed out", "The night did not pass, probably because other players stayed up; the player got up."),
    (r"timed out", "It ran out of time. Break the job up: a lower count, or a nearer target."),
    (r"health is down", "Back off and eat; health comes back when food is above 17. Craft a sword and armour "
                        "before fighting again."),
    (r"nothing safe to eat|to eat", "Hunt animals with fight(entity=minecraft:cow, pig, sheep or chicken), "
                                    "then smelt the raw meat."),
    (r"no (\S+) in the inventory", "You do not carry {0}: get or craft it first."),
    (r"is paused", "The player paused MineGPT; wait for them to resume."),
]


def hint(message: str) -> Optional[str]:
    for pattern, text in _HINTS:
        match = re.search(pattern, message)
        if match:
            return text.format(*(short(g) for g in match.groups()))
    return None


class FailureTracker:
    """Notices the same call failing the same way more than once."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def record(self, tool: str, args: dict[str, Any], message: str) -> int:
        """How many times this exact call has now failed with this message."""
        key = json.dumps([tool, args, re.sub(r"-?\d+", "#", message)], sort_keys=True)
        self._seen[key] = self._seen.get(key, 0) + 1
        return self._seen[key]


_NO_WAY = re.compile(r"no path to|cannot get closer|gave up going|made no headway|looks flooded")


class Unreachable:
    """Places goto could not get to lately.

    The same trip from about the same spot is refused for ``seconds``, twice as long after each
    further failure, so the model does not walk into the same wall turn after turn. A trip is
    let through again when it is started from somewhere else, or allows something the failed one
    did not (digging, placing).
    """

    def __init__(self, seconds: float = 120.0, near: int = 2, moved: int = 12,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.seconds = seconds
        self.near = near
        self.moved = moved
        self.clock = clock
        self._places: list[dict[str, Any]] = []

    @staticmethod
    def _target(args: dict[str, Any]) -> dict[str, int]:
        return {k: int(args[k]) for k in ("x", "y", "z") if isinstance(args.get(k), (int, float))}

    def _same(self, a: dict[str, int], b: dict[str, int]) -> bool:
        return a.keys() == b.keys() and all(abs(a[k] - b[k]) <= self.near for k in a)

    def _find(self, target: dict[str, int]) -> Optional[dict[str, Any]]:
        now = self.clock()
        self._places = [p for p in self._places if p["forget"] > now]
        return next((p for p in self._places if self._same(p["target"], target)), None)

    def note(self, args: dict[str, Any], message: str, pos: tuple[int, int, int]) -> bool:
        """Records a failed goto if it failed for want of a way there. True if it did."""
        target = self._target(args)
        if not target or not _NO_WAY.search(message):
            return False
        now = self.clock()
        old = self._find(target)
        strikes = min(old["strikes"] + 1, 3) if old else 1
        if old:
            self._places.remove(old)
        wait = self.seconds * 2 ** (strikes - 1)
        self._places.append({"target": target, "reason": message, "from": pos, "at": now, "until": now + wait,
                             "forget": now + wait * 3, "strikes": strikes,
                             "no_break": args.get("allow_break") is False,
                             "no_place": args.get("allow_place") is False})
        return True

    def reached(self, args: dict[str, Any]) -> None:
        """Forgets a place once a goto there worked."""
        place = self._find(self._target(args))
        if place:
            self._places.remove(place)

    def blocked(self, args: dict[str, Any], pos: tuple[int, int, int]) -> Optional[str]:
        """Why this goto should not be run right now, or None."""
        target = self._target(args)
        place = self._find(target) if target else None
        now = self.clock()
        if place is None or place["until"] <= now:
            return None
        if max(abs(a - b) for a, b in zip(pos, place["from"])) >= self.moved:
            return None
        if (place["no_break"] and args.get("allow_break") is not False) or \
                (place["no_place"] and args.get("allow_place") is not False):
            return None
        return (f"{_place_text(place['target'])} could not be reached {round(now - place['at'])}s ago from about "
                f"here ({place['reason']}). Nothing has changed since, so the same trip would fail the same way. "
                "Go somewhere else first, pick another target, or change what is in the way (dig with "
                f"allow_break=true, bring blocks to bridge or pillar). It can be tried again in "
                f"{round(place['until'] - now)}s, or sooner from a different spot.")

    def lines(self) -> list[str]:
        """A line for the state note listing what is out of reach right now."""
        now = self.clock()
        self._find({})
        active = [_place_text(p["target"]) for p in self._places if p["until"] > now]
        return ["Out of reach lately (do not head there again yet): " + "; ".join(active) + "."] if active else []


def _place_text(target: dict[str, int]) -> str:
    return " ".join(f"{k}={v}" for k, v in target.items())
