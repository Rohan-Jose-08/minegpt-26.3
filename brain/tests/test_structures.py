import pytest

from minegpt_brain.agent import Agent, AgentConfig, blueprints, critic, playbooks, requirements, structures, style
from minegpt_brain.bridge import Bridge
from minegpt_brain.observation import Observation
from fake_mod import FakeMod, make_obs
from test_agent import ScriptedBackend


# Structures --------------------------------------------------------------------------------

def cells(made):
    return {(x, y, z): (item, facing) for x, y, z, item, facing in made.blocks}


@pytest.mark.parametrize("kind", list(structures.KINDS))
def test_every_kind_generates_in_every_direction(kind):
    sizes = set()
    for facing in structures.FACINGS:
        made = structures.generate(kind, facing)
        solid = [b for b in made.blocks if b[3] != structures.AIR]
        assert solid and min(b[0] for b in solid) == 0 and min(b[2] for b in solid) == 0
        assert all(b[3].startswith("minecraft:") for b in made.blocks)
        # Nothing is placed twice.
        assert len({b[:3] for b in solid}) == len(solid)
        sizes.add(tuple(sorted(made.footprint())))
        assert sum(made.materials().values()) == len(solid)
    assert len(sizes) == 1  # turning never changes the footprint, only swaps its sides


def test_house_has_walls_a_door_a_roof_and_furniture():
    made = structures.generate("house", width=7, depth=7, style="spruce", detail=False)
    at = cells(made)
    assert at[(0, 0, 0)][0] == "minecraft:spruce_log"          # corner post
    assert at[(1, 0, 0)][0] == "minecraft:spruce_planks"
    assert at[(3, 0, 6)] == ("minecraft:spruce_door", "north")  # placed from outside, looking in
    assert (3, 1, 6) not in at                                   # room for the top half of the door
    assert at[(3, 0, 7)][0] == structures.AIR                    # the doorstep is dug clear
    assert at[(2, 1, 0)][0] == "minecraft:glass_pane"
    assert at[(3, -1, 3)][0] == "minecraft:spruce_planks"       # floor, set into the ground
    # The roof: stairs rising towards the ridge from both eaves, a slab ridge on top.
    assert at[(3, 3, 0)] == ("minecraft:spruce_stairs", "south")
    assert at[(3, 3, 6)] == ("minecraft:spruce_stairs", "north")
    assert at[(3, 6, 3)] == ("minecraft:spruce_slab", "bottom")
    assert at[(1, 0, 2)] == ("minecraft:white_bed", "north")
    assert any(item == "minecraft:chest" for item, _ in at.values())
    assert [p.name for p in made.parts] == ["walls", "inside", "roof"]
    assert made.entrance == (3, 0, 7)


def test_every_roof_block_rests_on_something():
    made = structures.generate("house", width=9, depth=6)
    at = cells(made)
    solid = {pos for pos, (item, _) in at.items() if item != structures.AIR}
    for (x, y, z) in solid:
        if y >= 4:  # above the ceiling, which spans the room from wall to wall
            assert (x, y - 1, z) in solid, f"roof block at {(x, y, z)} hangs in the air"


def test_turning_moves_the_door_and_the_facings():
    east = structures.generate("house", "east", width=7, depth=5, detail=False)
    at = cells(east)
    doors = [(pos, facing) for pos, (item, facing) in at.items() if item.endswith("_door")]
    assert len(doors) == 1
    (x, y, z), facing = doors[0]
    assert x == 4 and facing == "west"          # in the east wall, placed looking west
    assert east.footprint() == (5, 7)
    assert east.entrance == (5, 0, 3)
    north = cells(structures.generate("house", "north"))
    # The detailed house turns too: its eaves stay a block outside the walls on every side.
    detailed = structures.generate("house", "east", width=7, depth=5)
    assert detailed.footprint() == (7, 9)
    assert ("minecraft:oak_door", "south") in north.values()


def test_tower_ladder_runs_up_the_inside_of_a_wall():
    made = structures.generate("tower", size=5, height=8)
    at = cells(made)
    for y in range(0, 9):
        assert at[(2, y, 1)] == ("minecraft:ladder", "on:north")
        assert at[(2, y, 0)][0] == "minecraft:cobblestone"     # the wall each rung hangs on
    assert at[(1, 8, 1)][0] == "minecraft:cobblestone"         # lookout floor
    turned = cells(structures.generate("tower", "west"))
    assert any(facing == "on:east" for item, facing in turned.values() if item == "minecraft:ladder")


def test_a_detailed_house_has_bays_eaves_and_a_small_palette():
    made = structures.generate("house", width=9, depth=7, style="spruce")
    at = cells(made)
    # The origin is the corner of the eaves, so the walls start one block in.
    assert at[(1, 0, 1)][0] == "minecraft:spruce_log"
    assert at[(5, 0, 1)][0] == "minecraft:spruce_log"            # a post in the middle of the long wall
    assert at[(3, 1, 1)][0] == "minecraft:glass_pane"            # a window centred in the bay
    # No stretch of plain wall is longer than three blocks.
    for z in (1, 7):
        run = 0
        for x in range(1, 10):
            run = run + 1 if at.get((x, 2, z), ("", None))[0] == "minecraft:spruce_planks" else 0
            assert run <= 3
    # Eaves: a row of stairs outside each long wall, one below the wall top, against it.
    assert at[(4, 2, 0)] == ("minecraft:spruce_stairs", "south")
    assert at[(4, 2, 8)] == ("minecraft:spruce_stairs", "north")
    assert at[(4, 2, 1)][0] in ("minecraft:spruce_planks", "minecraft:spruce_log")
    assert (0, 2, 0) not in at                                   # eave corners have nothing to hang on
    assert at[(0, 3, 1)][0] == "minecraft:spruce_stairs"         # the roof runs on past the end wall
    # Torches either side of the door, outside.
    door = next(pos for pos, (item, _) in at.items() if item.endswith("_door"))
    assert at[(door[0] - 1, 0, door[2] + 1)] == ("minecraft:torch", "on:down")
    structural = {item for item, _ in at.values()} - {structures.AIR, "minecraft:torch", "minecraft:glass_pane",
                                                      "minecraft:spruce_door", "minecraft:white_bed",
                                                      "minecraft:crafting_table", "minecraft:furnace",
                                                      "minecraft:chest"}
    assert len(structural) <= 5, structural
    assert made.footing == "minecraft:cobblestone" and made.parts[0].footing and not made.parts[1].footing


@pytest.mark.parametrize("kind,options", [("house", {}), ("house", {"width": 12, "depth": 9, "height": 5}),
                                          ("storage", {}), ("tower", {}), ("wall", {"width": 13, "depth": 9})])
