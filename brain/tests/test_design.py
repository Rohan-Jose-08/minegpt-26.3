"""The design language: reading it, compiling it to blocks, reviewing it, and using it from the tools."""

import pytest

from fake_mod import FakeMod
from minegpt_brain.agent import design, designs, structures, stylebook
from minegpt_brain.agent.loop import Agent, AgentConfig
from minegpt_brain.bridge import Bridge
from test_agent import ScriptedBackend
from test_structures import building_world

COTTAGE = design.DEFAULT_PROGRAM


def compiled(program, **options):
    return design.compile_design(program, **options)


def block_at(made, x, y, z):
    """The block at a spot counted from the first mass's west, north corner, before the structure is moved."""
    found = made.cells.get((x, y, z))
    return (found[1], found[2]) if found else None


# Reading and laying out ---------------------------------------------------------------------------

def test_a_design_becomes_a_structure_with_no_spot_used_twice():
    made = compiled(COTTAGE)
    s = made.structure
    assert s.kind == "design" and [p.name for p in s.parts] == ["hall", "wing", "inside", "roof", "trim"]
    spots = [b[:3] for b in s.blocks]
    assert len(spots) == len(set(spots))
    assert s.footprint() == (15, 10)            # 13 of walls, the eaves, the chimney and the porch
    assert s.footing == "minecraft:cobblestone" and s.parts[0].footing and s.parts[-1].optional
    assert min(b[0] for b in s.blocks if b[3] != structures.AIR) == 0


def test_an_attached_mass_shares_the_wall_and_gets_a_doorway():
    made = compiled(COTTAGE)
    hall, wing = made.design.masses["hall"], made.design.masses["wing"]
    assert (wing.x1, wing.z0) == (hall.x0, hall.z0)             # shares the west wall, lined up at the back
    gaps = [(x, z) for (x, y, z), (_, item, _) in made.cells.items()
            if x == hall.x0 and y == 0 and item == structures.AIR]
    assert gaps == [(0, 2)]
    assert block_at(made, 0, 1, 2) == (structures.AIR, None) and block_at(made, 0, 2, 2)[0].endswith("planks")


def test_align_and_offset_slide_a_mass_along_the_wall():
    front = compiled("mass a box(9,4,9)\nmass b box(5,3,5) attach a.east align=front").design.masses["b"]
    assert (front.x0, front.z0) == (8, 4)
    nudged = compiled("mass a box(9,4,9)\nmass b box(5,3,5) attach a.north align=west offset=2").design.masses["b"]
    assert (nudged.x0, nudged.z0) == (2, -4)


def test_mirror_puts_a_twin_on_the_other_side():
    made = compiled("mass hall box(9,5,7)\nmass w box(5,3,5) attach hall.west align=back\n"
                    "roof w lean(to=east)\nfacade w.west -> [wide_window]\nmirror w as e")
    w, e = made.design.masses["w"], made.design.masses["e"]
    assert (e.x0, e.z0, e.side) == (8, 0, "east") and e.roof_options["to"] == "west"
    assert made.walls[("e", "east")] == ["wide_window"] and made.walls[("w", "west")] == ["wide_window"]


