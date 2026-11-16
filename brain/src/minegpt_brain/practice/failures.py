"""The failure miner: where MineGPT got stuck or failed, grouped by cause and ranked by time lost.

The mod sends a *scene* (the blocks in a 9 x 6 x 9 box round the player, see ``Scene.java``)
with every skill failure, and a ``stall`` event when a skill makes no progress for 15 seconds
followed by ``stall_end`` with how long it lasted. Each one is filed here as a record in
``failures/<date>.jsonl`` in the data folder.

Records are grouped by *cause*: the skill, the gist of the message (numbers and coordinates taken
out) and what the scene shows (in water, in a pit, a torch or plant in the way, leaves, lava).
:func:`report` ranks causes by minutes lost, so the worst trouble is fixed first. A scene can be
kept as a JUnit test (:func:`fixture`, ``practice keep``) or rebuilt in game to try the skill again
(:func:`scene_commands`, ``practice replay``).
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

#: Pathfinder cell codes (BlockView.java).
UNLOADED, PASSABLE, SOLID, OBSTACLE, WATER, DANGER, CLIMBABLE, SCAFFOLDING, DOOR = range(9)
#: Blocks with no collision that still catch the crosshair: the cause of many jump loops.
SOFT = ("torch", "short_grass", "tall_grass", "fern", "dead_bush", "dandelion", "poppy", "flower", "tulip",
        "orchid", "allium", "bluet", "daisy", "cornflower", "lily_of_the_valley", "sapling", "mushroom",
        "sweet_berry", "snow[", "seagrass", "vine", "wildflowers", "leaf_litter", "bush", "petals")


@dataclass
class Group:
    cause: str
    skill: str
    tags: list[str]
    count: int = 0
    seconds: float = 0.0
    ids: list[str] = field(default_factory=list)
    last: str = ""
    example: str = ""

    @property
    def minutes(self) -> float:
        return self.seconds / 60


def gist(message: str) -> str:
    """The message with numbers, coordinates and item lists taken out, so alike failures match."""
    text = (message or "").lower()
    text = re.sub(r"minecraft:", "", text)
    text = re.sub(r"-?\d+(\.\d+)?", "#", text)
    text = re.sub(r"(# ?){2,}", "# ", text)
    text = re.sub(r"\s+", " ", text).strip(" .;:")
    return text[:70]


class Box:
    """A scene's cells, read by position relative to the player's feet."""

    def __init__(self, scene: Mapping[str, Any]):
        self.scene = scene
        self.w, self.h, self.d = scene.get("size", [9, 6, 9])
        self.cells = scene.get("cells", "")
        self.blocks = scene.get("blocks", [])
        self.palette = scene.get("palette", [])
        origin, feet = scene.get("origin", [0, 0, 0]), scene.get("feet", [0, 0, 0])
        self.fx, self.fy, self.fz = (feet[i] - origin[i] for i in range(3))

    def _index(self, dx: int, dy: int, dz: int) -> Optional[int]:
        x, y, z = self.fx + dx, self.fy + dy, self.fz + dz
        if 0 <= x < self.w and 0 <= y < self.h and 0 <= z < self.d:
            return (y * self.d + z) * self.w + x
        return None

    def cell(self, dx: int, dy: int, dz: int) -> int:
        i = self._index(dx, dy, dz)
        return int(self.cells[i]) if i is not None and i < len(self.cells) else UNLOADED

    def block(self, dx: int, dy: int, dz: int) -> str:
        i = self._index(dx, dy, dz)
        if i is None or i >= len(self.blocks):
            return ""
        p = self.blocks[i]
        return self.palette[p] if 0 <= p < len(self.palette) else ""

    def around(self, radius: int, dys: Iterable[int]) -> Iterable[tuple[int, int, int]]:
        for dy in dys:
            for dx in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    yield dx, dy, dz


def tags(scene: Optional[Mapping[str, Any]]) -> list[str]:
    """What the scene shows about why the player was stuck, most telling first."""
    if not scene or not scene.get("cells"):
        return []
    box = Box(scene)
    out = []
    if scene.get("underWater"):
        out.append("under water")
    elif scene.get("inWater"):
        out.append("in water")
    walls = 0
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        if box.cell(dx, 0, dz) in (SOLID, OBSTACLE) and box.cell(dx, 1, dz) in (SOLID, OBSTACLE):
            walls += 1
    if walls == 4:
        out.append("in a pit" if box.cell(0, 2, 0) in (PASSABLE, WATER) else "boxed in")
    elif walls == 3:
        out.append("in a dead end")
    if any(any(s in box.block(dx, dy, dz) for s in SOFT) for dx, dy, dz in box.around(1, (0, 1))):
        out.append("plant or torch in the way")
    leaves = sum("leaves" in box.block(dx, dy, dz) for dx, dy, dz in box.around(1, (-1, 0, 1, 2)))
    if leaves >= 4:
        out.append("in leaves")
    if any(box.cell(dx, dy, dz) == DANGER for dx, dy, dz in box.around(2, (-1, 0, 1))):
        out.append("lava or fire near")
    if box.cell(0, -1, 0) in (PASSABLE,) and not scene.get("onGround") and not scene.get("inWater"):
        out.append("in the air")
    elif box.cell(0, -1, 0) in (SOLID, SCAFFOLDING) and all(
            box.cell(dx, -1, dz) == PASSABLE for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))):
        out.append("on a pillar")
    return out


def cause(skill: str, message: str, scene_tags: list[str]) -> str:
    where = f" [{scene_tags[0]}]" if scene_tags else ""
    return f"{skill}: {gist(message) or 'no message'}{where}"


class FailureLog:
    """Failure and stall records, one JSON line each, in a file per day."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self._open_stalls: dict[str, dict[str, Any]] = {}
        self._n = 0
        #: Stalls filed by this log so far, so a replay can tell whether the skill stalled again.
        self.stalls = 0

    def _new_id(self) -> str:
        self._n += 1
        return time.strftime("%Y%m%d-%H%M%S") + f"-{self._n}"

    def add_failure(self, skill: str, args: Mapping[str, Any], message: str, seconds: float,
                    scene: Optional[Mapping[str, Any]], goal: Optional[str] = None) -> dict[str, Any]:
        found = tags(scene)
        record = {"id": self._new_id(), "kind": "fail", "time": time.strftime("%Y-%m-%d %H:%M:%S"), "skill": skill,
                  "args": _small(args), "message": message[:300], "seconds": round(seconds, 1), "goal": goal,
                  "tags": found, "cause": cause(skill, message, found), "scene": scene}
        self._write(record)
        return record

    def add_stall(self, event: Mapping[str, Any], goal: Optional[str] = None,
                  args: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        """A ``stall`` event: filed now with the 15 seconds so far; ``stall_end`` adds the rest."""
        skill = str(event.get("skill") or "?")
        scene = event.get("scene")
        found = tags(scene)
        status = str(event.get("status") or "")
        record = {"id": self._new_id(), "kind": "stall", "time": time.strftime("%Y-%m-%d %H:%M:%S"), "skill": skill,
                  "args": _small(args or {}), "message": status[:300], "seconds": float(event.get("seconds") or 15.0),
                  "goal": goal,
                  "tags": found, "cause": cause(skill, "stalled: " + _status_gist(status), found), "scene": scene}
        self._write(record)
        self._open_stalls[skill] = record
        self.stalls += 1
        return record

    def end_stall(self, event: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """A ``stall_end`` event: files how long the stall went on for, as an update line."""
        record = self._open_stalls.pop(str(event.get("skill") or "?"), None)
        if record is None:
            return None
        update = {"id": record["id"], "kind": "stall_end", "seconds": round(float(event.get("seconds") or 0), 1)}
        self._write(update)
        return update

    def records(self, days: Optional[int] = None) -> list[dict[str, Any]]:
        """Every record (oldest first), stall lengths filled in, from the last ``days`` days or all."""
        out: dict[str, dict[str, Any]] = {}
        files = sorted(self.folder.glob("*.jsonl")) if self.folder.exists() else []
        if days is not None:
            cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
            files = [f for f in files if f.stem >= cutoff]
        for path in files:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") == "stall_end":
                    if rec.get("id") in out:
                        out[rec["id"]]["seconds"] = rec["seconds"]
                else:
                    out[rec["id"]] = rec
        return list(out.values())

    def get(self, record_id: str) -> Optional[dict[str, Any]]:
        matches = [r for r in self.records() if r["id"] == record_id or r["id"].startswith(record_id)]
        return matches[-1] if matches else None

    def _write(self, record: Mapping[str, Any]) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with (self.folder / f"{time.strftime('%Y-%m-%d')}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            pass


def report(records: Iterable[Mapping[str, Any]]) -> list[Group]:
    """Causes ranked by minutes lost, worst first."""
    groups: dict[str, Group] = {}
    for rec in records:
        g = groups.get(rec["cause"])
        if g is None:
            g = groups[rec["cause"]] = Group(rec["cause"], rec["skill"], list(rec.get("tags") or []))
        g.count += 1
        g.seconds += float(rec.get("seconds") or 0)
        g.ids.append(rec["id"])
        g.last = max(g.last, rec.get("time", ""))
        g.example = rec.get("message", "")[:120]
    return sorted(groups.values(), key=lambda g: (-g.seconds, -g.count))


def stall_minutes(records: Iterable[Mapping[str, Any]], since: str = "") -> float:
    """Minutes lost to stalls in records filed at or after ``since`` (a ``%Y-%m-%d %H:%M:%S`` time)."""
    return sum(float(r.get("seconds") or 0) for r in records
               if r.get("kind") == "stall" and r.get("time", "") >= since) / 60


def fixture(record: Mapping[str, Any], expect: str = "escape", breaking: bool = True) -> str:
    """The record's scene as a ``.scene`` file for the mod's SceneTest (JUnit)."""
    scene = record.get("scene") or {}
    if not scene.get("cells"):
        raise ValueError(f"record {record.get('id')} has no scene")
    w, h, d = scene["size"]
    box = Box(scene)
    lines = [f"# {record['id']}: {record['cause']}", f"# {record.get('message', '')[:150]}",
             f"# goal: {record.get('goal')}", f"size {w} {h} {d}", f"start {box.fx} {box.fy} {box.fz}",
             f"expect {expect}", f"breaking {str(breaking).lower()}", "cells"]
    cells = scene["cells"]
    for y in range(h):
        for z in range(d):
            start = (y * d + z) * w
            lines.append(cells[start:start + w])
    return "\n".join(lines) + "\n"


def scene_commands(scene: Mapping[str, Any], origin: tuple[int, int, int]) -> list[str]:
    """Commands that rebuild a scene with its lower corner at ``origin``: clear, then every block that is not air.

    Water and lava are put in last, so they settle against the blocks round them."""
    w, h, d = scene["size"]
    x0, y0, z0 = origin
    commands = [f"fill {x0 - 1} {y0} {z0 - 1} {x0 + w} {y0 + h + 2} {z0 + d} minecraft:air",
                f"fill {x0 - 1} {y0 - 1} {z0 - 1} {x0 + w} {y0 - 1} {z0 + d} minecraft:stone"]
    palette, blocks = scene.get("palette", []), scene.get("blocks", [])
    late = []
    for i, p in enumerate(blocks):
        state = palette[p] if 0 <= p < len(palette) else "minecraft:air"
        if state.startswith(("minecraft:air", "minecraft:cave_air", "minecraft:void_air")):
            continue
        x, z, y = i % w, (i // w) % d, i // (w * d)
        command = f"setblock {x0 + x} {y0 + y} {z0 + z} {state}"
        (late if state.startswith(("minecraft:water", "minecraft:lava")) else commands).append(command)
    return commands + late


def _status_gist(status: str) -> str:
    # The skill's status line names the step it was on, which is what tells stalls apart.
    return gist(status.split(":")[0] if ":" in status else status)


def _small(args: Mapping[str, Any]) -> dict[str, Any]:
    """Arguments without long block lists, which builds send by the hundred."""
    return {k: (v if not isinstance(v, list) or len(v) <= 12 else f"<{len(v)} items>") for k, v in args.items()}


def by_cause(records: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for rec in records:
        out[rec["cause"]].append(rec)
    return out
