"""The recipe graph, the item planner, the plan tree, the dry run, the map and the goal records."""

import json

import pytest

from minegpt_brain.agent import Agent, AgentConfig, contracts
from minegpt_brain.agent.experience import ExperiencePool, situation
from minegpt_brain.agent.skillstats import SkillStats
from minegpt_brain.agent.taskgraph import GoalStore, Plan, parse_step
from minegpt_brain.agent.worldmap import WorldMap
from minegpt_brain.bench import TASKS
from minegpt_brain.bridge import Bridge
from minegpt_brain.knowledge import load, plan_items
from minegpt_brain.knowledge import build as kbuild
from minegpt_brain.observation import Observation
from minegpt_brain.train import plans as plan_data
from fake_mod import FakeMod, make_obs
from test_agent import ScriptedBackend

K = load()


def obs_with(**items):
    slots = [{"slot": i, "id": f"minecraft:{item}", "count": n} for i, (item, n) in enumerate(items.items())]
    return Observation(make_obs(slots=slots))


# The graph and the planner --------------------------------------------------------------------

def test_the_shipped_data_covers_the_game():
    assert K.version == "26.3" and len(K.recipes) > 800
    assert K.blocks["iron_ore"] == {"raw_iron": 1.0} and K.blocks["gravel"]["flint"] == 0.1
    assert K.tools["iron_ore"] == {"tool": "pickaxe", "tier": 1} and K.tools["diamond_ore"]["tier"] == 2
    assert K.mobs["cow"]["beef"] == 2.0
    assert K.generic("#wooden_tool_materials") == "#planks" and K.generic("#stone_tool_materials") != "#planks"
    # Costs follow the tech tree.
    costs = K.costs()
    assert costs["stick"] < costs["wooden_pickaxe"] < costs["stone_pickaxe"] < costs["iron_pickaxe"] \
        < costs["diamond_pickaxe"]


def test_an_iron_pickaxe_from_nothing_is_planned_in_order():
    plan = plan_items({"iron_pickaxe": 1}, {}, K)
    order = [(s.how, s.item) for s in plan.steps]
    assert order[0] == ("mine", "#logs") and order[-1] == ("craft", "iron_pickaxe")
    position = {item: i for i, (_, item) in enumerate(order)}
    assert position["wooden_pickaxe"] < position["cobblestone"] < position["stone_pickaxe"] < position["raw_iron"] \
        < position["iron_ingot"]
    assert position["furnace"] < position["iron_ingot"] and position["crafting_table"] < position["wooden_pickaxe"]
    assert plan.tools == ["wooden_pickaxe", "stone_pickaxe"] and not plan.unknown
    assert plan.smelts == 3 and "planks" in plan.fuel
    # 3 for the pickaxe head; cobblestone covers a furnace (8) and a stone pickaxe (3).
    by_item = {s.item: s for s in plan.steps}
    assert by_item["raw_iron"].units == 3 and by_item["cobblestone"].count == 11
    assert by_item["raw_iron"].calls() == [{"tool": "mine", "args": {
        "block": ["minecraft:deepslate_iron_ore", "minecraft:iron_ore"], "count": 3}}]
    assert plan.goal_check() == {"have": {"iron_pickaxe": 1}}
    # Played forward on paper, the plan holds together and ends with the pickaxe.
    sim = contracts.simulate(plan.calls(), {}, K)
    assert sim.ok and sim.inventory["iron_pickaxe"] == 1


def test_what_is_carried_is_used_first():
    carried = {"minecraft:stone_pickaxe": 1, "minecraft:crafting_table": 1, "minecraft:furnace": 1,
               "minecraft:stick": 2, "minecraft:coal": 1, "minecraft:raw_iron": 1}
    plan = plan_items({"iron_pickaxe": 1}, carried, K)
    assert [(s.how, s.item, s.units) for s in plan.steps] == [
        ("mine", "raw_iron", 2), ("smelt", "iron_ingot", 3), ("craft", "iron_pickaxe", 1)]
    assert plan.fuel == "1 coal carried" and not plan.tools
    assert plan.steps[0].have == 3  # the one carried and the two mined
    assert plan_items({"stick": 2}, carried, K).empty()


