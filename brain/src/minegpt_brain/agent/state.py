"""Short text descriptions of observations, for the model to read."""

from __future__ import annotations

import fnmatch
import math
from typing import Any, Iterable, Mapping, Optional

from ..observation import ARMOR_SLOTS, Observation

#: Inventory slots that hold items (hotbar and main), not armour or the offhand.
CARRY_SLOTS = 36
ARMOR_NAMES = {36: "feet", 37: "legs", 38: "chest", 39: "head"}

#: What look_around searches for, by heading. Ores are listed so tags and ids both work.
NOTABLE = {
    "trees": ["#minecraft:logs"],
    "ores": ["#minecraft:coal_ores", "#minecraft:iron_ores", "#minecraft:copper_ores", "#minecraft:gold_ores",
             "#minecraft:redstone_ores", "#minecraft:lapis_ores", "#minecraft:diamond_ores",
             "#minecraft:emerald_ores"],
    "stone": ["minecraft:stone", "minecraft:cobblestone", "minecraft:deepslate", "minecraft:andesite",
              "minecraft:diorite", "minecraft:granite"],
    "useful blocks": ["minecraft:crafting_table", "minecraft:furnace", "minecraft:chest", "minecraft:barrel",
                      "#minecraft:beds"],
    "dangers": ["minecraft:lava", "minecraft:fire", "minecraft:magma_block"],
    "water": ["minecraft:water"],
}


def short(item_id: str) -> str:
    return item_id.removeprefix("minecraft:")


def full(item_id: str) -> str:
    if item_id.startswith("#"):
        tag = item_id[1:]
        return "#" + (tag if ":" in tag else f"minecraft:{tag}")
    return item_id if ":" in item_id else f"minecraft:{item_id}"


def counts(obs: Observation) -> dict[str, int]:
    """Items carried, worn or held in the offhand, by full id."""
    totals: dict[str, int] = {}
    for slot in obs.inventory:
        totals[slot["id"]] = totals.get(slot["id"], 0) + slot["count"]
    return totals


def count_matching(totals: Mapping[str, int], pattern: str) -> int:
    """How many items match an id or a pattern with ``*`` wildcards, such as ``*_planks``."""
    pattern = full(pattern)
    if "*" not in pattern and "?" not in pattern:
        return totals.get(pattern, 0)
    return sum(n for item, n in totals.items() if fnmatch.fnmatchcase(item, pattern))


def worn(obs: Observation) -> dict[str, str]:
    """Armour worn, by body part."""
    return {ARMOR_NAMES[s["slot"]]: s["id"] for s in obs.inventory if s["slot"] in ARMOR_SLOTS}


def held(obs: Observation) -> Optional[str]:
    selected = obs.data["inventory"].get("selected", 0)
    return next((s["id"] for s in obs.inventory if s["slot"] == selected), None)


def free_slots(obs: Observation) -> int:
    used = {s["slot"] for s in obs.inventory if s["slot"] < CARRY_SLOTS}
    return CARRY_SLOTS - len(used)


def block_pos(obs: Observation) -> tuple[int, int, int]:
    x, y, z = obs.pos
    return (math.floor(x), math.floor(y), math.floor(z))


def time_of_day(day_time: Optional[int]) -> str:
    if day_time is None:
        return "unknown time"
    t = day_time % 24000
    if t < 12000:
        return "day"
    if t < 13800:
        return "dusk, night is coming"
    if t < 22200:
        return "night, hostile mobs spawn in the dark"
    return "dawn"


def hostiles(obs: Observation, radius: float = 16.0) -> list[dict[str, Any]]:
    return [e for e in obs.entities if e.get("hostile") and e.get("distance", 99) <= radius]


def ground_items(obs: Observation, radius: float = 16.0) -> list[dict[str, Any]]:
    """Dropped items lying nearby, nearest first."""
    return [e for e in obs.entities if e.get("type") == "minecraft:item" and e.get("distance", 99) <= radius]


