"""Guides for big and loosely worded goals: how to turn them into phases the game can check.

"Build me a base", "get ready for the Nether" or "set up a food supply" do not say what done
looks like. Each guide here gives a concrete reading of such a goal, the phases to work through
in order with a checkable end for each, and the traps. The guides that match a goal's wording
are put in the first message; the model can read any of them with the ``guide`` tool.
"""

from __future__ import annotations

import re

#: topic -> (words that suggest it, the guide).
GUIDES: dict[str, tuple[tuple[str, ...], str]] = {
    "base": (("base", "home", "settle", "house", "live", "camp", "shelter", "village", "fort", "castle", "hut",
              "cabin", "build"), """\
Building a home or base. Done means the structures stand (condition built) and you are at the door.
1. Decide what to build: list_structures shows the ready-made kinds. For something of your own \
(a house with a wing, a barn, a keep), design_guide and check_design let you design it in a few lines. A first home is house (7x7); a full \
base is base, built one part at a time (house, storage, tower, farm, pen, wall). Use shelter only for a first night.
2. plan_structure(kind, ...) gives the size and every item it takes, minus what you carry, as a gather and \
craft list. Do this before gathering anything. Leave style out: it picks the theme that suits the biome and \
the wood growing here, and tells you which (styles is the style book, if you want another).
3. Pick the place: find_site(width, depth) lists flat, open ground nearby with the origin to use. Prefer a \
site with little work and no water. remember it as base.
4. Gather and craft the list in bulk: logs first, then stone, then sand for glass, wool for the bed. For a \
big build drop the materials in a chest at the site between trips (store), and take them back before building.
5. build_structure(kind, x, y, z, facing). It clears the site (trees, plants, ground above floor level), stands \
the walls on a stone footing that follows the ground, places everything the right way round, and takes its own \
scaffolding down. If it stops short for want of items, fetch them and call it again: blocks already placed are \
skipped. If it says blocks were left out or the build is stuck, repeating the call will not help: fill the gaps \
by hand or deal with what is in the way.
6. Give the house a setting: plan_structure(kind="yard", width, depth) then build_structure(kind="yard") with \
the house's own x y z, width and depth: a path to the door, a fence with a gate, lamp posts, and the flowers \
you carry planted along the path. Fences take planks and sticks; the lamp posts take 12 logs and 6 torches.
7. Finish: walk in through the door (goto opens and shuts doors), sleep in the bed to set the respawn point, \
store what you do not need.
Plan steps to use: have the materials (condition have), then built, then near the entrance."""),
    "iron": (("iron", "armor", "armour", "gear", "equip", "tools", "pickaxe", "sword"), """\
Getting iron tools and armour. Done means wearing or carrying them (conditions wearing, have).
Shortest way: run_plan(items={the pieces}) does all of the below by itself and stops only where it \
cannot find something, usually the iron ore. Then get to ore (recall, a cave, or step 3) and call it again.
1. Wood: 8 logs, planks, sticks, a crafting table, a wooden pickaxe.
2. Stone: 20 cobblestone, a stone pickaxe, a furnace. Coal if any is in sight (else charcoal from logs).
3. Iron: plan_materials tells you how much raw_iron the pieces take (full armour and a pickaxe: 27). Look for \
ore in sight first (find_blocks #minecraft:iron_ores, radius 48); else go down to about y=16 with \
goto(y=16, allow_break=true) and mine with explore so it tunnels.
4. Smelt it all in one call, craft the pieces, equip(armor=true).
5. Come back up with goto(y=<surface y>) which climbs, digs stairs or pillars as needed."""),
    "diamonds": (("diamond", "deep", "mine", "mining", "ore", "redstone", "gold", "lapis", "obsidian"), """\
Mining deep (diamonds, redstone, gold). Done means carrying them (condition have).
1. Go prepared: an iron pickaxe (diamond and redstone ore need one), a spare pickaxe, a stack of cobblestone \
or dirt for pillaring, 16 torches, food, and iron armour if you can. A bucket of water if you have one.
2. remember where you start as a landmark, with the surface height.
3. Go down to y=-54 with goto(y=-54, allow_break=true): it digs a safe shaft or stairs and never opens onto lava.
4. mine(block=[diamond_ore, deepslate_diamond_ore], count, explore=8): it tunnels and takes what the tunnel walls show.
5. If health is low or night mobs are about underground, eat and move on; do not fight in the dark.
6. Come back with goto(y=<surface y>), then goto the landmark."""),
    "food": (("food", "farm", "crop", "wheat", "hungry", "starv", "eat", "animal", "breed", "bread", "cook",
              "meat"), """\
A food supply. Done can be a count of cooked food carried (have), or a fenced, planted field (built).
Quick food: fight cows, pigs, sheep or chickens nearby, then smelt the meat in one batch.
Lasting food: 1. Craft a hoe. Get seeds by mining short_grass (a dozen). 2. Find or make a spot next to \
water. 3. build_structure(kind="farm_plot") fences it so nothing tramples it. 4. farm(till=true) hoes and \
plants. 5. Crops take one to three days: set a job with the player (/minegpt job every day at dawn: harvest \
the farm and store the wheat) rather than waiting. Three wheat make bread."""),
    "nether": (("nether", "portal", "obsidian", "blaze", "fortress"), """\
Getting ready for the Nether. Done means carrying the kit (have) and a lit portal stands.
Kit: iron armour, iron sword and pickaxe, a shield, 32 cooked food, a stack of cobblestone, a bed is useless \
there (it explodes). A portal needs 10 obsidian (mined with a diamond pickaxe) in a 4 wide, 5 high frame, lit \
with flint_and_steel (1 iron_ingot, 1 flint from gravel).
Phases: iron gear; diamonds for the pickaxe (3); find lava and water to make or mine obsidian; build the \
frame with build (blocks list, obsidian); light it. Say in goal_failed which phase stopped you if one cannot be done."""),
    "explore": (("explore", "find", "look for", "search", "locate", "scout", "map", "travel", "go to"), """\
Exploring or finding something. Done means standing near it (near) or seeing it (sees), with it remembered.
1. look_around and find_blocks (radius up to 64) first: it may be in sight.
2. Otherwise walk out in a straight line in legs of 60 to 100 blocks with goto(x, z), look_around at the end of \
each leg, and turn a quarter after two legs so the search spirals. Hills give longer views.
3. remember what you pass that is worth knowing (ore, villages, water, dangers), and where you started.
4. Before dusk, head back or build_structure(kind="shelter") and sleep."""),
    "survive": (("survive", "night", "safe", "defend", "protect", "mob", "monster", "danger"), """\
Staying alive. Done is usually a time passed with health intact, or defences standing (built).
First night with nothing: build_structure(kind="shelter") from dirt (70 blocks, a door from 6 planks), go in, \
and wait or sleep. Better: a house with a bed. Light the area with torches so nothing spawns close. \
Keep food above 17 so health comes back. Wear armour. A wall (build_structure kind=wall) round the base keeps \
mobs out at night; goto shuts doors behind it."""),
    "storage": (("store", "storage", "chest", "sort", "organi", "tidy", "stash", "collect all"), """\
Storage. Done means the items are in chests (store reports contents) and the chests are remembered.
build_structure(kind="storage") gives a shed lined with chests; or craft a chest (8 planks) and place it. \
store with no items empties everything but tools, armour and food into the nearest chest; store(items=[...]) \
puts only some. One kind of thing per chest keeps take simple. Chest contents are remembered across goals."""),
    "big jobs": (("everything", "all the", "fully", "complete", "automat", "improve", "upgrade", "best",
                  "beat", "win", "prepare", "ready"), """\
Any large or loosely worded goal.
1. Say in one line what you take the goal to mean, as things the game can check: items carried or worn, \
structures built, a place reached, blocks in sight. Put that in goal_check.
2. Split it into phases, each with its own done_when, and give a big phase substeps. A phase that is \
only "have these items" is one run_plan call. For the rest, think in phases of five to fifteen tool calls, each with its own done_when, ordered so each phase \
makes the next one easier (tools before mining, shelter before night, storage before bulk gathering).
3. Before each gathering phase use plan_materials or plan_structure, so one trip brings everything.
4. Work one phase at a time. When a phase is done the game checks it off and gives you more turns. If a \
phase fails twice, change the approach or the order, and set_plan again; do not push on with a broken plan.
5. Use remember for anything a later phase or a later goal needs: where the base, the chest and the mine are."""),
}


def names() -> list[str]:
    return list(GUIDES)


def get(topic: str) -> str:
    key = topic.strip().lower()
    if key in GUIDES:
        return GUIDES[key][1]
    close = relevant(topic, limit=1)
    if close:
        return GUIDES[close[0]][1]
    raise ValueError(f"no guide on {topic!r}; guides: {', '.join(GUIDES)}")


def relevant(goal: str, limit: int = 2) -> list[str]:
    """The guides whose trigger words appear in the goal, best match first."""
    text = goal.lower()
    words = set(re.findall(r"[a-z]+", text))
    scored = []
    for topic, (triggers, _) in GUIDES.items():
        score = sum(1 for t in triggers if (t in words) or (len(t) > 4 and t in text))
        if score:
            scored.append((score, topic))
    scored.sort(key=lambda s: -s[0])
    return [topic for _, topic in scored[:limit]]
