import json

import pytest

from minegpt_brain.agent import Agent, AgentConfig, critic
from minegpt_brain.agent.blueprints import BlueprintLibrary, expand, from_relative, materials, place_at, size
from minegpt_brain.bench import TASKS, Task, block_positions, check, format_goal, get
from minegpt_brain.bench.__main__ import print_report, run_task
from minegpt_brain.bridge import Bridge
from minegpt_brain.observation import Observation
from fake_mod import FakeMod, make_obs
from test_agent import ScriptedBackend


# Blueprints --------------------------------------------------------------------------------

HUT = {"key": {"c": "cobblestone", "p": "oak_planks"},
       "layers": [["ccc", "c.c", "ccc"], ["ppp", "p p", "ppp"]]}


def test_expands_a_drawing_bottom_up():
    blocks = expand(HUT["layers"], HUT["key"])
    assert len(blocks) == 16
    assert (0, 0, 0, "minecraft:cobblestone") in blocks and (1, 1, 2, "minecraft:oak_planks") in blocks
    assert (1, 0, 1, "minecraft:cobblestone") not in blocks  # '.' leaves the middle alone
    assert size(blocks) == (3, 2, 3)
    assert materials(blocks) == {"minecraft:cobblestone": 8, "minecraft:oak_planks": 8}
    assert place_at(blocks, 10, 64, -5)[0] == [10, 64, -5, "minecraft:cobblestone"]


@pytest.mark.parametrize("layers, key, problem", [
    ([], {"c": "stone"}, "layers is a list"),
    ([["cx"]], {"c": "stone"}, "'x' is not in the key"),
    ([["c"]], {"c": "#minecraft:logs"}, "one block item"),
    ([["c"]], {"cc": "stone"}, "single characters"),
    ([["..."]], {"c": "stone"}, "no blocks"),
])
def test_rejects_bad_drawings(layers, key, problem):
    with pytest.raises(ValueError, match=problem):
        expand(layers, key)


def test_relative_blocks_are_checked():
    assert from_relative([[0, 1, 2, "dirt"]]) == [(0, 1, 2, "minecraft:dirt")]
    with pytest.raises(ValueError, match="block 1"):
        from_relative([[0, 1.5, 2, "dirt"]])


def test_blueprints_are_saved_and_found_by_name(tmp_path):
    library = BlueprintLibrary(tmp_path / "blueprints.json")
    library.save("Small Hut", "a hut", HUT["layers"], HUT["key"])
    again = BlueprintLibrary(tmp_path / "blueprints.json")
    assert len(again.blocks("small hut")) == 16
    with pytest.raises(ValueError, match="saved blueprints: 'Small Hut'"):
        again.blocks("castle")


@pytest.fixture
def world(tmp_path):
    mod = FakeMod()
    bridge = Bridge.connect(port=mod.port, timeout=5)
    yield mod, bridge, tmp_path
    bridge.close()


def test_build_tool_sends_world_coordinates_to_the_mod(world):
    mod, bridge, tmp_path = world
    sent = []
    mod.skill_handler = lambda m, name, args: (sent.append((name, args)) or True,
                                                "built it: placed 16", {"placed": 16, "failed": 0})
    agent = Agent(bridge, ScriptedBackend([
        [("build", {"x": 10, "y": 64, "z": -5, **HUT, "clear": True})],
        [("save_blueprint", {"name": "hut", "description": "a hut", **HUT})],
        [("build", {"x": 0, "y": 70, "z": 0, "blueprint": "hut"}), ("build", {"x": 0, "y": 70, "z": 0})],
        [("goal_done", {"summary": "built"})],
    ]), AgentConfig(data_dir=tmp_path, poll=0.05))
    agent.goal = mod.goal = "build a hut"
    result = agent.run_goal("build a hut")

    assert result.outcome == "success"
    name, args = sent[0]
    assert name == "build" and args["clear"] is True and len(args["blocks"]) == 16
    assert [10, 64, -5, "minecraft:cobblestone"] in args["blocks"]
    assert sent[1][1]["blocks"][0] == [0, 70, 0, "minecraft:cobblestone"]
    conversation = agent.backend.conversations[0]
    saved = conversation.results(1)[1][0].content
    assert "3 wide (x), 2 high, 3 deep (z), 16 blocks. Needs 8 cobblestone, 8 oak_planks." in saved
    bad = conversation.results(2)[1][1]
    assert bad.is_error and "exactly one of blueprint" in bad.content
    # A build at fixed coordinates is no use as a routine elsewhere.
    routines = json.loads((tmp_path / "routines.json").read_text())["routines"] if (tmp_path / "routines.json").exists() else {}
    assert "build a hut" not in routines


