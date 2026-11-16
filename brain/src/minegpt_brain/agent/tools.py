"""The tools the model calls: one per skill, plus tools to look, plan, remember and finish."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from ..bridge import BridgeError
from ..llm import Image, ToolSpec
from . import (blueprints, contracts, critic, design, designs, playbooks, requirements, state, structures, style,
               stylebook)
from .memory import KINDS
from .routines import describe_steps
from .taskgraph import ANY, Plan, PlanStep, parse_step

if TYPE_CHECKING:
    from .loop import Agent, Episode

SKILLS = ("goto", "mine", "collect", "craft", "smelt", "place", "equip", "eat", "fight", "build", "farm", "store",
          "take", "sleep")
_OTHER_TOOLS = {"look_around", "find_blocks", "recipe", "set_plan", "remember", "save_routine", "run_routine",
                "save_blueprint", "goal_done", "goal_failed", "list_structures", "plan_structure", "find_site",
                "build_structure", "plan_materials", "guide", "review_build", "expand_step", "run_plan", "recall",
                "styles", "inspect_build", "check_design", "design_guide"}
#: Tools among those that run skills in the game, so a failure skips the skill calls after them.
_ACTING_TOOLS = ("run_routine", "build_structure", "review_build", "run_plan")
#: Times run_plan goes back to mining or hunting when a step came up short.
_GATHER_TRIES = 3
#: Blocks that also occur in nature, so one standing where a plan wants it says nothing about a build.
_NATURAL = re.compile(r"_(log|wood|leaves)$|:(dirt|coarse_dirt|rooted_dirt|grass_block|podzol|mycelium|stone|"
                      r"deepslate|granite|diorite|andesite|tuff|sand|red_sand|sandstone|gravel|clay|snow_block|"
                      r"terracotta|mud|moss_block)$")
#: Times build_structure goes over one part again in a call, while each pass still places something.
_BUILD_PASSES = 3
#: Small flowers a yard is planted with, if the player carries any (picked while clearing, usually).
_FLOWERS = ("dandelion", "poppy", "blue_orchid", "allium", "azure_bluet", "red_tulip", "orange_tulip",
            "white_tulip", "pink_tulip", "oxeye_daisy", "cornflower", "lily_of_the_valley")
#: What a view of a build may pass through and still count as clear.
_SEE_THROUGH = ("air", "cave_air", "short_grass", "tall_grass", "fern", "large_fern", "torch", "wall_torch", "water",
                "glass", "glass_pane", "snow", "vine", "leaf_litter", "wildflowers", "pink_petals", "dead_bush",
                "bush", "firefly_bush", "short_dry_grass", "tall_dry_grass") + _FLOWERS
#: Looks at one build before the model is told to stop tinkering and finish.
MAX_REVIEWS = 2
#: Where review_build stands, as (name, x direction, z direction) from the middle of the build.
_VIEWS = (("the south-east corner", 1, 1), ("the north-west corner", -1, -1), ("the south, facing the front", 0, 1),
          ("the north-east corner", 1, -1), ("the south-west corner", -1, 1))
#: A build failure that means the items ran out, not that the spot could not be reached.
_OUT_OF_ITEMS = re.compile(r"\bno \S+ left\b|\bnone left\b|not enough")
#: Skills whose x y z only make sense in one place, so routines leave those calls out.
PLACE_BOUND = ("goto", "place", "build", "store", "take", "sleep")

_XYZ = {"x": {"type": "integer"}, "y": {"type": "integer"}, "z": {"type": "integer"}}
_ITEM = "An item id like minecraft:oak_planks (or just oak_planks), or a tag like #minecraft:planks."


def _tool(name: str, description: str, properties: Optional[dict[str, Any]] = None,
          required: tuple[str, ...] = ()) -> ToolSpec:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = list(required)
    return ToolSpec(name, description, schema)


def tool_specs(vision: bool = False) -> list[ToolSpec]:
    """The tools offered to the model. ``vision`` adds the ones that return pictures."""
    specs = _tool_specs()
    if vision:
        specs.insert(next(i for i, t in enumerate(specs) if t.name == "goal_done"), _tool(
            "review_build", "Walks round something you built and takes pictures of it from two or three sides, "
            "so you can see how it looks. Give the box it fills: the west, north, bottom corner x y z and its "
            "width (x), depth (z) and height. Use it once after finishing a build, judge the pictures (shape, "
            "depth, palette, roof, how it sits on the ground, light), fix what is worth fixing with place, "
            "mine or build, and move on. At most two looks per build.", {
                **_XYZ,
                "width": {"type": "integer", "description": "Size along x."},
                "depth": {"type": "integer", "description": "Size along z."},
                "height": {"type": "integer", "description": "How tall it is. Default 6."},
                "views": {"type": "integer", "description": "How many sides to look from, 1 to 4. Default 3."},
            }, ("x", "y", "z", "width", "depth")))
    return specs


def _tool_specs() -> list[ToolSpec]:
    sub_step = {
        "type": "object",
        "properties": {
            "step": {"type": "string", "description": "What to do, in a few words."},
            "done_when": critic.CONDITION_SCHEMA,
            "done": {"type": "boolean", "description": "Mark a step without a done_when as finished."},
            "substeps": {"type": "array", "items": {"type": "object"}, "description": "Smaller steps, same shape."},
            "any": {"type": "boolean"},
        },
        "required": ["step"],
    }
    plan_step = {
        "type": "object",
        "properties": {
            **{k: v for k, v in sub_step["properties"].items() if k not in ("substeps", "any")},
            "substeps": {"type": "array", "items": sub_step, "description": "Optional: the smaller steps "
                         "this step breaks into, done in order. A step with substeps is done when they all are, "
                         "or as soon as its own done_when holds."},
            "any": {"type": "boolean", "description": "true makes the substeps different ways to do the "
                    "step, tried in order until one works: put the cheapest first."},
        },
        "required": ["step"],
    }
    drawing = {
        "layers": {"type": "array", "description": "Layers from the bottom up. Each layer is a list of rows "
                   "from north (-z) to south (+z); each row a string of characters from west (-x) to east (+x). "
                   "'.' or a space leaves that spot as it is.",
                   "items": {"type": "array", "items": {"type": "string"}}},
        "key": {"type": "object", "additionalProperties": {"type": "string"},
                "description": "Each character to a block item id, like {\"c\": \"cobblestone\"}."},
    }
    routine_step = {
        "type": "object",
        "properties": {"tool": {"type": "string", "enum": list(SKILLS)}, "args": {"type": "object"}},
        "required": ["tool"],
    }
    return [
        # Looking -------------------------------------------------------------------------
        _tool("look_around", "Describes the player's state, inventory, mobs nearby, and notable blocks in sight "
              "within 24 blocks (trees, ores, stone, crafting tables, furnaces, chests, water, lava), plus what "
              "you remember near here. Takes no game time."),
        _tool("find_blocks", "Lists blocks of a kind that a player could see from nearby, nearest first, with "
              "positions. Takes no game time.", {
                  "block": {"description": "Block id or #tag, or a list of them.",
                            "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                  "radius": {"type": "integer", "description": "Search radius, 1 to 64. Default 32."},
                  "limit": {"type": "integer", "description": "Most results to list, 1 to 64. Default 10."},
              }, ("block",)),
        _tool("recipe", "Shows the unlocked crafting recipes that make an item: ingredients, whether a "
              "crafting table is needed, and whether you can craft it now. Takes no game time.",
              {"item": {"type": "string", "description": _ITEM}}, ("item",)),
        # Skills ----------------------------------------------------------------------------
        _tool("goto", "Walks to a position with pathfinding: jumps, drops, climbs ladders, vines and scaffolding, "
              "opens wooden doors and gates (and shuts them behind it), hops gaps one or two blocks wide, drops "
              "into water from any height, keeps out of deep and flowing water, and swims at the surface only "
              "when there is no way round. It digs through natural blocks only when there is no walking route, "
              "and pillars up or bridges with spare dirt or cobblestone. Give x y z; or x and z to reach that "
              "column at any height; or only y to get to that height from where you are: down by digging a safe "
              "shaft or stairs (with allow_break true), up by climbing, digging stairs or pillaring.", {
                  **_XYZ,
                  "radius": {"type": "number", "description": "How close is close enough. Default 1."},
                  "allow_break": {"type": "boolean", "description": "true digs through natural blocks (stone, "
                                  "dirt, sand, ores, leaves) wherever that is quicker; false never digs. Leave it "
                                  "out to dig only when there is no other way."},
                  "allow_place": {"type": "boolean", "description": "Pillar and bridge with spare blocks. "
                                  "Default true."},
                  "sprint": {"type": "boolean", "description": "Sprint on long straight stretches, which uses "
                             "a little more food. Default true."},
                  "parkour": {"type": "boolean", "description": "Jump gaps where a miss is harmless. Default true."},
              }),
        _tool("mine", "Mines blocks of a kind that a player could see, walking to each and picking up the drops, "
              "then sweeps up any drops left nearby. Needs a tool that can harvest them (it equips the best one). "
              "Blocks it could not reach are skipped for a few minutes, also by later calls. If none are in "
              "range it tunnels 24 blocks in the direction the player faces, up to explore times, exposing new "
              "blocks.", {
                  "block": {"description": "Block id or #tag, or a list, e.g. #minecraft:logs or "
                            "[\"minecraft:iron_ore\", \"minecraft:deepslate_iron_ore\"].",
                            "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                  "count": {"type": "integer", "description": "How many blocks. Default 1."},
                  "radius": {"type": "integer", "description": "Search radius. Default 32."},
                  "explore": {"type": "integer", "description": "Tunnels to dig when none are in range. "
                              "Default 6; 0 to never tunnel."},
                  "tunnel": {"type": "boolean", "description": "Dig a straight tunnel two blocks high from where "
                             "the player stands and faces, counting the blocks that match: the quickest way to "
                             "a lot of stone or deepslate. Use it underground or facing a hillside. Default false."},
              }, ("block",)),
        _tool("collect", "Picks up dropped items nearby (not in deep water or lava) and says what it picked up. "
              "Only needed for drops that mine and fight left behind.", {
            "item": {"type": "string", "description": "Only this item. Default any."},
            "radius": {"type": "number", "description": "Default 16."},
        }),
        _tool("craft", "Crafts at least count more of an item with the recipe book. Uses a crafting table within 24 "
              "blocks for 3x3 recipes, placing one from the inventory or crafting one if there is none.", {
                  "item": {"type": "string", "description": _ITEM},
                  "count": {"type": "integer", "description": "How many more to make. Default 1."},
              }, ("item",)),
        _tool("smelt", "Smelts items in a furnace within 24 blocks, placing or crafting one if needed, and "
              "takes the results, including any left from an earlier smelt. Fuel is picked automatically from "
              "what you carry and topped up if it runs out.", {
                  "item": {"type": "string", "description": "The input, e.g. minecraft:raw_iron."},
                  "count": {"type": "integer", "description": "How many to smelt. Default 1."},
                  "fuel": {"type": "string", "description": "A fuel item to use instead of the automatic pick."},
              }, ("item",)),
        _tool("place", "Places a block from the inventory at x y z, or on a free spot next to the player when no "
              "position is given.", {"item": {"type": "string"}, **_XYZ}, ("item",)),
        _tool("equip", "Holds an item, the best tool of a kind, or puts on the best armour carried. Give one of "
              "item, tool or armor.", {
                  "item": {"type": "string"},
                  "tool": {"type": "string", "enum": ["pickaxe", "axe", "shovel", "hoe", "sword"]},
                  "armor": {"type": "boolean", "description": "true puts on the best armour carried."},
              }),
        _tool("eat", "Eats the most filling safe food carried, or the given item.", {"item": {"type": "string"}}),
        _tool("fight", "Fights hostile mobs nearby, or only the given entity (which can be an animal to hunt, "
              "like minecraft:cow, whose drops it then picks up). Stops when none are left or health drops to "
              "min_health.", {
                  "entity": {"type": "string", "description": "Entity id, e.g. minecraft:zombie or minecraft:pig."},
                  "radius": {"type": "number", "description": "Default 16."},
                  "min_health": {"type": "number", "description": "Stop at this health. Default 6."},
              }),
        _tool("build", "Builds a structure with blocks from the inventory, bottom layer first, walking to "
              "each spot. Give the origin x y z (where the west, north, bottom corner goes) and one of: "
              "blueprint (a saved one), layers with key (a drawing), or blocks ([dx, dy, dz, item] from the "
              "origin). Blocks already in place are skipped; natural terrain in the way is dug out with clear. "
              "Blocks face the way the player happens to face; leave a doorway so the player is not walled in. "
              "A drawing comes back with design notes when it would look flat or boxy; they are advice. "
              "Fails at once, listing what is missing, if the inventory does not hold enough blocks, unless "
              "partial is true.", {
                  **_XYZ,
                  "blueprint": {"type": "string", "description": "Name of a saved blueprint."},
                  **drawing,
                  "blocks": {"type": "array", "items": {"type": "array"},
                             "description": "[dx, dy, dz, item] entries, relative to the origin."},
                  "clear": {"type": "boolean", "description": "Dig natural blocks (dirt, grass, stone) out of "
                            "the way first. Default false."},
                  "partial": {"type": "boolean", "description": "Build what the blocks carried allow. Default "
                              "false."},
                  "foundation": {"type": "string", "description": "A block (e.g. cobblestone) to carry the "
                                 "bottom layer down to the ground with where the ground falls away under it, "
                                 "up to 5 deep, so the build stands on a footing instead of hanging over a "
                                 "slope. Carry extra of it."},
              }, ("x", "y", "z")),
        _tool("farm", "Tends crops within radius: harvests ripe wheat, carrots, potatoes and beetroot, picks up "
              "the drops and replants, and plants seeds on empty farmland. With till it also hoes dirt and grass "
              "near water into farmland (needs a hoe) and plants it. Unripe crops are left alone.", {
                  "radius": {"type": "integer", "description": "Default 16."},
                  "replant": {"type": "boolean", "description": "Plant again after harvesting. Default true."},
                  "plant": {"type": "boolean", "description": "Plant empty farmland. Default true."},
                  "till": {"type": "boolean", "description": "Make new farmland with a hoe. Default false."},
                  "max": {"type": "integer", "description": "Most crops to work on. Default 128."},
              }),
        _tool("store", "Puts items into the nearest chest or barrel (or the one at x y z). With no items it "
              "stores everything except tools, weapons, armour, food, torches, a crafting table, a furnace and a "
              "stack of scaffold blocks. Says what the chest holds afterwards, and remembers it.", {
                  "items": {"type": "array", "items": {"type": "string"}, "description": "Item ids or #tags."},
                  "keep": {"type": "array", "items": {"type": "string"}, "description": "Never store these."},
                  **_XYZ,
              }),
        _tool("take", "Takes an item out of the nearest chest or barrel (or the one at x y z).", {
            "item": {"type": "string", "description": _ITEM},
            "count": {"type": "integer", "description": "How many at most. Default 64."},
            **_XYZ,
        }, ("item",)),
        _tool("sleep", "Sleeps in the nearest bed within 32 blocks (or the one at x y z) until morning, which "
              "skips the night in singleplayer and sets the respawn point. Only at night or in a thunderstorm, "
              "and only in the overworld.", {**_XYZ}),
        # Planning and memory ----------------------------------------------------------------
        _tool("set_plan", "Sets or replaces the plan: steps in order, and goal_check, the condition that is true "
              "when the goal is achieved. The game checks done_when conditions itself and reports progress after "
              "every turn. A big step can carry substeps, and with any=true the substeps are alternatives. The "
              "plan is saved, so an interrupted goal carries on from it. To change one step only, use "
              "expand_step instead of setting the whole plan again.", {
                  "steps": {"type": "array", "items": plan_step},
                  "goal_check": critic.CONDITION_SCHEMA,
              }, ("steps",)),
        _tool("expand_step", "Breaks one step of the plan into substeps, or replaces its substeps with a new "
              "breakdown, leaving the rest of the plan alone. Use it when a step turns out bigger than "
              "expected, or is marked [!] because it failed: give another way to do it. Steps are numbered as "
              "the plan shows them, like 2 or 2.1.", {
                  "step": {"type": "string", "description": "The step's number, like 2 or 2.1."},
                  "substeps": {"type": "array", "items": sub_step},
                  "any": {"type": "boolean", "description": "true: the substeps are alternatives, tried in "
                          "order. Default false: all of them, in order."},
                  "done_when": critic.CONDITION_SCHEMA,
              }, ("step", "substeps")),
        _tool("remember", "Saves a note about this world for later goals, at the player's position unless x y z "
              "are given.", {
                  "kind": {"type": "string", "enum": [k for k in KINDS if k != "death"]},
                  "note": {"type": "string"},
                  **_XYZ,
              }, ("kind", "note")),
        _tool("save_routine", "Saves a sequence of skill calls that worked, to replay with run_routine on later "
              "goals. Leave out positions that only make sense in one place.", {
                  "name": {"type": "string", "description": "Short and descriptive, e.g. make a stone pickaxe."},
                  "description": {"type": "string", "description": "What it achieves and what it needs first."},
                  "steps": {"type": "array", "items": routine_step},
              }, ("name", "description", "steps")),
        _tool("run_routine", "Replays a saved routine, stopping at the first skill that fails. It is checked "
              "on paper first against what you carry, and refused with the reason if a step could not work.", {
                  "name": {"type": "string"},
                  "force": {"type": "boolean", "description": "Run it even if the check on paper says a step "
                            "will fail. Default false."},
              }, ("name",)),
        _tool("save_blueprint", "Saves a drawing of a structure under a name, to build again with "
              "build(blueprint=...) on this or later goals. Checks the drawing and lists the blocks it needs.", {
                  "name": {"type": "string", "description": "Short, e.g. small cobblestone hut."},
                  "description": {"type": "string", "description": "What it is and anything to know before "
                                  "building it."},
                  **drawing,
              }, ("name", "description", "layers", "key")),
        # Structures and big jobs -----------------------------------------------------------
        _tool("list_structures", "Lists the ready-made structures build_structure can put up (shelter, house, "
              "yard, storage shed, tower, wall, pen, farm plot, bridge, stairway, platform, and a whole walled "
              "base), their options, default sizes and what they cost, and the styles. Takes no game time."),
        _tool("styles", "The style book: which materials and shapes suit which place, and what makes a build "
              "look good. With no name it says which theme suits where you stand and lists the others; with a "
              "name it gives that theme's palette, rules and the calls that build it. Takes no game time. Read "
              "it before planning a house or drawing a build of your own.",
              {"name": {"type": "string", "enum": stylebook.names(), "description": "A theme to read in full."}}),
        _tool("plan_structure", "Works out a ready-made structure without building it: its size and parts, every "
              "item it takes, what you still lack, and the full list of what to gather, craft and smelt for it. "
              "Takes no game time. Call it before gathering.", _structure_properties(), ("kind",)),
        _tool("find_site", "Finds flat, open ground near the player for a footprint of width (x) by depth (z), "
              "best first, with the x y z to give build_structure and how much digging or filling each needs. "
              "Sites on water, lava, other builds or protected areas are left out. Takes no game time.", {
                  "width": {"type": "integer"}, "depth": {"type": "integer"},
                  "radius": {"type": "integer", "description": "How far to look, up to 40. Default 32."},
              }, ("width", "depth")),
        _tool("build_structure", "Builds a ready-made structure at x y z: the west (-x), north (-z) corner of its "
              "footprint, at the level you walk on (find_site gives this). It first clears the site (fells "
              "trees standing on it, picks plants, digs the ground level), then places every block the right "
              "way round (stairs, doors, chests, beds, ladders), and takes down its own scaffolding. Refused, "
              "listing what is missing, if you do not carry the items, unless partial is true. Blocks already "
              "standing are skipped, so call it again to carry on after fetching more; blocks that cannot be "
              "placed at all are left out and listed, and the build carries on without them. Give part to "
              "build one part of a big structure (a base's parts: house, storage, tower, farm, pen, wall). "
              "After a house, kind yard with the same x y z, width and depth adds the path, fence and lamp "
              "posts round it.", {
                  **_structure_properties(), **_XYZ,
                  "partial": {"type": "boolean", "description": "Build what the items carried allow. Default "
                              "false."},
              }, ("kind", "x", "y", "z")),
        _tool("inspect_build", "Compares a ready-made structure with what actually stands at x y z (give the "
              "same arguments as for build_structure) and lists every difference with its position: blocks "
              "missing, wrong blocks, blocks turned the wrong way (stairs, logs, doors), things left where it "
              "should be empty, and stray scaffolding. Takes no game time unless repair is true, which makes "
              "one pass putting right what MineGPT placed itself. build_structure does this check on its "
              "own when it ends; use this to look again after fixing things by hand.", {
                  **_structure_properties(), **_XYZ,
                  "repair": {"type": "boolean", "description": "Also put right what can be. Default false."},
              }, ("kind", "x", "y", "z")),
        _tool("design_guide", "How to design a building of your own instead of taking a ready-made one: the "
              "design language (masses joined to each other, a roof on each, facades of windows and doors, "
              "details), the parts there are with what each needs and costs, the style card for a theme, and "
              "two or three worked examples near the size and style you give, to start from. Takes no game "
              "time. Read it before writing a design.", {
                  "width": {"type": "integer", "description": "About how wide (x) the plot is."},
                  "depth": {"type": "integer", "description": "About how deep (z) the plot is."},
                  "style": {"type": "string", "description": "A theme or style, e.g. cabin or birch_lodge."},
              }),
        _tool("check_design", "Checks a design written in the design language without building it. A design "
              "that cannot be built is refused with the line and the reason, so you can put it right. One "
              "that can comes back with its size, its parts, a floor plan drawn in text, every item it takes "
              "and what you still lack, and a review: what to fix and what to consider so it looks good. "
              "Takes no game time. Fix what the review lists, check again, then build it with "
              "build_structure(kind=\"design\", program=..., x, y, z).", {
                  "program": {"type": "string", "description": "The design, one statement per line."},
                  "facing": {"type": "string", "enum": list(structures.FACINGS)},
                  "style": {"type": "string", "enum": list(structures.PALETTES)},
                  "theme": {"type": "string", "enum": stylebook.names()},
                  "texture": {"type": "string", "enum": list(structures.TEXTURES)},
                  "materials": {"type": "object", "additionalProperties": {"type": "string"}},
              }, ("program",)),
        _tool("plan_materials", "Works out everything a set of items takes, using what you carry first: what to "
              "gather (and how, and with which tool), then what to craft and smelt, in order, the fuel, and "
              "about how long it takes. Knows every recipe and drop in the game. Takes no game time. Use it "
              "to see the size of a job; use run_plan to have it done.", {
                  "items": {"type": "object", "additionalProperties": {"type": "integer", "minimum": 1},
                            "description": "Item id to the count you want to end up with, like "
                            "{\"iron_pickaxe\": 1, \"torch\": 16}."},
              }, ("items",)),
        _tool("run_plan", "Gets items the whole way: works out what they take from what you carry (as "
              "plan_materials does) and then does it, step after step, mining, hunting, crafting and smelting, "
              "making the tools, crafting table and furnace needed on the way. Each step is checked by the "
              "game. It stops at the first step that fails and says what is left, so you can deal with that "
              "(go somewhere with ore, dig down) and call it again: it starts from what you carry by then. "
              "This is the way to make anything from a pickaxe to a set of armour; plan only what it cannot "
              "do (where to look, building, farming).", {
                  "items": {"type": "object", "additionalProperties": {"type": "integer", "minimum": 1},
                            "description": "Item id to the count you want to end up with, like "
                            "{\"iron_pickaxe\": 1} or {\"iron_helmet\": 1, \"iron_boots\": 1}."},
                  "step": {"type": "string", "description": "Optional: the number of the plan step this is "
                           "for, like 2. Its progress then shows under that step."},
              }, ("items",)),
        _tool("recall", "Says where blocks of a kind were seen before in this world, nearest first, from the "
              "map the agent keeps as it looks around (ore, trees, water, lava, chests, beds). Takes no game "
              "time. Use it before searching for something you may have passed.", {
                  "block": {"type": "string", "description": "A block id, with * as a wildcard, like iron_ore, "
                            "*_ore or *_log."},
              }, ("block",)),
        _tool("guide", "Reads a guide on how to break down a kind of big or loosely worded goal into phases "
              "the game can check. Topics: " + ", ".join(playbooks.names()) + ". Takes no game time.",
              {"topic": {"type": "string"}}, ("topic",)),
        # Ending -----------------------------------------------------------------------------
        _tool("goal_done", "Reports the goal as achieved. Refused if the plan's goal_check is not met yet.",
              {"summary": {"type": "string", "description": "One or two sentences on what was done."}},
              ("summary",)),
        _tool("goal_failed", "Gives up on the goal, when it cannot be done after real attempts.",
              {"reason": {"type": "string"}}, ("reason",)),
    ]


def _structure_properties() -> dict[str, Any]:
    return {
        "kind": {"type": "string", "enum": list(structures.KINDS)},
        "facing": {"type": "string", "enum": list(structures.FACINGS),
                   "description": "Which way the front (the door) faces. Default south (+z)."},
        "style": {"type": "string", "enum": list(structures.PALETTES),
                  "description": "The set of materials. Leave it out for houses, sheds, pens and yards and the "
                  "theme that suits the biome and the wood at hand is used (plan_structure says which); "
                  "towers and walls default to cobblestone, a shelter to dirt."},
        "theme": {"type": "string", "enum": stylebook.names(),
                  "description": "A theme from the style book instead of a style: its palette and house shape. "
                  "See styles."},
        "program": {"type": "string", "description": "Kind design only: the building written in the design "
                    "language, one statement per line (design_guide explains it and gives examples). Give "
                    "the same program to every call for one build."},
        "width": {"type": "integer", "description": "Size along x before turning."},
        "depth": {"type": "integer", "description": "Size along z before turning."},
        "height": {"type": "integer", "description": "Wall height (house, wall), or tower and stairway height."},
        "size": {"type": "integer", "description": "Tower: side length."},
        "length": {"type": "integer", "description": "Bridge: length."},
        "roof": {"type": "string", "enum": ["gable", "flat"], "description": "House: roof shape. Default gable."},
        "floor": {"type": "boolean", "description": "House: lay a floor. Default true."},
        "windows": {"type": "boolean", "description": "House: glass windows. Default true."},
        "furnish": {"type": "array", "items": {"type": "string"},
                    "description": "House: items to put inside. Default bed, crafting_table, furnace, chest; "
                    "[] for none."},
        "detail": {"type": "boolean", "description": "House, storage: framed walls with a post every few "
                   "blocks, a window per bay, an overhanging roof and torches by the door. Default true; false "
                   "gives a plain box that costs fewer logs and stairs."},
        "trim": {"type": "boolean", "description": "House: a chimney up one end. Default true with detail."},
        "margin": {"type": "integer", "description": "Yard: how far it reaches beyond the house, 2 to 8. "
                   "Default 3."},
        "fence": {"type": "boolean", "description": "Yard: fence the plot, with a gate. Default true."},
        "texture": {"type": "string", "enum": list(structures.TEXTURES), "description": "mixed blends stone "
                    "walls and footings with similar blocks in patches (cobblestone with andesite and stone; "
                    "stone bricks with cracked and mossy ones), which needs those blocks too. Default plain."},
        "wall_style": {"type": "string", "enum": list(structures.PALETTES), "description": "Base: style of the "
                       "outer wall and tower. Default cobblestone."},
        "materials": {"type": "object", "additionalProperties": {"type": "string"},
                      "description": "Swap single materials of the style, by role: wall, post, floor, "
                      "foundation, roof (stairs), roof_fill, ridge (slab), door, fence, gate, window, light, "
                      "ladder, path, chimney. E.g. {\"wall\": \"birch_planks\"}."},
        "part": {"type": "string", "description": "Only this part of the structure, e.g. walls, roof, inside; "
                 "for a base: house, storage, tower, farm, pen, wall."},
    }


_STRUCTURE_OPTIONS = ("program", "width", "depth", "height", "size", "length", "style", "roof", "floor", "windows", "furnish",
                      "wall_style", "materials", "detail", "texture", "theme", "trim", "margin", "fence")
#: Kinds that are made of the local wood, so a theme is picked for them when no style is asked for.
_THEMED_KINDS = ("house", "storage", "pen", "farm_plot", "bridge", "yard", "base", "design")


@dataclass
class Outcome:
    """What a tool call returned to the model, and what it means for the loop."""

    content: str
    is_error: bool = False
    #: A skill failed, so later skill calls in the same turn are skipped.
    skill_failed: bool = False
    #: ``success`` or ``failed`` when the call ends the goal.
    end: Optional[str] = None
    summary: str = ""
    verified: bool = False
    #: Pictures for the model, for backends that can show them.
    images: list[Image] = field(default_factory=list)


class Toolbox:
    def __init__(self, agent: "Agent", episode: "Episode"):
        self.agent = agent
        self.episode = episode
        #: Advice on the drawing the build tool was last given, shown with its result.
        self._design_notes: list[str] = []
        #: Which theme the last structure was given without being asked, for the result.
        self._theme_note = ""

    def call(self, name: str, args: dict[str, Any]) -> Outcome:
        if not isinstance(args, dict):
            return Outcome(f"arguments for {name} must be an object", is_error=True)
        if name in SKILLS:
            try:
                return self._skill(name, args)
            except BridgeError as e:
                return Outcome(f"{name}: {e}", is_error=True, skill_failed=True)
        if name not in _OTHER_TOOLS:
            return Outcome(f"unknown tool {name!r}", is_error=True)
        try:
            return getattr(self, f"_{name}")(**args)
        except TypeError as e:
            return Outcome(f"bad arguments for {name}: {e}", is_error=True)
        except (ValueError, BridgeError) as e:
            return Outcome(f"{name}: {e}", is_error=True)

    # Skills -----------------------------------------------------------------------------

    def _skill(self, name: str, args: dict[str, Any]) -> Outcome:
        try:
            mod_args = self._skill_args(name, args)
        except ValueError as e:
            return Outcome(f"{name}: {e}", is_error=True, skill_failed=True)
        if name == "goto":
            # A trip that has just failed for want of a way is not worth setting off on again.
            why = self.episode.unreachable.blocked(args, state.block_pos(self.agent.observe()))
            if why:
                return Outcome("not run: " + why, is_error=True, skill_failed=True)
        run = self.agent.run_skill(name, mod_args, self.episode)
        result = run.result
        if name == "goto":
            if result.success:
                self.episode.unreachable.reached(args)
            elif run.after is not None:
                self.episode.unreachable.note(args, result.message, state.block_pos(run.after))
        lines = [f"{'ok' if result.success else 'failed'}: {result.message}"]
        if result.data:
            lines.append("Data: " + _short_json(result.data))
        lines.append("Inventory: " + state.delta_line(run.changes))
        if run.death:
            lines.append(run.death)
        if not result.success:
            hint = critic.hint(result.message)
            if hint:
                lines.append("Hint: " + hint)
            times = self.episode.failures.record(name, args, result.message)
            first = f"{name}: {result.message}".splitlines()[0][:140]
            if first not in self.episode.problems:
                self.episode.problems.append(first)
            if times >= 2:
                lines.append(f"This exact call has now failed {times} times the same way: do something different.")
                self._mark_failed(result.message, lines)
        else:
            self.episode.successful_calls.append({"tool": name, "args": args})
        if name == "build" and self._design_notes:
            lines.append("Design notes (advice, for the next part or a rebuild):")
            lines += [f"- {note}" for note in self._design_notes]
            self._design_notes = []
        if name in ("store", "take") and result.data:
            self._remember_container(name, result.data)
        return Outcome("\n".join(lines), is_error=not result.success, skill_failed=not result.success)

    def _mark_failed(self, message: str, lines: list[str]) -> None:
        """Counts a repeated failure against the plan step being worked on, and says so when it is given up."""
        current = self.episode.plan.current()
        if current is None:
            return
        _, leaf = current
        leaf.fail(message)
        stuck = self.episode.plan.stuck()
        if leaf.failed and stuck is not None:
            lines.append(f"Plan step {stuck[0]} is now marked [!]: it cannot go on as planned. Give it another "
                         "way with expand_step (only that step changes), or move on to an alternative.")

    def _skill_args(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "build":
            return self._build_args(args)
        renames = {"allow_break": "allowBreak", "allow_place": "allowPlace", "min_health": "minHealth"}
        allowed = {
            "goto": {"x", "y", "z", "radius", "allow_break", "allow_place", "sprint"},
            "mine": {"block", "count", "radius", "explore", "tunnel"},
            "collect": {"item", "radius"},
            "craft": {"item", "count"},
            "smelt": {"item", "count", "fuel"},
            "place": {"item", "x", "y", "z"},
            "equip": {"item", "tool", "armor"},
            "eat": {"item"},
            "fight": {"entity", "radius", "min_health"},
            "farm": {"radius", "replant", "plant", "till", "max"},
            "store": {"items", "keep", "x", "y", "z"},
            "take": {"item", "count", "x", "y", "z"},
            "sleep": {"x", "y", "z"},
        }[name]
        unknown = set(args) - allowed
        if unknown:
            raise ValueError(f"unknown arguments {', '.join(sorted(unknown))}; it takes {', '.join(sorted(allowed))}")
        mod_args = {renames.get(k, k): v for k, v in args.items() if v is not None}
        if name == "mine" and self.agent.config.xray:
            mod_args["hidden"] = True
        return mod_args

    def _build_args(self, args: dict[str, Any]) -> dict[str, Any]:
        allowed = {"x", "y", "z", "blueprint", "layers", "key", "blocks", "clear", "partial", "foundation"}
        unknown = set(args) - allowed
        if unknown:
            raise ValueError(f"unknown arguments {', '.join(sorted(unknown))}; it takes {', '.join(sorted(allowed))}")
        if not all(isinstance(args.get(k), int) for k in "xyz"):
            raise ValueError("give the origin as whole numbers x, y and z")
        sources = [k for k in ("blueprint", "layers", "blocks") if args.get(k) is not None]
        if len(sources) != 1:
            raise ValueError("give exactly one of blueprint, layers (with key) or blocks")
        if sources[0] == "blueprint":
            relative = self.agent.blueprints.blocks(args["blueprint"])
        elif sources[0] == "layers":
            relative = blueprints.expand(args["layers"], args.get("key"))
        else:
            relative = blueprints.from_relative(args["blocks"])
        mod_args: dict[str, Any] = {"blocks": blueprints.place_at(relative, args["x"], args["y"], args["z"])}
        for flag in ("clear", "partial"):
            if args.get(flag) is not None:
                mod_args[flag] = bool(args[flag])
        if args.get("foundation"):
            if not isinstance(args["foundation"], str) or args["foundation"].startswith("#"):
                raise ValueError("foundation names one block item, like cobblestone")
            mod_args["foundation"] = state.full(args["foundation"])
        # Kept for the result: what would make the drawing look better, if anything.
        self._design_notes = style.review(relative) if sources[0] != "blueprint" else []
        return mod_args

    def _remember_container(self, name: str, data: dict[str, Any]) -> None:
        """Notes what a chest holds after storing or taking, so later goals know where things are."""
        if not all(k in data for k in "xyz"):
            return
        contents = data.get("contents") or {}
        held = ", ".join(f"{state.short(i)} {n}" for i, n in sorted(contents.items(), key=lambda kv: -kv[1])[:12])
        obs = self.agent.observe()
        self.episode.memory.add("chest", f"holds {held or 'nothing'}", (data["x"], data["y"], data["z"]),
                                obs.data.get("dimension"))

    # Looking ----------------------------------------------------------------------------

    def _look_around(self) -> Outcome:
        obs = self.agent.observe()
        lines = [state.status_line(obs), state.inventory_line(obs)]
        ground = state.ground_line(obs)
        if ground:
            lines.append(ground)
        lines += state.nearby_lines(self.agent.scan(self.episode.world_map, obs))
        lines.append("Remembered nearby:\n" + self.episode.memory.describe(obs.pos, obs.data.get("dimension"), 8))
        return Outcome("\n".join(lines))

    def _find_blocks(self, block: str | list[str], radius: int = 32, limit: int = 10) -> Outcome:
        specs = [state.full(b) for b in (block if isinstance(block, list) else [block])]
        found = self.agent.bridge.find(specs, radius=radius, limit=limit)
        self._map_search(specs, found, radius)
        if not found:
            text = f"no visible {', '.join(state.short(s) for s in specs)} within {radius} blocks"
            known = self._known_places(specs)
            if known:
                text += "\nSeen before (recall lists more): " + "; ".join(known)
            return Outcome(text)
        return Outcome("\n".join(f"{state.short(b['block'])} at {b['pos'][0]} {b['pos'][1]} {b['pos'][2]} "
                                 f"({b['distance']:.0f}m)" for b in found))

    def _map_search(self, specs: list[str], found: list[dict[str, Any]], radius: float) -> None:
        """Files a block search on the world map: what it found, or that there is nothing here any more."""
        world_map = self.episode.world_map
        if world_map is None:
            return
        obs = self.agent.observe()
        dimension = obs.data.get("dimension")
        if found:
            world_map.note_found(found, dimension)
        else:
            world_map.note_absent(self._block_names(specs), obs.pos, radius, dimension)

    def _block_names(self, specs: list[str]) -> list[str]:
        """Block ids and #tags as plain block ids, using the game data for tags where it is loaded."""
        names: list[str] = []
        knowledge = self.agent.knowledge
        for spec in specs:
            name = state.short(spec)
            if name.startswith("#"):
                tag = name[1:].split(":")[-1]
                names += knowledge.block_tags.get(tag, []) if knowledge is not None else []
            else:
                names.append(name)
        return names

    def _known_places(self, specs: list[str], limit: int = 3) -> list[str]:
        world_map = self.episode.world_map
        names = self._block_names(specs)
        if world_map is None or not names:
            return []
        obs = self.agent.observe()
        return [f"{e['block']} at {e['pos'][0]} {e['pos'][1]} {e['pos'][2]} ({e['distance']:.0f} blocks away)"
                for e in world_map.nearest(names, obs.pos, obs.data.get("dimension"), limit=limit)]

    def _recall(self, block: str) -> Outcome:
        world_map = self.episode.world_map
        if world_map is None:
            return Outcome("No map is kept in this run.")
        obs = self.agent.observe()
        names = self._block_names([block]) or [state.short(block)]
        found = world_map.nearest(names, obs.pos, obs.data.get("dimension"), limit=8)
        if not found:
            return Outcome(f"No {state.short(block)} on the map yet: it only holds what look_around and "
                           "find_blocks have seen. Search with find_blocks, or explore.")
        return Outcome("\n".join(f"{e['block']} at {e['pos'][0]} {e['pos'][1]} {e['pos'][2]} "
                                 f"({e['distance']:.0f} blocks away, {e['n']} seen there)" for e in found)
                       + "\nThese are places seen earlier; a spot may have been mined out since.")

    def _recipe(self, item: str) -> Outcome:
        recipes = self.agent.bridge.recipes(state.full(item))
        if not recipes:
            return Outcome(f"No unlocked recipe makes {state.short(item)}. Either it is not crafted (mine, smelt "
                           "or hunt for it), or its recipe unlocks once you pick up one of its ingredients.")
        lines = []
        for r in recipes[:5]:
            needs: dict[str, int] = {}
            for options in r.get("ingredients", []):
                if options:
                    label = state.short(options[0]) + (" (or similar)" if len(options) > 1 else "")
                    needs[label] = needs.get(label, 0) + 1
            parts = ", ".join(f"{n} {label}" for label, n in needs.items())
            where = "needs a crafting table" if r.get("needsTable") else "no table needed"
            now = "you can craft it now" if r.get("craftable") else "missing ingredients"
            lines.append(f"{r['count']} {state.short(r['result'])} from {parts}; {where}; {now}")
        return Outcome("\n".join(lines))

    # Planning and memory ----------------------------------------------------------------

    def _set_plan(self, steps: list[dict[str, Any]], goal_check: Optional[dict[str, Any]] = None) -> Outcome:
        if not isinstance(steps, list) or not steps:
            raise ValueError("give at least one step")
        goal_check = critic.validate(goal_check)
        kept: list[str] = []
        old = self.episode.plan.goal_check if self.episode.plan.steps else None
        if old and old.get("built"):
            # A goal check may change, but not shrink to less of the same thing: dropping the parts
            # that would not go up is how an unfinished build gets reported as done.
            new = list((goal_check or {}).get("built", []))
            unbuilt = [b for b in old["built"] if b not in self.episode.built]
            if not new or set(new) < set(old["built"]):
                kept = [b for b in unbuilt if b not in new]
                if kept:
                    goal_check = {**(goal_check or {}), "built": new + kept}
        plan = Plan(goal_check=goal_check)
        for i, raw in enumerate(steps, 1):
            plan.steps.append(parse_step(raw, str(i)))
        obs = self.agent.observe()
        plan.update(obs, self.agent.world(self.episode))
        self.episode.plan = plan
        self.episode.log.write("plan", steps=steps, goal_check=goal_check)
        text = "Plan set: " + plan.describe()
        if plan.goal_check is None:
            text += ("\nNo goal_check, so goal_done cannot be verified by the game. Add one if the goal can be "
                     "checked with have, wearing, near, built, sees or height.")
        else:
            missing = critic.unmet(plan.goal_check, obs, self.agent.world(self.episode))
            text += "\nGoal check: " + ("already met" if not missing else "; ".join(missing))
            if kept:
                text += ("\nKept in the goal check from before: built " + ", ".join(kept) + ". They are not built "
                         "yet, and a goal check cannot be cut down to what happened to work. Finish them, or if "
                         "they truly cannot be done, end with goal_failed and say what stands and what does not.")
        return Outcome(text)

    def _expand_step(self, step: str, substeps: list[dict[str, Any]], any: bool = False,  # noqa: A002
                     done_when: Optional[dict[str, Any]] = None) -> Outcome:
        plan = self.episode.plan
        if not plan.steps:
            raise ValueError("there is no plan yet: call set_plan first")
        node = plan.find(str(step))
        if not isinstance(substeps, list) or not substeps:
            raise ValueError("give at least one substep")
        children = [parse_step(raw, f"{step}.{i}", depth=2) for i, raw in enumerate(substeps, 1)]
        condition = critic.validate(done_when)
        node.children = children
        node.kind = ANY if any else "all"
        node.done = node.failed = False
        node.attempts, node.last_error = 0, ""
        if condition is not None:
            node.done_when = condition
        obs = self.agent.observe()
        plan.update(obs, self.agent.world(self.episode))
        self.episode.log.write("expand", step=str(step), substeps=substeps, any=bool(any), done_when=done_when)
        return Outcome("Plan now: " + plan.describe())

    def _remember(self, kind: str, note: str, x: Optional[int] = None, y: Optional[int] = None,
                  z: Optional[int] = None) -> Outcome:
        if kind == "death":
            raise ValueError("deaths are recorded automatically")
        obs = self.agent.observe()
        pos = (x, y, z) if None not in (x, y, z) else state.block_pos(obs)
        self.episode.memory.add(kind, note, pos, obs.data.get("dimension"))
        return Outcome(f"Remembered {kind} at {pos[0]} {pos[1]} {pos[2]}: {note}")

    def _save_routine(self, name: str, description: str, steps: list[dict[str, Any]]) -> Outcome:
        clean = []
        for i, step in enumerate(steps, 1):
            if not isinstance(step, dict) or step.get("tool") not in SKILLS:
                raise ValueError(f"step {i}: tool must be one of {', '.join(SKILLS)}")
            args = step.get("args") or {}
            self._skill_args(step["tool"], args)
            clean.append({"tool": step["tool"], "args": args})
        self.agent.routines.save(name, description, clean)
        return Outcome(f"Saved routine {name!r} with {len(clean)} steps.")

    def _run_routine(self, name: str, force: bool = False) -> Outcome:
        routine = self.agent.routines.get(name)
        if routine is None:
            known = ", ".join(repr(n) for n in self.agent.routines.routines) or "none yet"
            raise ValueError(f"no routine named {name!r}; saved routines: {known}")
        if not force:
            problem = self.agent.check_calls(routine["steps"], state.counts(self.agent.observe()))
            if problem:
                return Outcome(f"Not run: checked on paper against what you carry, {routine['name']!r} would fail "
                               f"at {problem}. Get what is missing first (run_plan can), or pass force=true if "
                               "the check is wrong.", is_error=True)
        lines = [f"Running {routine['name']!r}: {describe_steps(routine['steps'])}"]
        for i, step in enumerate(routine["steps"], 1):
            outcome = self._skill(step["tool"], step.get("args") or {})
            lines.append(f"{i}. {step['tool']}: {outcome.content}")
            if outcome.skill_failed or self.agent.interrupted(self.episode.goal):
                self.agent.routines.record_run(routine["name"], False)
                lines.append(f"Stopped at step {i} of {len(routine['steps'])}.")
                return Outcome("\n".join(lines), is_error=True, skill_failed=True)
        self.agent.routines.record_run(routine["name"], True)
        return Outcome("\n".join(lines) + "\nAll steps succeeded.")

    def _save_blueprint(self, name: str, description: str, layers: list[Any], key: dict[str, str]) -> Outcome:
        blocks = self.agent.blueprints.save(name, description, layers, key)
        w, h, d = blueprints.size(blocks)
        needs = ", ".join(f"{n} {state.short(item)}" for item, n in blueprints.materials(blocks).items())
        lines = [f"Saved blueprint {name!r}: {w} wide (x), {h} high, {d} deep (z), {len(blocks)} blocks. "
                 f"Needs {needs}."]
        notes = style.review(blocks)
        if notes:
            lines.append("Design notes (advice: redraw and save again under the same name to act on them):")
            lines += [f"- {note}" for note in notes]
        else:
            lines.append("Design check: nothing to add.")
        return Outcome("\n".join(lines))

    # Structures and big jobs ------------------------------------------------------------

    def _generate(self, kind: str, facing: Optional[str], options: dict[str, Any]) -> structures.Structure:
        unknown = set(options) - set(_STRUCTURE_OPTIONS)
        if unknown:
            raise TypeError(f"unexpected arguments {', '.join(sorted(unknown))}")
        options = {k: v for k, v in options.items() if v is not None}
        named = options.pop("theme", None)
        self._theme_note = ""
        takes = structures.KINDS[kind][1] if kind in structures.KINDS else ()
        if "style" in takes:
            theme = None
            if named is not None:
                theme = stylebook.get(named)
            elif "style" not in options and kind in _THEMED_KINDS and not (
                    kind == "design" and design.header_style(options.get("program") or "")):
                theme = self._suited_theme()
            if theme is not None:
                options = stylebook.apply(theme, kind, options)
                if named is None:
                    self._theme_note = (f"Theme {theme.name}, picked for this place (style {theme.style}); pass "
                                        "style or theme to choose another, and the same one to every call for "
                                        "this build.")
        elif named is not None:
            raise ValueError(f"{kind} takes no theme")
        return structures.generate(kind, facing or "south", **options)

    def _suited_theme(self) -> Optional[stylebook.Theme]:
        """The theme for where the player is, kept for the whole goal so every call for one build agrees.
        The biome decides, unless it only says "some woodland": then the wood carried or standing near does."""
        episode = self.episode
        if episode.theme:
            return stylebook.THEMES.get(episode.theme)
        obs = self.agent.observe()
        theme = stylebook.for_biome(obs.data.get("biome"))
        if theme is None or theme.name == stylebook.DEFAULT:
            woods: dict[str, float] = {}
            for item, n in state.counts(obs).items():
                wood = stylebook.wood_of(item)
                if wood:
                    woods[wood] = woods.get(wood, 0) + (n / 4 if item.endswith("_planks") else n)
            if not woods or max(woods.values()) < 3:
                try:
                    for found in self.agent.bridge.find("#minecraft:logs", radius=32, limit=24):
                        wood = stylebook.wood_of(found.get("block", ""))
                        if wood:
                            woods[wood] = woods.get(wood, 0) + 1
                except (BridgeError, KeyError, TimeoutError):
                    pass
            theme = stylebook.for_wood(woods) or theme
        if theme is not None:
            episode.theme = theme.name
        return theme

    def _styles(self, name: Optional[str] = None) -> Outcome:
        if name:
            return Outcome(stylebook.describe(stylebook.get(name)))
        obs = self.agent.observe()
        suited = self._suited_theme()
        lines = [stylebook.PRINCIPLES, ""]
        if suited is not None:
            lines.append(f"Here ({state.short(obs.data.get('biome', '?'))}) the theme that fits is {suited.name}: "
                         f"{suited.summary} It is what build_structure uses when you give no style.")
        lines.append("Themes (styles(name=...) for the palette and rules of one):")
        lines += [f"- {t.name}: {t.summary}" for t in stylebook.THEMES.values()]
        lines.append("After the house, build_structure(kind=\"yard\", same x y z, width, depth) gives it a path, "
                     "a fence with a gate and lamp posts.")
        return Outcome("\n".join(lines))

    @staticmethod
    def _parts(made: structures.Structure, part: Optional[str]) -> list[structures.Part]:
        if part is None:
            return made.parts
        chosen = [p for p in made.parts if p.name == part.strip().lower()]
        if not chosen:
            raise ValueError(f"{made.kind} has no part {part!r}; parts: {', '.join(p.name for p in made.parts)}")
        return chosen

    def _list_structures(self) -> Outcome:
        lines = ["Ready-made structures (plan_structure for exact sizes and costs with your options):"]
        lines += [f"- {line}" for line in structures.catalogue()]
        lines.append("Styles: " + ", ".join(structures.PALETTES) + ". Any single material can be swapped with "
                     "materials. The front faces south unless facing says otherwise.")
        lines.append("Themes (a style with the shape that goes with it; see styles): "
                     + ", ".join(stylebook.names()) + ". With no style or theme, houses, sheds, pens and yards "
                     "take the theme that suits the biome.")
        saved = self.agent.blueprints.describe()
        if saved:
            lines += ["Your saved blueprints (build with build(blueprint=...)):"] + [f"- {d}" for d in saved]
        return Outcome("\n".join(lines))

    def _plan_structure(self, kind: str, facing: Optional[str] = None, part: Optional[str] = None,
                        **options: Any) -> Outcome:
        made = self._generate(kind, facing, options)
        parts = self._parts(made, part)
        blocks = [b for p in parts for b in p.blocks]
        needs = structures.materials(blocks)
        w, d = made.footprint()
        _, h, _ = made.size()
        lines = [f"{made.description}. Footprint {w} (x) by {d} (z), {h} high, {sum(needs.values())} blocks"
                 + (f" in part {part}" if part else "") + "."]
        if len(made.parts) > 1:
            lines.append("Parts: " + ", ".join(f"{p.name} ({sum(structures.materials(p.blocks).values())})"
                                               for p in made.parts) + ".")
        if self._theme_note:
            lines.append(self._theme_note)
        lines.append("Needs: " + ", ".join(f"{n} {state.short(item)}" for item, n in needs.items()) + ".")
        carried = state.counts(self.agent.observe())
        missing = {item: n - carried.get(item, 0) for item, n in needs.items() if carried.get(item, 0) < n}
        if not missing:
            lines.append("You carry all of it.")
        else:
            lines.append("Still missing: " + ", ".join(f"{n} {state.short(item)}" for item, n in missing.items())
                         + ".")
            lines.append(requirements.resolve(needs, carried).describe(carried))
        if made.entrance is not None:
            lines.append(f"The door ends up at origin + ({made.entrance[0]}, {made.entrance[2]}) in x, z, just "
                         "outside the footprint: keep that side clear.")
        lines += made.notes
        if made.footing:
            lines.append(f"On uneven ground the lowest layer is carried down to the ground in "
                         f"{state.short(made.footing)}: carry a dozen or two extra, or pick a level site.")
        if made.kind == "yard":
            lines.append("Next: build_structure(kind=\"yard\", ...) with the house's own x y z, width and depth.")
        else:
            lines.append(f"Next: find_site(width={w}, depth={d}) for a place, then build_structure.")
        return Outcome("\n".join(lines))

    def _design_guide(self, width: Optional[int] = None, depth: Optional[int] = None,
                      style: Optional[str] = None) -> Outcome:
        lines = [design.reference(), ""]
        name = (style or "").strip().lower().replace(" ", "_")
        theme = stylebook.THEMES.get(name) if name else self._suited_theme()
        if theme is not None:
            lines += [("Style card" if name else "Style card for this place") + ":", stylebook.describe(theme), ""]
        found = designs.find(width, depth, theme.name if theme else name, self.agent.designs)
        lines.append("Examples to start from" + (f" for about {width}x{depth}" if width and depth else "") + ":")
        for example, (w, d), blocks in found:
            lines += [f"- {example.name}: {example.pattern}. {w}x{d}, {blocks} blocks"
                      + (", built and checked here" if example.built else "") + ".",
                      *("    " + line for line in example.program.splitlines())]
        lines.append("Change the sizes, the parts and the style to suit; then check_design(program=...).")
        return Outcome("\n".join(lines))

    def _check_design(self, program: str, facing: Optional[str] = None, **options: Any) -> Outcome:
        options = {k: v for k, v in options.items() if v is not None}
        unknown = set(options) - {"style", "theme", "texture", "materials"}
        if unknown:
            raise TypeError(f"unexpected arguments {', '.join(sorted(unknown))}")
        if facing is not None and facing not in structures.FACINGS:
            raise ValueError(f"facing must be one of {', '.join(structures.FACINGS)}")
        named = options.pop("theme", None)
        theme = stylebook.get(named) if named else None
        if theme is None and "style" not in options and not design.header_style(program):
            theme = self._suited_theme()
        if theme is not None:
            options = stylebook.apply(theme, "design", options)
        try:
            made = design.compile_design(program, **options)
        except design.DesignError as error:
            return Outcome("The design cannot be built as written:\n" + str(error)
                           + "\nPut these right and check it again. design_guide shows the language.", is_error=True)
        built = structures.turned(made.structure, structures.FACINGS.index(facing or "south"))
        needs = built.materials()
        w, d = built.footprint()
        _, h, _ = built.size()
        lines = [f"The design can be built. {built.description}. Footprint {w} (x) by {d} (z), {h} high, "
                 f"{sum(needs.values())} blocks."]
        if theme is not None and named is None:
            lines.append(f"Style: theme {theme.name}, which suits this place; name a style on the build line or "
                         "with style= to choose another.")
        lines.append("Parts: " + ", ".join(f"{p.name} ({sum(structures.materials(p.blocks).values())})"
                                           for p in built.parts) + ".")
        lines += made.notes
        lines += ["Ground floor from above, north at the top, front facing south (# wall, P post, o window, "
                  "D door, _ doorway, b bed, c furniture, i torch, H ladder, + porch, C chimney or pier):",
                  design.floor_plan(made)]
        fix, consider = design.review(made, theme.name if theme else None)
        if fix:
            lines += ["Fix before building:"] + [f"- {f}" for f in fix]
        if consider:
            lines += ["Consider:"] + [f"- {c}" for c in consider]
        if not fix and not consider:
            lines.append("Review: nothing to fix.")
        lines.append("Needs: " + ", ".join(f"{n} {state.short(item)}" for item, n in needs.items()) + ".")
        carried = state.counts(self.agent.observe())
        missing = {item: n - carried.get(item, 0) for item, n in needs.items() if carried.get(item, 0) < n}
        lines.append("You carry all of it." if not missing else "Still missing: " + ", ".join(
            f"{n} {state.short(item)}" for item, n in missing.items()) + ". plan_structure(kind=\"design\", "
            "program=...) lists what to gather and craft for it.")
        asked = {k: v for k, v in options.items() if theme is None or k not in ("style", "materials")}
        same = (f", theme=\"{theme.name}\"" if theme is not None else "") + "".join(
            f", {k}={json.dumps(v)}" for k, v in asked.items())
        lines.append(("When the fixes are in, n" if fix else "N") + f"ext: find_site(width={w}, depth={d}), then "
                     f"build_structure(kind=\"design\", program=<this design>{same}"
                     + (f", facing=\"{facing}\"" if facing else "") + ", x, y, z).")
        return Outcome("\n".join(lines))

    def _find_site(self, width: int, depth: int, radius: int = 32) -> Outcome:
        sites = self.agent.bridge.survey(int(width), int(depth), radius=int(radius), limit=3)
        if not sites:
            return Outcome(f"no flat site of {width}x{depth} within {radius} blocks", is_error=True)
        lines = [f"Sites for {width}x{depth}, best first (x y z is the west, north corner at walking level):"]
        for s in sites:
            extras = [f"{s['work']} blocks to level" if s.get("work") else "level"]
            if s.get("trees"):
                extras.append(f"{s['trees']} columns under trees (mine the logs first, or pick another)")
            if s.get("water"):
                extras.append(f"{s['water']} columns of water")
            lines.append(f"- {s['x']} {s['y']} {s['z']}: {s['distance']}m away, " + ", ".join(extras))
        return Outcome("\n".join(lines))

    def _build_structure(self, kind: str, x: int, y: int, z: int, facing: Optional[str] = None,
                         part: Optional[str] = None, partial: bool = False, **options: Any) -> Outcome:
        if not all(isinstance(v, int) for v in (x, y, z)):
            raise ValueError("give the origin as whole numbers x, y and z")
        agent, episode = self.agent, self.episode
        call_key = json.dumps([kind, x, y, z, facing, part, partial, options], sort_keys=True, default=str)
        stop = episode.build_stops.get(call_key)
        if stop is not None and stop[0] >= 2 and stop[2] == len(episode.successful_calls):
            # Nothing has been done since it stopped twice the same way: a third go would stop there too.
            return Outcome(f"Not run: this exact build_structure call has stopped {stop[0]} times the same way "
                           f"({stop[1].lstrip('- ')[:200]}), and nothing has been done since. Deal with what "
                           "it names first (mine what is in the way, place what is missing, inspect_build to "
                           "see what stands), or build somewhere else. After any other action it may be "
                           "called again.", is_error=True, skill_failed=True)
        made = self._generate(kind, facing, options)
        theme_note = self._theme_note
        parts = self._parts(made, part)
        # A yard is given the corner of the house it goes round, and reaches out beyond it.
        ox, oz = x - made.anchor[0], z - made.anchor[1]

        # What is still to place: blocks already standing need no items.
        solid = [b for b in made.blocks if b[3] != structures.AIR]
        todo = [b for p in parts for b in p.blocks if b[3] != structures.AIR]
        standing: set[tuple[int, int, int]] = set()
        looked = False
        try:
            for i in range(0, len(solid), 4000):
                batch = solid[i:i + 4000]
                found = agent.bridge.blocks([(ox + b[0], y + b[1], oz + b[2]) for b in batch])
                standing |= {b[:3] for b, there in zip(batch, found) if there == b[3]}
            looked = True
        except (BridgeError, KeyError):
            pass  # an older mod: the build skill still skips what is in place
        needs = structures.materials([b for b in todo if b[:3] not in standing])
        carried = state.counts(agent.observe())
        missing = {item: n - carried.get(item, 0) for item, n in needs.items() if carried.get(item, 0) < n}
        if missing and not partial:
            text = (f"not enough for {made.kind}" + (f" part {part}" if part else "") + ": missing "
                    + ", ".join(f"{n} {state.short(item)}" for item, n in missing.items()) + ".")
            if theme_note:
                text += "\n" + theme_note
            hint = critic.hint(text)
            return Outcome(text + ("\nHint: " + hint if hint else ""), is_error=True, skill_failed=True)

        # A fresh site is cleared with the first build call: trees felled, plants picked, the ground
        # levelled down to the floor. Not when part of the build already stands: that is a resumed
        # build, and its own roof and furniture are inside the box.
        clear_box = None
        site = (made.kind, x, y, z)
        if made.clear_site and looked and not self._resumed(solid, standing) and site not in episode.cleared:
            clear_box = self._site_box(made, ox, y, oz)

        before = state.counts(agent.observe())
        lines = [f"Building {made.description} at {x} {y} {z}" + (f", part {part}" if part else "") + "."]
        if theme_note:
            lines.append(theme_note)
        failed = False
        gaps = 0
        # The box of the whole structure: the mod takes its own scaffolding down inside it and keeps
        # crafting tables out of it. And what is still to place after each call, held back from scaffolding.
        whole = self._whole_box(made, ox, y, oz)
        remaining = dict(needs)
        counted: set[tuple[int, int, int]] = set()
        tried: list[str] = []
        short_parts: set[str] = set()
        aborted = False
        for p in parts:
            tried.append(p.name)
            placed = present = problems = footings = kept = cleared = 0
            first_problem = ""
            short = stuck = False
            solid_ys = [b[1] for b in p.blocks if b[3] != structures.AIR]
            bottom = min(solid_ys) if solid_ys else None
            pending = structures.chunks(p.blocks)
            by_spot = {(b[0] + ox, b[1] + y, b[2] + oz): b for b in p.blocks}
            # Why each spot last failed: one that fails twice for the same reason is left out, not tried again.
            last_reason: dict[tuple[int, int, int], str] = {}
            given_up: set[tuple[int, int, int]] = set()
            for attempt in range(_BUILD_PASSES):
                again: list[list[structures.Block]] = []
                retry: dict[tuple[int, int, int], structures.Block] = {}
                repeated = False
                problems, first_problem, progress = 0, "", 0
                for chunk in pending:
                    if agent.interrupted(episode.goal):
                        failed = aborted = True
                        break
                    # Scaffold comes down once, when the whole build is done, not after every chunk.
                    build_args = {"blocks": structures.place_at(chunk, ox, y, oz), "clear": True, "partial": True,
                                  "timeout": 120 + 12 * len(chunk), "site": whole, "tidy": False}
                    for b in chunk:
                        if b[:3] in counted:
                            continue
                        counted.add(b[:3])
                        if b[3] != structures.AIR and b[:3] not in standing and remaining.get(b[3], 0) > 0:
                            remaining[b[3]] -= 1
                    reserve = {item: n for item, n in remaining.items() if n > 0}
                    if reserve:
                        build_args["reserve"] = reserve
                    if p.footing and made.footing and any(b[1] == bottom and b[3] != structures.AIR for b in chunk):
                        # The part's bottom layer: stand it on footings where the ground falls away.
                        build_args["foundation"] = made.footing
                        build_args["timeout"] = 120 + 24 * len(chunk)
                    if clear_box is not None:
                        build_args["clearBox"] = clear_box
                        build_args["timeout"] += 600
                    run = agent.run_skill("build", build_args, episode)
                    data = run.result.data or {}
                    if clear_box is not None:
                        clear_box = None
                        episode.cleared.add(site)
                    footings += int(data.get("footings", 0))
                    kept += int(data.get("kept", 0))
                    placed += int(data.get("placed", 0))
                    cleared += int(data.get("cleared", 0))
                    progress += int(data.get("placed", 0)) + int(data.get("cleared", 0)) + int(data.get("filled", 0))
                    if attempt == 0:
                        present += int(data.get("present", 0))
                    if int(data.get("failed", 0)):
                        problems += int(data["failed"])
                        reasons = [str(f) for f in data.get("failures") or []]
                        spots = data.get("failedAt")
                        if isinstance(spots, list):
                            # Go over just the blocks that failed and their neighbours: those often go in
                            # once the blocks around them stand.
                            for entry in spots:
                                try:
                                    at = (int(entry[0]), int(entry[1]), int(entry[2]))
                                except (TypeError, ValueError, IndexError):
                                    continue
                                why = str(entry[3]) if len(entry) > 3 else ""
                                if at in given_up or last_reason.get(at) == why:
                                    given_up.add(at)
                                    repeated = True
                                    continue
                                last_reason[at] = why
                                for dx in (-1, 0, 1):
                                    for dy in (-1, 0, 1):
                                        for dz in (-1, 0, 1):
                                            near = (at[0] + dx, at[1] + dy, at[2] + dz)
                                            if near in by_spot and near not in given_up:
                                                retry[near] = by_spot[near]
                        else:
                            # An older mod does not say which spots failed: go over the whole chunk.
                            again.append(chunk)
                        first_problem = first_problem or (reasons[0] if reasons else run.result.message)
                        if data.get("missing") or any(_OUT_OF_ITEMS.search(r) for r in reasons):
                            short = True
                    if run.death:
                        lines.append(run.death)
                        failed = aborted = True
                        break
                    if not run.result.success and not data.get("failed"):
                        # It stopped for a reason other than single blocks: timed out, cancelled, paused.
                        first_problem = first_problem or run.result.message
                        failed = True
                        break
                if retry:
                    again += structures.chunks([b for spot, b in retry.items() if spot not in given_up])
                if not failed and not again and not short and repeated:
                    # What is left failed just as it did last time round.
                    stuck = True
                if failed or not again or short:
                    break
                if progress == 0:
                    # A whole pass that changed nothing: going round again will not either.
                    stuck = True
                    break
                # Blocks that failed often go in once their neighbours stand: go over those again.
                pending = again
            total = len(solid_ys)
            if short:
                short_parts.add(p.name)
            # A few blocks nothing can reach do not hold up the rest; many mean something is wrong with the site.
            passable = stuck and (p.optional or problems <= max(6, total * 15 // 100))
            done = not failed and (problems == 0 or (p.optional and not short) or passable)
            line = (f"- {p.name}: placed {placed}" + (f", {present} already there" if present else "")
                    + (f", {footings} of them footings down to the ground" if footings else "")
                    + (f", {kept} footing spots left as the solid ground they were" if kept else "")
                    + (f", cleared {cleared}" if cleared else ""))
            if problems and done:
                gaps += problems
                line += f"; {problems} left out, nothing more can be done for them, e.g. {first_problem}"
            elif problems or not done:
                line += f"; {problems} not placed" + (f", e.g. {first_problem}" if first_problem else "")
            lines.append(line)
            if done:
                episode.built.add(f"{made.kind}:{p.name}")
            else:
                failed = True
                if stuck:
                    lines.append(f"Going over it again placed nothing: {problems} of {total} blocks of {p.name} "
                                 "cannot be placed as things are. Do not repeat this call unchanged. Look at "
                                 "what is in the way at the spots named, remove it by hand (mine, or goto and "
                                 "look_around), or choose another site with find_site.")
                    times = episode.failures.record("build_structure", {"kind": kind, "at": [x, y, z], "part": p.name},
                                                    "stuck")
                    if times >= 2:
                        lines.append(f"This build has now stuck {times} times the same way.")
                        self._mark_failed(first_problem or "build stuck", lines)
            if failed:
                break
        if not aborted and counted and not agent.interrupted(episode.goal):
            # Take the scaffold down once, now the parts are done, before the check looks at what stands.
            tidy = agent.run_skill("build", {"blocks": [], "tidyOnly": True, "site": whole}, episode)
            tidy_data = tidy.result.data or {}
            if tidy.death:
                lines.append(tidy.death)
                aborted = failed = True
            elif int(tidy_data.get("tidied", 0)) or int(tidy_data.get("leftover", 0)):
                lines.append(f"Took down {int(tidy_data.get('tidied', 0))} scaffold blocks"
                             + (f"; {int(tidy_data['leftover'])} could not be taken down"
                                if int(tidy_data.get("leftover", 0)) else "") + ".")
        # Look at what stands: a part is built when it matches the blueprint, not when the calls said so.
        check = None if aborted else self._check_build(made, ox, y, oz, whole, tried, lines)
        if check is not None:
            gaps = 0
            for p in parts:
                name = f"{made.kind}:{p.name}"
                if p.name not in tried:
                    continue
                diffs = check.get(p.name, 0)
                total = sum(1 for b in p.blocks if b[3] != structures.AIR)
                if p.optional and p.name not in short_parts:
                    episode.built.add(name)
                elif diffs > max(3, total * 4 // 100) or p.name in short_parts:
                    episode.built.discard(name)
                else:
                    episode.built.add(name)
                    gaps += diffs
            failed = any(f"{made.kind}:{p.name}" not in episode.built for p in parts)
            if failed:
                episode.built.discard(made.kind)
        if failed and not aborted:
            # The same build failing the same way again will not get better by asking again.
            wrong = next((line for line in lines if line.startswith("- ") and " not placed" in line), "")
            how = re.sub(r"placed \d+|\d+ already there|cleared \d+", "", wrong)
            times = episode.failures.record("build_structure", {"kind": kind, "at": [x, y, z], "part": part}, how)
            last = episode.build_stops.get(call_key)
            in_a_row = last[0] + 1 if last is not None and last[1] == how else 1
            episode.build_stops[call_key] = (in_a_row, how, len(episode.successful_calls))
            if times >= 2:
                lines.append(f"This is the {times}{'nd' if times == 2 else 'rd' if times == 3 else 'th'} time this "
                             "build has stopped the same way. Do not call it again unchanged: go to the spots "
                             "named and deal with what is there by hand (mine what is in the way, place what is "
                             "missing), or build somewhere else.")
                if times >= 3:
                    self._mark_failed("build keeps stopping the same way", lines)
        if not failed:
            episode.build_stops.pop(call_key, None)
        if not failed and part is None:
            episode.built.add(made.kind)
        elif not failed and all(f"{made.kind}:{p.name}" in episode.built for p in made.parts):
            episode.built.add(made.kind)
        if not failed and made.kind == "yard" and made.kind in episode.built:
            planted = self._plant_flowers(made, ox, y, oz)
            if planted:
                lines.append(f"- flowers: planted {planted} along the path")
        after = agent.observe()
        lines.append("Inventory: " + state.delta_line(state.delta(before, state.counts(after))))
        if failed:
            lines.append("Not finished. Fix what stopped it and call build_structure again with the same "
                         "arguments: blocks already placed are skipped.")
            hint = critic.hint("\n".join(lines))
            if hint:
                lines.append("Hint: " + hint)
            return Outcome("\n".join(lines), is_error=True, skill_failed=True)
        if made.kind in episode.built:
            where = (ox, y, oz) if made.entrance is None else (ox + made.entrance[0], y + made.entrance[1],
                                                               oz + made.entrance[2])
            if made.kind != "yard":
                kind_of_note = "base" if made.kind in ("house", "base", "shelter", "design") else "landmark"
                episode.memory.add(kind_of_note, f"{made.description}; origin {x} {y} {z}, door here",
                                   where, after.data.get("dimension"))
            if made.kind == "design" and options.get("program"):
                w0, d0 = made.footprint()
                try:
                    if self.agent.designs.add(made.description.split(":")[0], options["program"],
                                              self.episode.theme or options.get("style"), w0, d0):
                        lines.append("The design is kept: design_guide will offer it again.")
                except OSError:
                    pass
            lines.append(f"Finished and remembered. The way in is at {where[0]} {where[1]} {where[2]}."
                         + (f" {gaps} blocks differ from the plan (listed above): put them right by hand if "
                            "they show." if gaps else ""))
            w, d = made.footprint()
            _, h, _ = made.size()
            if made.kind == "house":
                same = "".join(f", {k}={json.dumps(options[k])}" for k in ("width", "depth", "style", "theme")
                               if options.get(k) is not None) + (f", facing=\"{facing}\"" if facing else "")
                lines.append(f"To give it a setting: build_structure(kind=\"yard\", x={x}, y={y}, z={z}{same}) "
                             "adds a path to the door, a fence with a gate and lamp posts; "
                             "plan_structure(kind=\"yard\", ...) lists what that takes.")
            if getattr(agent.backend, "vision", False):
                lines.append(f"To see how it looks: review_build(x={ox}, y={y}, z={oz}, width={w}, depth={d}, "
                             f"height={h}).")
        else:
            left = [p.name for p in made.parts if f"{made.kind}:{p.name}" not in episode.built]
            lines.append(f"Part done. Parts of {made.kind} still to build: {', '.join(left)}.")
        return Outcome("\n".join(lines))

    @staticmethod
    def _resumed(solid: list[structures.Block], standing: set[tuple[int, int, int]]) -> bool:
        """True if enough of a structure already stands to call this a build in progress, not a fresh
        site. A few logs, dirt or stone that happen to sit where the plan wants them do not count: in
        a forest that alone used to skip clearing and leave a tree inside the house."""
        items = {b[:3]: b[3] for b in solid}
        built = [pos for pos in standing if not _NATURAL.search(items.get(pos, ""))]
        return len(built) >= 3 or len(standing) >= max(10, len(solid) // 10)

    @staticmethod
    def _whole_box(made: structures.Structure, ox: int, y: int, oz: int) -> list[int]:
        """The box that holds every block of a structure, from walking level up."""
        solid = [b for b in made.blocks if b[3] != structures.AIR]
        return [ox + min(b[0] for b in solid), y, oz + min(b[2] for b in solid),
                ox + max(b[0] for b in solid), y + max(1, max(b[1] for b in solid)), oz + max(b[2] for b in solid)]

    @staticmethod
    def _plan_cells(made: structures.Structure) -> dict[tuple[int, int, int], tuple[str, Any]]:
        """One entry for each spot of the blueprint: the part it belongs to and its block. Where one part
        empties a spot and another puts something there (a room and its furniture), the block wins."""
        cells: dict[tuple[int, int, int], tuple[str, Any]] = {}
        for p in made.parts:
            for b in p.blocks:
                key = (b[0], b[1], b[2])
                if b[3] == structures.AIR and key in cells:
                    continue
                cells[key] = (p.name, b)
        return cells

    def _audit(self, made: structures.Structure, ox: int, y: int, oz: int, site: list[int]) -> Optional[dict[str, Any]]:
        """What the game says stands where the blueprint has blocks, or None from a mod too old to say."""
        entries = structures.place_at([b for _, b in self._plan_cells(made).values()], ox, y, oz)
        if len(entries) > 8000:
            return None
        cells = (site[3] - site[0] + 1) * (site[4] - site[1] + 1) * (site[5] - site[2] + 1)
        try:
            found = self.agent.bridge.audit(entries, box=site if cells <= 80_000 else None, limit=400,
                                            ground=made.footing)
        except (BridgeError, KeyError, TimeoutError):
            return None
        return found if isinstance(found.get("problems"), list) else None

    def _check_build(self, made: structures.Structure, ox: int, y: int, oz: int, site: list[int],
                     tried: list[str], lines: list[str], repair: bool = True) -> Optional[dict[str, int]]:
        """Compares the parts in ``tried`` with what stands, puts right what it can in one pass, and adds
        what it found to ``lines``. Returns how many spots of each part still differ, or None when the
        mod cannot say."""
        found = self._audit(made, ox, y, oz, site)
        if found is None:
            return None
        where = {(ox + c[0], y + c[1], oz + c[2]): v for c, v in self._plan_cells(made).items()}
        total = sum(1 for name, _ in where.values() if name in tried)

        def mine(found: dict[str, Any]) -> list[dict[str, Any]]:
            out = []
            for problem in found["problems"]:
                name, _ = where.get((problem["x"], problem["y"], problem["z"]), (None, None))
                if problem["kind"] == "extra" or name in tried:
                    out.append(problem)
            return out

        problems = mine(found)
        fixed = ""
        if repair and problems:
            carried = state.counts(self.agent.observe())
            again: list[list[Any]] = []
            for problem in problems:
                pos = (problem["x"], problem["y"], problem["z"])
                if problem["kind"] == "extra":
                    if problem.get("own"):
                        again.append([*pos, structures.AIR])
                    continue
                block = where[pos][1]
                if problem["kind"] == "turned" and not problem.get("own"):
                    continue
                if block[3] != structures.AIR and problem["kind"] != "turned" and carried.get(block[3], 0) < 1:
                    continue
                again += structures.place_at([block], ox, y, oz)
            # A handful of differences are worth one more pass; a part that mostly is not there is not.
            if again and len(again) <= max(12, total // 10):
                try:
                    run = self.agent.run_skill("build", {"blocks": again, "clear": True, "partial": True, "site": site,
                                                         "timeout": 120 + 20 * len(again)}, self.episode)
                    data = run.result.data or {}
                    done = [f"placed {data['placed']}" if data.get("placed") else "",
                            f"turned {data['turned']} the right way round" if data.get("turned") else "",
                            f"took down {data.get('tidied', 0) + data.get('cleared', 0)}"
                            if data.get("tidied") or data.get("cleared") else ""]
                    fixed = ", ".join(d for d in done if d)
                except BridgeError:
                    pass
                found = self._audit(made, ox, y, oz, site) or found
                problems = mine(found)
        truncated = sum(found.get(k, 0) for k in ("missing", "wrong", "turned", "blocked", "extra")) > len(
            found["problems"])
        per_part: dict[str, int] = {}
        for problem in problems:
            if problem["kind"] != "extra":
                name = where[(problem["x"], problem["y"], problem["z"])][0]
                per_part[name] = per_part.get(name, 0) + 1
        wrong = sum(per_part.values())
        extra = sum(1 for problem in problems if problem["kind"] == "extra")
        line = (f"Checked against the plan: {total - wrong} of {total} blocks are right"
                + (f" ({(total - wrong) * 100 // total}%)" if total else "")
                + (f", {extra} stray blocks in or beside it" if extra else "") + "."
                + (f" One repair pass {fixed}." if fixed else "")
                + (" There are more differences than can be listed." if truncated else ""))
        lines.append(line)
        lines += ["  " + self._problem_line(problem) for problem in problems[:8]]
        if len(problems) > 8:
            lines.append(f"  and {len(problems) - 8} more; inspect_build lists them.")
        return per_part

    @staticmethod
    def _problem_line(problem: dict[str, Any]) -> str:
        at = f"{problem['x']} {problem['y']} {problem['z']}"
        there, want = state.short(problem.get("is", "?")), state.short(problem.get("want", "?"))
        whose = "" if problem.get("own") else " (not placed by MineGPT, so it was left: mine it if it should go)"
        kind = problem["kind"]
        if kind == "missing":
            return f"missing: {want} at {at}"
        if kind == "turned":
            return f"turned wrong: {there} at {at}, should be {want}" + (
                "" if problem.get("own") else " (not placed by MineGPT: mine it and place it again)")
        if kind == "wrong":
            return f"wrong block: {there} at {at}, should be {want}{whose}"
        if kind == "blocked":
            return f"in the way: {there} at {at}, which should be empty{whose}"
        return f"stray: {there} at {at}" + (" (own scaffolding that would not come down)" if problem.get("own") else whose)

    def _inspect_build(self, kind: str, x: int, y: int, z: int, facing: Optional[str] = None,
                       part: Optional[str] = None, repair: bool = False, **options: Any) -> Outcome:
        if not all(isinstance(v, int) for v in (x, y, z)):
            raise ValueError("give the origin as whole numbers x, y and z")
        made = self._generate(kind, facing, options)
        parts = self._parts(made, part)
        ox, oz = x - made.anchor[0], z - made.anchor[1]
        site = self._whole_box(made, ox, y, oz)
        lines = [f"{made.description} at {x} {y} {z}" + (f", part {part}" if part else "") + ":"]
        names = [p.name for p in parts]
        before = len(lines)
        per_part = self._check_build(made, ox, y, oz, site, names, lines, repair=bool(repair))
        if per_part is None:
            return Outcome("inspect_build needs the newer MineGPT mod in the game: rebuild it (it compares the world with "
                           "the plan there).", is_error=True)
        if len(lines) == before + 1 and not per_part:
            lines.append("Nothing differs: every block is the right one, turned the right way, and nothing "
                         "is left inside.")
        elif len(made.parts) > 1:
            lines.append("By part: " + ", ".join(f"{name} {per_part.get(name, 0)} wrong" for name in names) + ".")
        if not repair and per_part:
            lines.append("inspect_build with repair=true puts right what MineGPT placed itself; "
                         "build_structure with the same arguments places what is missing.")
        return Outcome("\n".join(lines))

    @staticmethod
    def _site_box(made: structures.Structure, ox: int, y: int, oz: int) -> Optional[list[int]]:
        """The box to clear for a new build: its footprint and a block round it (so there is room to walk
        and work), from the walking level to a little over its top. None if that is too big for one call."""
        solid = [b for b in made.blocks if b[3] != structures.AIR]
        xs, zs = [b[0] for b in solid], [b[2] for b in solid]
        top = max(2, max(b[1] for b in solid)) + 1
        around = 0 if made.kind == "yard" else 1
        box = [ox + min(xs) - around, y, oz + min(zs) - around, ox + max(xs) + around, y + top, oz + max(zs) + around]
        cells = (box[3] - box[0] + 1) * (box[4] - box[1] + 1) * (box[5] - box[2] + 1)
        return box if cells <= 24_000 else None

    def _plant_flowers(self, made: structures.Structure, ox: int, y: int, oz: int) -> int:
        """Plants the small flowers the player carries beside a yard's path. How many went in."""
        carried = state.counts(self.agent.observe())
        flowers = [item for item in (state.full(f) for f in _FLOWERS) for _ in range(carried.get(item, 0))]
        spots = structures.flower_spots(made)[:len(flowers)]
        if not spots:
            return 0
        # Mixed along the path, not sorted by colour.
        flowers.sort(key=lambda item: hash(item) % 7)
        blocks = [[ox + sx, y + sy, oz + sz, item, "on:down"] for (sx, sy, sz), item in zip(spots, flowers)]
        try:
            run = self.agent.run_skill("build", {"blocks": blocks, "partial": True, "fill": False,
                                                 "timeout": 60 + 8 * len(blocks)}, self.episode)
        except BridgeError:
            return 0
        return int((run.result.data or {}).get("placed", 0))

    def _review_build(self, x: int, y: int, z: int, width: int, depth: int, height: int = 6,
                      views: int = 3) -> Outcome:
        agent, episode = self.agent, self.episode
        if not getattr(agent.backend, "vision", False):
            return Outcome("review_build needs a model that can read pictures; this one cannot.", is_error=True)
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (x, y, z, width, depth, height, views)):
            raise ValueError("x, y, z, width, depth, height and views are whole numbers")
        if not (1 <= width <= 64 and 1 <= depth <= 64 and 1 <= height <= 48):
            raise ValueError("width and depth go up to 64, height up to 48")
        key = (x // 8, z // 8)
        if episode.reviews.get(key, 0) >= MAX_REVIEWS:
            return Outcome(f"This build has been looked at {MAX_REVIEWS} times already. It is good enough: "
                           "finish the goal.", is_error=True)
        cx, cz = x + (width - 1) / 2, z + (depth - 1) / 2
        look_y = y + height / 2
        # Far enough back that the whole build fits in view, near enough to see the blocks.
        back = max(6.0, 0.9 * max(width, depth, height) + 3)
        images: list[Image] = []
        lines: list[str] = []
        box = (x, y, z, width, depth, height)
        for name, px, pz, hidden in self._viewpoints(box, back, max(1, min(4, views))):
            if agent.interrupted(episode.goal):
                break
            run = agent.run_skill("goto", {"x": px, "z": pz, "allowBreak": False, "timeout": 45}, episode)
            if run.death:
                lines.append(run.death)
                break
            here = state.block_pos(run.after or agent.observe())
            outside = max(x - here[0], here[0] - (x + width - 1), z - here[2], here[2] - (z + depth - 1))
            if outside < 3:
                lines.append(f"- from {name}: could not get far enough back ({run.result.message})")
                continue
            try:
                agent.bridge.act([{"type": "look_at", "x": cx + 0.5, "y": look_y, "z": cz + 0.5, "ticks": 1}])
                agent.bridge.step(ticks=3, grid=False)
                shot = agent.bridge.screenshot(width=agent.config.screenshot_width)
            except (BridgeError, TimeoutError, KeyError, ValueError) as e:
                return Outcome(f"review_build: the game could not take a picture ({e}). This needs the current "
                               "mod; carry on without it.", is_error=True)
            caption = (f"View {len(images) + 1}: from {name}, standing at {here[0]} {here[1]} {here[2]}, "
                       f"{round(math.hypot(here[0] - cx, here[2] - cz))} blocks from the middle."
                       + (" Trees or terrain hide part of the build from here; no clearer spot was found."
                          if hidden else ""))
            images.append(Image(shot["data"], shot["format"], caption))
            lines.append("- " + caption)
            self._keep_picture(shot, len(images))
        if not images:
            return Outcome("review_build: got no pictures.\n" + "\n".join(lines), is_error=True, skill_failed=True)
        episode.reviews[key] = episode.reviews.get(key, 0) + 1
        left = MAX_REVIEWS - episode.reviews[key]
        east, south, top = x + width - 1, z + depth - 1, y + height - 1
        text = [f"{len(images)} pictures of the build in the box {x} {y} {z} to {east} {top} {south} "
                "(x grows east, z grows south):", *lines,
                "Judge what you see, not what you meant to build:",
                "- Shape: is it more than a box? Is the roof pitched and does it overhang the walls?",
                "- Depth: any long, flat, single-material wall? Posts, window bays or a set-back would break it.",
                "- Palette: do the materials go together, with one main block and a contrasting trim? Anything "
                "that looks like a mistake or leftover scaffolding (stray dirt or cobblestone)?",
                "- Symmetry: are the door and windows balanced on each side?",
                "- Ground: does it sit on the terrain, or float over a slope or leave holes beside it?",
                "- Light: torches or lanterns at the door and inside?",
                "- Setting: does it suit the place (the wood and stone found here)? Is there a path to the "
                "door, a fence or lamp posts, or does it stand on bare grass among stumps and stray blocks? "
                "build_structure(kind=\"yard\") adds a path, fence and lamps round a house.",
                "Then list at most five concrete edits, each as a block and its x y z, and make them with place, "
                "mine or build. If it already looks right, say so and move on."
                + (f" You may look {left} more time after the edits." if left else
                   " This was the last look at this build: make the edits and finish.")]
        episode.log.write("review", box=[x, y, z, width, depth, height], views=len(images))
        return Outcome("\n".join(text), images=images)

    def _viewpoints(self, box: tuple[int, int, int, int, int, int], back: float,
                    count: int) -> list[tuple[str, int, int, bool]]:
        """Where to stand for each picture: (name, x, z, partly hidden). For each side in turn it takes
        the usual spot if the build can be seen from there, and otherwise the clearest of a few others
        on that side: nearer in, and a little to the left or right. Trees are the usual thing in the way."""
        x, y, z, width, depth, height = box
        cx, cz = x + (width - 1) / 2, z + (depth - 1) / 2

        def spot(sx: float, sz: float, scale: float) -> tuple[int, int]:
            norm = math.hypot(sx, sz)
            corner = 1.2 if sx and sz else 1
            return (round(cx + sx * (width / 2 + back * scale) / norm * corner),
                    round(cz + sz * (depth / 2 + back * scale) / norm * corner))

        out: list[tuple[str, int, int, bool]] = []
        for name, sx, sz in _VIEWS[:count]:
            # The usual spot first; then nearer, then swung a little either way round the build.
            tries = [spot(sx, sz, 1.0), spot(sx, sz, 0.65)]
            for turn in (0.5, -0.5):
                rx, rz = sx * math.cos(turn) - sz * math.sin(turn), sx * math.sin(turn) + sz * math.cos(turn)
                tries += [spot(rx, rz, 1.0), spot(rx, rz, 0.65)]
            best, best_clear = tries[0], -1.0
            for px, pz in tries:
                clear = self._clear_view(px, pz, box)
                if clear > best_clear:
                    best, best_clear = (px, pz), clear
                if clear >= 0.75:
                    break
            out.append((name, best[0], best[1], 0 <= best_clear < 0.5))
        return out

    def _clear_view(self, px: int, pz: int, box: tuple[int, int, int, int, int, int]) -> float:
        """How much of a build can be seen from a spot: the share of sight lines to points on it that
        nothing solid crosses, from 0 to 1. 1 when the game cannot say (an older mod, unloaded chunks)."""
        x, y, z, width, depth, height = box
        try:
            column = [(px, yy, pz) for yy in range(y + 12, y - 9, -1)]
            found = self.agent.bridge.blocks(column)
            eye = None
            for (_, yy, _), below, feet in zip(column[2:], found[2:], found[1:]):
                # The first solid ground from the top down that is not a tree, with room to stand on it.
                if below is None:
                    return 1.0
                name = state.short(below)
                if name not in _SEE_THROUGH and not name.endswith(("_leaves", "_log")) \
                        and state.short(feet or "air") in _SEE_THROUGH:
                    eye = yy + 2.6
                    break
            if eye is None:
                # Nothing but air: nothing is known. Otherwise there is no room to stand here (a trunk, a wall).
                return 1.0 if all(state.short(b or "air") in _SEE_THROUGH for b in found) else 0.3
            targets = [(x + fx * (width - 1), y + fy * (height - 1), z + fz * (depth - 1))
                       for fx in (0.15, 0.5, 0.85) for fz in (0.15, 0.5, 0.85) for fy in (0.3, 0.8)]
            rays: list[list[tuple[int, int, int]]] = []
            for tx, ty, tz in targets:
                steps = max(2, int(math.dist((px + 0.5, eye, pz + 0.5), (tx + 0.5, ty, tz + 0.5))))
                cells = []
                for i in range(1, steps):
                    t = i / steps
                    cell = (math.floor(px + 0.5 + (tx - px) * t), math.floor(eye + (ty - eye) * t),
                            math.floor(pz + 0.5 + (tz - pz) * t))
                    inside = x - 1 <= cell[0] <= x + width and z - 1 <= cell[2] <= z + depth
                    if not inside and (not cells or cells[-1] != cell):
                        cells.append(cell)
                rays.append(cells)
            flat = [c for ray in rays for c in ray][:4000]
            seen = dict(zip(flat, self.agent.bridge.blocks(flat))) if flat else {}
        except (BridgeError, KeyError, TimeoutError, ValueError):
            return 1.0
        clear = sum(1 for ray in rays if all(state.short(seen.get(c) or "air") in _SEE_THROUGH for c in ray))
        return clear / len(rays)

    def _keep_picture(self, shot: dict[str, Any], number: int) -> None:
        """Saves what the model was shown next to the episode logs, so the player can look at it too."""
        try:
            folder = self.agent.config.data_dir / "screenshots"
            folder.mkdir(parents=True, exist_ok=True)
            extension = "jpg" if shot["format"] == "jpeg" else shot["format"]
            (folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{number}.{extension}").write_bytes(shot["data"])
        except OSError:
            pass

    def _plan_materials(self, items: dict[str, int]) -> Outcome:
        if not isinstance(items, dict) or not items or not all(
                isinstance(k, str) and isinstance(v, int) and v > 0 for k, v in items.items()):
            raise ValueError('items maps item ids to counts, like {"iron_pickaxe": 1}')
        carried = state.counts(self.agent.observe())
        knowledge = self.agent.knowledge
        if knowledge is None:
            return Outcome(requirements.resolve(items, carried).describe(carried))
        from ..knowledge import plan_items

        return Outcome(plan_items(items, carried, knowledge).describe(carried))

    def _run_plan(self, items: dict[str, int], step: Optional[str] = None) -> Outcome:
        if not isinstance(items, dict) or not items or not all(
                isinstance(k, str) and isinstance(v, int) and v > 0 for k, v in items.items()):
            raise ValueError('items maps item ids to counts, like {"iron_pickaxe": 1}')
        knowledge = self.agent.knowledge
        if knowledge is None:
            raise ValueError("the game data is not loaded, so there is nothing to plan from; use plan_materials "
                             "and the skills directly")
        from ..knowledge import plan_items

        episode = self.episode
        carried = state.counts(self.agent.observe())
        item_plan = plan_items(items, carried, knowledge)
        parent = episode.plan.find(str(step)) if step and episode.plan.steps else None
        if item_plan.empty():
            return Outcome("You already carry everything for that.")
        nodes = [PlanStep(s.text(), s.done_when()) for s in item_plan.steps]
        if parent is not None:
            parent.children, parent.kind = nodes, "all"
            parent.done = parent.failed = False
        elif not episode.plan.steps:
            wanted = ", ".join(f"{n} {item}" for item, n in item_plan.wanted.items())
            episode.plan = Plan([PlanStep(f"get {wanted}", item_plan.goal_check(), kind="all", children=nodes)],
                                item_plan.goal_check())
            episode.log.write("plan", steps=[s.to_dict() for s in episode.plan.steps],
                              goal_check=episode.plan.goal_check, source="run_plan")
        lines = [f"Plan for {', '.join(f'{n} {i}' for i, n in item_plan.wanted.items())}: "
                 f"{len(item_plan.steps)} steps, about {max(1, round(item_plan.seconds() / 60))} minutes."]
        if item_plan.unknown:
            lines.append("Left out, no recipe or source known: " + ", ".join(item_plan.unknown) + ".")
        total = len(item_plan.steps)
        for i, (plan_step, node) in enumerate(zip(item_plan.steps, nodes), 1):
            pattern = plan_step.pattern()
            have = state.count_matching(state.counts(self.agent.observe()), pattern)
            if have >= plan_step.have:
                node.finish()
                lines.append(f"{i}. {plan_step.text()}: already have {have}")
                continue
            failure = self._run_plan_step(plan_step, lines, i)
            have = state.count_matching(state.counts(self.agent.observe()), pattern)
            if failure is None and have < plan_step.have:
                failure = f"ended with {have} of {plan_step.have} {pattern}"
            if failure is not None:
                node.fail(failure)
                left = "; ".join(s.text() for s in item_plan.steps[i - 1:][:6])
                lines.append(f"Stopped at step {i} of {total}: {failure}")
                lines.append(f"Left to do: {left}{' ...' if total - i >= 6 else ''}.")
                known = self._known_places([state.full(t) for t in plan_step.targets]) \
                    if plan_step.how == "mine" else []
                if known:
                    lines.append("Seen before: " + "; ".join(known) + ". Go there, then call run_plan again.")
                lines.append("Deal with what stopped it, then call run_plan again with the same items: it "
                             "carries on from what you hold.")
                return Outcome("\n".join(lines), is_error=True, skill_failed=True)
            node.finish()
            if self.agent.interrupted(episode.goal):
                lines.append(f"Stopped after step {i} of {total}: the goal changed.")
                return Outcome("\n".join(lines), is_error=True, skill_failed=True)
            self.agent.between_steps(episode)
        lines.append("All steps done. Inventory: " + state.inventory_line(self.agent.observe()))
        return Outcome("\n".join(lines))

    def _run_plan_step(self, plan_step, lines: list[str], number: int) -> Optional[str]:
        """Runs one step of an item plan. Returns what went wrong, or None."""
        pattern = plan_step.pattern()
        calls = plan_step.calls()
        tries = _GATHER_TRIES if plan_step.how in ("mine", "kill") else 1
        if plan_step.how == "kill":
            tries = plan_step.units + 2
        for attempt in range(tries):
            for call in calls:
                args = dict(call["args"])
                if plan_step.how == "mine" and attempt:
                    have = state.count_matching(state.counts(self.agent.observe()), pattern)
                    each = max(0.05, plan_step.count / max(1, plan_step.units))
                    args["count"] = max(1, math.ceil((plan_step.have - have) / each))
                outcome = self._skill(call["tool"], args)
                first = outcome.content.splitlines()[0] if outcome.content else ""
                lines.append(f"{number}. {call['tool']} {_short_json(args, 120)}: {first}")
                if outcome.skill_failed:
                    return first
            if state.count_matching(state.counts(self.agent.observe()), pattern) >= plan_step.have:
                return None
        return None

    def _guide(self, topic: str) -> Outcome:
        return Outcome(playbooks.get(topic))

    # Ending -----------------------------------------------------------------------------

    def _goal_done(self, summary: str) -> Outcome:
        obs = self.agent.observe()
        check = self.episode.plan.goal_check
        if check is not None:
            missing = critic.unmet(check, obs, self.agent.world(self.episode))
            if missing:
                return Outcome("Not done yet: " + "; ".join(missing) + ". Keep going, or fix goal_check with "
                               "set_plan if it was wrong.", is_error=True)
        return Outcome("Goal accepted.", end="success", summary=summary, verified=check is not None)

    def _goal_failed(self, reason: str) -> Outcome:
        return Outcome("Stopped working on the goal.", end="failed", summary=reason)


def _short_json(data: Any, limit: int = 400) -> str:
    text = json.dumps(data, separators=(",", ":"))
    return text if len(text) <= limit else text[:limit] + "..."
