"""Structures to build: drawn as layers of text, turned into block lists for the build skill.

A blueprint is drawn the way a model (or a person) can write it down::

    {"key": {"c": "cobblestone", "p": "oak_planks", "d": "oak_door"},
     "layers": [               # bottom layer first
        ["ccccc",              # each layer: rows from north (-z) to south (+z),
         "c...c",              # characters from west (-x) to east (+x)
         "ccccc"],
        ["ppppp", "p...p", "pp.pp"],
     ]}

``.`` and spaces leave the spot as it is. The first character of the first row of the bottom
layer sits at the origin given to ``build``. Saved blueprints live in ``blueprints.json`` in the
data folder, so a structure designed once can be built again elsewhere.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from .state import full

#: Characters that leave a spot alone.
EMPTY = {".", " "}
#: The build skill takes at most this many blocks per call.
MAX_BLOCKS = 2048


def expand(layers: Any, key: Any) -> list[tuple[int, int, int, str]]:
    """Relative blocks ``(dx, dy, dz, item)`` from a drawing. Raises ValueError on a bad one."""
    if not isinstance(layers, list) or not layers:
        raise ValueError("layers is a list of layers, bottom first; each layer is a list of row strings")
    if not isinstance(key, dict) or not key:
        raise ValueError('key maps each character to a block item, like {"c": "cobblestone"}')
    for char, item in key.items():
        if not isinstance(char, str) or len(char) != 1 or char in EMPTY:
            raise ValueError(f"key entries must be single characters other than '.' and space, not {char!r}")
        if not isinstance(item, str) or not item or item.startswith("#"):
            raise ValueError(f"key {char!r} must name one block item, not {item!r}")
    blocks = []
    for dy, layer in enumerate(layers):
        if isinstance(layer, str):
            layer = layer.split("\n")
        if not isinstance(layer, list) or not all(isinstance(row, str) for row in layer):
            raise ValueError(f"layer {dy + 1} must be a list of row strings")
        for dz, row in enumerate(layer):
            for dx, char in enumerate(row):
                if char in EMPTY:
                    continue
                if char not in key:
                    raise ValueError(f"layer {dy + 1}, row {dz + 1}: {char!r} is not in the key")
                blocks.append((dx, dy, dz, full(key[char])))
    if not blocks:
        raise ValueError("the drawing has no blocks in it")
    if len(blocks) > MAX_BLOCKS:
        raise ValueError(f"{len(blocks)} blocks is more than {MAX_BLOCKS}; build it in parts")
    return blocks


def from_relative(blocks: Any) -> list[tuple[int, int, int, str]]:
    """Checks a list of ``[dx, dy, dz, item]``."""
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("blocks is a list of [dx, dy, dz, item]")
    out = []
    for i, b in enumerate(blocks, 1):
        if (not isinstance(b, (list, tuple)) or len(b) != 4 or not all(isinstance(v, int) for v in b[:3])
                or not isinstance(b[3], str)):
            raise ValueError(f"block {i} must be [dx, dy, dz, item] with whole numbers")
        out.append((b[0], b[1], b[2], full(b[3])))
    if len(out) > MAX_BLOCKS:
        raise ValueError(f"{len(out)} blocks is more than {MAX_BLOCKS}; build it in parts")
    return out


def place_at(blocks: Iterable[tuple[int, int, int, str]], x: int, y: int, z: int) -> list[list[Any]]:
    """World-coordinate blocks for the build skill, with the drawing's origin at x y z."""
    return [[x + dx, y + dy, z + dz, item] for dx, dy, dz, item in blocks]


def materials(blocks: Iterable[tuple[int, int, int, str]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for *_, item in blocks:
        totals[item] = totals.get(item, 0) + 1
    return totals


def size(blocks: Iterable[tuple[int, int, int, str]]) -> tuple[int, int, int]:
    """Width (x), height (y) and depth (z)."""
    blocks = list(blocks)
    return tuple(max(b[i] for b in blocks) - min(b[i] for b in blocks) + 1 for i in range(3))  # type: ignore[return-value]


class BlueprintLibrary:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.blueprints: dict[str, dict[str, Any]] = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.blueprints = {}

    def save(self, name: str, description: str, layers: list[Any], key: dict[str, str]) -> list[tuple]:
        blocks = expand(layers, key)
        self.blueprints[name] = {"name": name, "description": description, "layers": layers, "key": key,
                                 "saved": time.strftime("%Y-%m-%d %H:%M")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.blueprints, indent=1))
        tmp.replace(self.path)
        return blocks

    def get(self, name: str) -> Optional[dict[str, Any]]:
        if name in self.blueprints:
            return self.blueprints[name]
        lowered = {k.lower(): v for k, v in self.blueprints.items()}
        return lowered.get(name.lower())

    def blocks(self, name: str) -> list[tuple[int, int, int, str]]:
        blueprint = self.get(name)
        if blueprint is None:
            known = ", ".join(repr(n) for n in self.blueprints) or "none yet"
            raise ValueError(f"no blueprint named {name!r}; saved blueprints: {known}")
        return expand(blueprint["layers"], blueprint["key"])

    def describe(self) -> list[str]:
        lines = []
        for bp in self.blueprints.values():
            blocks = expand(bp["layers"], bp["key"])
            w, h, d = size(blocks)
            needs = ", ".join(f"{n} {item.removeprefix('minecraft:')}" for item, n in materials(blocks).items())
            lines.append(f"{bp['name']!r} ({w}x{h}x{d}): {bp['description']} Needs {needs}.")
        return lines