def test_storing_remembers_what_the_chest_holds(world):
    mod, bridge, tmp_path = world
    mod.skill_handler = lambda m, name, args: (True, "stored 64 items in the chest at 5 64 2",
                                                {"x": 5, "y": 64, "z": 2, "stored": {"minecraft:cobblestone": 64},
                                                 "contents": {"minecraft:cobblestone": 128, "minecraft:dirt": 3}})
    agent = Agent(bridge, ScriptedBackend([[("store", {"items": ["cobblestone"]})],
                                           [("goal_done", {"summary": "stored"})]]),
                  AgentConfig(data_dir=tmp_path, poll=0.05))
    agent.goal = mod.goal = "tidy up"
    agent.run_goal("tidy up")
    memory = json.loads((tmp_path / "memory.json").read_text())["worlds"][mod.world]["entries"]
    assert memory == [{"kind": "chest", "note": "holds cobblestone 128, dirt 3", "pos": [5, 64, 2],
                       "dimension": "minecraft:overworld", "time": memory[0]["time"]}]


def test_hints_for_new_skills():
    assert "partial=true" in critic.hint("not enough blocks: need 12 more minecraft:cobblestone")
    assert "protected" in critic.hint("oak_planks at 1, 2, 3: it is in a protected region")
    assert "one to three in-game days" in critic.hint("nothing to do: no ripe crops within 16 blocks")
    assert "other players" in critic.hint("sleep timed out after 660s")


# Benchmark ---------------------------------------------------------------------------------

def test_goal_text_gets_positions():
    assert format_goal("floor from {x+2} {z+2} to {x+6} {z-1} at {y}", (10, 64, -3)) == "floor from 12 -1 to 16 -4 at 64"


def test_tasks_by_name_and_tier():
    assert [t.name for t in get(["logs", "iron"])][:2] == ["logs", "raw_iron"]
    assert len(get(["all"])) == len(TASKS) >= 20
    assert len({t.name for t in TASKS}) == len(TASKS)
    with pytest.raises(ValueError, match="unknown"):
        get(["nope"])


def _obs(slots=(), day_time=1000):
    return Observation(make_obs(slots=list(slots), day_time=day_time))


def test_checks_items_blocks_and_time():
    task = Task("t", "x", "g", check={"have": {"*_log": 2}}, at_most={"dirt": 0})
    ok, detail = check(task, (0, 0, 0), _obs([{"slot": 0, "id": "minecraft:oak_log", "count": 2}]))
    assert ok and detail == "ok"
    ok, detail = check(task, (0, 0, 0), _obs([{"slot": 0, "id": "minecraft:dirt", "count": 5}]))
    assert not ok and "have 0 of 2" in detail and "still carrying 5 dirt" in detail

    build = Task("b", "x", "g", blocks=[(0, 0, 0, "cobblestone"), (1, 0, 0, "cobblestone")], min_fraction=1.0)
    assert block_positions(build, (5, 64, 5)) == [[5, 64, 5], [6, 64, 5]]
    assert check(build, (5, 64, 5), _obs(), ["minecraft:cobblestone", "minecraft:cobblestone"])[0]
    assert not check(build, (5, 64, 5), _obs(), ["minecraft:cobblestone", "minecraft:air"])[0]

    sleep = Task("s", "x", "g", morning=True)
    assert check(sleep, (0, 0, 0), _obs(day_time=24500))[0]
    assert not check(sleep, (0, 0, 0), _obs(day_time=15000))[0]


