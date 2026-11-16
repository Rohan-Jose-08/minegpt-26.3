"""Builds MineGPT's game knowledge file from a Minecraft jar: recipes, tags, drops and tool needs.

The client is not told the full recipe list (since 1.21.2), and a server may not run the mod, so
the brain carries its own copy of the game's data. The jar's ``data/minecraft`` folder holds it as
JSON: recipes, loot tables for blocks and mobs, and block and item tags. This module boils that
down to one small file::

    python -m minegpt_brain.knowledge build <minecraft jar or extracted folder> [--version 26.3]

The jar is the one Loom puts in ``.gradle/loom-cache/minecraftMaven/.../minecraft-common-*.jar``
(or any vanilla client or server jar). A file for 26.3 ships with the brain, so this is only needed
after a game update.

Everything is reduced to expected values: a gravel block gives 0.1 flint and 0.9 gravel, a cow
1 leather and 2 beef. Drops that need silk touch or shears are left out; fortune and looting are
taken as absent.
"""

from __future__ import annotations

import gzip
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Iterator, Optional

#: Recipe types kept, and what MineGPT's skills call them.
_CRAFTING = {"minecraft:crafting_shaped", "minecraft:crafting_shapeless", "minecraft:crafting_transmute"}
_SMELTING = {"minecraft:smelting"}
#: Pickaxe, axe, shovel and hoe tiers, lowest first. Copper mines what stone mines.
TIER_LEVEL = {"wooden": 0, "golden": 0, "stone": 1, "copper": 1, "iron": 2, "diamond": 3, "netherite": 4}
_NEEDS = {"needs_stone_tool": 1, "needs_iron_tool": 2, "needs_diamond_tool": 3}


def short(item: str) -> str:
    return item[len("minecraft:"):] if item.startswith("minecraft:") else item


class _Source:
    """Reads ``data/minecraft/...`` files from a jar or from an extracted folder."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.zip = zipfile.ZipFile(self.path) if self.path.is_file() else None

    def files(self, folder: str) -> Iterator[tuple[str, Any]]:
        prefix = f"data/minecraft/{folder}/"
        if self.zip is not None:
            for name in self.zip.namelist():
                if name.startswith(prefix) and name.endswith(".json"):
                    yield name[len(prefix):-5], json.loads(self.zip.read(name))
        else:
            root = self.path / prefix
            if not root.is_dir():
                root = self.path / "data" / "minecraft" / folder
            for file in sorted(root.rglob("*.json")):
                yield file.relative_to(root).as_posix()[:-5], json.loads(file.read_text())


# Tags -----------------------------------------------------------------------------------------

def _tags(source: _Source, kind: str) -> dict[str, list[str]]:
    raw = {name: data.get("values", []) for name, data in source.files(f"tags/{kind}")}
    done: dict[str, list[str]] = {}

    def expand(name: str, seen: frozenset[str]) -> list[str]:
        if name in done:
            return done[name]
        out: list[str] = []
        for value in raw.get(name, []):
            if isinstance(value, dict):
                value = value.get("id", "")
            if value.startswith("#"):
                inner = short(value[1:])
                if inner not in seen:
                    out += expand(inner, seen | {inner})
            elif value:
                out.append(short(value))
        done[name] = list(dict.fromkeys(out))
        return done[name]

    for name in raw:
        expand(name, frozenset({name}))
    return done


# Recipes --------------------------------------------------------------------------------------

def _options(ingredient: Any) -> Optional[list[str]]:
    """An ingredient as a list of item ids and ``#tag`` names."""
    if isinstance(ingredient, str):
        return ["#" + short(ingredient[1:])] if ingredient.startswith("#") else [short(ingredient)]
    if isinstance(ingredient, list):
        out = [o for i in ingredient for o in (_options(i) or [])]
        return out or None
    if isinstance(ingredient, dict):
        if "tag" in ingredient:
            return ["#" + short(ingredient["tag"])]
        if "item" in ingredient:
            return [short(ingredient["item"])]
    return None


def _inputs(slots: list[Any]) -> Optional[list[dict[str, Any]]]:
    """Ingredient slots grouped: the same options in several slots become one entry with a count."""
    grouped: dict[tuple[str, ...], int] = {}
    for slot in slots:
        options = _options(slot)
        if options is None:
            return None
        key = tuple(options)
        grouped[key] = grouped.get(key, 0) + 1
    return [{"any": list(k), "n": n} for k, n in grouped.items()]


