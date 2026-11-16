"""A small book of building styles: which materials and shapes suit which place.

A build looks right when it fits where it stands: the wood that grows around it, the stone
under it, a roof darker than the walls, a base heavier than what it carries. Each theme here
is a set of choices for the ready-made structures (a palette, a few options) plus the rules a
builder would follow when drawing something of their own in that style.

``for_biome`` picks the theme for where the player is, so a house asked for without a style
comes out in spruce in a taiga and sandstone in a desert. ``build_structure(theme=...)`` and
the ``styles`` tool give the model the same choices by name. It is all text, so it works for a
model that cannot look at pictures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .state import short


@dataclass(frozen=True)
class Theme:
    name: str
    summary: str
    #: Parts of biome names this theme suits, e.g. "taiga" matches snowy_taiga and old_growth_pine_taiga.
    biomes: tuple[str, ...]
    #: The palette of the ready-made structures it starts from.
    style: str
    #: Single materials swapped in that palette, by role.
    materials: dict[str, str] = field(default_factory=dict)
    #: Other options for a house: roof, height, texture.
    options: dict[str, Any] = field(default_factory=dict)
    #: What makes the style, for a builder drawing their own.
    rules: str = ""


THEMES: dict[str, Theme] = {t.name: t for t in (
    Theme("cottage", "Oak cottage: plank walls between oak log posts, an oak stair roof, a cobblestone base "
          "and chimney. The friendly default for plains, meadows and oak forest.",
          ("plains", "meadow", "forest", "river", "beach", "sunflower"), "oak",
          rules="Planks between log posts, a pitched roof one block wider than the walls, a cobblestone "
          "course at the ground, a chimney at one end. If spruce or dark oak grows near, use it for the roof "
          "(roof, roof_fill, ridge): a roof darker than the walls reads best."),
    Theme("cabin", "Spruce cabin: dark plank walls between spruce log posts, a spruce roof, cobblestone "
          "footing and chimney. For taiga, snow and mountain woods.",
          ("taiga", "snowy", "grove", "frozen", "ice", "pine", "spruce"), "spruce",
          rules="Dark and heavy. A steep roof with deep eaves so snow would slide off, small windows, a big "
          "chimney. With logs to spare, make the walls of spruce_log too (materials wall=spruce_log)."),
    Theme("birch_lodge", "Pale birch walls and posts under a grey cobblestone roof. For birch forest.",
          ("birch",), "birch",
          {"roof": "cobblestone_stairs", "roof_fill": "cobblestone", "ridge": "cobblestone_slab"},
          rules="Birch is very pale: it needs a darker roof to read as a house and not a white box, and "
          "stone is the dark block that is always near. Keep the birch logs as posts, their bark gives the "
          "walls a pattern."),
    Theme("manor", "Dark oak, walls four high with two rows of windows, on a cobblestone base. For dark "
          "forest and pale garden.",
          ("dark_forest", "pale_garden"), "dark_oak", {}, {"height": 4},
          rules="Tall and sombre. Walls four or five high, a steep dark roof, torches at every door."),
    Theme("desert_house", "Sandstone walls with cut-sandstone posts and a flat roof. For desert and badlands.",
          ("desert", "badlands", "mesa"), "sandstone", {}, {"roof": "flat"},
          rules="Thick pale walls, small windows, a flat roof you can stand on. No timber frame: wood is "
          "scarce here, so it is kept for the door."),
    Theme("savanna_hut", "Orange acacia planks between acacia log posts on a cobblestone base. For savanna.",
          ("savanna",), "acacia",
          rules="Warm colours: acacia planks and logs over a grey stone base, a low wide roof."),
    Theme("jungle_lodge", "Jungle planks and logs on a cobblestone base. For jungle and bamboo.",
          ("jungle", "bamboo"), "jungle",
          rules="Clear the plot well first. A stone base, walls three high, wide eaves against the rain."),
    Theme("swamp_house", "Mangrove planks between mangrove log posts on a cobblestone base. For swamps.",
          ("swamp", "mangrove"), "mangrove",
          rules="Build on the driest ground or on footings: the base course matters most here."),
    Theme("cherry_house", "Pink cherry planks between cherry log posts on a cobblestone base. For cherry "
          "groves.",
          ("cherry",), "cherry",
          rules="The planks are the colour: keep everything else quiet, and let the roof match the walls."),
    Theme("keep", "Stone brick walls four high with spruce log posts and a stone brick roof. For mountains, "
          "stony shores and hills.",
          ("peaks", "stony", "windswept", "mountain", "hills", "slopes"), "stone_brick", {}, {"height": 4},
          rules="Stone below, wood only as trim. Stone bricks are smelted stone (cobblestone smelted, then "
          "four to a craft): plan the fuel. texture=mixed weathers the walls if you have cracked and mossy "
          "bricks; blend them in patches, never in stripes."),
)}

@dataclass(frozen=True)
class Card:
    """What a style looks like as a whole building, for someone designing one: its shape, its
    roof, the touches that make it, and what spoils it."""

    name: str
    #: Typical sizes of the main mass, as width x depth and wall height.
    footprint: str
    #: How the masses go together.
    massing: str
    #: Roof forms that belong to the style, the usual one first.
    roofs: tuple[str, ...]
    motifs: tuple[str, ...]
    avoid: tuple[str, ...]


CARDS: dict[str, Card] = {c.name: c for c in (
    Card("cottage", "7x7 to 9x7, walls 3 or 4", "A hall with one lower wing or a lean-to at the back or side.",
         ("gable", "hip"), ("porch at the door", "chimney on a gable end", "a window in every bay"),
         ("a flat roof", "walls over 4 high", "more than two wings")),
    Card("cabin", "9x7 to 11x7, walls 3 or 4", "One long low hall; at most a lean-to woodshed on a short side.",
         ("gable",), ("deep eaves", "a big chimney", "small single windows", "a porch on the long side"),
         ("wide windows", "a flat or hip roof", "tall thin shapes")),
    Card("birch_lodge", "9x7, walls 4", "A hall with a wing set back from the front.",
         ("gable",), ("grey stone roof over pale walls", "log posts between bays", "porch"),
         ("a birch roof on birch walls: it reads as a white box",)),
    Card("manor", "11x9 to 13x9, walls 7 with two floors", "A tall hall with two matching wings (mirror).",
         ("gable", "hip"), ("two rows of windows", "symmetry about the door", "chimneys on both ends"),
         ("one floor", "a lean roof on the main hall", "a door off centre")),
    Card("desert_house", "7x7 to 9x9, walls 4", "Square blocks of different heights pushed together.",
         ("flat",), ("a parapet round the roof", "few small windows", "an open doorway to a yard"),
         ("pitched roofs", "wide windows", "timber posts")),
    Card("savanna_hut", "7x7, walls 3", "A low hall with a lean-to.",
         ("gable", "lean"), ("a low wide roof", "an open porch"), ("tall walls", "stone walls")),
    Card("jungle_lodge", "9x7, walls 3 or 4", "A hall with a porch along the front.",
         ("hip", "gable"), ("wide eaves", "wide windows for the air"), ("a roof without overhang",)),
    Card("swamp_house", "7x7, walls 3", "One compact hall on a strong footing.",
         ("hip", "gable"), ("a porch", "a stone base course"), ("wings on soft ground",)),
    Card("cherry_house", "7x7 to 9x7, walls 3 or 4", "A hall with a small wing.",
         ("gable", "hip"), ("quiet trim, the planks are the colour", "a porch"), ("mixing in other bright woods",)),
    Card("keep", "9x9 hall with a 5x5 tower, walls 5 to 9", "A square hall with a taller tower on a corner.",
         ("flat", "hip"), ("a parapet", "buttresses on long walls", "narrow tall windows", "two floors in the tower"),
         ("wide windows", "a porch", "timber walls")),
)}

#: The theme that changes nothing: plain oak, as the generators draw by default.
DEFAULT = "cottage"


def names() -> list[str]:
    return list(THEMES)


def get(name: str) -> Theme:
    key = (name or "").strip().lower().replace(" ", "_")
    if key not in THEMES:
        raise ValueError(f"unknown theme {name!r}; themes: {', '.join(THEMES)}")
    return THEMES[key]


def for_biome(biome: Optional[str]) -> Optional[Theme]:
    """The theme that suits a biome, or None when the biome is unknown."""
    name = short(biome or "")
    if not name or name == "?":
        return None
    # The most telling word wins: birch_forest is birch before it is forest, so the default
    # theme's words, which are the broad ones, only count when nothing else matches.
    best, best_score = None, 0.0
    for theme in THEMES.values():
        for word in theme.biomes:
            score = 0.5 if theme.name == DEFAULT else float(len(word))
            if word in name and score > best_score:
                best, best_score = theme, score
    return best


_WOODS = ("oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "cherry", "mangrove")


def wood_of(item: str) -> Optional[str]:
    """The wood a log or plank item is made of, if a style is built from it."""
    name = short(item).removeprefix("stripped_")
    for suffix in ("_log", "_wood", "_planks"):
        if name.endswith(suffix) and name[: -len(suffix)] in _WOODS:
            return name[: -len(suffix)]
    return None


def for_wood(counts: dict[str, float]) -> Optional[Theme]:
    """The theme built from the wood there is most of, given how much of each wood is to hand
    (in logs), or None when there is too little of any to tell."""
    if not counts:
        return None
    wood, amount = max(counts.items(), key=lambda kv: kv[1])
    if amount < 3:
        return None
    return next((t for t in THEMES.values() if t.style == wood), None)


def apply(theme: Theme, kind: str, options: dict[str, Any]) -> dict[str, Any]:
    """The options for a structure with the theme's choices filled in under what was asked for."""
    out = dict(options)
    out.setdefault("style", theme.style)
    if out["style"] == theme.style:
        swaps = {**theme.materials, **(options.get("materials") or {})}
        if swaps:
            out["materials"] = swaps
        if kind == "house":
            for key, value in theme.options.items():
                out.setdefault(key, value)
    return out