def test_full_iron_armour_and_a_wanted_tool_is_not_made_twice():
    plan = plan_items({"iron_helmet": 1, "iron_chestplate": 1, "iron_leggings": 1, "iron_boots": 1}, {}, K)
    assert {s.item: s.units for s in plan.steps}["raw_iron"] == 24
    both = plan_items({"diamond": 2, "iron_pickaxe": 1}, {}, K)
    assert sum(s.count for s in both.steps if s.item == "iron_pickaxe") == 1
    assert [s.item for s in both.steps][-1] == "diamond"


def test_unknown_items_and_single_kinds_of_wood():
    plan = plan_items({"elytra": 1, "birch_door": 1}, {}, K)
    assert plan.unknown == ["elytra"] and "recipe tool" in plan.describe()
    mined = [s for s in plan.steps if s.how == "mine"]
    assert any(s.item == "birch_log" and s.targets == ("birch_log",) for s in mined)
    assert all(s.item != "elytra" for s in plan.steps)


def test_every_benchmark_item_goal_can_be_planned_from_nothing():
    for task in TASKS:
        have = (task.check or {}).get("have") if isinstance(getattr(task, "check", None), dict) else None
        if not have:
            continue
        wanted = {item: n for item, n in have.items() if "*" not in item}
        if not wanted:
            continue
        plan = plan_items(wanted, {}, K)
        assert not plan.unknown, (task.name, plan.unknown)
        sim = contracts.simulate(plan.calls(), {}, K)
        assert sim.ok, (task.name, sim.problems)
        for item, n in wanted.items():
            assert sim.inventory.get(item, 0) >= n, (task.name, item, sim.inventory)


def test_measured_times_change_the_costs():
    slow = K.with_costs({"mine:iron_ore": 300.0, "mine:deepslate_iron_ore": 300.0})
    assert slow.costs()["raw_iron"] == 300.0 and K.costs()["raw_iron"] == 30.0
    assert slow.costs()["iron_pickaxe"] > K.costs()["iron_pickaxe"] + 500


def test_building_the_data_from_game_files(tmp_path):
    root = tmp_path / "data" / "minecraft"
    for folder, name, content in [
        ("recipe", "stick", {"type": "minecraft:crafting_shaped", "key": {"#": "#minecraft:planks"},
                             "pattern": ["#", "#"], "result": {"id": "minecraft:stick", "count": 4}}),
        ("recipe", "glass", {"type": "minecraft:smelting", "ingredient": "minecraft:sand",
                             "result": {"id": "minecraft:glass"}}),
        ("recipe", "odd", {"type": "minecraft:crafting_special_repairitem"}),
        ("tags/item", "planks", {"values": ["minecraft:oak_planks", "#minecraft:more_planks"]}),
        ("tags/item", "more_planks", {"values": ["minecraft:birch_planks"]}),
        ("tags/block", "mineable/pickaxe", {"values": ["minecraft:stone", "minecraft:iron_ore"]}),
        ("tags/block", "needs_stone_tool", {"values": ["minecraft:iron_ore"]}),
        ("loot_table/blocks", "gravel", {"pools": [{"rolls": 1, "entries": [{"type": "minecraft:alternatives", "children": [
            {"type": "minecraft:item", "name": "minecraft:gravel", "condition": "minecraft:tool/can_silk_touch"},
            {"type": "minecraft:item", "name": "minecraft:flint",
             "condition": {"type": "minecraft:table_bonus", "chances": [0.1, 0.2]}},
            {"type": "minecraft:item", "name": "minecraft:gravel"}]}]}]}),
        ("loot_table/entities", "cow", {"pools": [{"rolls": 1, "entries": [{"type": "minecraft:item",
            "name": "minecraft:beef", "modifier": [{"type": "minecraft:set_count",
                                                    "count": {"type": "minecraft:uniform", "min": 1, "max": 3}}]}]}]}),
    ]:
        path = root / folder / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content))
    data = kbuild.build(tmp_path, "test")
    assert [r["id"] for r in data["recipes"]] == ["glass", "stick"]
    stick = data["recipes"][1]
    assert stick["in"] == [{"any": ["#planks"], "n": 2}] and stick["n"] == 4 and "table" not in stick
    assert data["recipes"][0]["how"] == "smelt"
    assert data["item_tags"]["planks"] == ["oak_planks", "birch_planks"]
    assert data["blocks"]["gravel"] == {"flint": 0.1, "gravel": 0.9} and data["mobs"]["cow"] == {"beef": 2.0}
    assert data["tools"] == {"stone": {"tool": "pickaxe"}, "iron_ore": {"tool": "pickaxe", "tier": 1}}
    out = kbuild.write(data, tmp_path / "out" / "minecraft-test.json.gz")
    assert load(path=out).recipes["stick"][0].n == 4


