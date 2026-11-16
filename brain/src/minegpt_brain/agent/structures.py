"""Ready-made structures: houses, towers, walls, pens and a whole base, sized and styled to order.

The model does not have to draw these block by block. It picks a kind, a size and a style
(``build_structure(kind="house", style="spruce", width=9, ...)``), and the generator here
works out every block, which way stairs, doors, chests and beds face, what to dig out first,
and how many of each item the build needs. A big structure comes in parts (walls, roof,
inside), each built by one or more calls of the mod's build skill, so a build that stops
half way can be picked up again: blocks already standing are skipped.

Coordinates: ``x`` grows east, ``z`` grows south, ``y`` up. A structure is generated with its
front (the door) facing south and then turned to face the way asked. The origin given to a
build is the west, north corner of the footprint at the level the player walks on: the first
layer of air above the ground. Floors go one block lower, in place of the grass.

A block is ``(dx, dy, dz, item, facing)``. ``facing`` is None or the text the mod's place
skill reads: a compass direction the player must look in when placing (stairs rise that way,
doors and beds point that way, chests face back at the player), ``top`` or ``bottom`` for the
half of a slab, ``on:<side>`` for the side of the spot the supporting block must be on
(``on:down`` keeps a torch on the floor), and ``axis:x``, ``axis:y`` or ``axis:z`` for the way a
log lies (a log with none stands upright, as a post). The item ``air`` marks a spot to dig out.

Looks: buildings follow a few rules that keep them from coming out as flat boxes. Each style is
a small palette (a main wall block, a contrasting trim for posts, a footing course, a roof and a
light). Walls get a post at least every fourth block with a window centred in each bay between
posts, so no stretch of plain wall is longer than three. Pitched roofs overhang the walls by a
block all round. A stone footing course runs under the walls and, where the ground falls away,
is carried down to it by the build skill. ``texture="mixed"`` swaps stone-like walls for a
patchy blend of similar blocks. ``detail=False`` gives the plain box back, which is cheaper.

Setting: a house with ``trim`` (the default with detail) gets a chimney up one gable end. The
``yard`` kind dresses the plot round a house: a path from the door to a gate, a fence round the
plot, and lamp posts at the gate and the corners. It takes the house's own origin, width and
depth, and reaches ``margin`` blocks beyond the house on every side (``Structure.anchor`` says
where the origin given sits inside it). Which materials suit where is in ``stylebook``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .state import full, short

Block = tuple[int, int, int, str, Optional[str]]

AIR = "minecraft:air"
#: Blocks per build call: the mod allows 12 seconds a block, and a call should end well inside an hour.
CHUNK = 160

#: Turning a quarter clockwise seen from above: south -> west -> north -> east.
_TURN = {"south": "west", "west": "north", "north": "east", "east": "south"}
FACINGS = ("south", "west", "north", "east")


def _wood(kind: str) -> dict[str, str]:
    return {"wall": f"{kind}_planks", "post": f"{kind}_log", "floor": f"{kind}_planks", "foundation": "cobblestone",
            "roof": f"{kind}_stairs", "roof_fill": f"{kind}_planks", "ridge": f"{kind}_slab", "door": f"{kind}_door",
            "fence": f"{kind}_fence", "gate": f"{kind}_fence_gate", "window": "glass_pane", "light": "torch",
            "ladder": "ladder", "path": "cobblestone", "chimney": "cobblestone"}


#: Styles: which item plays which role. Any role can be overridden with ``materials``.
PALETTES: dict[str, dict[str, str]] = {
    "oak": _wood("oak"),
    "spruce": _wood("spruce"),
    "birch": _wood("birch"),
    "dark_oak": _wood("dark_oak"),
    "acacia": _wood("acacia"),
    "jungle": _wood("jungle"),
    "cherry": _wood("cherry"),
    "mangrove": _wood("mangrove"),
    # Stone walls keep log posts and a wooden floor: the contrast is what stops them reading as one grey slab.
    "cobblestone": {**_wood("oak"), "wall": "cobblestone", "floor": "oak_planks",
                    "roof": "cobblestone_stairs", "roof_fill": "cobblestone", "ridge": "cobblestone_slab"},
    "stone_brick": {**_wood("spruce"), "wall": "stone_bricks",
                    "roof": "stone_brick_stairs", "roof_fill": "stone_bricks", "ridge": "stone_brick_slab"},
    "sandstone": {**_wood("birch"), "wall": "sandstone", "post": "cut_sandstone", "foundation": "sandstone",
                  "floor": "sandstone", "path": "sandstone", "chimney": "sandstone"},
    # Whatever is to hand: for a first night.
    "dirt": {**_wood("oak"), "wall": "dirt", "post": "dirt", "floor": "dirt", "foundation": "dirt",
             "roof_fill": "dirt", "chimney": "dirt"},
}


#: Blends for ``texture="mixed"``: a block and the similar-looking ones to break it up with, by weight.
MIXES: dict[str, list[tuple[str, int]]] = {
    "cobblestone": [("cobblestone", 6), ("andesite", 2), ("stone", 2)],
    "stone_bricks": [("stone_bricks", 7), ("cracked_stone_bricks", 2), ("mossy_stone_bricks", 1)],
    "sandstone": [("sandstone", 7), ("cut_sandstone", 3)],
    "stone": [("stone", 6), ("andesite", 2), ("cobblestone", 2)],
    "deepslate_bricks": [("deepslate_bricks", 7), ("cracked_deepslate_bricks", 2), ("deepslate_tiles", 1)],
}
TEXTURES = ("plain", "mixed")
#: Roles a blend applies to: big surfaces, not trim or roofs.
_MIXED_ROLES = ("wall", "foundation", "path", "chimney")


def _hash(x: int, y: int, z: int) -> float:
    """A fixed pseudo-random number from 0 to 1 for a position: the same every time, so a build can be resumed."""
    n = (x * 73856093) ^ (y * 19349663) ^ (z * 83492791)
    n = (n ^ (n >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFF) / 0x10000


def _noise(x: int, y: int, z: int) -> float:
    """Patchy noise: neighbours tend to match, so a blend comes out in small patches, not as static."""
    return (0.55 * _hash(x >> 1, y >> 1, z >> 1) + 0.45 * _hash(x + 57, y + 131, z + 911)) % 1.0


def mixed(item: str, x: int, y: int, z: int) -> str:
    """The block a blend puts at this spot, or the item itself when it has no blend."""
    blend = MIXES.get(short(item))
    if not blend:
        return item
    pick = _noise(x, y, z) * sum(w for _, w in blend)
    for name, weight in blend:
        pick -= weight
        if pick < 0:
            return full(name)
    return full(blend[0][0])


@dataclass
class Part:
    name: str
    blocks: list[Block] = field(default_factory=list)
    #: Carry this part's lowest layer down to the ground with the structure's footing block.
    footing: bool = False
    #: Dressing: blocks of it that cannot be placed do not hold the build up.
    optional: bool = False


@dataclass
class Structure:
    kind: str
    description: str
    parts: list[Part]
    #: Where to stand to walk in, relative to the origin: just outside the door.
    entrance: Optional[tuple[int, int, int]] = None
    notes: list[str] = field(default_factory=list)
    #: The block footings are made of where the ground falls away under the lowest layer, or None.
    footing: Optional[str] = None
    #: Where the origin given to a build sits inside the structure, in x and z. (0, 0) for most:
    #: the west, north corner. A yard reaches out beyond the house whose origin it is given.
    anchor: tuple[int, int] = (0, 0)
    #: Whether the site is cleared of trees and levelled above the walking level before building.
    clear_site: bool = True

    @property
    def blocks(self) -> list[Block]:
        return [b for part in self.parts for b in part.blocks]

    def size(self) -> tuple[int, int, int]:
        """Width (x), height (y) and depth (z) of everything, the dug-out spots included."""
        blocks = self.blocks
        return tuple(max(b[i] for b in blocks) - min(b[i] for b in blocks) + 1 for i in (0, 1, 2))  # type: ignore

    def footprint(self) -> tuple[int, int]:
        blocks = [b for b in self.blocks if b[3] != AIR]
        return (max(b[0] for b in blocks) - min(b[0] for b in blocks) + 1,
                max(b[2] for b in blocks) - min(b[2] for b in blocks) + 1)

    def materials(self) -> dict[str, int]:
        return materials(self.blocks)


def materials(blocks: list[Block]) -> dict[str, int]:
    """Items needed, most first. Dug-out spots need none."""
    totals: dict[str, int] = {}
    for b in blocks:
        if b[3] != AIR:
            totals[b[3]] = totals.get(b[3], 0) + 1
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


class _Canvas:
    """Collects blocks by position; a later block at the same spot replaces an earlier one."""

    def __init__(self, palette: dict[str, str], texture: str = "plain"):
        if texture not in TEXTURES:
            raise ValueError(f"texture must be one of {', '.join(TEXTURES)}")
        self.palette = palette
        self.texture = texture
        self.cells: dict[tuple[int, int, int], tuple[str, Optional[str]]] = {}

    def put(self, x: int, y: int, z: int, role: str, facing: Optional[str] = None) -> None:
        item = AIR if role == "air" else full(self.palette.get(role, role))
        if self.texture == "mixed" and role in _MIXED_ROLES:
            item = mixed(item, x, y, z)
        self.cells[(x, y, z)] = (item, facing)

    def box(self, x1: int, y1: int, z1: int, x2: int, y2: int, z2: int, role: str) -> None:
        for x in range(x1, x2 + 1):
            for y in range(y1, y2 + 1):
                for z in range(z1, z2 + 1):
                    self.put(x, y, z, role)

    def ring(self, x1: int, z1: int, x2: int, z2: int, y1: int, y2: int, role: str,
             corner: Optional[str] = None) -> None:
        """The outline of a rectangle, as a wall from y1 to y2."""
        for x in range(x1, x2 + 1):
            for z in range(z1, z2 + 1):
                if x in (x1, x2) or z in (z1, z2):
                    is_corner = x in (x1, x2) and z in (z1, z2)
                    for y in range(y1, y2 + 1):
                        self.put(x, y, z, corner if corner and is_corner else role)

    def clear(self, x: int, y: int, z: int) -> None:
        self.cells.pop((x, y, z), None)

    def take(self) -> list[Block]:
        """The blocks so far, dug-out spots first, then bottom up; the canvas starts again empty."""
        out = [(x, y, z, item, facing) for (x, y, z), (item, facing) in self.cells.items()]
        out.sort(key=lambda b: (b[3] != AIR, b[1], b[2], b[0]))
        self.cells = {}
        return out


def palette(style: str = "oak", overrides: Optional[dict[str, str]] = None) -> dict[str, str]:
    if style not in PALETTES:
        raise ValueError(f"unknown style {style!r}; styles: {', '.join(PALETTES)}")
    chosen = dict(PALETTES[style])
    for role, item in (overrides or {}).items():
        if role not in chosen:
            raise ValueError(f"unknown material role {role!r}; roles: {', '.join(chosen)}")
        if not isinstance(item, str) or not item or item.startswith("#"):
            raise ValueError(f"material {role!r} must name one block item, not {item!r}")
        chosen[role] = item
    return chosen


def _limit(name: str, value: Any, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"{name} must be a whole number from {low} to {high}, not {value!r}")
    return value


# Generators ---------------------------------------------------------------------------------
# Each builds with the front facing south (+z) and x from 0 east, z from 0 south.


def _posts(length: int) -> list[int]:
    """Where posts go along a wall this long: both ends, and between them so no gap is over three blocks."""
    bays = max(1, math.ceil((length - 1) / 4))
    spots = set()
    for i in range(bays + 1):
        p = round(i * (length - 1) / bays)
        spots |= {p, length - 1 - p}
    return sorted(spots)


def _bay_windows(length: int, blocked: Callable[[int], bool] = lambda i: False) -> list[int]:
    """One window per bay between posts: the free spot nearest the middle of the bay, leaning to the wall's middle."""
    posts = _posts(length)
    centre = (length - 1) / 2
    out = []
    for a, b in zip(posts, posts[1:]):
        inner = [i for i in range(a + 1, b) if not blocked(i)]
        if inner:
            out.append(min(inner, key=lambda i: (abs(i - (a + b) / 2), abs(i - centre))))
    return out