@pytest.mark.parametrize("program, said", [
    ("", "the design is empty"),
    ("mass hall box(9,4,7)\nmass wing box(5,3,5)", "line 2: wing must attach to a mass already written"),
    ("mass hall box(9,4,7)\nmass wing box(5,3,5) attach barn.west", "line 2: no mass called barn (masses so far: hall)"),
    ("mass hall box(30,4,7)", "line 1: hall is 30 by 7; width and depth go from 4 to 21"),
    ("mass hall box(9,2,7)", "wall height goes from 3 to 9"),
    ("mass hall box(7,4,7)\nmass wing box(9,3,5) attach hall.north", "wing is 9 long where it meets hall.north, which is only 7"),
    ("mass hall box(9,4,7)\nmass a box(5,3,5) attach hall.west\nmass b box(5,3,5) attach hall.west", "line 3: b overlaps a"),
    ("mass hall box(9,4,7)\nroof hall dome", "line 2: roofs are gable, hip, flat, lean; not dome"),
    ("mass hall box(9,4,7)\nfacade hall.south -> [window, gate]", "no facade part called gate"),
    ("mass hall box(5,4,7)\nfacade hall.south -> [window, door, window, window]", "hall.south is 5 long: room for at most 3 bays, not 4"),
    ("mass hall box(9,4,7)\nmass wing box(5,3,5) attach hall.south\nfacade hall.south -> [window, door, window]",
     "the door of hall.south would open into wing"),
    ("mass hall box(9,4,7)\nmass wing box(5,3,5) attach hall.east\ndetail hall chimney at east", "no room for the chimney of hall at east"),
    ("mass hall box(9,4,7)\nroof hall lean(to=north)", "a lean roof on hall would span 7 blocks towards north; at most 6"),
    ("mass hall box(9,5,7) floors=2", "two floors need walls 7 high"),
    ("mass hall box(9,4,7)\nwindow hall.south", "lines start with build, mass, roof, facade, detail or mirror"),
    ("build x style=marble\nmass hall box(9,4,7)", "unknown style marble"),
])
def test_what_cannot_be_built_is_refused_in_plain_words(program, said):
    with pytest.raises(design.DesignError) as error:
        compiled(program)
    assert said in str(error.value)


def test_every_mistake_is_listed_at_once():
    with pytest.raises(design.DesignError) as error:
        compiled("mass hall box(9,4,7)\nroof barn gable\nfacade hall.up -> [window]\ndetail hall dormer")
    assert [line.split(":")[0] for line in str(error.value).splitlines()] == ["line 2", "line 3", "line 4"]


# Blocks and the way they are turned --------------------------------------------------------------

def test_doors_stairs_chests_and_beds_are_turned_by_the_compiler():
    made = compiled("mass hall box(7,3,7)\nroof hall gable(overhang=1, ridge=x)\n"
                    "facade hall.east -> [window, door, window]\ndetail hall furnish [bed, chest]")
    assert block_at(made, 6, 0, 3) == ("minecraft:oak_door", "west")          # placed from outside, looking in
    assert block_at(made, 3, 2, -1) == ("minecraft:oak_stairs", "south")       # the north eave rises south
    assert block_at(made, 3, 2, 7) == ("minecraft:oak_stairs", "north")
    assert block_at(made, 5, 0, 2) == ("minecraft:white_bed", "north")         # foot down, head to the wall
    assert block_at(made, 4, 0, 1) == ("minecraft:chest", "north")
    turned = compiled("mass hall box(7,3,7)\nroof hall gable(ridge=z)")
    assert block_at(turned, -1, 2, 3) == ("minecraft:oak_stairs", "east")
    assert block_at(turned, 3, 6, 3) == ("minecraft:oak_slab", "bottom")


def test_each_roof_form_covers_the_room_and_rests_on_something():
    for roof in ("gable(overhang=1)", "gable(overhang=0, ridge=z)", "hip(overhang=1)", "hip(overhang=0)",
                 "flat(parapet=1)", "lean(to=north)", "lean(to=east, overhang=0)"):
        made = compiled(f"mass hall box(6,3,6)\nroof hall {roof}")
        for x in range(6):
            for z in range(6):
                assert made.cells[(x, 3, z)][0] == "roof", (roof, x, z)
        roof_cells = {pos for pos, (part, _, _) in made.cells.items() if part == "roof"}
        for (x, y, z) in roof_cells:
            # Something to place it on or against: the block below, or a neighbour at its own level.
            near = [(x, y - 1, z), (x + 1, y, z), (x - 1, y, z), (x, y, z + 1), (x, y, z - 1)]
            assert any(n in made.cells and made.cells[n][1] != structures.AIR for n in near), (roof, x, y, z)


