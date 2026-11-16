"""A small language for designing buildings, and the compiler that turns a design into blocks.

Models are poor at placing single blocks and at turning them the right way, and good at saying
what a building is made of: a hall with a wing, a steep roof, a porch at the door. So a design
is written at that level, in three layers, and code owns every coordinate::

    build cottage style=birch_lodge
    mass hall box(9,4,7)
    mass wing box(5,3,5) attach hall.west align=back
    roof hall gable(overhang=1) ; roof wing gable(overhang=1)
    facade hall.south -> [window, door_porch, window]
    detail hall chimney at east

* Massing: ``mass <name> box(width, wall height, depth)``. The first stands on the ground; the
  others ``attach <mass>.<side>`` and share that wall, with a doorway cut through it.
  ``align`` slides one along the wall (back, center, front on an east or west wall; west,
  center, east on a north or south wall), ``offset`` nudges it, ``floors=2`` adds an upper floor
  with a ladder. ``mirror <mass> as <name>`` puts a twin on the opposite side.
* Roof and facade: ``roof <mass> gable|hip|flat|lean(...)`` and
  ``facade <mass>.<side> -> [part, part, ...]``, one part per bay from west to east (north and
  south walls) or north to south (east and west walls). Walls with no facade get a window in
  every bay.
* Detail: ``detail <mass> chimney [at <side>]`` and ``detail <mass> furnish [bed, chest, ...]``.

``compile_design`` returns a :class:`~.structures.Structure`, so everything that works for a
ready-made house works for a design: materials, building part by part, the check against the
plan, turning it to face another way. A design that cannot be built is refused with the line
and the reason in plain words (``line 3: wing overlaps porch``), which is what lets a small
model repair its own design. ``review`` reads a design as a builder would and lists what
would make it look better; it needs no pictures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from . import structures, stylebook
from .state import full, short
from .structures import AIR, Block, Part, Structure

SIDES = ("north", "east", "south", "west")
_OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
#: The way out of a wall on each side, in x and z.
_OUT = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
_RESERVED = ("inside", "roof", "trim")
_FACING_ITEMS = ("chest", "furnace", "barrel", "blast_furnace", "smoker", "trapped_chest")

MAX_MASSES = 8
MAX_BLOCKS = 6000
MAX_SPAN = 48
ROOFS = ("gable", "hip", "flat", "lean")


class DesignError(ValueError):
    """A design that cannot be read or built, with every reason found, one per line."""


@dataclass(frozen=True)
class PartCard:
    """One entry of the parts library: where a part goes, what it needs, and what it costs."""

    name: str
    where: str
    what: str
    needs: str
    cost: str


#: The parts library. Facade parts fill one bay of a wall; details hang on a mass; roofs sit on one.
PARTS: dict[str, PartCard] = {p.name: p for p in (
    PartCard("wall", "facade bay", "Plain wall.", "nothing", "nothing extra"),
    PartCard("window", "facade bay", "One glass pane at eye level, two high under a tall ceiling.",
             "a bay at least 1 wide", "1 or 2 window"),
    PartCard("tall_window", "facade bay", "A pane two high.", "walls 4 high or more", "2 window"),
    PartCard("wide_window", "facade bay", "Panes across the bay, up to three wide. Also written window_bay.",
             "a bay 2 or 3 wide reads best", "2 to 6 window"),
    PartCard("door", "facade bay", "A door with a torch on each side outside.",
             "open ground in front; nothing attached there", "1 door, 2 light"),
    PartCard("door_porch", "facade bay", "A door under a small roof on two fence posts, with torches.",
             "three clear blocks in front of the wall", "1 door, 4 fence, 6 ridge (slab), 2 light"),
    PartCard("opening", "facade bay", "A doorway with no door, for a barn or a shed.",
             "open ground in front", "nothing extra"),
    PartCard("buttress", "facade bay", "A short stone pier against the wall, capped with a stair. For stone walls.",
             "open ground in front", "2 foundation, 1 roof (stair)"),
    PartCard("chimney", "detail <mass> chimney [at <side>]", "A chimney up the outside of a wall to over the roof.",
             "a free wall: best a gable end", "wall height plus roof height, in chimney blocks"),
    PartCard("furnish", "detail <mass> furnish [items]", "Furniture along the walls: bed, crafting_table, furnace, "
             "chest, barrel and the like. The first mass gets bed, crafting_table, furnace, chest unless told "
             "otherwise; [] for none.", "free wall inside", "the items; a bed is 3 wool and 3 planks"),
    PartCard("gable", "roof", "Two slopes and a ridge. gable(overhang=1, ridge=x|z): ridge=x runs it east-west. "
             "By default along the longer side.", "any mass", "about 1.5 stairs per block of floor, plus the core"),
    PartCard("hip", "roof", "Slopes on all four sides. hip(overhang=1).", "a mass at least 5 by 5",
             "a little more than a gable"),
    PartCard("flat", "roof", "A flat deck. flat(parapet=1) adds a low rim of slabs.", "any mass",
             "one roof_fill per block of floor"),
    PartCard("lean", "roof", "One slope, rising towards a side: lean(to=east). On an attached mass it rises "
             "towards the mass it is attached to.", "a span of 6 or less across the slope",
             "about 1 stair per block of floor, plus the core"),
)}
_FACADE_PARTS = ("wall", "window", "tall_window", "wide_window", "door", "door_porch", "opening", "buttress")
_ALIASES = {"window_bay": "wide_window", "porch": "door_porch", "blank": "wall", "doorway": "opening"}


@dataclass
class Mass:
    name: str
    w: int
    h: int
    d: int
    line: int
    parent: Optional[str] = None
    side: Optional[str] = None
    align: str = "center"
    offset: int = 0
    floors: int = 1
    roof: str = "gable"
    roof_options: dict[str, Any] = field(default_factory=dict)
    facades: dict[str, list[str]] = field(default_factory=dict)
    chimney: Optional[str] = None
    wants_chimney: bool = False
    furnish: Optional[list[str]] = None
    x0: int = 0
    z0: int = 0

    @property
    def x1(self) -> int:
        return self.x0 + self.w - 1

    @property
    def z1(self) -> int:
        return self.z0 + self.d - 1

    def covers(self, x: int, z: int) -> bool:
        return self.x0 <= x <= self.x1 and self.z0 <= z <= self.z1

    def inside(self, x: int, z: int) -> bool:
        return self.x0 < x < self.x1 and self.z0 < z < self.z1

    def wall(self, side: str) -> list[tuple[int, int]]:
        """The wall on a side, corner to corner, from west to east or from north to south."""
        if side in ("north", "south"):
            z = self.z0 if side == "north" else self.z1
            return [(x, z) for x in range(self.x0, self.x1 + 1)]
        x = self.x0 if side == "west" else self.x1
        return [(x, z) for z in range(self.z0, self.z1 + 1)]

    @property
    def upper(self) -> Optional[int]:
        """The level of the upper floor's boards, or None with one floor."""
        return (self.h - 1) // 2 if self.floors == 2 else None

    def storeys(self) -> list[tuple[int, int]]:
        """Each floor as (the level you stand on, clear height)."""
        if self.upper is None:
            return [(0, self.h)]
        return [(0, self.upper), (self.upper + 1, self.h - self.upper - 1)]