def _room(c: _Canvas, x0: int, z0: int, width: int, depth: int, height: int, *, floor: bool, windows: bool,
          door: bool = True, detail: bool = False) -> int:
    """Floor, walls with corner posts, windows and a door in the middle of the south wall. Returns the door's x.

    With ``detail`` the walls are framed: a post at least every fourth block, a window centred in
    each bay, a footing course of the foundation block under tall walls, and torches by the door.
    """
    x1, z1 = x0 + width - 1, z0 + depth - 1
    door_x = x0 + width // 2
    # Dig the inside out first, and the doorway with a step in front of it.
    c.box(x0 + 1, 0, z0 + 1, x1 - 1, height - 1, z1 - 1, "air")
    if door:
        c.box(door_x, 0, z1 + 1, door_x, 1, z1 + 1, "air")
    if floor:
        c.box(x0, -1, z0, x1, -1, z1, "foundation")
        c.box(x0 + 1, -1, z0 + 1, x1 - 1, -1, z1 - 1, "floor")
    c.ring(x0, z0, x1, z1, 0, height - 1, "wall", corner="post")
    if detail:
        if height >= 4:
            c.ring(x0, z0, x1, z1, 0, 0, "foundation", corner="post")
        for i in _posts(width):
            for y in range(height):
                c.put(x0 + i, y, z0, "post")
                c.put(x0 + i, y, z1, "post")
        for i in _posts(depth):
            for y in range(height):
                c.put(x0, y, z0 + i, "post")
                c.put(x1, y, z0 + i, "post")
    if windows and height >= 3:
        rows = (1, 2) if detail and height >= 5 else (1,)
        if detail:
            by_door = (lambda i: door and abs(x0 + i - door_x) < 2)
            north, south = _bay_windows(width), _bay_windows(width, by_door)
            sides = _bay_windows(depth)
        else:
            north = list(range(2, width - 2, 2))
            south = [i for i in north if abs(x0 + i - door_x) >= 2]
            sides = list(range(2, depth - 2, 2))
        for y in rows:
            for i in north:
                c.put(x0 + i, y, z0, "window")
            for i in south:
                c.put(x0 + i, y, z1, "window")
            for i in sides:
                c.put(x0, y, z0 + i, "window")
                c.put(x1, y, z0 + i, "window")
    if door:
        # Placed from outside looking in; the spot above it stays open for the door's top half.
        c.put(door_x, 0, z1, "door", "north")
        c.clear(door_x, 1, z1)
        if detail:
            for x in (door_x - 1, door_x + 1):
                if x0 <= x <= x1:
                    c.put(x, 0, z1 + 1, "light", "on:down")
    return door_x


