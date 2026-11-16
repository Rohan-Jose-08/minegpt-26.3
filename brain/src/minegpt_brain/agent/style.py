"""A look over a drawn build before it goes up: does it follow the rules that make a build look good?

The ready-made structures follow these rules by construction. A blueprint the model draws
itself does not have to, so this reads the block list and says, in a few lines, what would
make it look less like a box: long flat walls, too many or too few materials, a roof that
stops flush with the walls, no windows, no light, no way in. The notes go back to the model
with the result of ``save_blueprint`` and ``build``, as advice; nothing is refused.

It works from the drawing alone, so it runs with any model, including a small local one that
cannot look at screenshots.
"""

from __future__ import annotations

from typing import Iterable

from .state import short

#: Most plain wall in a row before it reads as a flat slab.
MAX_FLAT_RUN = 4
#: Most structural materials before a build looks like a patchwork.
MAX_PALETTE = 5
#: Smaller drawings than this are too small to judge: a pillar, a path, a well.
MIN_BLOCKS = 30

_LIGHTS = ("torch", "lantern", "glowstone", "campfire", "sea_lantern", "shroomlight", "jack_o_lantern", "candle",
           "froglight", "end_rod")
_OPENINGS = ("_door", "_fence_gate", "_trapdoor")
_GLASS = ("glass",)
_FURNITURE = ("chest", "barrel", "furnace", "crafting_table", "_bed", "ladder", "smoker", "anvil", "bookshelf",
              "flower_pot", "_carpet", "_sign", "composter", "cauldron")
_ROOFING = ("_stairs", "_slab")
#: Variants that count as the same material: a stair, slab, wall or fence of a block, and stripped logs.
_SUFFIXES = ("_stairs", "_slab", "_wall", "_fence", "_planks", "_log", "_wood")


def _is(item: str, names: Iterable[str]) -> bool:
    name = short(item)
    return any(name.endswith(n) or name == n.strip("_") or (not n.startswith("_") and n in name) for n in names)


def _family(item: str) -> str:
    """Blocks that are shapes of one material count once: oak planks, stairs, slabs and fences are all oak."""
    name = short(item).removeprefix("stripped_")
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    for prefix in ("cracked_", "mossy_", "cut_", "smooth_", "chiseled_", "polished_"):
        name = name.removeprefix(prefix)
    return name.removesuffix("_brick").removesuffix("_bricks").removesuffix("s") or name


def review(blocks: Iterable[tuple]) -> list[str]:
    """Notes on a drawing, most important first; empty when there is nothing to say.

    ``blocks`` are ``(dx, dy, dz, item, ...)`` with air left out or named ``minecraft:air``.
    """
    cells = {(b[0], b[1], b[2]): b[3] for b in blocks if not short(b[3]) == "air"}
    if len(cells) < MIN_BLOCKS:
        return []
    ys = [y for _, y, _ in cells]
    low, high = min(ys), max(ys)
    if high - low < 2:
        return []  # a floor, a path, a field: nothing to style
    notes: list[str] = []

    structural = {p: item for p, item in cells.items()
                  if not _is(item, _LIGHTS + _OPENINGS + _GLASS + _FURNITURE)}
    longest, where = _longest_flat_run(structural)
    if longest > MAX_FLAT_RUN:
        notes.append(f"A wall runs {longest} blocks of plain {short(where)} in a row. Break up any stretch over "
                     f"{MAX_FLAT_RUN}: a post of logs or another trim block every 3 or 4 blocks, a window in each "
                     "bay, or a section set a block in or out.")

    families = sorted({_family(item) for item in structural.values()})
    if len(families) > MAX_PALETTE:
        notes.append(f"{len(families)} different materials ({', '.join(families[:8])}): keep to {MAX_PALETTE} or "
                     "fewer. One main wall block, one contrasting trim, one roof material, one accent.")
    elif len(families) == 1:
        notes.append(f"Everything is {families[0]}. Add a contrasting trim (log posts on planks, stone under "
                     "wood) and a different roof material.")

    roof = _roof_note(structural, low, high)
    if roof:
        notes.append(roof)

    enclosed = _encloses_space(cells, low, high)
    if enclosed and not any(_is(item, _GLASS) for item in cells.values()):
        notes.append("No windows: put glass or glass panes in the walls, one per stretch between posts, at eye "
                     "height.")
    if enclosed and not any(_is(item, _OPENINGS) for item in cells.values()) and not _has_gap(cells, low):
        notes.append("No door and no gap to walk in through: add a door in a wall.")
    if not any(_is(item, _LIGHTS) for item in cells.values()):
        notes.append("No light: torches or lanterns inside and by the door keep mobs from spawning.")
    return notes[:5]