def ground_line(obs: Observation, radius: float = 16.0) -> Optional[str]:
    """Dropped items nearby, like ``On the ground: raw_iron 3 (2m), cobblestone 12 (5m).``, or None."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in ground_items(obs, radius):
        groups.setdefault(e.get("item", "unknown item"), []).append(e)
    if not groups:
        return None
    parts = []
    for item, group in sorted(groups.items(), key=lambda kv: min(e["distance"] for e in kv[1])):
        count = sum(e.get("count", 1) for e in group)
        parts.append(f"{short(item)} {count} ({min(e['distance'] for e in group):.0f}m)")
    return "On the ground: " + ", ".join(parts) + "."


def items_line(totals: Mapping[str, int]) -> str:
    if not totals:
        return "nothing"
    return ", ".join(f"{short(item)} {n}" for item, n in sorted(totals.items(), key=lambda kv: -kv[1]))


def delta(before: Mapping[str, int], after: Mapping[str, int]) -> dict[str, int]:
    changes = {item: after.get(item, 0) - before.get(item, 0) for item in set(before) | set(after)}
    return {item: n for item, n in changes.items() if n}


def delta_line(changes: Mapping[str, int]) -> str:
    if not changes:
        return "no change"
    return ", ".join(f"{'+' if n > 0 else ''}{n} {short(item)}" for item, n in sorted(changes.items()))


def status_line(obs: Observation) -> str:
    """One line on where the player is and how it is doing."""
    data, player = obs.data, obs.player
    x, y, z = block_pos(obs)
    where = f"{short(data.get('dimension', 'minecraft:overworld'))}, {short(data.get('biome', '?'))}"
    parts = [
        f"At {x} {y} {z} ({where}), {time_of_day(data.get('dayTime'))}.",
        f"Health {player['health']:.0f}/{player.get('maxHealth', 20):.0f}, food {player['food']}/20.",
    ]
    if player.get("inWater"):
        parts.append(f"In water, air {player.get('air', 300)}/{player.get('maxAir', 300)}.")
    if player.get("onFire"):
        parts.append("On fire!")
    hand = held(obs)
    parts.append(f"Holding {short(hand) if hand else 'nothing'}.")
    armour = worn(obs)
    parts.append("Wearing " + (", ".join(short(i) for i in armour.values()) if armour else "no armour") + ".")
    threats = hostiles(obs)
    if threats:
        parts.append("Hostile: " + ", ".join(f"{short(e['type'])} {e['distance']:.0f}m" for e in threats[:5]) + ".")
    return " ".join(parts)


def inventory_line(obs: Observation) -> str:
    carried = {}
    for slot in obs.inventory:
        if slot["slot"] < CARRY_SLOTS or slot["slot"] == 40:
            carried[slot["id"]] = carried.get(slot["id"], 0) + slot["count"]
    return f"Inventory: {items_line(carried)} ({free_slots(obs)} of {CARRY_SLOTS} slots free)."


def nearby_lines(found: Mapping[str, Iterable[Mapping[str, Any]]]) -> list[str]:
    """Groups ``find`` results (by heading) into lines like ``ores: iron_ore 3 (nearest 4 60 -2, 6m)``."""
    lines = []
    for heading, blocks in found.items():
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for block in blocks:
            groups.setdefault(block["block"], []).append(block)
        if not groups:
            lines.append(f"{heading}: none in sight")
            continue
        described = []
        for block_id, group in sorted(groups.items(), key=lambda kv: kv[1][0]["distance"]):
            nearest = group[0]
            x, y, z = nearest["pos"]
            described.append(f"{short(block_id)} {len(group)}{'+' if len(group) >= 8 else ''} "
                             f"(nearest {x} {y} {z}, {nearest['distance']:.0f}m)")
        lines.append(f"{heading}: " + "; ".join(described))
    return lines