def _gable_roof(c: _Canvas, x0: int, z0: int, width: int, depth: int, y: int, overhang: int = 0) -> None:
    """A pitched roof with its ridge running east-west: stairs up both slopes over a solid core,
    so every block rests on the one below and the room keeps a flat ceiling.

    With ``overhang`` the slopes start that far outside the walls and that much lower, as eaves,
    and the roof runs on past the end walls by as much. The corners of the eaves are left off:
    nothing there has a solid face to be placed against.
    """
    x1 = x0 + width - 1
    low, high, level = z0 - overhang, z0 + depth - 1 + overhang, y - overhang
    while low <= high:
        eave = level < y
        for x in range(x0 - overhang, x1 + overhang + 1):
            if eave and not x0 <= x <= x1:
                continue
            if low == high:
                c.put(x, level, low, "ridge", "bottom")
            else:
                c.put(x, level, low, "roof", "south")
                c.put(x, level, high, "roof", "north")
                if not eave:
                    for z in range(low + 1, high):
                        c.put(x, level, z, "roof_fill")
        low, high, level = low + 1, high - 1, level + 1


def _chimney(c: _Canvas, x: int, z: int, top: int) -> None:
    """A chimney up the outside of a gable end, from the ground to one block over the ridge. It takes
    the place of the roof's overhang where it passes through it."""
    for y in range(-1, top + 1):
        c.put(x, y, z, "chimney")