# The dry run ----------------------------------------------------------------------------------

def test_a_dry_run_finds_the_first_step_that_cannot_work():
    calls = [{"tool": "mine", "args": {"block": "#minecraft:logs", "count": 2}},
             {"tool": "craft", "args": {"item": "#minecraft:planks", "count": 8}},
             {"tool": "craft", "args": {"item": "minecraft:stone_pickaxe", "count": 1}},
             {"tool": "mine", "args": {"block": "#minecraft:iron_ores", "count": 2}},
             {"tool": "smelt", "args": {"item": "minecraft:raw_iron", "count": 2}}]
    sim = contracts.simulate(calls, {}, K)
    assert sim.first() == "step 3: craft stone_pickaxe: 3 cobblestone, 2 stick short"
    assert [n for n, _ in sim.problems] == [3, 4, 5] and "stone pickaxe or better" in sim.problems[1][1]
    fine = contracts.simulate(calls[3:], {"minecraft:stone_pickaxe": 1}, K)
    assert fine.ok and fine.inventory == {"stone_pickaxe": 1, "iron_ingot": 2} and fine.seconds > 0
    assert contracts.seconds_for("mine", {"block": "minecraft:iron_ore", "count": 3}, {"mine:iron_ore": 40.0}) == 120.0


def test_skill_stats_average_what_worked(tmp_path):
    stats = SkillStats(tmp_path / "skillstats.json")
    for seconds in (30, 60, 90):
        stats.record("mine", {"block": ["minecraft:iron_ore", "minecraft:deepslate_iron_ore"], "count": 3}, True,
                     seconds, {"broken": 3})
    stats.record("mine", {"block": "minecraft:iron_ore"}, False, 500, {})
    stats.record("craft", {"item": "stick"}, True, 2.0, {})
    assert stats.measured() == {"mine:iron_ore": 20.0, "mine:deepslate_iron_ore": 20.0}
    assert stats.rate("mine:iron_ore") == 0.75 and stats.rate("craft") is None
    stats.save()
    assert SkillStats(tmp_path / "skillstats.json").measured()["mine:iron_ore"] == 20.0


# The plan tree --------------------------------------------------------------------------------

def nested_plan():
    return Plan([
        parse_step({"step": "tools", "done_when": {"have": {"stone_pickaxe": 1}}}, "1"),
        parse_step({"step": "get iron", "done_when": {"have": {"raw_iron": 3}}, "any": True, "substeps": [
            {"step": "mine the vein seen earlier"},
            {"step": "dig down and look", "substeps": [
                {"step": "go to y=16", "done_when": {"height": {"max": 16}}}, {"step": "mine with explore"}]},
        ]}, "2"),
        parse_step({"step": "smelt", "done_when": {"have": {"iron_ingot": 3}}}, "3"),
    ], {"have": {"iron_ingot": 3}})


def test_a_flat_plan_reads_as_before_and_a_nested_one_opens_the_step_in_hand():
    flat = Plan([parse_step({"step": "get logs", "done_when": {"have": {"oak_log": 3}}}, "1"),
                 parse_step({"step": "craft planks"}, "2")])
    assert flat.update(obs_with(oak_log=5)) == 1
    assert flat.describe() == "[x] 1. get logs; [ ] 2. craft planks" and flat.current()[0] == 2

    plan = nested_plan()
    assert plan.update(obs_with(stone_pickaxe=1)) == 1
    number, leaf = plan.current()
    assert number == 2 and leaf.text == "mine the vein seen earlier"
    text = plan.describe()
    assert text.startswith("[x] 1. tools; [ ] 2. get iron (one of: [ ] 2.1 mine the vein seen earlier; ")
    assert "[ ] 2.2 dig down and look (0 of 2 substeps done)" in text and text.endswith("[ ] 3. smelt")