def test_a_lower_roof_stops_at_a_taller_neighbours_wall():
    made = compiled("mass hall box(9,6,7)\nmass wing box(5,3,5) attach hall.west align=back\n"
                    "roof wing gable(ridge=x)")
    hall = made.design.masses["hall"]
    for (x, y, z), (part, _, _) in made.cells.items():
        if part == "roof" and hall.inside(x, z):
            assert y >= hall.h


def test_windows_never_look_into_an_attached_mass_and_two_floors_get_two_rows():
    made = compiled("mass hall box(9,7,7) floors=2\nmass wing box(5,3,5) attach hall.west align=back")
    hall = made.design.masses["hall"]
    glass = [(x, y, z) for (x, y, z), (_, item, _) in made.cells.items() if item.endswith("glass_pane")]
    assert not any(x == hall.x0 and z <= 4 and y < 4 for x, y, z in glass)
    assert {y for x, y, z in glass if z == hall.z1} == {1, 5}
    ladder = [(pos, facing) for pos, (_, item, facing) in made.cells.items() if item == "minecraft:ladder"]
    assert [pos[1] for pos, _ in ladder] == [0, 1, 2, 3] and {f for _, f in ladder} == {"on:north"}
    assert made.cells[(ladder[0][0][0], 3, ladder[0][0][2] + 1)][1] == "minecraft:oak_planks"   # the upper floor


def test_a_design_with_no_door_is_given_one_and_says_so():
    made = compiled("mass hall box(7,3,7)")
    assert any(item.endswith("_door") for (_, item, _) in made.cells.values())
    assert made.notes == ["No door was written, so one was put in the middle of hall.south."]
    assert made.structure.entrance == (4, 0, 8)      # moved by the eaves


def test_style_comes_from_the_call_then_the_build_line_and_themes_bring_their_swaps():
    lodge = compiled("build a style=birch_lodge\nmass hall box(7,3,7)").structure.materials()
    assert "minecraft:birch_planks" in lodge and "minecraft:cobblestone_stairs" in lodge
    spruce = compiled("build a style=birch_lodge\nmass hall box(7,3,7)", style="spruce").structure.materials()
    assert "minecraft:spruce_planks" in spruce and "minecraft:birch_planks" not in spruce
    assert design.header_style("build a style=cabin\nmass h box(7,3,7)") == "cabin"
    assert design.header_style("mass h box(7,3,7)") is None


def test_a_design_turns_like_any_structure():
    made = structures.generate("design", "east", program=COTTAGE)
    doors = [b for b in made.blocks if b[3].endswith("_door")]
    assert len(doors) == 1 and doors[0][4] == "west"
    assert made.footprint() == (10, 15)


# The review -------------------------------------------------------------------------------------------

def test_the_review_names_what_makes_a_design_plain():
    fix, _ = design.review(compiled("mass hall box(9,3,9)\nroof hall gable(overhang=0)\n"
                                    "facade hall.north -> [wall]"))
    text = "\n".join(fix)
    assert "One plain box" in text and "the roof stops at the walls" in text
    assert "hall.north: 9 blocks of blank wall" in text and "walls 3 high over a 9x9 room" in text
    fix, _ = design.review(compiled("mass a box(7,4,7)\nmass b box(5,4,5) attach a.west\nroof a flat"))
    assert any("Every mass has walls 4 high" in f for f in fix) and any("flat roof" in f for f in fix)
    fix, _ = design.review(compiled("mass a box(7,3,7)\nfacade a.south -> [window, door_porch, window]", style="dirt"))
    assert any("dirt" in f for f in fix)


def test_the_review_follows_the_style_card():
    made = compiled("build d style=desert_house\nmass a box(7,4,7)\nroof a flat(parapet=1)\n"
                    "facade a.south -> [window, door_porch, window]")
    assert design.review(made, "desert_house")[0] == []
    _, consider = design.review(compiled(COTTAGE), "desert_house")
    assert any("desert_house style goes with a flat roof" in c for c in consider)