@dataclass
class Design:
    name: str = "design"
    style: Optional[str] = None
    texture: Optional[str] = None
    masses: dict[str, Mass] = field(default_factory=dict)

    @property
    def first(self) -> Mass:
        return next(iter(self.masses.values()))


@dataclass
class Compiled:
    design: Design
    structure: Structure
    palette: dict[str, str]
    #: Things the compiler changed or filled in, for the designer to know.
    notes: list[str] = field(default_factory=list)
    #: (mass, side) -> the parts on that wall as built.
    walls: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    #: Where torches stand inside, and every floor cell, by mass.
    lights: list[tuple[int, int, int]] = field(default_factory=list)
    floors: dict[str, list[tuple[int, int, int]]] = field(default_factory=dict)
    roof_tops: dict[str, int] = field(default_factory=dict)
    cells: dict[tuple[int, int, int], tuple[str, str, Optional[str]]] = field(default_factory=dict)


# Reading ----------------------------------------------------------------------------------------

_MASS = re.compile(r"^mass\s+(\w+)\s+box\(\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\)\s*(.*)$")
_ROOF = re.compile(r"^roof\s+(\w+)\s+(\w+)\s*(?:\((.*)\))?$")
_FACADE = re.compile(r"^facade\s+(\w+)\.(\w+)\s*(?:bays\(\s*(\d+)\s*\))?\s*(?:->)?\s*\[(.*)\]$")
_DETAIL = re.compile(r"^detail\s+(\w+)(?:\.roof)?\s+(\w+)\s*(.*)$")
_MIRROR = re.compile(r"^mirror\s+(\w+)\s+as\s+(\w+)$")
_BUILD = re.compile(r"^build\s+(\w+)\s*(.*)$")
_ALIGNS = {"back": "start", "north": "start", "west": "start", "left": "start", "start": "start",
           "front": "end", "south": "end", "east": "end", "right": "end", "end": "end",
           "center": "center", "centre": "center", "middle": "center"}


def _options(text: str, where: str, errors: list[str]) -> dict[str, str]:
    out = {}
    for token in re.split(r"[\s,]+", text.strip()):
        if not token:
            continue
        if "=" not in token:
            errors.append(f"{where}: cannot read {token!r}; options are written name=value")
            continue
        key, value = token.split("=", 1)
        out[key.lower()] = value.lower()
    return out


def _number(options: dict[str, str], key: str, default: int, low: int, high: int, where: str,
            errors: list[str]) -> int:
    if key not in options:
        return default
    try:
        value = int(options[key])
    except ValueError:
        value = low - 1
    if not low <= value <= high:
        errors.append(f"{where}: {key} must be a whole number from {low} to {high}, not {options[key]}")
        return default
    return value