def _longest_flat_run(structural: dict[tuple[int, int, int], str]) -> tuple[int, str]:
    """The longest row of one block along a thin wall: cells with open space on both sides of the row,
    above the bottom layer, with wall above or below them. Floors and roofs do not count."""
    best, item_at = 0, ""
    low = min(y for _, y, _ in structural) if structural else 0
    for axis in (0, 2):
        other = 2 if axis == 0 else 0
        rows: dict[tuple[int, int], list[tuple[int, str]]] = {}
        for (x, y, z), item in structural.items():
            pos = (x, y, z)
            if y == low or _is(item, _ROOFING):
                continue
            side_a = list(pos)
            side_b = list(pos)
            side_a[other] += 1
            side_b[other] -= 1
            if tuple(side_a) in structural or tuple(side_b) in structural:
                continue  # thick here: a floor, a roof core, or a corner
            if (x, y + 1, z) not in structural and (x, y - 1, z) not in structural:
                continue  # a single course: a rail or a beam, not a wall
            rows.setdefault((y, pos[other]), []).append((pos[axis], item))
        for row in rows.values():
            row.sort()
            run, last, last_item = 0, None, None
            for i, item in row:
                run = run + 1 if last is not None and i == last + 1 and item == last_item else 1
                last, last_item = i, item
                if run > best:
                    best, item_at = run, item
    return best, item_at


def _roof_note(structural: dict[tuple[int, int, int], str], low: int, high: int) -> str:
    top = [(x, z) for (x, y, z) in structural if y == high]
    below_top = {(x, z) for (x, y, z) in structural if low < y < high}
    if not below_top:
        return ""
    wx = [x for x, _ in below_top]
    wz = [z for _, z in below_top]
    all_x = [x for x, _, _ in structural]
    all_z = [z for _, _, z in structural]
    footprint = (max(all_x) - min(all_x) + 1) * (max(all_z) - min(all_z) + 1)
    if len(top) >= 0.8 * footprint and footprint >= 16:
        return ("The top is one flat layer the size of the whole build. Pitch it: step each layer in by a block "
                "with stairs on the slopes and slabs on the ridge, and let the lowest row hang a block outside "
                "the walls.")
    upper = [(x, z) for (x, y, z), item in structural.items() if y > low + (high - low) // 2 and _is(item, _ROOFING)]
    walls = {(x, z) for (x, y, z), item in structural.items() if y == low + 1}
    if upper and walls:
        ux = [x for x, _ in upper]
        uz = [z for _, z in upper]
        lx = [x for x, _ in walls]
        lz = [z for _, z in walls]
        if min(ux) >= min(lx) and max(ux) <= max(lx) and min(uz) >= min(lz) and max(uz) <= max(lz):
            return ("The roof stops flush with the walls. Let it overhang by a block: a row of stairs just "
                    "outside the top of each wall gives the walls a shadow line.")
    del wx, wz
    return ""


def _encloses_space(cells: dict[tuple[int, int, int], str], low: int, high: int) -> bool:
    """True if the drawing has room inside it: an empty spot above the bottom layer with blocks on all four sides
    somewhere along each direction and something overhead."""
    xs = [x for x, _, _ in cells]
    zs = [z for _, _, z in cells]
    for y in range(low + 1, high):
        for x in range(min(xs) + 1, max(xs)):
            for z in range(min(zs) + 1, max(zs)):
                if (x, y, z) in cells:
                    continue
                if (any((i, y, z) in cells for i in range(min(xs), x)) and any((i, y, z) in cells for i in range(x + 1, max(xs) + 1))
                        and any((x, y, k) in cells for k in range(min(zs), z)) and any((x, y, k) in cells for k in range(z + 1, max(zs) + 1))
                        and any((x, j, z) in cells for j in range(y + 1, high + 1))):
                    return True
    return False


def _has_gap(cells: dict[tuple[int, int, int], str], low: int) -> bool:
    """True if the outline of the first wall layer has a gap two high to walk through."""
    layer = {(x, z) for (x, y, z) in cells if y == low + 1}
    if not layer:
        return True
    xs = [x for x, _ in layer]
    zs = [z for _, z in layer]
    edge = [(x, z) for x in range(min(xs), max(xs) + 1) for z in range(min(zs), max(zs) + 1)
            if x in (min(xs), max(xs)) or z in (min(zs), max(zs))]
    return any((x, z) not in layer and (x, low + 2, z) not in cells for x, z in edge)