def test_everything_has_something_to_be_placed_against(kind, options):
    made = structures.generate(kind, **options)
    at = cells(made)
    full_blocks = {pos for pos, (item, _) in at.items()
                   if item != structures.AIR and not item.endswith(("_stairs", "_slab", "torch", "glass_pane",
                                                                    "_door", "ladder", "_bed"))}
    lowest = min(y for (_, y, _), (item, _) in at.items() if item != structures.AIR)
    for (x, y, z), (item, _) in at.items():
        if item == structures.AIR or y <= max(lowest, 0):
            continue  # the ground holds these up
        around = [(x + 1, y, z), (x - 1, y, z), (x, y, z + 1), (x, y, z - 1), (x, y - 1, z), (x, y + 1, z)]
        assert any(n in full_blocks for n in around), f"{item} at {(x, y, z)} has no solid block beside it"


def test_mixed_texture_blends_stone_walls_the_same_way_every_time():
    plain = structures.generate("tower", texture="plain").materials()
    first = structures.generate("tower", texture="mixed")
    again = structures.generate("tower", texture="mixed")
    assert first.blocks == again.blocks                          # so a build can be resumed
    blend = first.materials()
    assert {"minecraft:cobblestone", "minecraft:andesite", "minecraft:stone"} <= set(blend)
    assert sum(blend.values()) == sum(plain.values())
    assert blend["minecraft:cobblestone"] > blend["minecraft:andesite"]
    # Wood has no blend: only the stone footing of a wooden house changes.
    wooden = structures.generate("house", texture="mixed").materials()
    assert wooden["minecraft:oak_planks"] == structures.generate("house").materials()["minecraft:oak_planks"]
    with pytest.raises(ValueError, match="texture"):
        structures.generate("house", texture="shiny")


def test_base_is_built_in_parts_that_do_not_overlap():
    made = structures.generate("base")
    assert [p.name for p in made.parts] == ["house", "storage", "tower", "farm", "pen", "wall"]
    seen = {}
    for part in made.parts:
        for x, y, z, item, _ in part.blocks:
            if item != structures.AIR:
                assert (x, y, z) not in seen, f"{part.name} and {seen[(x, y, z)]} both use {(x, y, z)}"
                seen[(x, y, z)] = part.name
    assert made.footprint() == (27, 23)


def test_options_are_checked():
    with pytest.raises(ValueError, match="unknown structure"):
        structures.generate("pyramid")
    with pytest.raises(ValueError, match="width must be"):
        structures.generate("house", width=3)
    with pytest.raises(ValueError, match="does not take"):
        structures.generate("pen", roof="flat")
    with pytest.raises(ValueError, match="unknown style"):
        structures.generate("house", style="gold")
    with pytest.raises(ValueError, match="unknown material role"):
        structures.generate("house", materials={"spire": "bricks"})
    made = structures.generate("house", materials={"wall": "birch_planks"})
    assert made.materials()["minecraft:birch_planks"] > 0


def test_chunks_dig_first_and_keep_under_the_limit():
    made = structures.generate("house")
    walls = made.parts[0].blocks
    parts = structures.chunks(walls, size=60)
    assert all(b[3] == structures.AIR for b in parts[0])
    assert all(len(p) <= 60 for p in parts[1:])
    assert sorted(b for p in parts for b in p) == sorted(walls)
    lowest = [min(b[1] for b in p) for p in parts[1:]]
    assert lowest == sorted(lowest)  # bottom layers go first
    assert structures.place_at([(1, 0, 2, "minecraft:oak_door", "north"), (0, 0, 0, "minecraft:dirt", None)],
                               10, 64, 20) == [[11, 64, 22, "minecraft:oak_door", "north"],
                                               [10, 64, 20, "minecraft:dirt"]]


# Requirements --------------------------------------------------------------------------------

def test_resolves_a_recipe_tree_down_to_what_to_gather():
    plan = requirements.resolve({"stone_pickaxe": 1}, {})
    assert plan.gather == {"oak_log": 2, "cobblestone": 3}
    assert plan.tools == ["wooden_pickaxe"]          # needed to mine the cobblestone
    order = [(s.how, s.item) for s in plan.steps]
    assert order.index(("craft", "oak_planks")) < order.index(("craft", "stick")) < order.index(
        ("craft", "wooden_pickaxe")) < order.index(("craft", "stone_pickaxe"))


def test_uses_what_is_carried_first():
    carried = {"minecraft:spruce_planks": 6, "minecraft:stick": 4, "minecraft:stone_pickaxe": 1,
               "minecraft:cobblestone": 2}
    plan = requirements.resolve({"minecraft:stone_axe": 1, "furnace": 1}, carried)
    assert plan.gather == {"cobblestone": 9}
    assert plan.tools == []
    assert [(s.how, s.item, s.count) for s in plan.steps] == [("craft", "stone_axe", 1), ("craft", "furnace", 1)]
    assert requirements.resolve({"stick": 4}, carried).empty()


def test_counts_smelting_and_fuel_and_sticks_to_one_wood():
    plan = requirements.resolve({"glass_pane": 16, "iron_pickaxe": 1, "birch_door": 1}, {"minecraft:stone_pickaxe": 1})
    assert plan.gather["sand"] == 6 and plan.gather["raw_iron"] == 3
    assert plan.smelts == 9
    assert "oak_log" not in plan.gather and plan.gather["birch_log"] >= 2  # sticks come from birch too
    text = plan.describe({"minecraft:coal": 2})
    assert "smelt 6 glass" in text and "stone pickaxe or better" in text and "smelts about 16" in text


def test_unknown_items_are_listed_not_guessed():
    plan = requirements.resolve({"elytra": 1}, {})
    assert plan.unknown == ["elytra"] and "recipe tool" in plan.describe()


def test_a_house_shopping_list_adds_up():
    made = structures.generate("house")
    plan = requirements.resolve(made.materials(), {})
    assert not plan.unknown
    assert set(plan.gather) >= {"oak_log", "cobblestone", "sand", "white_wool"}
    assert plan.tools == ["wooden_pickaxe"]


# Guides ----------------------------------------------------------------------------------------

def test_guides_match_loosely_worded_goals():
    assert playbooks.relevant("build me a nice base to live in")[0] == "base"
    assert "diamonds" in playbooks.relevant("go mining for diamonds")
    assert "nether" in playbooks.relevant("get ready for the Nether")
    assert playbooks.relevant("xyzzy") == []
    assert "find_site" in playbooks.get("base")
    assert playbooks.get("houses").startswith("Building a home")
    with pytest.raises(ValueError):
        playbooks.get("qwerty")


# Conditions ------------------------------------------------------------------------------------

def test_built_sees_and_height_conditions():
    obs = Observation(make_obs(y=12.0))
    world = critic.World(find=lambda block, radius, limit: [{"block": block}] if block == "minecraft:chest" else [],
                         built={"house", "base:wall"})
    assert critic.unmet({"built": ["house", "base:wall"]}, obs, world) == []
    assert "tower not built" in critic.unmet({"built": ["tower"]}, obs, world)[0]
    assert critic.unmet({"built": ["house"]}, obs) != []           # nothing to check against
    assert critic.unmet({"sees": {"block": "chest"}}, obs, world) == []
    assert critic.unmet({"sees": {"block": "chest", "count": 2}}, obs, world) == ["see 1 of 2 chest within 16 blocks"]
    assert critic.unmet({"sees": {"block": "bed"}}, obs, world) == ["see 0 of 1 bed within 16 blocks"]
    assert critic.unmet({"height": {"max": 16}}, obs, world) == []
    assert critic.unmet({"height": {"min": 60}}, obs, world) == ["at y=12, not up at 60 or above"]
    for bad in ({"built": "house"}, {"sees": {"count": 2}}, {"height": {"below": 3}}, {"height": {}}):
        with pytest.raises(ValueError):
            critic.validate(bad)