def _furnish(c: _Canvas, x0: int, z0: int, width: int, depth: int, items: list[str]) -> None:
    """Furniture along the back (north) wall, a bed in the north-west corner, torches by the front wall."""
    x1, z1 = x0 + width - 1, z0 + depth - 1
    wanted = list(items)
    if "bed" in wanted and depth >= 5:
        wanted.remove("bed")
        # The foot goes down first and the head lands one block north of it.
        c.put(x0 + 1, 0, z0 + 2, "white_bed", "north")
    slots = list(range(x1 - 1, x0 + 1, -1))
    for item, x in zip([i for i in wanted if i != "bed"], slots):
        # Chests and furnaces face back at the player, so they open towards the room.
        c.put(x, 0, z0 + 1, item, "north" if item in ("chest", "furnace", "barrel") else None)
    for x in {x0 + 1, x1 - 1}:
        if x != x0 + width // 2:
            c.put(x, 0, z1 - 1, "light", "on:down")


def house(width: int = 7, depth: int = 7, height: int = 3, style: str = "oak", roof: str = "gable",
          floor: bool = True, windows: bool = True, furnish: Optional[list[str]] = None,
          materials: Optional[dict[str, str]] = None, detail: bool = True, texture: str = "plain",
          trim: Optional[bool] = None) -> Structure:
    """A house: framed walls with log posts and a window in each bay, a door with torches beside
    it, a pitched stair roof that overhangs the walls, a chimney up one end, a floor on a stone
    footing, and inside a bed, crafting table, furnace, chest and torches. detail=false gives a
    plain, cheaper box; trim=false leaves the chimney off."""
    width, depth = _limit("width", width, 5, 21), _limit("depth", depth, 5, 21)
    height = _limit("height", height, 3, 6)
    if roof not in ("gable", "flat"):
        raise ValueError("roof must be gable or flat")
    colours = palette(style, materials)
    c = _Canvas(colours, texture)
    door_x = _room(c, 0, 0, width, depth, height, floor=floor, windows=windows, detail=bool(detail))
    parts = [Part("walls", c.take(), footing=True)]
    trim = bool(detail) if trim is None else bool(trim)
    if roof == "gable":
        _gable_roof(c, 0, 0, width, depth, height, overhang=1 if detail else 0)
        if trim and detail:
            # On the east end, on the ridge line, so it comes up through the peak of the gable.
            ridge = max(y for (_, y, _) in c.cells)
            _chimney(c, width, depth // 2, ridge + 1)
    else:
        c.box(0, height, 0, width - 1, height, depth - 1, "roof_fill")
    parts.append(Part("roof", c.take()))
    furniture = ["bed", "crafting_table", "furnace", "chest"] if furnish is None else list(furnish)
    _furnish(c, 0, 0, width, depth, furniture)
    inside = c.take()
    if inside:
        # Before the roof: furniture has to face the right way, and that is easiest while the room is open.
        parts.insert(1, Part("inside", inside))
    return Structure("house", f"{width}x{depth} {style} house, walls {height} high, {roof} roof"
                     + (", overhanging" if detail and roof == "gable" else ""), parts,
                     entrance=(door_x, 0, depth),
                     notes=["The bed needs 3 white wool and 3 planks; leave bed out of furnish to skip it."]
                     if "bed" in furniture else [], footing=full(colours["foundation"]))


def shelter(style: str = "dirt", materials: Optional[dict[str, str]] = None) -> Structure:
    """A 5x5 hut with a door, a flat roof and a torch: enough to sit out a night, cheap to build."""
    made = house(5, 5, 3, style, roof="flat", floor=False, windows=False, furnish=[], materials=materials,
                 detail=False)
    c = _Canvas(palette(style, materials))
    c.put(1, 0, 1, "light", "on:down")
    inside = next((p for p in made.parts if p.name == "inside"), None)
    if inside is None:
        made.parts.insert(1, Part("inside", c.take()))
    else:
        inside.blocks.extend(c.take())
    made.kind, made.description = "shelter", f"5x5 {style} shelter with a door and a flat roof"
    return made


def storage(width: int = 7, depth: int = 5, style: str = "oak", materials: Optional[dict[str, str]] = None,
            detail: bool = True, texture: str = "plain") -> Structure:
    """A storage shed: a small house whose back wall is lined with chests (pairs join into double chests)."""
    width, depth = _limit("width", width, 5, 15), _limit("depth", depth, 5, 9)
    made = house(width, depth, 3, style, furnish=["chest"] * (width - 3), materials=materials, detail=detail,
                 texture=texture, trim=False)
    made.kind, made.description = "storage", f"{width}x{depth} {style} storage shed with {width - 3} chests"
    return made


def tower(size: int = 5, height: int = 8, style: str = "cobblestone",
          materials: Optional[dict[str, str]] = None, texture: str = "plain") -> Structure:
    """A watchtower: a door, a ladder up the inside, and a lookout platform with battlements."""
    size, height = _limit("size", size, 5, 9), _limit("height", height, 5, 16)
    colours = palette(style, materials)
    c = _Canvas(colours, texture)
    door_x = _room(c, 0, 0, size, size, height, floor=True, windows=False)
    # Arrow slits up the sides, so the shaft is not a blank column.
    for y in range(2, height - 1, 3):
        # Not the north wall: the ladder hangs on it.
        for x, z in ((size // 2, size - 1), (0, size // 2), (size - 1, size // 2)):
            c.put(x, y, z, "window")
    parts = [Part("walls", c.take(), footing=True)]
    mid = size // 2
    # The ladder hangs on the inside of the north wall and comes up through the platform.
    for y in range(0, height):
        c.put(mid, y, 1, "ladder", "on:north")
    parts.append(Part("ladder", c.take()))
    c.box(0, height, 0, size - 1, height, size - 1, "wall")
    c.put(mid, height, 1, "ladder", "on:north")
    for x in range(size):
        for z in range(size):
            if (x in (0, size - 1) or z in (0, size - 1)) and (x + z) % 2 == 0:
                c.put(x, height + 1, z, "wall")
    c.put(size - 2, height + 1, size - 2, "light", "on:down")
    parts.append(Part("top", c.take()))
    c.put(size - 2, 0, size - 2, "light", "on:down")
    parts.append(Part("inside", c.take()))
    return Structure("tower", f"{size}x{size} {style} watchtower, {height} high, with a ladder and battlements",
                     parts, entrance=(door_x, 0, size), footing=full(colours["foundation"]))


def wall(width: int = 21, depth: int = 21, height: int = 3, style: str = "cobblestone",
         materials: Optional[dict[str, str]] = None, texture: str = "plain") -> Structure:
    """A wall round an area, with a door in the middle of the south side and a taller pier every
    fourth block with a torch on top."""
    width, depth = _limit("width", width, 5, 48), _limit("depth", depth, 5, 48)
    height = _limit("height", height, 2, 5)
    colours = palette(style, materials)
    c = _Canvas(colours, texture)
    c.ring(0, 0, width - 1, depth - 1, 0, height - 1, "wall")
    door_x = width // 2
    # Piers one block taller than the wall break up its length and carry the torches.
    piers = {(x, z) for x in range(0, width, 4) for z in (0, depth - 1) if x != door_x}
    piers |= {(x, z) for z in range(0, depth, 4) for x in (0, width - 1)}
    piers |= {(0, 0), (width - 1, 0), (0, depth - 1), (width - 1, depth - 1)}
    for x, z in piers:
        for y in range(height + 1):
            c.put(x, y, z, "post")
    c.put(door_x, 0, depth - 1, "door", "north")
    c.clear(door_x, 1, depth - 1)
    c.box(door_x, 0, depth, door_x, 1, depth, "air")
    parts = [Part("wall", c.take(), footing=True)]
    for x, z in sorted(piers):
        c.put(x, height + 1, z, "light", "on:down")
    parts.append(Part("torches", c.take()))
    return Structure("wall", f"{width}x{depth} {style} wall, {height} high, with piers and a door on the south side",
                     parts, entrance=(door_x, 0, depth), footing=full(colours["foundation"]))


def pen(width: int = 7, depth: int = 7, style: str = "oak", materials: Optional[dict[str, str]] = None) -> Structure:
    """A fenced pen with a gate, for animals or to keep them off crops."""
    width, depth = _limit("width", width, 3, 32), _limit("depth", depth, 3, 32)
    c = _Canvas(palette(style, materials))
    c.ring(0, 0, width - 1, depth - 1, 0, 0, "fence")
    gate_x = width // 2
    c.put(gate_x, 0, depth - 1, "gate", "north")
    return Structure("pen", f"{width}x{depth} {style} fenced pen with a gate", [Part("fence", c.take())],
                     entrance=(gate_x, 0, depth))


def farm_plot(width: int = 9, depth: int = 9, style: str = "oak",
              materials: Optional[dict[str, str]] = None) -> Structure:
    """A fenced field with a gate and torches. Till and plant it afterwards with farm(till=true)."""
    made = pen(width, depth, style, materials)
    c = _Canvas(palette(style, materials))
    for x in (1, width - 2):
        for z in (1, depth - 2):
            c.put(x, 0, z, "light", "on:down")
    made.parts.append(Part("torches", c.take()))
    made.kind, made.description = "farm_plot", f"{width}x{depth} fenced field with a gate"
    made.notes = ["Crops need water within 4 blocks: build it next to water, or pour a bucket of water into a "
                  "hole in the middle. Then farm(till=true) with a hoe and seeds."]
    return made


def bridge(length: int = 8, width: int = 3, style: str = "oak", materials: Optional[dict[str, str]] = None) -> Structure:
    """A plank bridge with fence rails, running south from the origin, level with the ground."""
    length, width = _limit("length", length, 2, 48), _limit("width", width, 1, 5)
    c = _Canvas(palette(style, materials))
    c.box(0, -1, 0, width - 1, -1, length - 1, "floor")
    parts = [Part("deck", c.take())]
    if width >= 3:
        for z in range(length):
            c.put(0, 0, z, "fence")
            c.put(width - 1, 0, z, "fence")
        parts.append(Part("rails", c.take()))
    return Structure("bridge", f"{style} bridge {length} long and {width} wide", parts,
                     notes=["Start it on the bank: the first row must touch solid ground."], clear_site=False)


def stairway(height: int = 6, width: int = 2, style: str = "cobblestone",
             materials: Optional[dict[str, str]] = None) -> Structure:
    """A solid staircase rising to the south, one block up per block along: up a cliff or onto a wall."""
    height, width = _limit("height", height, 2, 24), _limit("width", width, 1, 4)
    c = _Canvas(palette(style, materials))
    for step in range(height):
        for x in range(width):
            for y in range(step):
                c.put(x, y, step, "roof_fill")
            c.put(x, step, step, "roof", "south")
    return Structure("stairway", f"{style} staircase {height} high, rising to the south", [Part("stairs", c.take())],
                     clear_site=False)


def platform(width: int = 9, depth: int = 9, style: str = "oak", materials: Optional[dict[str, str]] = None) -> Structure:
    """A level floor set into the ground: a foundation, a yard, or a pad to build on."""
    width, depth = _limit("width", width, 1, 32), _limit("depth", depth, 1, 32)
    c = _Canvas(palette(style, materials))
    c.box(0, 0, 0, width - 1, 2, depth - 1, "air")
    c.box(0, -1, 0, width - 1, -1, depth - 1, "floor")
    return Structure("platform", f"{width}x{depth} {style} floor", [Part("floor", c.take())])


def yard(width: int = 7, depth: int = 7, style: str = "oak", margin: int = 3, fence: bool = True,
         detail: bool = True, materials: Optional[dict[str, str]] = None, texture: str = "plain") -> Structure:
    """The plot round a house: a path from the door to a gate, a fence round the plot and lamp posts
    (a log with a torch on it) at the gate and the corners. Give it the same x y z, width, depth,
    facing and style as the house; it reaches margin blocks beyond the house on every side.
    fence=false leaves just the path and the lamps."""
    width, depth = _limit("width", width, 5, 21), _limit("depth", depth, 5, 21)
    margin = _limit("margin", margin, 2, 8)
    # The house's own footprint, eaves included, as the house generator lays it out.
    eave = 1 if detail else 0
    house_w, house_d = width + 2 * eave, depth + 2 * eave
    plot_w, plot_d = house_w + 2 * margin, house_d + 2 * margin
    door_x = margin + eave + width // 2
    step_z = margin + eave + depth          # the spot in front of the door, under the eave
    last = plot_d - 1
    c = _Canvas(palette(style, materials), texture)
    # A doorstep as wide as the door and its torches, then the path out to the gate.
    wide = 1 if width < 9 else 3
    for z in range(step_z + 1, last + 1):
        for x in range(door_x - wide // 2, door_x + wide // 2 + 1):
            c.put(x, -1, z, "path")
    for x in range(door_x - 1, door_x + 2):
        c.put(x, -1, step_z + 1, "path")
    parts = [Part("path", c.take())]
    corners = [(0, 0), (plot_w - 1, 0), (0, last), (plot_w - 1, last)]
    if fence:
        c.ring(0, 0, plot_w - 1, last, 0, 0, "fence")
        c.put(door_x, 0, last, "gate", "north")
        for x, z in corners:
            c.clear(x, 0, z)
        for x in (door_x - 1, door_x + 1):
            c.clear(x, 0, last)
        parts.append(Part("fence", c.take(), optional=True))
    # Lamp posts where the fence turns and beside the gate: two logs and a torch.
    lamps = corners + [(door_x - 1, last), (door_x + 1, last)]
    for x, z in lamps:
        c.put(x, 0, z, "post")
        c.put(x, 1, z, "post")
    posts = c.take()
    for x, z in lamps:
        c.put(x, 2, z, "light", "on:down")
    parts.append(Part("lamps", posts + c.take(), optional=True))
    return Structure("yard", f"{plot_w}x{plot_d} {style} yard round a {width}x{depth} house: path, "
                     + ("fence with a gate, " if fence else "") + "lamp posts", parts,
                     entrance=(door_x, 0, plot_d), anchor=(margin, margin),
                     notes=["Give it the same x y z, width, depth, facing and style as the house it goes round.",
                            "Flowers you carry are planted along the path when it is done."])


def flower_spots(made: Structure) -> list[tuple[int, int, int]]:
    """Where flowers go in a yard, best first: either side of the path, every other block, then along
    the front of the house. Relative to the yard's own corner, at walking level."""
    path = [b for p in made.parts if p.name == "path" for b in p.blocks]
    if not path:
        return []
    taken = {(b[0], b[2]) for b in made.blocks}
    xs = sorted({b[0] for b in path})
    zs = sorted({b[2] for b in path})
    out = []
    for z in zs[1:-1:2]:
        row = [b[0] for b in path if b[2] == z]
        for x in (min(row) - 1, max(row) + 1):
            if (x, z) not in taken:
                out.append((x, 0, z))
    front = zs[0] - 1
    for x in range(min(xs) - 4, max(xs) + 5):
        if abs(x - (min(xs) + max(xs)) // 2) >= 2 and (x, front + 1) not in taken and x > 0:
            out.append((x, 0, front + 1))
    return out


def base(style: str = "oak", wall_style: str = "cobblestone",
         materials: Optional[dict[str, str]] = None, texture: str = "plain") -> Structure:
    """A walled base, 27 by 23: a furnished house, a storage shed, a watchtower, a fenced field
    and an animal pen inside a torch-lit wall with a door. Build it part by part."""
    parts: list[Part] = []

    def add(name: str, made: Structure, dx: int, dz: int) -> None:
        blocks = [(x + dx, y, z + dz, item, facing) for x, y, z, item, facing in made.blocks]
        parts.append(Part(name, sorted(blocks, key=lambda b: (b[3] != AIR, b[1], b[2], b[0])),
                          footing=made.footing is not None))

    # Houses first, so there is somewhere to sleep and store things while the rest goes up.
    add("house", house(7, 7, 3, style, materials=materials, texture=texture, trim=False), 2, 2)
    add("storage", storage(7, 5, style, materials=materials, texture=texture), 11, 2)
    add("tower", tower(5, 8, wall_style, materials=materials, texture=texture), 20, 2)
    add("farm", farm_plot(9, 9, style, materials=materials), 2, 12)
    add("pen", pen(7, 7, style, materials=materials), 14, 12)
    add("wall", wall(27, 23, 3, wall_style, materials=materials, texture=texture), 0, 0)
    return Structure("base", f"27x23 walled base ({style} buildings, {wall_style} wall): house, storage shed, "
                     "watchtower, field and pen", parts, entrance=(13, 0, 23),
                     notes=["Build one part per call with part=...: house, storage, tower, farm, pen, wall.",
                            "Gather for one part at a time; plan_structure with part=... lists what it needs."],
                     footing=full(palette(wall_style, materials)["foundation"]))


#: kind -> (generator, the options it takes).
KINDS: dict[str, tuple[Callable[..., Structure], tuple[str, ...]]] = {
    "shelter": (shelter, ("style", "materials")),
    "house": (house, ("width", "depth", "height", "style", "roof", "floor", "windows", "furnish", "materials",
                      "detail", "texture", "trim")),
    "yard": (yard, ("width", "depth", "style", "margin", "fence", "detail", "materials", "texture")),
    "storage": (storage, ("width", "depth", "style", "materials", "detail", "texture")),
    "tower": (tower, ("size", "height", "style", "materials", "texture")),
    "wall": (wall, ("width", "depth", "height", "style", "materials", "texture")),
    "pen": (pen, ("width", "depth", "style", "materials")),
    "farm_plot": (farm_plot, ("width", "depth", "style", "materials")),
    "bridge": (bridge, ("length", "width", "style", "materials")),
    "stairway": (stairway, ("height", "width", "style", "materials")),
    "platform": (platform, ("width", "depth", "style", "materials")),
    "base": (base, ("style", "wall_style", "materials", "texture")),
}


def generate(kind: str, facing: str = "south", **options: Any) -> Structure:
    """The structure of this kind with these options, turned so its front faces ``facing``."""
    if kind not in KINDS:
        raise ValueError(f"unknown structure {kind!r}; kinds: {', '.join(KINDS)}")
    if facing not in FACINGS:
        raise ValueError(f"facing must be one of {', '.join(FACINGS)}")
    generator, allowed = KINDS[kind]
    options = {k: v for k, v in options.items() if v is not None}
    unknown = set(options) - set(allowed)
    if unknown:
        raise ValueError(f"{kind} does not take {', '.join(sorted(unknown))}; it takes {', '.join(allowed)}")
    made = generator(**options)
    return turned(made, FACINGS.index(facing))


def turned(made: Structure, quarters: int) -> Structure:
    """The structure turned clockwise by quarter turns, moved so its west, north corner is the origin again."""
    quarters %= 4
    if quarters == 0:
        return _normalised(made)
    parts = [Part(p.name, [_turn_block(b, quarters) for b in p.blocks], p.footing, p.optional) for p in made.parts]
    entrance = made.entrance
    if entrance is not None:
        x, y, z = entrance
        for _ in range(quarters):
            x, z = -z, x
        entrance = (x, y, z)
    ax, az = made.anchor
    for _ in range(quarters):
        ax, az = -az, ax
    # A corner of the house, not a point: after a turn the house's west, north corner is another of its corners.
    if made.anchor != (0, 0):
        w, d = made.footprint()
        hw, hd = w - 2 * made.anchor[0], d - 2 * made.anchor[1]
        if quarters % 2:
            hw, hd = hd, hw
        ax, az = (ax - hw + 1 if quarters in (1, 2) else ax), (az - hd + 1 if quarters in (2, 3) else az)
    return _normalised(Structure(made.kind, made.description, parts, entrance, list(made.notes), made.footing,
                                 (ax, az), made.clear_site))


def _turn_block(block: Block, quarters: int) -> Block:
    x, y, z, item, facing = block
    for _ in range(quarters):
        x, z = -z, x
        facing = _turn_facing(facing)
    return (x, y, z, item, facing)


def _turn_facing(facing: Optional[str]) -> Optional[str]:
    if not facing:
        return facing
    out = []
    for token in facing.split(","):
        if token.startswith("axis:"):
            # A quarter turn swaps the two level axes; an upright log stays upright.
            out.append("axis:" + {"x": "z", "z": "x"}.get(token[5:], token[5:]))
            continue
        prefix, name = ("on:", token[3:]) if token.startswith("on:") else ("", token)
        out.append(prefix + _TURN.get(name, name))
    return ",".join(out)


def _normalised(made: Structure) -> Structure:
    solid = [b for b in made.blocks if b[3] != AIR]
    dx, dz = min(b[0] for b in solid), min(b[2] for b in solid)
    if dx == 0 and dz == 0:
        return made
    parts = [Part(p.name, [(x - dx, y, z - dz, item, facing) for x, y, z, item, facing in p.blocks], p.footing,
                  p.optional) for p in made.parts]
    entrance = None if made.entrance is None else (made.entrance[0] - dx, made.entrance[1], made.entrance[2] - dz)
    return Structure(made.kind, made.description, parts, entrance, list(made.notes), made.footing,
                     (made.anchor[0] - dx, made.anchor[1] - dz), made.clear_site)


def place_at(blocks: list[Block], x: int, y: int, z: int) -> list[list[Any]]:
    """World-coordinate entries for the build skill: ``[x, y, z, item]`` or ``[x, y, z, item, facing]``."""
    out: list[list[Any]] = []
    for dx, dy, dz, item, facing in blocks:
        entry: list[Any] = [x + dx, y + dy, z + dz, item]
        if facing:
            entry.append(facing)
        out.append(entry)
    return out


def chunks(blocks: list[Block], size: int = CHUNK) -> list[list[Block]]:
    """Splits a part into build calls: digging first, then layers from the bottom, never splitting a layer
    unless it alone is too big."""
    digging = [b for b in blocks if b[3] == AIR]
    solid = [b for b in blocks if b[3] != AIR]
    out: list[list[Block]] = [digging[i:i + size * 2] for i in range(0, len(digging), size * 2)]
    current: list[Block] = []
    for y in sorted({b[1] for b in solid}):
        layer = [b for b in solid if b[1] == y]
        if current and len(current) + len(layer) > size:
            out.append(current)
            current = []
        while len(layer) > size:
            out.append(layer[:size])
            layer = layer[size:]
        current += layer
    if current:
        out.append(current)
    return out


def catalogue() -> list[str]:
    """One line per kind for the model: what it is, its options and its default size and cost."""
    lines = []
    for kind, (generator, allowed) in KINDS.items():
        made = generator()
        w, d = made.footprint()
        needs = made.materials()
        top = ", ".join(f"{n} {short(item)}" for item, n in list(needs.items())[:4])
        doc = " ".join((generator.__doc__ or "").split())
        lines.append(f"{kind}: {doc} Options: {', '.join(allowed)}. By default {w}x{d}, {sum(needs.values())} "
                     f"blocks ({top}{', ...' if len(needs) > 4 else ''}).")
    return lines