def parse(program: str) -> Design:
    """Reads a design. Raises DesignError listing every line that cannot be read."""
    if not isinstance(program, str) or not program.strip():
        raise DesignError("the design is empty: write it as lines of mass, roof, facade and detail (see design_guide)")
    design = Design()
    errors: list[str] = []
    statements: list[tuple[int, str]] = []
    for number, raw in enumerate(program.replace("\\n", "\n").splitlines(), 1):
        for piece in raw.split("#", 1)[0].split(";"):
            if piece.strip():
                statements.append((number, " ".join(piece.strip().split())))
    for number, text in statements:
        where = f"line {number}"
        lowered = text.lower()
        word = lowered.split()[0]

        def mass_named(name: str) -> Optional[Mass]:
            found = design.masses.get(name)
            if found is None:
                known = ", ".join(design.masses) or "none yet"
                errors.append(f"{where}: no mass called {name} (masses so far: {known}); a mass must be "
                              "written before the lines that use it")
            return found

        if word == "build":
            m = _BUILD.match(lowered)
            if not m:
                errors.append(f"{where}: write it as: build <name> style=<style>")
                continue
            design.name = m.group(1)
            options = _options(m.group(2), where, errors)
            design.style = options.get("style", options.get("theme"))
            design.texture = options.get("texture")
            for key in set(options) - {"style", "theme", "texture"}:
                errors.append(f"{where}: build takes style and texture, not {key}")
        elif word == "mass":
            m = _MASS.match(lowered)
            if not m:
                errors.append(f"{where}: write it as: mass <name> box(width, height, depth) [attach <mass>.<side>]")
                continue
            name, w, h, d, rest = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(5)
            if name in design.masses:
                errors.append(f"{where}: there is already a mass called {name}")
                continue
            if name in _RESERVED or name in SIDES:
                errors.append(f"{where}: {name} cannot be the name of a mass; pick another")
                continue
            if len(design.masses) >= MAX_MASSES:
                errors.append(f"{where}: at most {MAX_MASSES} masses in one design")
                continue
            if not (4 <= w <= 21 and 4 <= d <= 21):
                errors.append(f"{where}: {name} is {w} by {d}; width and depth go from 4 to 21")
                continue
            if not 3 <= h <= 9:
                errors.append(f"{where}: {name} has walls {h} high; wall height goes from 3 to 9")
                continue
            mass = Mass(name, w, h, d, number)
            rest = rest.replace("on ground", "").strip()
            attach = re.match(r"^attach\s+(\w+)\.(\w+)\s*(.*)$", rest)
            if attach:
                parent = mass_named(attach.group(1))
                if attach.group(2) not in SIDES:
                    errors.append(f"{where}: attach to a side: {', '.join(SIDES)}; not {attach.group(2)}")
                    continue
                if parent is None:
                    continue
                mass.parent, mass.side, rest = parent.name, attach.group(2), attach.group(3)
            elif design.masses and not rest.startswith("attach"):
                errors.append(f"{where}: {name} must attach to a mass already written, like: attach "
                              f"{design.first.name}.west")
                continue
            elif rest.startswith("attach"):
                errors.append(f"{where}: write the attachment as: attach <mass>.<side>")
                continue
            options = _options(rest, where, errors)
            if "align" in options:
                if options["align"] not in _ALIGNS:
                    errors.append(f"{where}: align is back, center or front (east and west walls) or west, "
                                  f"center or east (north and south walls); not {options['align']}")
                else:
                    mass.align = _ALIGNS[options["align"]]
            mass.offset = _number(options, "offset", 0, -20, 20, where, errors)
            mass.floors = _number(options, "floors", 1, 1, 2, where, errors)
            if mass.floors == 2 and (h < 7 or w < 5 or d < 5):
                errors.append(f"{where}: two floors need walls 7 high or more and a mass at least 5 by 5; "
                              f"{name} is {w} by {d}, {h} high")
                mass.floors = 1
            for key in set(options) - {"align", "offset", "floors"}:
                errors.append(f"{where}: mass takes align, offset and floors, not {key}")
            if mass.parent is None and design.masses:
                continue
            design.masses[name] = mass
        elif word == "roof":
            m = _ROOF.match(lowered)
            if not m:
                errors.append(f"{where}: write it as: roof <mass> gable(overhang=1)")
                continue
            mass = mass_named(m.group(1))
            if m.group(2) not in ROOFS:
                errors.append(f"{where}: roofs are {', '.join(ROOFS)}; not {m.group(2)}")
                continue
            if mass is None:
                continue
            options = _options(m.group(3) or "", where, errors)
            allowed = {"gable": ("overhang", "ridge"), "hip": ("overhang",), "flat": ("parapet",),
                       "lean": ("to", "overhang")}[m.group(2)]
            for key in set(options) - set(allowed):
                errors.append(f"{where}: a {m.group(2)} roof takes {', '.join(allowed)}; not {key}")
            chosen: dict[str, Any] = {}
            if "overhang" in allowed:
                chosen["overhang"] = _number(options, "overhang", 1, 0, 1, where, errors)
            if "parapet" in allowed:
                chosen["parapet"] = _number(options, "parapet", 0, 0, 1, where, errors)
            if "ridge" in options:
                if options["ridge"] not in ("x", "z"):
                    errors.append(f"{where}: ridge is x (running east-west) or z (running north-south)")
                else:
                    chosen["ridge"] = options["ridge"]
            if "to" in options:
                if options["to"] not in SIDES:
                    errors.append(f"{where}: a lean roof rises to a side: {', '.join(SIDES)}")
                else:
                    chosen["to"] = options["to"]
            mass.roof, mass.roof_options = m.group(2), chosen
        elif word == "facade":
            m = _FACADE.match(lowered)
            if not m:
                errors.append(f"{where}: write it as: facade <mass>.<side> -> [window, door, window]")
                continue
            mass = mass_named(m.group(1))
            if m.group(2) not in SIDES:
                errors.append(f"{where}: a facade is on a side: {', '.join(SIDES)}; not {m.group(2)}")
                continue
            parts = [_ALIASES.get(p, p) for p in re.split(r"[\s,]+", m.group(4).strip()) if p]
            bad = [p for p in parts if p not in _FACADE_PARTS]
            if bad:
                errors.append(f"{where}: no facade part called {', '.join(bad)}; parts: {', '.join(_FACADE_PARTS)}")
                continue
            if not parts:
                errors.append(f"{where}: the facade lists no parts")
                continue
            if m.group(3) and int(m.group(3)) != len(parts):
                errors.append(f"{where}: bays({m.group(3)}) but {len(parts)} parts are listed; give one part per bay")
                continue
            if mass is not None:
                mass.facades[m.group(2)] = parts
        elif word == "detail":
            m = _DETAIL.match(lowered)
            if not m:
                errors.append(f"{where}: write it as: detail <mass> chimney at east, or detail <mass> furnish [bed, chest]")
                continue
            mass = mass_named(m.group(1))
            kind, rest = m.group(2), m.group(3)
            if kind == "chimney":
                side = next((s for s in SIDES if re.search(rf"\b{s}\b", rest)), None)
                if rest and side is None:
                    errors.append(f"{where}: say which side the chimney is at: {', '.join(SIDES)}")
                elif mass is not None:
                    mass.wants_chimney, mass.chimney = True, side
            elif kind == "furnish":
                items = re.match(r"^\[(.*)\]$", rest)
                if not items:
                    errors.append(f"{where}: write it as: detail <mass> furnish [bed, chest]; [] for none")
                elif mass is not None:
                    mass.furnish = [short(i) for i in re.split(r"[\s,]+", items.group(1).strip()) if i]
            else:
                errors.append(f"{where}: details are chimney and furnish; not {kind}. A porch or a window is a "
                              "facade part")
        elif word == "mirror":
            m = _MIRROR.match(lowered)
            if not m:
                errors.append(f"{where}: write it as: mirror <mass> as <new name>")
                continue
            source = mass_named(m.group(1))
            if source is None:
                continue
            if source.parent is None:
                errors.append(f"{where}: {source.name} stands on the ground; only an attached mass can be mirrored")
                continue
            if m.group(2) in design.masses or m.group(2) in _RESERVED or m.group(2) in SIDES:
                errors.append(f"{where}: {m.group(2)} is taken; pick another name")
                continue
            if len(design.masses) >= MAX_MASSES:
                errors.append(f"{where}: at most {MAX_MASSES} masses in one design")
                continue
            swap = {source.side: _OPPOSITE[source.side], _OPPOSITE[source.side]: source.side}
            twin = Mass(m.group(2), source.w, source.h, source.d, number, source.parent, _OPPOSITE[source.side],
                        source.align, source.offset, source.floors, source.roof, dict(source.roof_options),
                        {swap.get(s, s): list(p) for s, p in source.facades.items()},
                        swap.get(source.chimney, source.chimney) if source.chimney else None,
                        source.wants_chimney, None if source.furnish is None else list(source.furnish))
            if "to" in twin.roof_options:
                twin.roof_options["to"] = swap.get(twin.roof_options["to"], twin.roof_options["to"])
            design.masses[twin.name] = twin
        else:
            errors.append(f"{where}: lines start with build, mass, roof, facade, detail or mirror; not {word!r}")
    if not design.masses and not errors:
        errors.append("the design has no mass: start with a line like: mass hall box(9,4,7)")
    if errors:
        raise DesignError("\n".join(errors[:8]) + (f"\nand {len(errors) - 8} more" if len(errors) > 8 else ""))
    return design


# Layout -----------------------------------------------------------------------------------------