# Tools -----------------------------------------------------------------------------------------

def building_world(mod, name, args):
    """A build skill that puts every block down and uses up the items."""
    if name == "build":
        placed = 0
        for entry in args["blocks"]:
            x, y, z, item = entry[:4]
            if item == "minecraft:air" or mod.world_blocks.get((x, y, z)) == item:
                continue
            if not any(s["id"] == item and s["count"] > 0 for s in mod.slots):
                return False, f"placed {placed}; 1 could not be placed", {
                    "placed": placed, "present": 0, "failed": 1, "failures": [f"{item} at {x} {y} {z}: none left"]}
            mod.take(item, 1)
            mod.world_blocks[(x, y, z)] = item
            placed += 1
        return True, f"built it: placed {placed}", {"placed": placed, "present": 0, "failed": 0, "failures": []}
    return True, "done", {}


@pytest.fixture
def setup(tmp_path):
    mod = FakeMod()
    mod.skill_handler = building_world
    mod.goal = "build a pen"
    bridge = Bridge.connect(port=mod.port, timeout=5)

    def make(script, **config):
        backend = ScriptedBackend(script)
        return Agent(bridge, backend, AgentConfig(data_dir=tmp_path, poll=0.05, jobs=False, **config)), backend

    yield mod, make
    bridge.close()


def test_plans_finds_a_site_and_builds_a_structure(setup):
    mod, make = setup
    mod.sites = [{"x": 10, "y": 64, "z": 20, "work": 3, "trees": 0, "water": 0, "distance": 12}]
    mod.give("oak_fence", 30)
    mod.give("oak_fence_gate", 1)
    agent, backend = make([
        [("set_plan", {"steps": [{"step": "build the pen", "done_when": {"built": ["pen"]}}],
                       "goal_check": {"built": ["pen"]}})],
        [("list_structures", {}), ("plan_structure", {"kind": "pen", "width": 5, "depth": 5}),
         ("find_site", {"width": 5, "depth": 5})],
        [("goal_done", {"summary": "not yet"})],
        [("build_structure", {"kind": "pen", "width": 5, "depth": 5, "x": 10, "y": 64, "z": 20})],
        [("goal_done", {"summary": "pen built"})],
    ])
    result = agent.run_goal("build a pen")
    conversation = backend.conversations[0]
    looked = [r.content for r in conversation.results(1)[1]]
    assert "house:" in looked[0] and "Styles:" in looked[0]
    assert "Needs: 15 oak_fence, 1 oak_fence_gate" in looked[1] and "You carry all of it" in looked[1]
    assert "10 64 20" in looked[2] and "3 blocks to level" in looked[2]
    assert "pen not built yet" in conversation.results(2)[1][0].content
    built = conversation.results(3)[1][0].content
    assert "fence: placed 16" in built and "Finished and remembered" in built
    assert result.outcome == "success" and result.verified
    assert mod.world_blocks[(12, 64, 24)] == "minecraft:oak_fence_gate"
    build_calls = [r for r in mod.received if r["op"] == "skill" and r["name"] == "build"]
    gate = [b for b in build_calls[0]["args"]["blocks"] if b[3].endswith("gate")][0]
    assert gate == [12, 64, 24, "minecraft:oak_fence_gate", "north"]
    assert build_calls[0]["args"]["clear"] is True


def test_build_structure_refuses_without_the_items_and_resumes(setup):
    mod, make = setup
    mod.give("oak_fence", 10)
    agent, backend = make([
        [("build_structure", {"kind": "pen", "width": 5, "depth": 5, "x": 0, "y": 64, "z": 0})],
        [("build_structure", {"kind": "pen", "width": 5, "depth": 5, "x": 0, "y": 64, "z": 0, "partial": True})],
        [("build_structure", {"kind": "pen", "width": 5, "depth": 5, "x": 0, "y": 64, "z": 0})],
        [("goal_failed", {"reason": "test over"})],
    ])

    def restock(conversation):
        mod.give("oak_fence", 5)
        mod.give("oak_fence_gate", 1)
        return [("build_structure", {"kind": "pen", "width": 5, "depth": 5, "x": 0, "y": 64, "z": 0})]

    backend.script[2] = restock
    agent.run_goal("build a pen")
    conversation = backend.conversations[0]
    refused = conversation.results(0)[1][0]
    assert refused.is_error and "missing 5 oak_fence, 1 oak_fence_gate" in refused.content
    assert not [r for r in mod.received if r["op"] == "skill"][:0]
    partial = conversation.results(1)[1][0]
    assert partial.is_error and "placed 10" in partial.content and "Not finished" in partial.content
    # Only what is not standing yet is counted the second time round.
    resumed = conversation.results(2)[1][0]
    assert not resumed.is_error and "Finished" in resumed.content
    assert sum(1 for v in mod.world_blocks.values() if v.endswith("fence")) == 15


def test_plan_materials_and_guide_tools(setup):
    mod, make = setup
    mod.slots[:] = [{"slot": 0, "id": "minecraft:oak_log", "count": 1}]
    agent, backend = make([
        [("plan_materials", {"items": {"wooden_pickaxe": 1}}), ("guide", {"topic": "base"}),
         ("plan_structure", {"kind": "house", "style": "birch"}), ("plan_structure", {"kind": "castle"})],
        [("goal_failed", {"reason": "test over"})],
    ])
    agent.goal = "build me a home"
    agent.run_goal("build me a home")
    conversation = backend.conversations[0]
    first = conversation.messages[0][1]
    assert "Guide that looks relevant (base)" in first and "more for every plan step" in first
    out = [r.content for r in conversation.results(0)[1]]
    assert "2 logs (any wood)" in out[0] and "craft 1 wooden_pickaxe (crafting table)" in out[0]
    assert out[1].startswith("Building a home")
    assert "Still missing" in out[2] and "birch_log" in out[2] and "find_site(width=9, depth=9)" in out[2]
    assert "unknown structure" in out[3]


def test_verified_plan_steps_earn_more_turns(setup):
    mod, make = setup

    def give_logs(conversation):
        mod.give("diamond", 2)
        return [("look_around", {})]

    agent, backend = make([
        [("set_plan", {"steps": [{"step": "get diamonds", "done_when": {"have": {"diamond": 2}}},
                                 {"step": "more", "done_when": {"have": {"diamond": 50}}}]})],
        give_logs,
        [("look_around", {})],
        [("look_around", {})],
        [("look_around", {})],
        [("look_around", {})],
    ], max_turns=3, turns_per_step=2, turn_cap=5)
    agent.goal = "get wood"
    result = agent.run_goal("get wood")
    assert result.outcome == "turn_limit" and result.turns == 5
    note = backend.conversations[0].results(1)[2]
    assert "2 more turns granted" in note