def test_runs_a_task_and_checks_it_independently(world, tmp_path):
    mod, bridge, _ = world
    mod.skill_handler = lambda m, name, args: (True, "done", {})
    mod.world_blocks = {(0, 64, 0): "minecraft:dirt", (0, 65, 0): "minecraft:dirt"}
    agent = Agent(bridge, ScriptedBackend([[("goal_done", {"summary": "built"})]]),
                  AgentConfig(data_dir=tmp_path, poll=0.05, jobs=False))
    task = Task("tower", "build", "Build a pillar at {x} {y} {z}.", 5, blocks=[(0, 0, 0, "dirt"), (0, 1, 0, "dirt"),
                                                                              (0, 2, 0, "dirt")], min_fraction=1.0)
    record = run_task(agent, task, 1, "test", do_reset=False, rng=None)
    # The agent said it was done, but the benchmark looks for itself.
    assert record["outcome"] == "success" and not record["passed"] and "2 of 3 blocks" in record["detail"]
    assert record["goal"] == "Build a pillar at 0 64 0." and mod.commands == []

    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(record) + "\n" + json.dumps({**record, "label": "next", "passed": True}) + "\n")
    print_report(results)


def test_reset_uses_commands(world, tmp_path):
    import random
    from minegpt_brain.bench.__main__ import reset
    mod, bridge, _ = world
    agent = Agent(bridge, ScriptedBackend([]), AgentConfig(data_dir=tmp_path, jobs=False))
    task = next(t for t in TASKS if t.name == "store")
    reset(bridge, agent, task, random.Random(1))
    assert mod.commands[:2] == ["gamemode survival @s", "clear @s"]
    assert "setblock ~2 ~ ~ minecraft:chest" in mod.commands
    assert "give @s minecraft:cobblestone 64" in mod.commands
    assert not any(c.startswith("spreadplayers") for c in mod.commands)  # store starts where the player is


def test_stops_when_the_model_never_answers(world, monkeypatch):
    from minegpt_brain.bench import __main__ as bench_main
    from minegpt_brain.llm import BackendError
    mod, bridge, tmp_path = world

    class NoKey(ScriptedBackend):
        def start(self, system, tools):
            raise BackendError("the Claude API rejected the credentials: set ANTHROPIC_API_KEY")

    monkeypatch.setattr(bench_main, "backend_from_args", lambda args: NoKey([]))
    monkeypatch.setattr(bench_main.Bridge, "connect", staticmethod(lambda host, port: bridge))
    code = bench_main.main(["--tasks", "logs,planks", "--no-reset", "--data-dir", str(tmp_path)])
    assert code == 1
    rows = [json.loads(line) for line in (tmp_path / "results.jsonl").read_text().splitlines()] \
        if (tmp_path / "results.jsonl").exists() else []
    assert rows == []  # the planks task never started


def test_tries_elsewhere_when_spreadplayers_does_not_move_the_player(world, monkeypatch):
    import random
    from minegpt_brain.bench import __main__ as bench_main
    mod, bridge, tmp_path = world
    monkeypatch.setattr(bench_main, "_wait_for_ground", lambda agent: None)
    agent = Agent(bridge, ScriptedBackend([]), AgentConfig(data_dir=tmp_path, jobs=False))
    bench_main._move_far(bridge, agent, random.Random(1), tries=3)
    assert sum(c.startswith("spreadplayers") for c in mod.commands) == 3


def test_build_sites_are_flattened():
    for name in ("platform", "tower", "hut"):
        task = next(t for t in TASKS if t.name == name)
        assert any(c.startswith("fill") and c.endswith("minecraft:air") for c in task.setup)