def test_a_failed_way_gives_way_to_the_next_and_only_that_step_is_replanned():
    plan = nested_plan()
    plan.update(obs_with(stone_pickaxe=1))
    leaf = plan.current()[1]
    leaf.fail("found no visible iron_ore within 32 blocks")
    assert not leaf.failed and "failed once: found no visible" in plan.describe()
    leaf.fail("found no visible iron_ore within 32 blocks")
    # The first way is given up; the second is now in hand, and nothing is blocked yet.
    assert leaf.failed and plan.current()[1].text == "go to y=16" and plan.stuck() is None
    assert "[!] 2.1 mine the vein seen earlier (gave up" in plan.describe()
    for step in plan.find("2.2").children:
        step.fail("x"), step.fail("x")
    assert plan.stuck()[0] == "2" and plan.stuck()[1].text == "get iron"
    assert plan.describe().count("[!]") >= 2 and plan.current()[0] == 2
    # A condition met by other means still finishes the step, failures and all.
    assert plan.update(obs_with(stone_pickaxe=1, raw_iron=3)) == 1 and plan.current()[0] == 3
    with pytest.raises(ValueError, match="no step 7"):
        plan.find("7")
    with pytest.raises(ValueError, match="any needs substeps"):
        parse_step({"step": "x", "any": True}, "1")
    with pytest.raises(ValueError, match="nest at most"):
        parse_step({"step": "a", "substeps": [{"step": "b", "substeps": [{"step": "c", "substeps": [
            {"step": "d", "substeps": [{"step": "e"}]}]}]}]}, "1")


def test_plans_are_kept_on_disk_per_world_and_goal(tmp_path):
    store = GoalStore(tmp_path / "goals")
    plan = nested_plan()
    plan.update(obs_with(stone_pickaxe=1))
    plan.current()[1].fail("no ore")
    store.save("singleplayer:A", "Get iron", plan, {"house"})
    again, built = store.load("singleplayer:A", "get iron ")
    assert again.resumed and built == {"house"} and again.describe() == plan.describe()
    assert again.goal_check == {"have": {"iron_ingot": 3}}
    assert store.load("singleplayer:B", "Get iron") is None
    store.clear("singleplayer:A", "Get iron")
    assert store.load("singleplayer:A", "Get iron") is None
    store.save("singleplayer:A", "nothing planned", Plan())
    assert store.load("singleplayer:A", "nothing planned") is None


# The map and the records ----------------------------------------------------------------------

def test_the_map_remembers_ore_and_forgets_it_when_it_is_gone(tmp_path):
    world_map = WorldMap(tmp_path / "worldmap.json", "singleplayer:A")
    world_map.note_found([{"pos": [100, 40, 100], "block": "minecraft:iron_ore", "distance": 5},
                          {"pos": [101, 40, 100], "block": "minecraft:iron_ore", "distance": 6},
                          {"pos": [400, 12, -80], "block": "minecraft:deepslate_diamond_ore", "distance": 9},
                          {"pos": [3, 64, 3], "block": "minecraft:stone", "distance": 1}], "minecraft:overworld")
    world_map.save()
    again = WorldMap(tmp_path / "worldmap.json", "singleplayer:A")
    found = again.nearest(["iron_ore", "deepslate_iron_ore"], (0, 64, 0), "minecraft:overworld")
    assert [(e["block"], e["pos"], e["n"]) for e in found] == [("iron_ore", [100, 40, 100], 2)]
    assert again.nearest(["*"], (0, 64, 0), "minecraft:the_nether") == []
    assert again.lines((0, 64, 0), "minecraft:overworld") == [
        "iron_ore seen at 100 40 100 (143 blocks away)", "diamond_ore seen at 400 12 -80 (411 blocks away)"]
    assert WorldMap(tmp_path / "worldmap.json", "server:other").cells == {}
    # Looking again from right there and seeing none means it was mined.
    again.note_absent(["*_ore"], (102, 41, 99), 24, "minecraft:overworld")
    assert [e["block"] for e in again.nearest(["*_ore"], (0, 64, 0), "minecraft:overworld")] == [
        "deepslate_diamond_ore"]