def _recipe(name: str, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    kind = data.get("type")
    result = data.get("result")
    if not isinstance(result, dict) or "id" not in result:
        return None
    out = {"id": name, "out": short(result["id"]), "n": int(result.get("count", 1))}
    if kind == "minecraft:crafting_shaped":
        pattern = data.get("pattern", [])
        key = data.get("key", {})
        slots = [key[ch] for row in pattern for ch in row if ch != " " and ch in key]
        table = len(pattern) > 2 or any(len(row) > 2 for row in pattern)
    elif kind == "minecraft:crafting_shapeless":
        slots = data.get("ingredients", [])
        table = len(slots) > 4
    elif kind == "minecraft:crafting_transmute":
        slots = [data.get("input"), data.get("material")]
        table = False
    elif kind in _SMELTING:
        slots = [data.get("ingredient")]
        table = False
    else:
        return None
    inputs = _inputs(slots)
    if not inputs:
        return None
    out["how"] = "smelt" if kind in _SMELTING else "craft"
    out["in"] = inputs
    if table:
        out["table"] = True
    return out


# Loot -----------------------------------------------------------------------------------------

def _number(value: Any, default: float = 1.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        kind = value.get("type", "minecraft:constant")
        if kind.endswith("uniform"):
            return (_number(value.get("min"), 0) + _number(value.get("max"), 0)) / 2
        if kind.endswith("binomial"):
            return _number(value.get("n"), 0) * _number(value.get("p"), 0)
        if "value" in value:
            return _number(value["value"], default)
    return default


def _chance(condition: Any) -> float:
    """How likely a loot condition passes for a player with a plain tool and no enchantments."""
    if isinstance(condition, list):
        p = 1.0
        for c in condition:
            p *= _chance(c)
        return p
    if isinstance(condition, str):
        return 0.0 if ("silk_touch" in condition or "shear" in condition) else 1.0
    if not isinstance(condition, dict):
        return 1.0
    kind = short(condition.get("type", ""))
    if kind == "table_bonus":
        chances = condition.get("chances") or [1.0]
        return float(chances[0])
    if kind == "random_chance":
        return _number(condition.get("chance"), 1.0)
    if kind == "random_chance_with_enchanted_bonus":
        return _number(condition.get("unenchanted_chance"), 0.5)
    if kind == "inverted":
        return 1.0 - _chance(condition.get("term"))
    if kind == "any_of":
        return max((_chance(t) for t in condition.get("terms", [])), default=0.0)
    if kind == "all_of":
        return _chance(condition.get("terms", []))
    if kind in ("match_tool", "entity_properties", "damage_source_properties"):
        # A special tool, or a special kind of mob or death (a baby riding a chicken, killed by lightning).
        return 0.0
    return 1.0


def _conditions(node: dict[str, Any]) -> float:
    p = 1.0
    if "condition" in node:
        p *= _chance(node["condition"])
    if "conditions" in node:
        p *= _chance(node["conditions"])
    return p


def _count(entry: dict[str, Any]) -> float:
    n = 1.0
    for modifier in entry.get("modifier", entry.get("functions", [])) or []:
        if isinstance(modifier, dict) and short(modifier.get("type", modifier.get("function", ""))) == "set_count":
            n = _number(modifier.get("count"), 1.0)
    return n


def _entry(entry: dict[str, Any], drops: dict[str, float], weight: float) -> float:
    """Adds an entry's expected drops; returns the chance that it produced something."""
    p = _conditions(entry)
    if p <= 0:
        return 0.0
    kind = short(entry.get("type", "item"))
    if kind == "item":
        item = short(entry.get("name", ""))
        if item and item != "air":
            drops[item] = drops.get(item, 0.0) + weight * p * _count(entry)
        return p
    children = entry.get("children", [])
    if kind == "alternatives":
        left = 1.0
        for child in children:
            sub: dict[str, float] = {}
            q = _entry(child, sub, 1.0)
            for item, n in sub.items():
                drops[item] = drops.get(item, 0.0) + weight * p * left * n
            left *= 1.0 - q
        return p * (1.0 - left)
    if kind in ("group", "sequence"):
        for child in children:
            _entry(child, drops, weight * p)
        return p
    return 0.0


def _loot(data: dict[str, Any]) -> dict[str, float]:
    drops: dict[str, float] = {}
    for pool in data.get("pools", []):
        p = _conditions(pool)
        if p <= 0:
            continue
        rolls = _number(pool.get("rolls"), 1.0)
        entries = pool.get("entries", [])
        weights = [max(1.0, float(e.get("weight", 1))) for e in entries]
        if len(entries) > 1 and all(short(e.get("type", "item")) == "item" for e in entries):
            # One entry is picked per roll, by weight.
            total = sum(weights)
            for e, w in zip(entries, weights):
                _entry(e, drops, p * rolls * w / total)
        else:
            for e in entries:
                _entry(e, drops, p * rolls)
    return {item: round(n, 3) for item, n in drops.items() if n >= 0.005}


# The whole file -------------------------------------------------------------------------------

def build(path: Path, version: str = "unknown") -> dict[str, Any]:
    source = _Source(path)
    item_tags = _tags(source, "item")
    block_tags = _tags(source, "block")
    recipes = []
    for name, data in source.files("recipe"):
        recipe = _recipe(name, data)
        if recipe is not None:
            recipes.append(recipe)
    blocks = {}
    for name, data in source.files("loot_table/blocks"):
        drops = _loot(data)
        if drops:
            blocks[name] = drops
    mobs = {}
    for name, data in source.files("loot_table/entities"):
        if "/" in name:
            continue  # variants such as sheep colours
        drops = _loot(data)
        if drops:
            mobs[name] = drops
    # Sheep drop wool by colour from sub-tables; the usual sheep is white and gives one.
    if "sheep" in mobs:
        mobs["sheep"].setdefault("white_wool", 1.0)
    tools: dict[str, dict[str, Any]] = {}
    for tool in ("pickaxe", "axe", "shovel", "hoe"):
        for block in block_tags.get(f"mineable/{tool}", []):
            tools.setdefault(block, {})["tool"] = tool
    for tag, level in _NEEDS.items():
        for block in block_tags.get(tag, []):
            tools.setdefault(block, {})["tier"] = level
    keep_block_tags = {k: v for k, v in block_tags.items()
                       if re.search(r"(^|_)(ores|logs|leaves|planks|wool|sand|dirt)$", k)}
    return {
        "version": version,
        "recipes": sorted(recipes, key=lambda r: r["id"]),
        "item_tags": item_tags,
        "block_tags": keep_block_tags,
        "blocks": blocks,
        "mobs": mobs,
        "tools": tools,
    }


def write(data: dict[str, Any], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), sort_keys=True)
    return out