def describe(theme: Theme) -> str:
    """One theme for the model: what it is, the call that builds it, and the rules behind it."""
    call = [f'theme="{theme.name}"']
    lines = [f"{theme.name}: {theme.summary}",
             f"Suits: {', '.join(theme.biomes)}.",
             f"Palette: style {theme.style}"
             + ("; " + ", ".join(f"{role} {short(item)}" for role, item in theme.materials.items())
                if theme.materials else "") + ".",
             ]
    if theme.options:
        lines.append("House options: " + ", ".join(f"{k}={v}" for k, v in theme.options.items()) + ".")
    lines.append(f"Rules: {theme.rules}")
    card = CARDS.get(theme.name)
    if card is not None:
        lines += [f"Shape: main mass {card.footprint}. {card.massing}",
                  f"Roof: {' or '.join(card.roofs)}.",
                  f"Touches that make it: {'; '.join(card.motifs)}.",
                  f"Avoid: {'; '.join(card.avoid)}.",
                  f"Designing your own in this style: design_guide(style=\"{theme.name}\") gives examples."]
    lines.append(f"Build it: build_structure(kind=\"house\", {', '.join(call)}, x, y, z), then "
                 f"build_structure(kind=\"yard\", {', '.join(call)}, same x y z, width and depth) for the path, "
                 "fence and lamp posts.")
    return "\n".join(lines)


#: What makes any build look finished, whatever the style. Kept short: it goes to the model as is.
PRINCIPLES = """\
What makes a build look good, in order of effect:
1. It fits the place. Use the wood and stone found around it (the theme for this biome does), and \
sit it on the ground: a stone base that follows the slope, no floating corners, no pit beside it.
2. Contrast in three tones: a base darker or rougher than the walls, walls of one main block with a \
trim block at the corners and every 3 or 4 blocks, and a roof darker than the walls.
3. A roof with a shape: pitched, overhanging the walls by a block, with a chimney or a dormer to \
break the ridge. Flat roofs only where the style asks for one (desert).
4. Depth: nothing longer than 4 blocks flat. Posts, window bays, a porch, eaves, a chimney.
5. A setting: a path to the door, a fence or hedge round the plot, light at the gate and the door, \
flowers or a field beside it. A house with a yard looks lived in; a house on bare grass looks dropped.
6. Tidy up: no leftover scaffolding, tree stumps, or holes from digging; fill and smooth the ground \
you disturbed."""