def test_goal_records_are_found_by_wording_and_start_and_become_training_examples(tmp_path):
    pool = ExperiencePool(tmp_path / "experience.json")
    fresh = situation({}, 70, "minecraft:overworld")
    plan = nested_plan()
    plan.update(obs_with(stone_pickaxe=1, raw_iron=3, iron_ingot=3))
    pool.add("Make an iron pickaxe", "singleplayer:A", fresh, "success", True, 31, 400, plan.to_dict(),
             plan.outline(), ["mine: found no visible iron_ore"], "made it")
    pool.add("Make an iron pickaxe", "singleplayer:A", fresh, "turn_limit", False, 80, 900, None, [],
             ["goto: no path to 10 20 30"], "ran out of turns (80)")
    pool.add("Build a house", "singleplayer:A", fresh, "success", True, 40, 600, plan.to_dict(), plan.outline(),
             [], "built")
    again = ExperiencePool(tmp_path / "experience.json")
    found = again.similar("make me an iron pickaxe", fresh)
    assert [r["outcome"] for r in found] == ["success", "turn_limit"]
    lines = ExperiencePool.describe(found[0]) + ExperiencePool.describe(found[1])
    assert "worked after 31 turns" in lines[0] and "with no pickaxe, carrying nothing" in lines[0]
    assert "Plan at the end: [x] 1 tools" in lines[1] and "Trouble on the way: mine: found no visible" in lines[2]
    assert "ran out of turns after 80 turns" in lines[3] and "Why it stopped" in lines[-1]
    assert again.similar("tame a wolf", fresh) == []

    counts = plan_data.export(tmp_path, tmp_path / "out")
    assert counts["kept"] == 2 and counts["not a success"] == 1
    sample = json.loads((tmp_path / "out" / "plans.jsonl").read_text().splitlines()[0])
    assert sample["messages"][1]["content"].startswith("Goal: Make an iron pickaxe\nThe player started on the surface")
    call = sample["messages"][2]["tool_calls"][0]["function"]
    args = call["arguments"]
    assert call["name"] == "set_plan" and args["goal_check"] == {"have": {"iron_ingot": 3}}
    assert args["steps"][1]["any"] is True and "done" not in args["steps"][0] and "failed" not in json.dumps(args)
    assert sample["tools"][0]["function"]["name"] == "set_plan"

    # Records of collection and benchmark runs, kept in their own folders, are found too, and the
    # episode export adds the plan examples to the training file.
    other = ExperiencePool(tmp_path / "collect" / "agent" / "experience.json")
    other.add("Mine a diamond", "singleplayer:A", fresh, "success", True, 9, 300, plan.to_dict(), plan.outline(),
              [], "found one")
    rows, counts = plan_data.examples(tmp_path)
    assert counts["kept"] == 3 and counts["records"] == 4
    from minegpt_brain.train import dataset
    from minegpt_brain.train.finetune import normalize

    info = dataset.export([], tmp_path / "bundle", plan_rows=rows, bundle=False)
    assert info.plan_samples == 3 and info.train_samples == 3 and info.replies == 3
    written = [json.loads(line) for line in (tmp_path / "bundle" / "train.jsonl").read_text().splitlines()]
    messages, learn = normalize(written[0]["messages"])
    assert learn == [True] and messages[2]["tool_calls"][0]["function"]["arguments"]["goal_check"]


# In the agent ---------------------------------------------------------------------------------

class PaperWorld:
    """A fake game that follows the real recipes and drops, with ore that can run out."""

    def __init__(self, missing=()):
        self.missing = set(missing)
        self.calls = []

    def __call__(self, mod, name, args):
        self.calls.append((name, dict(args)))
        if name == "mine":
            blocks = args["block"] if isinstance(args["block"], list) else [args["block"]]
            if any(b in self.missing for b in blocks):
                return False, f"found no visible {' or '.join(blocks)} within 32 blocks", {}
        carried = {}
        for slot in mod.slots:
            carried[slot["id"]] = carried.get(slot["id"], 0) + slot["count"]
        if name in ("mine", "craft", "smelt"):
            sim = contracts.simulate([{"tool": name, "args": args}], carried, K)
            if not sim.ok:
                return False, sim.problems[0][1], {}
            mod.slots[:] = [{"slot": i, "id": f"minecraft:{item}", "count": n}
                            for i, (item, n) in enumerate(sim.inventory.items())]
            return True, f"{name} done", {"broken": args.get("count", 1)} if name == "mine" else {}
        return True, "done", {}


@pytest.fixture
def game(tmp_path):
    mod = FakeMod()
    mod.slots[:] = []
    mod.goal = "Make an iron pickaxe"
    bridge = Bridge.connect(port=mod.port, timeout=5)

    def make(script, world=None, **config):
        mod.skill_handler = world or PaperWorld()
        backend = ScriptedBackend(script)
        return Agent(bridge, backend, AgentConfig(data_dir=tmp_path, poll=0.05, **config)), backend

    yield mod, make, tmp_path
    bridge.close()