# Design --------------------------------------------------------------------------------------------

BOX = {"key": {"c": "cobblestone"},
       "layers": [["ccccccc"] * 7] + [["ccccccc"] + ["c.....c"] * 5 + ["ccccccc"]] * 3 + [["ccccccc"] * 7]}


def test_a_plain_box_gets_design_notes():
    notes = style.review(blueprints.expand(BOX["layers"], BOX["key"]))
    text = " ".join(notes)
    assert "5 blocks of plain cobblestone" in text and "Everything is cobblestone" in text
    assert "one flat layer" in text and "No windows" in text and "No door" in text


def test_the_ready_made_buildings_pass_their_own_rules():
    for kind, options in (("house", {}), ("storage", {}), ("tower", {}), ("wall", {}),
                          ("house", {"width": 13, "depth": 9, "height": 5, "style": "stone_brick"}),
                          ("house", {"style": "sandstone", "width": 11}), ("house", {"style": "spruce", "facing": "west"})):
        made = structures.generate(kind, **options)
        assert style.review([b for b in made.blocks if b[3] != structures.AIR]) == [], (kind, options)
    plain = structures.generate("house", detail=False)
    assert any("overhang" in n for n in style.review([b for b in plain.blocks if b[3] != structures.AIR]))


def test_small_and_flat_things_are_left_alone():
    assert style.review([(x, 0, z, "minecraft:oak_planks") for x in range(9) for z in range(9)]) == []
    assert style.review([(0, y, 0, "minecraft:cobblestone") for y in range(8)]) == []


def test_too_many_materials_are_counted_by_family():
    blocks = blueprints.expand(BOX["layers"], BOX["key"])
    kinds = ["oak_planks", "oak_stairs", "oak_log", "stripped_oak_log", "cobblestone", "mossy_cobblestone"]
    few = [(x, y, z, "minecraft:" + kinds[(x + y + z) % len(kinds)]) for x, y, z, _ in blocks]
    assert not any("different materials" in n for n in style.review(few))      # two families: oak and cobblestone
    many = ["oak_planks", "bricks", "diorite", "sandstone", "prismarine", "netherrack", "quartz_block"]
    lots = [(x, y, z, "minecraft:" + many[(x + y + z) % len(many)]) for x, y, z, _ in blocks]
    assert any("7 different materials" in n for n in style.review(lots))


def test_drawings_come_back_with_design_notes_and_houses_stand_on_footings(setup):
    mod, make = setup
    builds = []

    def world(mod, name, args):
        if name == "build":
            builds.append(args)
            return True, "built it", {"placed": len(args["blocks"]), "present": 0, "failed": 0, "failures": [],
                                      "footings": 7 if "foundation" in args else 0}
        return True, "done", {}

    mod.skill_handler = world
    made = structures.generate("house")
    mod.slots[:] = [{"slot": i, "id": item, "count": n} for i, (item, n) in enumerate(made.materials().items())]
    mod.slots.append({"slot": 30, "id": "minecraft:cobblestone", "count": 400})
    agent, backend = make([
        [("save_blueprint", {"name": "box", "description": "a box", **BOX}),
         ("build", {"x": 0, "y": 64, "z": 0, "foundation": "cobblestone", **BOX})],
        [("build_structure", {"kind": "house", "x": 20, "y": 64, "z": 20})],
        [("goal_failed", {"reason": "test over"})],
    ])
    agent.goal = "build"
    agent.run_goal("build")
    conversation = backend.conversations[0]
    saved, drawn = [r.content for r in conversation.results(0)[1]]
    assert "Design notes" in saved and "No windows" in saved
    assert "Design notes" in drawn and builds[0]["foundation"] == "minecraft:cobblestone"
    house = conversation.results(1)[1][0].content
    assert "footings down to the ground" in house
    with_footing = [b for b in builds[1:] if "foundation" in b]
    # Only the call that lays the bottom of the walls asks for footings: the roof must not grow legs.
    assert len(with_footing) == 1 and with_footing[0]["foundation"] == "minecraft:cobblestone"
    assert min(b[1] for b in with_footing[0]["blocks"]) == 63


# Looking at a build -----------------------------------------------------------------------------

class SeeingBackend(ScriptedBackend):
    vision = True


def test_review_build_walks_round_and_shows_pictures(setup, tmp_path):
    mod, make = setup
    walks = []

    def world(mod, name, args):
        if name == "goto":
            walks.append(args)
            mod.pos = (args["x"] + 0.5, 64.0, args["z"] + 0.5)
        return True, "arrived", {}

    mod.skill_handler = world
    review = {"x": 10, "y": 64, "z": 10, "width": 9, "depth": 9, "height": 8}
    agent, _ = make([])
    backend = SeeingBackend([[("review_build", review)], [("review_build", {**review, "views": 1})],
                             [("review_build", review)], [("goal_failed", {"reason": "test over"})]])
    agent.backend = backend
    agent.goal = "build"
    agent.run_goal("build")

    assert "review_build" in [t.name for t in backend.tools]
    conversation = backend.conversations[0]
    first = conversation.results(0)[1][0]
    assert not first.is_error and len(first.images) == 3
    assert first.images[0].data.startswith(b"\xff\xd8") and first.images[0].format == "jpeg"
    assert "View 1: from the south-east corner" in first.images[0].caption
    assert "at most five concrete edits" in first.content and "1 more time" in first.content
    # It stood well outside the box each time, on different sides, and did not dig to get there.
    assert len(walks) == 4 and all(w["allowBreak"] is False for w in walks)   # three views, then one
    assert len({(w["x"], w["z"]) for w in walks[:3]}) == 3
    assert all(w["x"] > 21 or w["x"] < 7 or w["z"] > 21 or w["z"] < 7 for w in walks)
    assert [r["op"] for r in mod.received].count("screenshot") == 4
    second = conversation.results(1)[1][0]
    assert len(second.images) == 1 and "last look" in second.content
    third = conversation.results(2)[1][0]
    assert third.is_error and "looked at 2 times already" in third.content and not third.images
    assert len(list((tmp_path / "screenshots").iterdir())) >= 1


def test_review_build_is_only_offered_to_models_that_read_pictures(setup):
    mod, make = setup
    agent, backend = make([[("review_build", {"x": 0, "y": 64, "z": 0, "width": 5, "depth": 5})],
                           [("goal_failed", {"reason": "test over"})]])
    agent.goal = "build"
    agent.run_goal("build")
    assert "review_build" not in [t.name for t in backend.tools]
    refused = backend.conversations[0].results(0)[1][0]
    assert refused.is_error and "cannot" in refused.content