def _lay_out(design: Design) -> None:
    """Gives every mass its place. Raises DesignError where they do not fit together."""
    errors: list[str] = []
    for mass in design.masses.values():
        if mass.parent is None:
            mass.x0 = mass.z0 = 0
            continue
        parent = design.masses[mass.parent]
        where = f"line {mass.line}"
        along_x = mass.side in ("north", "south")
        mine, theirs = (mass.w, parent.w) if along_x else (mass.d, parent.d)
        if mine > theirs:
            errors.append(f"{where}: {mass.name} is {mine} long where it meets {parent.name}.{mass.side}, which is "
                          f"only {theirs}; make it {theirs} or less, or attach it to a longer side")
            continue
        slack = theirs - mine
        base = {"start": 0, "center": slack // 2, "end": slack}[mass.align]
        shift = base + mass.offset
        if not 0 <= shift <= slack:
            errors.append(f"{where}: offset={mass.offset} slides {mass.name} off the end of {parent.name}.{mass.side}; "
                          f"here offset can go from {-base} to {slack - base}")
            continue
        if along_x:
            mass.x0 = parent.x0 + shift
            mass.z0 = parent.z0 - (mass.d - 1) if mass.side == "north" else parent.z1
        else:
            mass.z0 = parent.z0 + shift
            mass.x0 = parent.x0 - (mass.w - 1) if mass.side == "west" else parent.x1
        if not _doorway(mass, parent):
            errors.append(f"{where}: {mass.name} meets {parent.name} only at a corner, so no doorway fits between "
                          "them; slide it along with align or offset")
    masses = list(design.masses.values())
    for i, a in enumerate(masses):
        for b in masses[i + 1:]:
            wide = min(a.x1, b.x1) - max(a.x0, b.x0) + 1
            deep = min(a.z1, b.z1) - max(a.z0, b.z0) + 1
            if wide > 1 and deep > 1:
                errors.append(f"line {b.line}: {b.name} overlaps {a.name} by {wide} by {deep} blocks; masses may "
                              "share a wall, not floor space. Move it with align or offset, or attach it elsewhere")
    xs = [v for m in masses for v in (m.x0, m.x1)]
    zs = [v for m in masses for v in (m.z0, m.z1)]
    if xs and (max(xs) - min(xs) + 1 > MAX_SPAN or max(zs) - min(zs) + 1 > MAX_SPAN):
        errors.append(f"the design is {max(xs) - min(xs) + 1} by {max(zs) - min(zs) + 1}; at most {MAX_SPAN} either way")
    if errors:
        raise DesignError("\n".join(errors))


def _doorway(mass: Mass, parent: Mass) -> Optional[tuple[int, int]]:
    """Where the doorway between an attached mass and its parent goes: the middle of the wall they share."""
    line = mass.wall(_OPPOSITE[mass.side or "north"])
    shared = [(x, z) for (x, z) in line[1:-1]
              if (parent.x0 < x < parent.x1 if mass.side in ("north", "south") else parent.z0 < z < parent.z1)]
    return shared[len(shared) // 2] if shared else None


# Compiling --------------------------------------------------------------------------------------

class _Site:
    """Every block of the design by position. A later block at a spot replaces an earlier one, whatever
    part it belongs to, so no two parts ever want the same spot."""

    def __init__(self, palette: dict[str, str], texture: str):
        self.palette = palette
        self.texture = texture
        self.cells: dict[tuple[int, int, int], tuple[str, str, Optional[str]]] = {}

    def put(self, part: str, x: int, y: int, z: int, role: str, facing: Optional[str] = None) -> None:
        item = AIR if role == "air" else full(self.palette.get(role, role))
        if self.texture == "mixed" and role in structures._MIXED_ROLES:
            item = structures.mixed(item, x, y, z)
        self.cells[(x, y, z)] = (part, item, facing)

    def item(self, x: int, y: int, z: int) -> Optional[str]:
        found = self.cells.get((x, y, z))
        return found[1] if found else None

    def solid(self, x: int, y: int, z: int) -> bool:
        found = self.cells.get((x, y, z))
        return found is not None and found[1] != AIR


def _split(length: int, parts: list[str], where: str) -> tuple[list[tuple[int, int]], list[int]]:
    """The bays of a wall for a list of parts: each as (first, last) along the wall, and the posts between."""
    n, inner = len(parts), length - 2
    room = inner - (n - 1)
    posts = True
    if room < n:
        room, posts = inner, False
    if room < n:
        raise DesignError(f"{where} is {length} long: room for at most {max(1, inner)} bays, not {n}")
    widths = [room // n] * n
    for i in sorted(range(n), key=lambda i: (abs(i - (n - 1) / 2), i))[:room - sum(widths)]:
        widths[i] += 1
    bays, between, at = [], [], 1
    for i, width in enumerate(widths):
        bays.append((at, at + width - 1))
        at += width
        if posts and i < n - 1:
            between.append(at)
            at += 1
    return bays, between


def _auto(length: int) -> tuple[list[tuple[int, int]], list[int]]:
    """Bays for a wall with no facade: a post at least every fourth block."""
    posts = structures._posts(length)
    return [(a + 1, b - 1) for a, b in zip(posts, posts[1:]) if b - a > 1], posts[1:-1]


def compile_design(program: str, style: Optional[str] = None, materials: Optional[dict[str, str]] = None,
                   texture: Optional[str] = None) -> Compiled:
    """Turns a design into a structure. Raises DesignError saying what is wrong, in the design's own words."""
    design = parse(program)
    _lay_out(design)
    palette = _palette(design, style, materials)
    texture = texture or design.texture or "plain"
    if texture not in structures.TEXTURES:
        raise DesignError(f"texture must be one of {', '.join(structures.TEXTURES)}; not {texture}")
    site = _Site(palette, texture)
    out = Compiled(design, Structure("design", "", []), palette)
    masses = list(design.masses.values())
    errors: list[str] = []

    def others(mass: Mass) -> list[Mass]:
        return [m for m in masses if m is not mass]

    def covered(mass: Mass, x: int, z: int, y: int = 0) -> Optional[Mass]:
        return next((m for m in others(mass) if m.covers(x, z) and y <= m.h + 1), None)

    # Shells: dug-out rooms, floors, walls with corner posts, upper floors.
    for mass in masses:
        name = mass.name
        for x in range(mass.x0, mass.x1 + 1):
            for z in range(mass.z0, mass.z1 + 1):
                edge = not mass.inside(x, z)
                corner = x in (mass.x0, mass.x1) and z in (mass.z0, mass.z1)
                site.put(name, x, -1, z, "foundation" if edge else "floor")
                for y in range(mass.h):
                    if not edge:
                        site.put(name, x, y, z, "floor" if y == mass.upper else "air")
                    elif corner:
                        site.put(name, x, y, z, "post")
                    else:
                        site.put(name, x, y, z, "foundation" if y == 0 and mass.h >= 4 else "wall")
        out.floors[name] = [(x, 0, z) for x in range(mass.x0 + 1, mass.x1) for z in range(mass.z0 + 1, mass.z1)]

    # A door somewhere, if the design names none: the first free wall of the first mass, front first.
    has_door = any(p in ("door", "door_porch", "opening") for m in masses for parts in m.facades.values() for p in parts)
    auto_door: Optional[tuple[str, str]] = None
    if not has_door:
        for candidate in masses:
            for side in ("south", "east", "west", "north"):
                line = candidate.wall(side)
                mid = line[len(line) // 2]
                ox, oz = _OUT[side]
                if side not in candidate.facades and not any(
                        covered(candidate, x + ox * k, z + oz * k) for (x, z) in line[1:-1] for k in (1, 2)
                        if abs(x - mid[0]) + abs(z - mid[1]) <= 1):
                    auto_door = (candidate.name, side)
                    break
            if auto_door:
                break
        if auto_door is None:
            errors.append("there is no free wall for a door: give one facade a door, like: facade "
                          f"{design.first.name}.south -> [window, door, window]")
        else:
            out.notes.append(f"No door was written, so one was put in the middle of {auto_door[0]}.{auto_door[1]}.")

    # Facades.
    entrance: Optional[tuple[int, int, int]] = None
    entrance_is_door = False
    blocked: set[tuple[int, int]] = set()    # floor cells to keep clear: in front of doors and doorways
    for mass in masses:
        for side in SIDES:
            line = mass.wall(side)
            ox, oz = _OUT[side]
            ax, az = (1, 0) if side in ("north", "south") else (0, 1)
            where = f"{mass.name}.{side}"
            explicit = side in mass.facades
            try:
                if explicit:
                    parts = list(mass.facades[side])
                    bays, posts = _split(len(line), parts, where)
                else:
                    if auto_door == (mass.name, side):
                        # The door in the middle, whatever the posts would have done.
                        parts = ["window", "door", "window"] if len(line) >= 7 else ["door"]
                        bays, posts = _split(len(line), parts, where)
                    else:
                        bays, posts = _auto(len(line))
                        parts = ["window"] * len(bays)
            except DesignError as error:
                errors.append(f"line {mass.line}: {error}")
                continue
            for i in posts:
                x, z = line[i]
                for y in range(mass.h):
                    site.put(mass.name, x, y, z, "post")
            built: list[str] = []
            for (a, b), part in zip(bays, parts):
                c = (a + b) // 2
                x, z = line[c]
                facing_other = covered(mass, x + ox, z + oz)
                if part != "wall" and facing_other is not None:
                    if part in ("door", "door_porch", "opening") and explicit:
                        errors.append(f"line {mass.line}: the {part} of {where} would open into {facing_other.name}, "
                                      "which is attached there; put it in another bay or on another side (the "
                                      "doorway between the two is made for you)")
                    built.append("wall")
                    continue
                built.append(part)
                if part in ("window", "tall_window", "wide_window"):
                    cells = [c]
                    if part == "wide_window":
                        cells = list(range(a, b + 1)) if b - a < 3 else [c - 1, c, c + 1]
                    for base, clear in mass.storeys():
                        rows = [base + 1] + ([base + 2] if clear >= 5 or (part == "tall_window" and clear >= 4) else [])
                        for j in cells:
                            wx, wz = line[j]
                            if covered(mass, wx + ox, wz + oz, rows[0]) is None:
                                for y in rows:
                                    site.put(mass.name, wx, y, wz, "window")
                elif part in ("door", "door_porch", "opening"):
                    depth = 3 if part == "door_porch" else 1
                    hit = next((m for k in range(1, depth + 1) for j in (c - 1, c, c + 1) if 0 <= j < len(line)
                                for m in [covered(mass, line[j][0] + ox * k, line[j][1] + oz * k)] if m), None)
                    if hit is not None and part == "door_porch":
                        errors.append(f"line {mass.line}: the porch of {where} runs into {hit.name}; use door there, "
                                      "or move it to a bay with three clear blocks in front")
                        continue
                    if part == "opening":
                        site.put(mass.name, x, 0, z, "air")
                        site.put(mass.name, x, 1, z, "air")
                    else:
                        # The door's upper half fills this spot by itself: no entry, so nothing tries
                        # to keep it empty.
                        site.put(mass.name, x, 0, z, "door", _OPPOSITE[side])
                        site.cells.pop((x, 1, z), None)
                    for y in (0, 1):
                        site.put(mass.name, x + ox, y, z + oz, "air")
                    blocked.add((x - ox, z - oz))
                    for s in (-1, 1):
                        lx, lz = x + ax * s + ox, z + az * s + oz
                        if covered(mass, lx, lz) is None and part != "opening":
                            site.put(mass.name if part == "door" else "trim", lx, 0, lz, "light", "on:down")
                    if part == "door_porch":
                        for s in (-1, 1):
                            for y in (0, 1):
                                site.put("trim", x + ax * s + ox * 2, y, z + az * s + oz * 2, "fence")
                        for k in (1, 2):
                            for s in (-1, 0, 1):
                                site.put("trim", x + ax * s + ox * k, 2, z + az * s + oz * k, "ridge", "bottom")
                    if entrance is None or (part != "opening" and not entrance_is_door):
                        entrance = (x + ox * depth, 0, z + oz * depth)
                        entrance_is_door = part != "opening"
                elif part == "buttress":
                    for y in (0, 1):
                        site.put("trim", x + ox, y, z + oz, "foundation")
                    site.put("trim", x + ox, 2, z + oz, "roof", _OPPOSITE[side])
                for base, clear in mass.storeys()[1:]:
                    if part in ("door", "door_porch", "opening") and covered(mass, x + ox, z + oz, base + 1) is None:
                        site.put(mass.name, x, base + 1, z, "window")
            out.walls[(mass.name, side)] = built

    # Doorways between a mass and the one it is attached to.
    for mass in masses:
        if mass.parent is None:
            continue
        gap = _doorway(mass, design.masses[mass.parent])
        if gap is None:
            continue
        ox, oz = _OUT[mass.side or "north"]
        for y in (0, 1):
            site.put(mass.name, gap[0], y, gap[1], "air")
        blocked |= {(gap[0] + ox, gap[1] + oz), (gap[0] - ox, gap[1] - oz)}

    for mass in masses:
        to = mass.roof_options.get("to") or (_OPPOSITE[mass.side] if mass.side else "north")
        span = mass.d if to in ("north", "south") else mass.w
        if mass.roof == "lean" and span > 6:
            errors.append(f"line {mass.line}: a lean roof on {mass.name} would span {span} blocks towards {to}; at "
                          "most 6. Use a gable roof, or lean it the short way")
        if mass.roof == "hip" and min(mass.w, mass.d) < 5:
            errors.append(f"line {mass.line}: a hip roof needs a mass at least 5 by 5; {mass.name} is {mass.w} by "
                          f"{mass.d}. Use gable")
    if errors:
        raise DesignError("\n".join(errors))

    # Roofs, the lowest first, so a taller mass's roof wins where two meet.
    for mass in sorted(masses, key=lambda m: m.h):
        cells = _roof(mass, design)
        for (x, y, z), (role, facing) in cells.items():
            if any(m.covers(x, z) and y < m.h for m in others(mass)):
                continue     # inside a neighbour's walls or room
            site.put("roof", x, y, z, role, facing)
        out.roof_tops[mass.name] = max((y for (_, y, _) in cells), default=mass.h)

    # Chimneys: up the outside of a wall, through the eaves, to one over the top of the roof.
    for mass in masses:
        if not mass.wants_chimney:
            continue
        ends = ("east", "west") if _ridge(mass) == "x" else ("north", "south")
        choices = [mass.chimney] if mass.chimney else list(ends) + [s for s in SIDES if s not in ends]
        placed = False
        for side in choices:
            line = mass.wall(side)
            ox, oz = _OUT[side]
            mid = len(line) // 2
            spot = None
            for j in (mid, mid - 1, mid + 1):
                if not 0 < j < len(line) - 1:
                    continue
                cx, cz = line[j][0] + ox, line[j][1] + oz
                if any(m.covers(cx, cz) for m in others(mass)):
                    continue
                # Not through a porch, a buttress or a doorstep.
                if any((cx, y, cz) in site.cells and site.cells[(cx, y, cz)][0] != "roof" for y in range(0, 3)):
                    continue
                spot = (cx, cz)
                break
            if spot is None:
                continue
            for y in range(-1, out.roof_tops[mass.name] + 2):
                site.put("trim", spot[0], y, spot[1], "chimney")
            placed = True
            break
        if not placed:
            raise DesignError(f"line {mass.line}: no room for the chimney of {mass.name}"
                              + (f" at {mass.chimney}" if mass.chimney else "") + ": that wall is built against; "
                              "name a free side, like: detail " + mass.name + " chimney at "
                              + next((s for s in SIDES if s != mass.chimney), "north"))

    # Inside: ladders, furniture, then light.
    for index, mass in enumerate(masses):
        taken = set(blocked)
        hole: set[tuple[int, int]] = set()
        if mass.upper is not None:
            rung = _ladder(mass, site, taken)
            if rung is None:
                raise DesignError(f"line {mass.line}: no wall inside {mass.name} is free for the ladder to the "
                                  "upper floor; leave one bay as wall")
            (lx, lz), side = rung
            for y in range(0, mass.upper + 1):
                site.put("inside", lx, y, lz, "ladder", f"on:{side}")
            taken.add((lx, lz))
            hole.add((lx, lz))
        wanted = mass.furnish if mass.furnish is not None else (
            ["bed", "crafting_table", "furnace", "chest"] if index == 0 else [])
        left = _furnish(mass, site, taken, list(wanted))
        if left:
            out.notes.append(f"No room in {mass.name} for: {', '.join(left)}.")
        for base, _ in mass.storeys():
            for (x, z) in _lights(mass, taken if base == 0 else hole):
                site.put("inside", x, base, z, "light", "on:down")
                out.lights.append((x, base, z))
            if base:
                out.floors[mass.name] += [(x, base, z) for x in range(mass.x0 + 1, mass.x1)
                                          for z in range(mass.z0 + 1, mass.z1)]

    total = sum(1 for (_, item, _) in site.cells.values() if item != AIR)
    if total > MAX_BLOCKS:
        raise DesignError(f"the design takes {total} blocks; at most {MAX_BLOCKS}. Make the masses smaller or fewer")

    order = [m.name for m in masses] + ["inside", "roof", "trim"]
    by_part: dict[str, list[Block]] = {name: [] for name in order}
    for (x, y, z), (part, item, facing) in site.cells.items():
        by_part[part].append((x, y, z, item, facing))
    parts = []
    for name in order:
        blocks = sorted(by_part[name], key=lambda b: (b[3] != AIR, b[1], b[2], b[0]))
        if blocks:
            parts.append(Part(name, blocks, footing=name in design.masses, optional=name == "trim"))
    xs = [m.x1 for m in masses] + [m.x0 for m in masses]
    zs = [m.z1 for m in masses] + [m.z0 for m in masses]
    shape = ", ".join(f"{m.name} {m.w}x{m.d} walls {m.h} with a {m.roof} roof" for m in masses)
    notes = list(out.notes)
    if any("white_bed" in b[3] for b in by_part["inside"]):
        notes.append("The bed needs 3 white wool and 3 planks; write detail <mass> furnish [...] without bed to skip it.")
    if len(parts) > 1:
        notes.append("Build it part by part with part=...: " + ", ".join(p.name for p in parts) + ".")
    out.structure = structures._normalised(Structure(
        "design", f"{design.name}: {max(xs) - min(xs) + 1}x{max(zs) - min(zs) + 1} design of {len(masses)} "
        f"{'mass' if len(masses) == 1 else 'masses'} ({shape})", parts, entrance=entrance, notes=notes,
        footing=full(palette["foundation"])))
    out.cells = site.cells
    return out


def _palette(design: Design, style: Optional[str], materials: Optional[dict[str, str]]) -> dict[str, str]:
    """The palette: the style given with the call, else the one the design names, else oak. A theme of
    the style book counts as a style and brings its own swaps."""
    name = (style or design.style or "oak").strip().lower()
    swaps: dict[str, str] = {}
    if name in stylebook.THEMES:
        theme = stylebook.THEMES[name]
        name, swaps = theme.style, dict(theme.materials)
    if name not in structures.PALETTES:
        raise DesignError(f"unknown style {name}; styles: {', '.join(structures.PALETTES)}; themes: "
                          f"{', '.join(stylebook.THEMES)}")
    try:
        return structures.palette(name, {**swaps, **(materials or {})})
    except ValueError as error:
        raise DesignError(str(error)) from None


def header_style(program: str) -> Optional[str]:
    """The style a design names on its build line, if it reads that far."""
    m = re.search(r"^\s*build\s+\w+[^\n;#]*?\b(?:style|theme)=(\w+)", program or "", re.MULTILINE | re.IGNORECASE)
    return m.group(1).lower() if m else None


def _ridge(mass: Mass) -> str:
    return mass.roof_options.get("ridge") or ("x" if mass.w >= mass.d else "z")


def _roof(mass: Mass, design: Design) -> dict[tuple[int, int, int], tuple[str, Optional[str]]]:
    cells: dict[tuple[int, int, int], tuple[str, Optional[str]]] = {}
    x0, x1, z0, z1, h = mass.x0, mass.x1, mass.z0, mass.z1, mass.h
    overhang = mass.roof_options.get("overhang", 1)
    if mass.roof == "flat":
        for x in range(x0, x1 + 1):
            for z in range(z0, z1 + 1):
                cells[(x, h, z)] = ("roof_fill", None)
                if mass.roof_options.get("parapet") and (x in (x0, x1) or z in (z0, z1)):
                    cells[(x, h + 1, z)] = ("ridge", "bottom")
    elif mass.roof == "gable":
        along_x = _ridge(mass) == "x"
        u0, u1, v0, v1 = (x0, x1, z0, z1) if along_x else (z0, z1, x0, x1)

        def put(u: int, y: int, v: int, role: str, facing: Optional[str] = None) -> None:
            if not along_x and facing in ("south", "north"):
                facing = "east" if facing == "south" else "west"
            cells[(u, y, v) if along_x else (v, y, u)] = (role, facing)

        low, high, level = v0 - overhang, v1 + overhang, h - overhang
        while low <= high:
            eave = level < h
            for u in range(u0 - overhang, u1 + overhang + 1):
                if eave and not u0 <= u <= u1:
                    continue
                if low == high:
                    put(u, level, low, "ridge", "bottom")
                else:
                    put(u, level, low, "roof", "south")
                    put(u, level, high, "roof", "north")
                    if not eave:
                        for v in range(low + 1, high):
                            put(u, level, v, "wall" if u in (u0, u1) else "roof_fill")
            low, high, level = low + 1, high - 1, level + 1
    elif mass.roof == "hip":
        ax0, ax1, az0, az1, level = x0 - overhang, x1 + overhang, z0 - overhang, z1 + overhang, h - overhang
        while ax0 <= ax1 and az0 <= az1:
            eave = level < h
            if ax0 == ax1 or az0 == az1:
                for x in range(ax0, ax1 + 1):
                    for z in range(az0, az1 + 1):
                        cells[(x, level, z)] = ("ridge", "bottom")
                break
            for x in range(ax0, ax1 + 1):
                for z in range(az0, az1 + 1):
                    on_x, on_z = x in (ax0, ax1), z in (az0, az1)
                    if on_x and on_z and eave:
                        continue
                    if on_z:
                        cells[(x, level, z)] = ("roof", "south" if z == az0 else "north")
                    elif on_x:
                        cells[(x, level, z)] = ("roof", "east" if x == ax0 else "west")
                    elif not eave:
                        cells[(x, level, z)] = ("roof_fill", None)
            ax0, ax1, az0, az1, level = ax0 + 1, ax1 - 1, az0 + 1, az1 - 1, level + 1
    else:
        to = mass.roof_options.get("to") or (_OPPOSITE[mass.side] if mass.side else "north")
        across_z = to in ("north", "south")
        span = mass.d if across_z else mass.w
        start, step = {"north": (z1, -1), "south": (z0, 1), "west": (x1, -1), "east": (x0, 1)}[to]
        u0, u1 = (x0, x1) if across_z else (z0, z1)
        for row in range(-overhang, span):
            y, v = h + row, start + step * row
            for u in range(u0, u1 + 1):
                cells[(u, y, v) if across_z else (v, y, u)] = ("roof", to)
                for behind in range(row + 1, span):
                    if row < 0:
                        break
                    w = start + step * behind
                    cells[(u, y, w) if across_z else (w, y, u)] = ("wall" if u in (u0, u1) else "roof_fill", None)
    return cells


def _ring(mass: Mass) -> list[tuple[tuple[int, int], str]]:
    """The floor cells along the walls with the wall each stands at: north wall first, then west, east, south."""
    out: list[tuple[tuple[int, int], str]] = []
    out += [((x, mass.z0 + 1), "north") for x in range(mass.x1 - 1, mass.x0, -1)]
    out += [((mass.x0 + 1, z), "west") for z in range(mass.z0 + 2, mass.z1)]
    out += [((mass.x1 - 1, z), "east") for z in range(mass.z0 + 2, mass.z1)]
    out += [((x, mass.z1 - 1), "south") for x in range(mass.x0 + 2, mass.x1 - 1)]
    return out


def _ladder(mass: Mass, site: _Site, taken: set[tuple[int, int]]) -> Optional[tuple[tuple[int, int], str]]:
    """A floor cell by a stretch of wall that is solid all the way up to the upper floor."""
    top = mass.upper or 0
    for (x, z), side in _ring(mass):
        ox, oz = _OUT[side]
        if (x, z) in taken:
            continue
        wall = [site.item(x + ox, y, z + oz) for y in range(0, top + 1)]
        if all(item not in (None, AIR) and "glass" not in item and "door" not in item for item in wall):
            return (x, z), side
    return None


def _furnish(mass: Mass, site: _Site, taken: set[tuple[int, int]], wanted: list[str]) -> list[str]:
    """Furniture along the walls, each piece facing into the room. Returns what found no place."""
    spots = [(cell, side) for cell, side in _ring(mass) if cell not in taken]
    left: list[str] = []
    if "bed" in wanted:
        wanted.remove("bed")
        placed = False
        for (x, z), side in sorted(spots, key=lambda s: s[1] != "north"):
            ox, oz = _OUT[side]
            foot = (x - ox, z - oz)
            if (x, z) in taken or foot in taken or not mass.inside(*foot) or site.solid(foot[0], 0, foot[1]):
                continue
            # The foot goes down first, looking at the wall; the head lands against the wall.
            site.put("inside", foot[0], 0, foot[1], "white_bed", side)
            taken |= {(x, z), foot}
            placed = True
            break
        if not placed:
            left.append("bed")
    for item in wanted:
        spot = next(((cell, side) for cell, side in spots if cell not in taken), None)
        if spot is None:
            left.append(item)
            continue
        (x, z), side = spot
        site.put("inside", x, 0, z, item, side if short(item) in _FACING_ITEMS else None)
        taken.add((x, z))
    return left


def _lights(mass: Mass, taken: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Torches on the floor so that no floor cell is more than seven steps from one."""
    floor = [(x, z) for x in range(mass.x0 + 1, mass.x1) for z in range(mass.z0 + 1, mass.z1)]
    free = [c for c in floor if c not in taken]
    corners = [(mass.x0 + 1, mass.z1 - 1), (mass.x1 - 1, mass.z1 - 1), (mass.x1 - 1, mass.z0 + 1),
               (mass.x0 + 1, mass.z0 + 1)]
    chosen: list[tuple[int, int]] = []

    def dark() -> list[tuple[int, int]]:
        return [c for c in floor if all(abs(c[0] - t[0]) + abs(c[1] - t[1]) > 7 for t in chosen)]

    for corner in corners[:2]:
        if corner in free and corner not in chosen:
            chosen.append(corner)
    while free and dark():
        unlit = dark()
        best = max((c for c in free if c not in chosen),
                   key=lambda c: (sum(1 for u in unlit if abs(c[0] - u[0]) + abs(c[1] - u[1]) <= 7),
                                  c in corners), default=None)
        if best is None:
            break
        chosen.append(best)
    return chosen


def designed(program: Optional[str] = None, style: Optional[str] = None,
             materials: Optional[dict[str, str]] = None, texture: Optional[str] = None) -> Structure:
    """A building of your own design, written in the design language as program (see design_guide):
    masses joined to each other, a roof on each, facades of windows and doors, a chimney, furniture.
    Check it with check_design first."""
    return compile_design(program or DEFAULT_PROGRAM, style, materials, texture).structure


DEFAULT_PROGRAM = """build cottage
mass hall box(9,4,7)
mass wing box(5,3,5) attach hall.west align=back
roof hall gable(overhang=1) ; roof wing gable(overhang=1)
facade hall.south -> [window, door_porch, window]
detail hall chimney at east"""


# Reading a design as a builder would ----------------------------------------------------------------

def review(made: Compiled, theme: Optional[str] = None) -> tuple[list[str], list[str]]:
    """What would make the design look better: things to fix, and things to consider. Read from the
    design itself, so it works without pictures."""
    fix: list[str] = []
    consider: list[str] = []
    masses = list(made.design.masses.values())
    first = masses[0]
    palette = made.palette
    card = stylebook.CARDS.get(theme or made.design.style or "")
    porch = any("door_porch" in parts for parts in made.walls.values())
    if len(masses) == 1 and first.w * first.d >= 35 and not porch:
        fix.append(f"One plain box. Attach a smaller mass (mass wing box(5,3,5) attach {first.name}.west "
                   "align=back) or give the door a porch (door_porch).")
    for m in masses:
        if m.roof in ("gable", "hip") and m.roof_options.get("overhang", 1) == 0:
            fix.append(f"{m.name}: the roof stops at the walls. Let it overhang: roof {m.name} {m.roof}(overhang=1).")
        if m.roof == "flat" and (card is None or "flat" not in card.roofs) and m is first:
            fix.append(f"{m.name}: a flat roof makes the main building a box. Use gable or hip unless the "
                       "style is a flat-roofed one (desert_house).")
        if card is not None and m is first and m.roof not in card.roofs:
            consider.append(f"{m.name}: the {card.name} style goes with a {' or '.join(card.roofs)} roof, not {m.roof}.")
        if m.h == 3 and min(m.w, m.d) >= 9:
            fix.append(f"{m.name}: walls 3 high over a {m.w}x{m.d} room make it squat. Give it walls 4 or 5 high.")
        if m.h >= m.w + 3 and m.h >= m.d + 3 and m.floors == 1:
            consider.append(f"{m.name}: walls {m.h} high on a {m.w}x{m.d} footprint are a shaft; floors=2 uses the height.")
        for side in SIDES:
            parts = made.walls.get((m.name, side), [])
            line = m.wall(side)
            ox, oz = _OUT[side]
            hidden = sum(1 for (x, z) in line if any(o.covers(x + ox, z + oz) for o in masses if o is not m))
            if len(line) >= 5 and hidden * 2 < len(line) and all(p == "wall" for p in parts):
                fix.append(f"{m.name}.{side}: {len(line)} blocks of blank wall. Give it windows: facade "
                           f"{m.name}.{side} -> [window, window].")
        if m is not first and m.w * m.d > first.w * first.d:
            consider.append(f"{m.name} is bigger than {first.name}: write the largest mass first, so the others "
                            "read as its wings.")
        if m.parent and m.h <= made.design.masses[m.parent].h and (
                made.roof_tops.get(m.name, 0) > made.roof_tops.get(m.parent, 0)):
            consider.append(f"The roof of {m.name} rises above that of {m.parent}: lower its walls or give it "
                            "a lean roof, so the main roof stays the highest.")
    if len(masses) > 1 and len({m.h for m in masses}) == 1:
        fix.append(f"Every mass has walls {first.h} high, so the roofs run together. Make {first.name} one or two "
                   "higher than the rest.")
    earthy = sorted({short(item) for role, item in palette.items()
                     if role in ("wall", "foundation", "floor", "roof_fill") and short(item) in ("dirt", "coarse_dirt")})
    if earthy:
        fix.append("Walls or roof of " + ", ".join(earthy) + ": fine for one night, not for a house. Use a style "
                   "(oak, spruce, cobblestone) or swap the material.")
    dark = 0
    for name, cells in made.floors.items():
        dark += sum(1 for (x, y, z) in cells
                    if all(ly != y or abs(x - lx) + abs(z - lz) > 7 for (lx, ly, lz) in made.lights))
    if dark:
        fix.append(f"{dark} floor blocks are more than 7 steps from a torch: monsters can appear there. Leave "
                   "wall free inside for torches, or make the rooms smaller.")
    long_ridge = [m for m in masses if m.roof == "gable" and max(m.w, m.d) >= 7]
    if long_ridge and not any(m.wants_chimney for m in masses):
        consider.append(f"Nothing breaks the roof line. A chimney does: detail {long_ridge[0].name} chimney.")
    wall_wood, roof_wood = stylebook.wood_of(palette["wall"]), short(palette["roof"]).removesuffix("_stairs")
    if wall_wood and roof_wood == wall_wood:
        consider.append(f"Walls and roof are both {wall_wood}. A darker roof reads better: materials "
                        "{\"roof\": \"spruce_stairs\", \"roof_fill\": \"spruce_planks\", \"ridge\": \"spruce_slab\"} "
                        "or cobblestone_stairs, cobblestone, cobblestone_slab.")
    if not porch and len(masses) > 1 and any("door" in parts for parts in made.walls.values()):
        consider.append("A porch marks the way in: door_porch in place of door.")
    return fix, consider


def floor_plan(made: Compiled) -> str:
    """The ground floor drawn from above, north at the top: # wall, P post, o window, D door, _ doorway,
    b bed, c chest and other furniture, i torch, H ladder, + porch, C chimney, . floor."""
    cells = made.cells
    masses = list(made.design.masses.values())
    rows = []
    for z in range(min(m.z0 for m in masses) - 3, max(m.z1 for m in masses) + 4):
        row = ""
        for x in range(min(m.x0 for m in masses) - 2, max(m.x1 for m in masses) + 3):
            low, eye = cells.get((x, 0, z)), cells.get((x, 1, z))
            on_wall = any(m.covers(x, z) and not m.inside(x, z) for m in masses)
            within = any(m.inside(x, z) for m in masses)
            item = short(low[1]) if low else ""
            eye_item = short(eye[1]) if eye else ""
            if low and low[0] == "trim" and "fence" not in item and item != "torch":
                char = "C"
            elif "door" in item:
                char = "D"
            elif on_wall and ("pane" in eye_item or "glass" in eye_item):
                char = "o"
            elif on_wall and low and low[1] == AIR:
                char = "_"
            elif on_wall and ("log" in item or item.startswith("cut_")):
                char = "P"
            elif on_wall:
                char = "#"
            elif "bed" in item:
                char = "b"
            elif item == "torch":
                char = "i"
            elif item == "ladder":
                char = "H"
            elif "fence" in item or (cells.get((x, 2, z)) or ("", "", None))[0] == "trim":
                char = "+"
            elif within and low and low[1] != AIR:
                char = "c"
            elif within:
                char = "."
            else:
                char = " "
            row += char
        rows.append(row.rstrip())
    while rows and not rows[0]:
        rows.pop(0)
    while rows and not rows[-1]:
        rows.pop()
    return "\n".join(rows)


def reference() -> str:
    """The language on one card, for the model."""
    facade = ", ".join(_FACADE_PARTS)
    lines = [
        "The design language. One statement per line (or several split with ;). Sizes are in blocks.",
        "  build <name> style=<style or theme>          optional first line",
        "  mass <name> box(width, wall height, depth)   the first mass; width 4-21 (x), height 3-9, depth 4-21 (z)",
        "  mass <name> box(w,h,d) attach <mass>.<side> [align=back|center|front or west|center|east] [offset=n] [floors=2]",
        "                                               shares that wall; a doorway between the two is cut for you",
        "  mirror <mass> as <name>                      a twin of an attached mass on the opposite side",
        "  roof <mass> gable(overhang=1, ridge=x|z) | hip(overhang=1) | flat(parapet=1) | lean(to=<side>)",
        "  facade <mass>.<side> -> [part, part, ...]    one part per bay, west to east or north to south",
        f"                                               parts: {facade}",
        "  detail <mass> chimney [at <side>]",
        "  detail <mass> furnish [bed, crafting_table, furnace, chest]     [] for none",
        "Sides are north, east, south, west; the front is south unless facing turns the whole design.",
        "Left to the compiler: every coordinate, which way stairs, doors, beds and chests are turned, posts at "
        "the corners and between bays, a window in every bay of a wall with no facade, a door if you write none, "
        "the floor, the stone footing, torches inside, the ladder to an upper floor.",
        "Parts:",
    ]
    lines += [f"  {p.name} ({p.where}): {p.what} Needs: {p.needs}. Costs: {p.cost}." for p in PARTS.values()]
    return "\n".join(lines)


structures.KINDS["design"] = (designed, ("program", "style", "materials", "texture"))