def test_every_room_is_lit():
    made = compiled("mass hall box(21,4,21)")
    for cells in made.floors.values():
        for (x, y, z) in cells:
            assert any(abs(x - lx) + abs(z - lz) <= 7 for (lx, ly, lz) in made.lights if ly == y)


def test_the_floor_plan_draws_walls_doors_and_furniture():
    plan = design.floor_plan(compiled(COTTAGE)).splitlines()
    assert plan[0].strip() == "P#o#P#o#P#o#P"
    assert "D" in plan[6] and "_" in plan[2] and "b" in plan[2] and "C" in plan[3]


# Examples, parts and style cards ----------------------------------------------------------------------

@pytest.mark.parametrize("example", designs.EXAMPLES, ids=lambda e: e.name)
def test_every_example_compiles_and_passes_its_own_review(example):
    made = compiled(example.program)
    fix, _ = design.review(made, example.styles[0])
    assert fix == []
    assert all(s in stylebook.THEMES or s in structures.PALETTES for s in example.styles)
    spots = [b[:3] for b in made.structure.blocks]
    assert len(spots) == len(set(spots)) and made.structure.entrance is not None
    for quarters in range(4):
        structures.turned(made.structure, quarters)


def test_examples_are_found_by_size_and_style(tmp_path):
    names = [e.name for e, _, _ in designs.find(9, 9, "cottage")]
    assert names[0] == "first_house"
    assert designs.find(24, 14, "manor")[0][0].name == "manor"
    assert designs.find(style="keep")[0][0].name == "keep"
    library = designs.DesignLibrary(tmp_path / "designs.json")
    program = "mass hut box(6,3,6)\nroof hut hip\nfacade hut.south -> [door_porch]"
    assert library.add("hut", program, "cabin", 8, 9) and not library.add("hut", program, "cabin", 8, 9)
    assert not library.add("again", designs.EXAMPLES[0].program, None, 1, 1)
    again = designs.DesignLibrary(tmp_path / "designs.json")
    found = designs.find(8, 9, "cabin", again)[0][0]
    assert found.name == "hut" and found.built


def test_every_theme_has_a_card_and_the_reference_lists_every_part():
    assert set(stylebook.CARDS) == set(stylebook.THEMES)
    assert all(set(card.roofs) <= set(design.ROOFS) for card in stylebook.CARDS.values())
    assert "Shape: main mass" in stylebook.describe(stylebook.get("cabin"))
    text = design.reference()
    assert all(name in text for name in design.PARTS) and set(design._FACADE_PARTS) <= set(design.PARTS)


# From the tools ------------------------------------------------------------------------------------------

@pytest.fixture
def setup(tmp_path):
    mod = FakeMod()
    mod.skill_handler = building_world
    mod.goal = "build a cottage"
    bridge = Bridge.connect(port=mod.port, timeout=5)

    def make(script, **config):
        backend = ScriptedBackend(script)
        return Agent(bridge, backend, AgentConfig(data_dir=tmp_path, poll=0.05, jobs=False, **config)), backend

    yield mod, make
    bridge.close()


def results(backend, turn):
    return [r for r in backend.conversations[0].results(turn)[1]]


def test_design_guide_gives_the_language_a_style_card_and_examples(setup):
    mod, make = setup
    agent, backend = make([[("design_guide", {"width": 10, "depth": 10, "style": "cabin"})],
                           [("goal_failed", {"reason": "test over"})]])
    agent.run_goal("build a cottage")
    text = results(backend, 0)[0].content
    assert "mass <name> box(width, wall height, depth)" in text and "door_porch (facade bay)" in text
    assert "cabin: Spruce cabin" in text and "Avoid: wide windows" in text
    assert "- small_cabin:" in text or "- first_house:" in text
    assert "    mass hall box(" in text