def test_review_build_says_so_when_the_mod_is_too_old(setup):
    mod, make = setup
    mod.screenshots = False

    def world(mod, name, args):
        if name == "goto":
            mod.pos = (args["x"] + 0.5, 64.0, args["z"] + 0.5)
        return True, "arrived", {}

    mod.skill_handler = world
    agent, _ = make([])
    agent.backend = backend = SeeingBackend([[("review_build", {"x": 10, "y": 64, "z": 10, "width": 5, "depth": 5})],
                                             [("goal_failed", {"reason": "test over"})]])
    agent.goal = "build"
    agent.run_goal("build")
    out = backend.conversations[0].results(0)[1][0]
    assert out.is_error and "could not take a picture" in out.content


def test_claude_gets_pictures_inside_the_tool_result():
    from minegpt_brain.llm import Image, ToolResult
    from minegpt_brain.llm.claude import ClaudeConversation

    conversation = ClaudeConversation(None, "system", [])
    conversation.add_results([ToolResult("call_1", "2 pictures", images=[Image(b"abc", "jpeg", "View 1"),
                                                                           Image(b"def", "png")]),
                              ToolResult("call_2", "plain")], note="State: fine")
    first, second, note = conversation.messages[0]["content"]
    assert [part["type"] for part in first["content"]] == ["text", "text", "image", "image"]
    assert first["content"][2]["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "YWJj"}
    assert first["content"][3]["source"]["media_type"] == "image/png"
    assert second["content"] == "plain" and note == {"type": "text", "text": "State: fine"}



# Phase 16: styles, yards, site clearing, builds that stick ---------------------------------------

from minegpt_brain.agent import stylebook  # noqa: E402


def test_the_biome_picks_the_theme():
    assert stylebook.for_biome("minecraft:snowy_taiga").name == "cabin"
    assert stylebook.for_biome("minecraft:birch_forest").name == "birch_lodge"
    assert stylebook.for_biome("minecraft:old_growth_birch_forest").name == "birch_lodge"
    assert stylebook.for_biome("minecraft:dark_forest").name == "manor"
    assert stylebook.for_biome("minecraft:forest").name == "cottage"
    assert stylebook.for_biome("minecraft:desert").name == "desert_house"
    assert stylebook.for_biome("minecraft:the_void") is None and stylebook.for_biome(None) is None
    # Every theme's palette exists, and every theme builds a house.
    for theme in stylebook.THEMES.values():
        made = structures.generate("house", **stylebook.apply(theme, "house", {}))
        assert made.blocks, theme.name
        assert theme.name in stylebook.describe(theme)


def test_wood_to_hand_picks_the_theme_and_what_was_asked_for_wins():
    assert stylebook.wood_of("minecraft:stripped_spruce_log") == "spruce"
    assert stylebook.wood_of("minecraft:dark_oak_planks") == "dark_oak"
    assert stylebook.wood_of("minecraft:cobblestone") is None
    assert stylebook.for_wood({"birch": 9, "oak": 2}).name == "birch_lodge"
    assert stylebook.for_wood({"birch": 2}) is None
    lodge = stylebook.get("Birch Lodge")
    assert stylebook.apply(lodge, "house", {})["materials"]["roof"] == "cobblestone_stairs"
    # A style of one's own drops the theme's swaps; a material of one's own is kept over them.
    assert stylebook.apply(lodge, "house", {"style": "oak"}) == {"style": "oak"}
    mine = stylebook.apply(lodge, "house", {"materials": {"roof": "stone_brick_stairs"}})
    assert mine["materials"]["roof"] == "stone_brick_stairs" and mine["materials"]["ridge"] == "cobblestone_slab"
    assert stylebook.apply(stylebook.get("manor"), "house", {"height": 3})["height"] == 3
    assert "height" not in stylebook.apply(stylebook.get("manor"), "yard", {})
    with pytest.raises(ValueError):
        stylebook.get("palace")


def test_a_detailed_house_has_a_chimney_above_the_ridge():
    made = structures.generate("house", width=7, depth=7)
    at = cells(made)
    stack = sorted(pos for pos, (item, _) in at.items() if item == "minecraft:cobblestone" and pos[1] > 0)
    top = max(y for (_, y, _) in stack)
    roof_top = max(y for (_, y, _), (item, _) in at.items() if item.endswith(("_slab", "_stairs")))
    assert top > roof_top
    assert len({(x, z) for x, _, z in stack if _ > 3}) == 1          # one column, straight up
    plain = cells(structures.generate("house", width=7, depth=7, trim=False))
    assert max(y for (_, y, _) in plain) < top


@pytest.mark.parametrize("facing", list(structures.FACINGS))
@pytest.mark.parametrize("width,depth", [(7, 7), (9, 6), (11, 8)])
def test_a_yard_goes_round_its_house_and_its_path_meets_the_door(facing, width, depth):
    house = structures.generate("house", facing, width=width, depth=depth)
    yard = structures.generate("yard", facing, width=width, depth=depth)
    assert [p.name for p in yard.parts] == ["path", "fence", "lamps"]
    assert not yard.parts[0].optional and yard.parts[1].optional and yard.parts[2].optional
    ax, az = yard.anchor
    house_cells = {(x, z) for x, _, z, item, _ in house.blocks if item != structures.AIR}
    yard_cells = {(x - ax, z - az): item for x, _, z, item, _ in yard.blocks}
    assert not house_cells & set(yard_cells), "the yard runs through the house"
    # The plot reaches the same distance beyond the house on every side.
    hw, hd = house.footprint()
    xs, zs = [x for x, _ in yard_cells], [z for _, z in yard_cells]
    assert (min(xs), min(zs), max(xs), max(zs)) == (-3, -3, hw + 2, hd + 2)
    # The path starts one step out from the door and ends at the gate.
    ex, _, ez = house.entrance
    path = {(x - ax, z - az) for x, _, z, _, _ in yard.parts[0].blocks}
    assert any(abs(px - ex) + abs(pz - ez) == 1 for px, pz in path)
    gate = [(x - ax, z - az) for x, _, z, item, _ in yard.blocks if item.endswith("_gate")]
    assert len(gate) == 1 and any(abs(px - gate[0][0]) + abs(pz - gate[0][1]) <= 1 for px, pz in path)
    assert sum(1 for b in yard.blocks if b[3].endswith("torch")) == 6
    for spot in structures.flower_spots(yard):
        assert (spot[0], spot[2]) not in {(b[0], b[2]) for b in yard.blocks}


def test_a_yard_without_a_fence_and_what_does_not_clear_its_site():
    yard = structures.generate("yard", fence=False)
    assert [p.name for p in yard.parts] == ["path", "lamps"]
    assert not any(b[3].endswith(("_fence", "_gate")) for b in yard.blocks)
    assert structures.generate("house").clear_site and structures.generate("yard").clear_site
    assert not structures.generate("bridge").clear_site and not structures.generate("stairway").clear_site


def test_a_new_site_is_cleared_with_the_first_build_call_only(setup):
    mod, make = setup
    mod.give("oak_fence", 30)
    mod.give("oak_fence_gate", 2)
    pen = {"kind": "pen", "width": 5, "depth": 5, "x": 10, "y": 64, "z": 20}
    agent, backend = make([
        [("build_structure", pen)],
        [("build_structure", pen)],
        [("goal_failed", {"reason": "test over"})],
    ])
    agent.run_goal("build a pen")
    build_calls = [r["args"] for r in mod.received if r["op"] == "skill" and r["name"] == "build"]
    assert build_calls[0]["clearBox"] == [9, 64, 19, 15, 67, 25]      # the pen, a block round it, head room
    assert all("clearBox" not in call for call in build_calls[1:])
    assert "blocks" in build_calls[0]


def stubborn_world(blocked):
    """A build skill that never manages the blocks at ``blocked`` and places the rest."""
    def handler(mod, name, args):
        if name != "build":
            return True, "done", {}
        placed, failures, spots = 0, [], []
        for x, y, z, item, *_ in args["blocks"]:
            if item == "minecraft:air" or mod.world_blocks.get((x, y, z)) == item:
                continue
            if (x, y, z) in blocked:
                failures.append(f"{item} at {x} {y} {z}: cobblestone is in the way")
                spots.append([x, y, z, "cobblestone is in the way"])
                continue
            mod.world_blocks[(x, y, z)] = item
            placed += 1
        return not failures, f"placed {placed}", {"placed": placed, "present": 0, "failed": len(failures),
                                                   "failures": failures, "failedAt": spots}
    return handler


def test_a_few_blocks_that_cannot_be_placed_are_left_out_not_retried_for_ever(setup):
    mod, make = setup
    mod.skill_handler = stubborn_world({(10, 64, 20)})
    mod.give("oak_fence", 30)
    mod.give("oak_fence_gate", 1)
    pen = {"kind": "pen", "width": 5, "depth": 5, "x": 10, "y": 64, "z": 20}
    agent, backend = make([[("build_structure", pen)], [("goal_failed", {"reason": "test over"})]])
    agent.run_goal("build a pen")
    result = backend.conversations[0].results(0)[1][0]
    assert not result.is_error
    assert "1 left out" in result.content and "is in the way" in result.content
    assert "missing: oak_fence at 10 64 20" in result.content
    assert "Finished and remembered" in result.content and "1 blocks differ from the plan" in result.content
    # One pass that placed the rest, one that changed nothing, one repair pass after the check, then it stopped.
    assert len(build_calls(mod)) == 3


def test_a_build_that_sticks_says_so_and_is_marked_after_the_second_time(setup):
    mod, make = setup
    pen = structures.generate("pen", width=5, depth=5)
    mod.skill_handler = stubborn_world({(10 + b[0], 64 + b[1], 20 + b[2]) for b in pen.blocks})
    mod.give("oak_fence", 30)
    mod.give("oak_fence_gate", 1)
    call = {"kind": "pen", "width": 5, "depth": 5, "x": 10, "y": 64, "z": 20}
    agent, backend = make([[("build_structure", call)], [("build_structure", call)],
                           [("goal_failed", {"reason": "test over"})]])
    agent.run_goal("build a pen")
    conversation = backend.conversations[0]
    first = conversation.results(0)[1][0]
    assert first.is_error and "Going over it again placed nothing" in first.content
    assert "Do not repeat this call unchanged" in first.content and "find_site" in first.content
    second = conversation.results(1)[1][0]
    assert second.is_error and "stuck 2 times the same way" in second.content
    # Each call gave up after one pass: nothing went in, so there was nothing to go over again.
    assert len(build_calls(mod)) == 2


def test_the_wood_around_picks_the_house_style_and_the_yard_follows(setup):
    mod, make = setup
    mod.blocks = [{"block": "minecraft:birch_log", "tag": "#minecraft:logs", "pos": [3 + i, 64, 3],
                   "distance": 4.0 + i}
                  for i in range(5)]
    house = structures.generate("house", width=7, depth=7, **stylebook.apply(stylebook.get("birch_lodge"), "house", {}))
    for item, n in house.materials().items():
        mod.give(item.removeprefix("minecraft:"), n)
    yard = structures.generate("yard", width=7, depth=7, style="birch")
    for item, n in yard.materials().items():
        mod.give(item.removeprefix("minecraft:"), n)
    mod.give("poppy", 3)
    at = {"x": 30, "y": 64, "z": 30, "width": 7, "depth": 7}
    agent, backend = make([
        [("styles", {}), ("styles", {"name": "cabin"}), ("plan_structure", {"kind": "house", "width": 7, "depth": 7})],
        [("build_structure", {"kind": "house", **at})],
        [("build_structure", {"kind": "yard", **at})],
        [("goal_failed", {"reason": "test over"})],
    ])
    agent.run_goal("build a pen")
    conversation = backend.conversations[0]
    book, cabin, plan = [r.content for r in conversation.results(0)[1]]
    assert "What makes a build look good" in book and "the theme that fits is birch_lodge" in book
    assert cabin.startswith("cabin:") and "Rules:" in cabin and 'theme="cabin"' in cabin
    assert "Theme birch_lodge, picked for this place" in plan and "birch_planks" in plan
    built = conversation.results(1)[1][0]
    assert not built.is_error, built.content
    assert "Theme birch_lodge" in built.content and 'build_structure(kind="yard", x=30, y=64, z=30' in built.content
    assert "minecraft:cobblestone_stairs" in mod.world_blocks.values()
    assert "minecraft:oak_planks" not in mod.world_blocks.values()
    yarded = conversation.results(2)[1][0]
    assert not yarded.is_error, yarded.content
    assert "flowers: planted 3" in yarded.content
    # The yard's corner is margin blocks out from the house's, and nothing of it replaced the house.
    assert mod.world_blocks[(27, 64, 27)] == "minecraft:birch_log"
    assert sum(1 for v in mod.world_blocks.values() if v == "minecraft:poppy") == 3
    assert sum(1 for v in mod.world_blocks.values() if v.endswith("birch_door")) == 1


def test_review_moves_a_view_that_has_something_in_the_way(setup):
    mod, make = setup
    walks = []

    def world(mod, name, args):
        if name == "goto":
            walks.append((args["x"], args["z"]))
            mod.pos = (args["x"] + 0.5, 64.0, args["z"] + 0.5)
        return True, "arrived", {}

    mod.skill_handler = world
    review = {"x": 10, "y": 64, "z": 10, "width": 9, "depth": 9, "height": 8, "views": 1}
    agent, _ = make([])
    backend = SeeingBackend([[("review_build", review)], [("review_build", review)],
                             [("goal_failed", {"reason": "test over"})]])

    def wall_it_in(conversation):
        # Ground everywhere, and a ring of stone close round the spot the first view was taken from.
        sx, sz = walks[0]
        for x in range(-10, 50):
            for z in range(-10, 50):
                mod.world_blocks[(x, 63, z)] = "minecraft:grass_block"
                if max(abs(x - sx), abs(z - sz)) == 2:
                    for y in range(64, 76):
                        mod.world_blocks[(x, y, z)] = "minecraft:stone"
        return [("review_build", review)]

    backend.script[1] = wall_it_in
    agent.backend = backend
    agent.goal = "build"
    agent.run_goal("build")
    conversation = backend.conversations[0]
    assert len(walks) == 2
    assert walks[1] != walks[0]                    # it stood somewhere it could see the build from
    second = conversation.results(1)[1][0]
    assert len(second.images) == 1 and "hidden" not in second.images[0].caption


# Checking what stands against the plan ------------------------------------------------------------

PEN = {"kind": "pen", "width": 5, "depth": 5, "x": 10, "y": 64, "z": 20}


def pen_agent(setup, script):
    mod, make = setup
    mod.give("oak_fence", 30)
    mod.give("oak_fence_gate", 2)
    agent, backend = make(script + [[("goal_failed", {"reason": "test over"})]])
    agent.run_goal("build a pen")
    return mod, backend.conversations[0]


def build_calls(mod):
    """The build calls that place blocks, leaving out the one that takes the scaffold down at the end."""
    return [r["args"] for r in mod.received if r["op"] == "skill" and r["name"] == "build"
            and not r["args"].get("tidyOnly")]


def test_a_build_is_checked_against_the_plan_and_says_so(setup):
    mod, conversation = pen_agent(setup, [[("build_structure", PEN)]])
    text = conversation.results(0)[1][0].content
    assert "Checked against the plan: 16 of 16 blocks are right (100%)." in text
    assert "Finished and remembered" in text and "differ from the plan" not in text
    calls = build_calls(mod)
    assert len(calls) == 1
    assert calls[0]["site"] == [10, 64, 20, 14, 65, 24]
    assert [r for r in mod.received if r["op"] == "audit"][0]["box"] == calls[0]["site"]


def test_blocks_still_to_place_are_held_back_from_scaffolding(setup):
    mod, make = setup
    for slot in (1, 2, 3):
        mod.give("dirt", 64, slot=slot)
    mod.give("oak_door", 1)
    mod.give("torch", 4)
    mod.give("white_bed", 1)
    shelter = {"kind": "shelter", "x": 10, "y": 64, "z": 20}
    agent, backend = make([[("build_structure", shelter)], [("goal_failed", {"reason": "test over"})]])
    agent.run_goal("build a pen")
    calls = build_calls(mod)
    assert len(calls) > 1, backend.conversations[0].results(0)[1][0].content
    assert calls[0]["reserve"].get("minecraft:dirt", 0) > 0
    assert all(n > 0 for call in calls for n in call.get("reserve", {}).values())
    assert "reserve" not in calls[-1] or "minecraft:dirt" not in calls[-1]["reserve"]


def test_a_block_turned_the_wrong_way_is_found_and_put_right(setup):
    mod, make = setup

    def handler(mod, name, args):
        ok, message, data = building_world(mod, name, args)
        turned = 0
        for entry in args.get("blocks", []):
            if tuple(entry[:3]) in mod.turned_wrong and "site" in args and len(args["blocks"]) < 5:
                del mod.turned_wrong[tuple(entry[:3])]
                turned += 1
        return ok, message, {**data, "turned": turned}

    mod.skill_handler = handler
    mod.turned_wrong[(12, 64, 24)] = "facing=east"
    _, conversation = pen_agent(setup, [[("build_structure", PEN)]])
    text = conversation.results(0)[1][0].content
    assert "One repair pass turned 1 the right way round." in text
    assert "16 of 16 blocks are right" in text and "Finished and remembered" in text
    repair = build_calls(mod)[-1]["blocks"]
    assert repair == [[12, 64, 24, "minecraft:oak_fence_gate", "north"]]


def test_a_block_someone_else_turned_wrong_is_reported_and_left(setup):
    mod, _ = setup
    mod.turned_wrong[(12, 64, 24)] = "facing=east"
    mod.foreign.add((12, 64, 24))
    _, conversation = pen_agent(setup, [[("build_structure", PEN)]])
    text = conversation.results(0)[1][0].content
    assert "turned wrong: oak_fence_gate[facing=east] at 12 64 24" in text
    assert "not placed by MineGPT: mine it and place it again" in text
    assert "1 blocks differ from the plan" in text
    assert len(build_calls(mod)) == 1


def test_scaffolding_left_inside_is_taken_down_by_the_repair_pass(setup):
    mod, _ = setup

    def handler(mod, name, args):
        for entry in args.get("blocks", []):
            if entry[3] == "minecraft:air" and tuple(entry[:3]) in mod.strays:
                del mod.world_blocks[tuple(entry[:3])]
                return True, "done", {"cleared": 1}
        return building_world(mod, name, args)

    mod.skill_handler = handler
    mod.world_blocks[(12, 64, 22)] = "minecraft:cobblestone"
    mod.strays.add((12, 64, 22))
    _, conversation = pen_agent(setup, [[("build_structure", PEN)]])
    text = conversation.results(0)[1][0].content
    assert "One repair pass took down 1." in text and "stray" not in text
    assert (12, 64, 22) not in mod.world_blocks
    assert build_calls(mod)[-1]["blocks"] == [[12, 64, 22, "minecraft:air"]]


def test_a_stray_block_that_is_not_its_own_is_reported_and_left(setup):
    mod, _ = setup
    mod.world_blocks[(12, 64, 22)] = "minecraft:cobblestone"
    mod.strays.add((12, 64, 22))
    mod.foreign.add((12, 64, 22))
    _, conversation = pen_agent(setup, [[("build_structure", PEN)]])
    text = conversation.results(0)[1][0].content
    assert "1 stray blocks in or beside it" in text
    assert "stray: cobblestone at 12 64 22 (not placed by MineGPT" in text
    assert mod.world_blocks[(12, 64, 22)] == "minecraft:cobblestone" and len(build_calls(mod)) == 1


def test_a_part_that_mostly_is_not_there_does_not_count_as_built(setup):
    mod, _ = setup

    def handler(mod, name, args):
        # Says it placed everything and places nothing.
        if name == "build":
            return True, "done", {"placed": len(args["blocks"]), "present": 0, "failed": 0, "failures": []}
        return True, "done", {}

    mod.skill_handler = handler
    plan = ("set_plan", {"steps": [{"step": "build the pen"}], "goal_check": {"built": ["pen"]}})
    _, conversation = pen_agent(setup, [[plan], [("build_structure", PEN)], [("goal_done", {"summary": "pen built"})]])
    text = conversation.results(1)[1][0]
    assert text.is_error and "0 of 16 blocks are right" in text.content and "Not finished" in text.content
    assert "pen not built yet" in conversation.results(2)[1][0].content


def test_an_older_mod_without_the_check_builds_as_before(setup):
    mod, _ = setup
    mod.audits = False
    _, conversation = pen_agent(setup, [[("build_structure", PEN)], [("inspect_build", PEN)]])
    text = conversation.results(0)[1][0].content
    assert "Finished and remembered" in text and "Checked against the plan" not in text
    asked = conversation.results(1)[1][0]
    assert asked.is_error and "needs the newer MineGPT mod" in asked.content


def test_inspect_build_lists_what_differs_and_repairs_only_when_asked(setup):
    mod, _ = setup
    script = [[("build_structure", PEN)], [("inspect_build", PEN)], [("inspect_build", PEN)],
              [("inspect_build", {**PEN, "repair": True})]]
    mod_, make = setup
    mod.give("oak_fence", 30)
    mod.give("oak_fence_gate", 2)
    agent, backend = make(script + [[("goal_failed", {"reason": "test over"})]])

    seen = []
    original = mod._audit

    def audit(req):
        # After the first two checks someone knocks a fence out.
        seen.append(1)
        if len(seen) == 3:
            del mod.world_blocks[(10, 64, 20)]
        return original(req)

    mod._audit = audit
    agent.run_goal("build a pen")
    conversation = backend.conversations[0]
    clean = conversation.results(1)[1][0].content
    assert "Nothing differs" in clean
    broken = conversation.results(2)[1][0].content
    assert "15 of 16 blocks are right" in broken and "missing: oak_fence at 10 64 20" in broken
    assert "repair=true" in broken
    assert len(build_calls(mod)) == 2       # the build, and the one repair that was asked for
    mended = conversation.results(3)[1][0].content
    assert "One repair pass placed 1." in mended and "16 of 16 blocks are right" in mended
    assert mod.world_blocks[(10, 64, 20)] == "minecraft:oak_fence"


def test_a_new_plan_cannot_drop_what_is_still_to_build(setup):
    mod, make = setup
    agent, backend = make([
        [("set_plan", {"steps": [{"step": "build the pen"}], "goal_check": {"built": ["pen"]}})],
        [("set_plan", {"steps": [{"step": "stand near it"}], "goal_check": {"height": {"min": 1}}})],
        [("goal_done", {"summary": "done"})],
        [("goal_failed", {"reason": "test over"})],
    ])
    result = agent.run_goal("build a pen")
    conversation = backend.conversations[0]
    assert "Kept in the goal check from before: built pen" in conversation.results(1)[1][0].content
    assert "pen not built yet" in conversation.results(2)[1][0].content
    assert result.outcome != "success"


def test_a_build_that_stops_the_same_way_twice_says_so_and_then_gives_up(setup):
    mod, make = setup
    blocked = {(10 + i, 64, 20) for i in range(5)} | {(10, 64, 21 + i) for i in range(4)}
    mod.skill_handler = stubborn_world(blocked)
    mod.give("oak_fence", 30)
    mod.give("oak_fence_gate", 1)
    agent, backend = make([[("build_structure", PEN)], [("build_structure", PEN)], [("build_structure", PEN)],
                           [("goal_failed", {"reason": "test over"})]])
    result = agent.run_goal("build a pen")
    conversation = backend.conversations[0]
    first, second = (conversation.results(i)[1][0].content for i in (0, 1))
    assert "time this build has stopped the same way" not in first
    assert "Do not call it again unchanged" in second or "stuck 2 times" in second
    assert result.outcome != "success"


def test_logs_keep_their_axis_when_a_structure_is_turned():
    block = (1, 0, 0, "minecraft:oak_log", "axis:x")
    turned = structures._turn_facing(block[4])
    assert turned == "axis:z" and structures._turn_facing(turned) == "axis:x"
    assert structures._turn_facing("axis:y") == "axis:y"


def test_a_third_identical_build_call_is_refused_until_something_else_is_done(setup):
    mod, make = setup
    pen = structures.generate("pen", width=5, depth=5)
    stubborn = stubborn_world({(10 + b[0], 64 + b[1], 20 + b[2]) for b in pen.blocks})

    def handler(mod, name, args):
        return stubborn(mod, name, args) if name == "build" else (True, "done", {})
    mod.skill_handler = handler
    mod.give("oak_fence", 30)
    mod.give("oak_fence_gate", 1)
    call = {"kind": "pen", "width": 5, "depth": 5, "x": 10, "y": 64, "z": 20}
    agent, backend = make([[("build_structure", call)], [("build_structure", call)], [("build_structure", call)],
                           [("goto", {"x": 10, "y": 64, "z": 18})], [("build_structure", call)],
                           [("goal_failed", {"reason": "test over"})]])
    agent.run_goal("build a pen")
    conversation = backend.conversations[0]
    third = conversation.results(2)[1][0]
    assert third.is_error and third.content.startswith("Not run: this exact build_structure call has stopped 2 times")
    # The refused call sent nothing to the game; after the walk the fourth call ran again.
    assert len(build_calls(mod)) == 3
    assert not conversation.results(4)[1][0].content.startswith("Not run")


def test_only_the_blocks_that_failed_and_their_neighbours_are_gone_over_again(setup):
    mod, make = setup
    mod.skill_handler = stubborn_world({(10, 64, 20), (14, 64, 24)})
    mod.give("oak_fence", 30)
    mod.give("oak_fence_gate", 1)
    pen = {"kind": "pen", "width": 5, "depth": 5, "x": 10, "y": 64, "z": 20}
    agent, backend = make([[("build_structure", pen)], [("goal_failed", {"reason": "test over"})]])
    agent.run_goal("build a pen")
    builds = build_calls(mod)
    # Placed the rest, then one more go that changed nothing, then the repair pass: never a third pass.
    assert len(builds) == 3
    again = {tuple(b[:3]) for b in builds[1]["blocks"]}
    assert (10, 64, 20) in again and (14, 64, 24) in again
    assert all(max(abs(x - fx), abs(z - fz)) <= 1 for x, _, z in again
               for fx, fz in [min([(10, 20), (14, 24)], key=lambda f: abs(x - f[0]) + abs(z - f[1]))])
    assert len(again) < len(builds[0]["blocks"])
    # Every chunk leaves its scaffold up; it all comes down in one go at the end.
    assert all(call.get("tidy") is False for call in builds[:2])
    tidy = [r["args"] for r in mod.received if r["op"] == "skill" and r["name"] == "build" and r["args"].get("tidyOnly")]
    assert tidy == [{"blocks": [], "tidyOnly": True, "site": [10, 64, 20, 14, 65, 24]}]


def test_a_few_natural_blocks_in_the_right_spots_do_not_make_a_site_a_build_in_progress():
    from minegpt_brain.agent.tools import Toolbox as Tools
    solid = [(x, 0, z, "minecraft:birch_log" if x == 0 else "minecraft:birch_planks", None)
             for x in range(10) for z in range(10)]
    # Two trunks of the forest stand where the plan wants posts: still a fresh site, to be cleared.
    assert not Tools._resumed(solid, {(0, 0, 1), (0, 0, 5)})
    # Planks do not grow: three of them standing mean the build was started.
    assert Tools._resumed(solid, {(1, 0, 1), (2, 0, 1), (3, 0, 1)})
    # And so does a tenth of the plan, whatever it is made of.
    assert Tools._resumed(solid, {(0, 0, z) for z in range(10)})