def test_run_plan_makes_an_iron_pickaxe_from_nothing_in_one_turn(game):
    mod, make, tmp_path = game
    agent, backend = make([
        [("run_plan", {"items": {"iron_pickaxe": 1}})],
        [("goal_done", {"summary": "made the pickaxe"})],
    ])
    result = agent.run_goal("Make an iron pickaxe")
    assert result.outcome == "success" and result.verified and result.turns == 2
    out, note = backend.conversations[0].results(0)[1][0], backend.conversations[0].results(0)[2]
    assert not out.is_error and "All steps done" in out.content and "11 steps" in out.content.splitlines()[0]
    assert "[x] 1. get 1 iron_pickaxe (11 of 11 substeps done)" in note
    carried = {s["id"]: s["count"] for s in mod.slots}
    assert carried["minecraft:iron_pickaxe"] == 1 and carried["minecraft:stone_pickaxe"] == 1
    # The run is on record, with its plan, and timings were kept.
    record = json.loads((tmp_path / "experience.json").read_text())["records"][0]
    assert record["outcome"] == "success" and record["situation"]["tier"] == -1
    assert record["plan"]["goal_check"] == {"have": {"iron_pickaxe": 1}} and len(record["outline"]) == 12
    assert "craft" in json.loads((tmp_path / "skillstats.json").read_text())["skills"]
    assert not list((tmp_path / "goals").glob("*.json"))


def test_run_plan_stops_where_it_is_stuck_and_carries_on_later(game):
    mod, make, tmp_path = game
    world = PaperWorld(missing={"minecraft:iron_ore", "minecraft:deepslate_iron_ore"})
    seen = WorldMap(tmp_path / "worldmap.json", mod.world)
    seen.note_found([{"pos": [200, 30, 40], "block": "minecraft:iron_ore", "distance": 3}], "minecraft:overworld")
    seen.save()

    def found_ore(conversation):
        world.missing.clear()
        return [("goto", {"x": 200, "z": 40}), ("run_plan", {"items": {"iron_pickaxe": 1}, "step": "2"})]

    agent, backend = make([
        [("set_plan", {"steps": [{"step": "find iron"}, {"step": "make the pickaxe"}],
                       "goal_check": {"have": {"iron_pickaxe": 1}}})],
        [("run_plan", {"items": {"iron_pickaxe": 1}, "step": "2"}), ("goto", {"x": 1, "z": 1})],
        [("recall", {"block": "#minecraft:iron_ores"})],
        found_ore,
        [("goal_done", {"summary": "done"})],
    ], world=world)
    result = agent.run_goal("Make an iron pickaxe")
    conversation = backend.conversations[0]
    assert "Ore seen earlier in this world" in conversation.messages[0][1]
    stopped, skipped = conversation.results(1)[1]
    assert stopped.is_error and "Stopped at step 9 of 11: failed: found no visible" in stopped.content
    assert "Left to do: mine 3 deepslate_iron_ore, iron_ore for 3 raw_iron; smelt 3 raw_iron" in stopped.content
    assert "Seen before: iron_ore at 200 30 40" in stopped.content and "run_plan again" in stopped.content
    assert skipped.content == "not run: skipped because run_plan failed"
    assert "2.9 mine 3 deepslate_iron_ore, iron_ore for 3 raw_iron (failed once" in conversation.results(1)[2]
    assert "iron_ore at 200 30 40" in conversation.results(2)[1][0].content
    resumed = conversation.results(3)[1][1]
    # The second call starts from what is carried: only the iron is left to do.
    assert not resumed.is_error and "3 steps" in resumed.content.splitlines()[0]
    assert result.outcome == "success" and result.verified
    assert [name for name, _ in world.calls].count("mine") == 4