def test_check_design_refuses_a_broken_design_and_reviews_a_sound_one(setup):
    mod, make = setup
    broken = "mass hall box(9,4,7)\nmass wing box(5,3,5) attach hall.up"
    plain = "build box style=oak\nmass hall box(9,3,9)\nroof hall flat"
    agent, backend = make([[("check_design", {"program": broken})], [("check_design", {"program": plain})],
                           [("check_design", {"program": COTTAGE, "theme": "cabin", "facing": "east"})],
                           [("goal_failed", {"reason": "test over"})]])
    agent.run_goal("build a cottage")
    refused = results(backend, 0)[0]
    assert refused.is_error and "line 2: attach to a side: north, east, south, west; not up" in refused.content
    reviewed = results(backend, 1)[0]
    assert not reviewed.is_error and "Fix before building:" in reviewed.content
    assert "One plain box" in reviewed.content and "P#o#P#o#P" in reviewed.content
    good = results(backend, 2)[0].content
    assert "The design can be built." in good and "Footprint 10 (x) by 15 (z)" in good
    assert "Fix before building" not in good and "spruce_planks" in good
    assert 'build_structure(kind="design", program=<this design>, theme="cabin", facing="east", x, y, z)' in good


def test_a_design_is_built_checked_and_kept(setup, tmp_path):
    mod, make = setup
    program = "build hut style=oak\nmass hut box(6,3,6)\nroof hut hip(overhang=1)\nfacade hut.south -> [door_porch]\n" \
              "detail hut furnish []"
    needs = structures.generate("design", program=program).materials()
    for slot, (item, count) in enumerate(needs.items(), 1):
        mod.give(item.split(":")[1], count, slot=slot) if count <= 64 else [
            mod.give(item.split(":")[1], min(64, count - i), slot=slot * 10 + i // 64) for i in range(0, count, 64)]
    call = {"kind": "design", "program": program, "x": 10, "y": 64, "z": 20}
    agent, backend = make([
        [("set_plan", {"steps": [{"step": "build it"}], "goal_check": {"built": ["design"]}})],
        [("plan_structure", {"kind": "design", "program": program})],
        [("build_structure", call)],
        [("inspect_build", call)],
        [("design_guide", {"width": 8, "depth": 9})],
        [("goal_done", {"summary": "built"})],
    ])
    result = agent.run_goal("build a cottage")
    planned = results(backend, 1)[0].content
    assert "hut: 6x6 design of 1 mass" in planned and "You carry all of it" in planned
    built = results(backend, 2)[0]
    assert not built.is_error, built.content
    assert "blocks are right (100%)" in built.content and "The design is kept" in built.content
    assert "Nothing differs" in results(backend, 3)[0].content
    assert "- hut:" in results(backend, 4)[0].content and "built and checked here" in results(backend, 4)[0].content
    assert result.outcome == "success" and result.verified
    doors = [pos for pos, item in mod.world_blocks.items() if item == "minecraft:oak_door"]
    assert len(doors) == 1
    sent = [r["args"] for r in mod.received if r["op"] == "skill" and r["name"] == "build"]
    assert any(len(b) > 4 and b[3] == "minecraft:oak_stairs" for call in sent for b in call["blocks"])


def test_list_structures_offers_the_design_kind(setup):
    mod, make = setup
    agent, backend = make([[("list_structures", {})], [("goal_failed", {"reason": "test over"})]])
    agent.run_goal("build a cottage")
    assert "design: A building of your own design" in results(backend, 0)[0].content


def test_nothing_asks_for_the_spot_above_a_door_to_be_kept_empty():
    made = compiled("mass hall box(7,4,6)\nfacade hall.south -> [window, door, window]")
    door = next((x, y, z) for (x, y, z), (_, item, _) in made.cells.items() if item.endswith("_door"))
    # The door's own upper half goes there: an entry for it, even "empty", only makes the builder fight it.
    assert (door[0], door[1] + 1, door[2]) not in made.cells
    opened = compiled("mass hall box(7,4,6)\nfacade hall.south -> [window, opening, window]")
    gap = next((x, y, z) for (x, y, z), (_, item, _) in opened.cells.items()
               if item == structures.AIR and y == 0 and z == 5)
    assert opened.cells[(gap[0], 1, gap[2])][1] == structures.AIR