def test_a_routine_that_cannot_work_is_refused_on_paper(game):
    mod, make, _ = game
    agent, backend = make([
        [("save_routine", {"name": "stone pick", "description": "from cobblestone and sticks", "steps": [
            {"tool": "craft", "args": {"item": "stone_pickaxe"}}]})],
        [("run_routine", {"name": "stone pick"})],
        [("run_routine", {"name": "stone pick", "force": True})],
        [("goal_failed", {"reason": "test over"})],
    ])
    agent.run_goal("Make an iron pickaxe")
    conversation = backend.conversations[0]
    refused = conversation.results(1)[1][0]
    assert refused.is_error and "would fail at step 1: craft stone_pickaxe: 3 cobblestone, 2 stick short" in refused.content
    forced = conversation.results(2)[1][0]
    assert "Stopped at step 1 of 1" in forced.content
    # Offered again for a like goal, the routine comes with the same warning.
    agent2, backend2 = make([[("goal_failed", {"reason": "test over"})]])
    agent2.run_goal("stone pick please")
    assert "from what you carry now it would fail at step 1" in backend2.conversations[0].messages[0][1]


def test_an_interrupted_goal_carries_on_from_its_saved_plan(game):
    mod, make, tmp_path = game
    steps = [{"step": "wood", "done_when": {"have": {"*_log": 2}}},
             {"step": "pickaxe", "substeps": [{"step": "planks"}, {"step": "craft it"}]}]
    agent, _ = make([
        [("set_plan", {"steps": steps, "goal_check": {"have": {"wooden_pickaxe": 1}}})],
        [("mine", {"block": "#minecraft:logs", "count": 2})],
        [("expand_step", {"step": "2", "any": True, "substeps": [{"step": "craft it by hand"},
                                                                 {"step": "use run_plan"}]})],
    ], max_turns=3, turns_per_step=0)
    agent.goal = "Make a wooden pickaxe"
    first = agent.run_goal("Make a wooden pickaxe")
    assert first.outcome == "turn_limit" and len(list((tmp_path / "goals").glob("*.json"))) == 1

    agent2, backend2 = make([
        [("run_plan", {"items": {"wooden_pickaxe": 1}})],
        [("goal_done", {"summary": "done"})],
    ])
    mod.set_goal("Make a wooden pickaxe")
    agent2.goal = "Make a wooden pickaxe"
    second = agent2.run_goal("Make a wooden pickaxe")
    opening = backend2.conversations[0].messages[0][1]
    assert "This goal was started before and interrupted" in opening
    assert "[x] 1. wood; [ ] 2. pickaxe (one of: [ ] 2.1 craft it by hand; [ ] 2.2 use run_plan)" in opening
    assert "Earlier goals like this one" in opening and "ran out of turns after 3 turns" in opening
    assert second.outcome == "success" and second.verified
    assert not list((tmp_path / "goals").glob("*.json"))


def test_benchmark_and_collection_runs_do_not_pick_up_old_plans(game):
    mod, make, tmp_path = game
    script = [[("set_plan", {"steps": [{"step": "wood", "done_when": {"have": {"*_log": 2}}}]})],
              [("mine", {"block": "#minecraft:logs", "count": 2})], [("look_around", {})]]
    agent, _ = make(list(script), max_turns=3, turns_per_step=0, resume=False)
    assert agent.run_goal("Make an iron pickaxe").outcome == "turn_limit"
    assert not list((tmp_path / "goals").glob("*.json"))
    mod.slots[:] = []
    mod.set_goal("Make an iron pickaxe")
    agent2, backend2 = make(list(script), max_turns=1, resume=False)
    agent2.goal = "Make an iron pickaxe"
    agent2.run_goal("Make an iron pickaxe")
    opening = backend2.conversations[0].messages[0][1]
    assert "started before and interrupted" not in opening and "Earlier goals like this one" in opening


def test_a_call_that_keeps_failing_marks_its_plan_step(game):
    mod, make, _ = game
    world = PaperWorld(missing={"minecraft:diamond_ore"})
    mine = ("mine", {"block": "minecraft:diamond_ore", "count": 1})
    agent, backend = make([
        [("set_plan", {"steps": [{"step": "diamonds", "done_when": {"have": {"diamond": 1}}}]})],
        [mine], [mine], [mine],
        [("goal_failed", {"reason": "none here"})],
    ], world=world)
    agent.run_goal("Make an iron pickaxe")
    conversation = backend.conversations[0]
    assert "marked [!]" not in conversation.results(2)[1][0].content
    last = conversation.results(3)
    assert "Plan step 1 is now marked [!]" in last[1][0].content
    assert "Step 1 (diamonds) is stuck: found no visible" in last[2] and "expand_step" in last[2]
