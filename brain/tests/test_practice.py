"""Practice: the skill tuner, the failure miner, drills, replays and the practice curriculum."""

import json
import random
import re
import time
from pathlib import Path

import pytest

from fake_mod import FakeMod
from minegpt_brain.agent.loop import Agent, AgentConfig
from minegpt_brain.bench import Task
from minegpt_brain.bridge import Bridge
from minegpt_brain.observation import Observation
from minegpt_brain.practice import failures as fl
from minegpt_brain.practice.curriculum import Picker, frontier, failed_goals
from minegpt_brain.practice.drills import DRILLS, Drill, check
from minegpt_brain.practice.replay import moved_args, replay, replayable
from minegpt_brain.practice.tuner import KNOBS, MIN_RUNS, Tuner
from minegpt_brain.practice.__main__ import gate_worse, main

from fake_mod import make_obs

REPO = Path(__file__).resolve().parents[2]


class NoModel:
    name = "none"


# Scenes ---------------------------------------------------------------------------------------

def make_scene(fill=fl.PASSABLE, ground=fl.SOLID, set_cells=(), blocks=None, **flags):
    """A 9x6x9 scene, feet at (4, 2, 4) in the box: ground below y=2 unless changed."""
    w, h, d = 9, 6, 9
    cells = [[[ground if y < 2 else fill for x in range(w)] for z in range(d)] for y in range(h)]
    for (dx, dy, dz), c in set_cells:
        cells[2 + dy][4 + dz][4 + dx] = c
    flat = "".join(str(cells[y][z][x]) for y in range(h) for z in range(d) for x in range(w))
    palette = ["minecraft:air", "minecraft:stone"]
    block_ids = [1 if c == "2" else 0 for c in flat]
    for (dx, dy, dz), name in (blocks or {}).items():
        if name not in palette:
            palette.append(name)
        block_ids[((2 + dy) * d + 4 + dz) * w + 4 + dx] = palette.index(name)
    scene = {"feet": [100, 64, 200], "exact": [100.5, 64.0, 200.5], "yaw": 90.0, "pitch": 10.0,
             "dimension": "minecraft:overworld", "onGround": True, "inWater": False, "underWater": False,
             "origin": [96, 62, 196], "size": [w, h, d], "palette": palette, "blocks": block_ids, "cells": flat}
    scene.update(flags)
    return scene


def pit_scene():
    walls = [((dx, dy, dz), fl.SOLID) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)) for dy in (0, 1)]
    return make_scene(set_cells=walls)


def test_tags_read_the_scene():
    assert fl.tags(pit_scene())[0] == "in a pit"
    assert "in water" in fl.tags(make_scene(inWater=True))
    torch = make_scene(blocks={(1, 0, 0): "minecraft:torch"})
    assert "plant or torch in the way" in fl.tags(torch)
    lava = make_scene(set_cells=[((2, 0, 0), fl.DANGER)])
    assert "lava or fire near" in fl.tags(lava)
    assert fl.tags(make_scene()) == []
    assert fl.tags(None) == []


def test_gist_takes_out_numbers():
    assert fl.gist("made no headway near 12 64 -30 for 12s") == fl.gist("made no headway near 1 70 5 for 12s")
    assert "minecraft:" not in fl.gist("cannot see minecraft:grass_block at 1 2 3")


def test_failures_are_filed_grouped_and_ranked(tmp_path):
    log = fl.FailureLog(tmp_path / "failures")
    log.add_failure("goto", {"x": 1, "z": 2}, "made no headway near 1 2 3 for 12s", 30, pit_scene(), "get iron")
    log.add_failure("goto", {"x": 5, "z": 9}, "made no headway near 9 9 9 for 12s", 40, pit_scene(), "get iron")
    log.add_failure("mine", {"block": "stone"}, "timed out after 300s", 300, make_scene(), "get stone")
    stall = log.add_stall({"skill": "goto", "status": "pillar up at 3 4 5: jumping", "seconds": 15,
                           "scene": make_scene(inWater=True)}, "get iron", {"x": 3, "y": 70, "z": 5})
    log.end_stall({"skill": "goto", "seconds": 95})
    records = log.records()
    assert len(records) == 4
    assert next(r for r in records if r["id"] == stall["id"])["seconds"] == 95
    groups = fl.report(records)
    assert groups[0].skill == "mine" and groups[0].minutes == 5
    pit = next(g for g in groups if "in a pit" in g.cause)
    assert pit.count == 2 and pit.seconds == 70
    assert fl.stall_minutes(records) == pytest.approx(95 / 60)
    assert log.get(stall["id"][:15])["kind"] in ("stall", "fail")
    assert log.stalls == 1


def test_a_scene_becomes_a_junit_fixture():
    record = {"id": "x-1", "cause": "goto: stuck [in a pit]", "message": "m", "goal": "g", "scene": pit_scene()}
    text = fl.fixture(record, "escape")
    lines = text.splitlines()
    assert "size 9 6 9" in lines and "start 4 2 4" in lines and "expect escape" in lines
    rows = lines[lines.index("cells") + 1:]
    assert len(rows) == 6 * 9 and all(len(r) == 9 for r in rows)
    assert rows[2 * 9 + 4][5] == "2", "the wall east of the feet"
    with pytest.raises(ValueError):
        fl.fixture({"id": "y", "cause": "c", "scene": None})


def test_scene_commands_rebuild_the_box_water_last():
    scene = make_scene(blocks={(1, 0, 0): "minecraft:water[level=0]", (0, -1, 0): "minecraft:oak_planks"})
    commands = fl.scene_commands(scene, (10, 60, 20))
    assert commands[0].startswith("fill 9 60 19 19 68 29 minecraft:air")
    assert "setblock 14 61 24 minecraft:oak_planks" in commands
    assert commands[-1] == "setblock 15 62 24 minecraft:water[level=0]"
    assert not any("minecraft:air" in c for c in commands[2:])


def test_mod_scene_test_reads_what_keep_writes():
    """The JUnit side parses the same format; check the Java test exists with the same keywords."""
    java = REPO / "src" / "test" / "java" / "com" / "xzier9" / "minegpt" / "path" / "SceneTest.java"
    if not java.exists():
        pytest.skip("mod sources not next to the brain")
    text = java.read_text()
    for word in ('"size"', '"start"', '"expect"', '"breaking"', '"cells"', '"scaffold"'):
        assert word in text


# Tuner ------------------------------------------------------------------------------------------

def test_knobs_match_the_mod():
    java = REPO / "src" / "client" / "java" / "com" / "xzier9" / "minegpt" / "client" / "skill" / "Tuning.java"
    if not java.exists():
        pytest.skip("mod sources not next to the brain")
    found = re.findall(r'(whole|number)\("([a-z_.]+)", ([\d.]+), ([\d.]+), ([\d.]+),', java.read_text())
    assert {name for _, name, *_ in found} == set(KNOBS)
    for kind, name, base, low, high in found:
        k = KNOBS[name]
        assert (k.default, k.low, k.high, k.whole) == (float(base), float(low), float(high), kind == "whole"), name


def test_candidates_stay_in_range():
    for knob in KNOBS.values():
        values = knob.candidates()
        assert knob.default in values
        assert all(knob.low <= v <= knob.high for v in values)


def test_tuner_sends_nothing_until_something_is_learnt(tmp_path):
    tuner = Tuner(tmp_path / "tuning.json", random.Random(1))
    assert tuner.settings("goto") == ({}, None)
    tried = [tuner.settings("mine", explore=True)[1] for _ in range(40)]
    trials = [t for t in tried if t]
    assert trials and all(name.startswith("mine.") for name, _ in trials)
    assert tuner.settings("craft", explore=True) == ({}, None), "craft has no settings"


def test_tuner_promotes_a_clearly_better_value_and_can_undo_it(tmp_path):
    tuner = Tuner(tmp_path / "tuning.json", random.Random(2))
    since = time.time() - 1
    for _ in range(MIN_RUNS):
        tuner.record("goto", {"x": 1}, None, False, 60)  # the default fails
    promoted = None
    for _ in range(MIN_RUNS):
        promoted = tuner.record("goto", {"x": 1}, ("goto.still_ticks", 60.0), True, 20) or promoted
    assert promoted and "goto.still_ticks: 100 -> 60" in promoted
    assert tuner.value("goto.still_ticks") == 60
    assert tuner.settings("goto")[0] == {"goto.still_ticks": 60.0}
    again = Tuner(tmp_path / "tuning.json")
    assert again.value("goto.still_ticks") == 60, "kept on disk"
    assert again.revert_since(since) == ["goto.still_ticks: back to 100"]
    assert again.value("goto.still_ticks") == 100
    again.reset()
    assert again.table()[0]["runs"] == 0


def test_tuner_does_not_promote_on_a_small_edge(tmp_path):
    tuner = Tuner(tmp_path / "tuning.json")
    for _ in range(MIN_RUNS):
        tuner.record("mine", {"count": 4}, None, True, 40, {"broken": 4})
        tuner.record("mine", {"count": 4}, ("mine.batch", 15.0), True, 39.5, {"broken": 4})
    assert tuner.value("mine.batch") == 12


def test_gate_worse():
    before = {"passed": 10, "runs": 12, "seconds": 30}
    assert gate_worse(before, {"passed": 9, "runs": 12, "seconds": 30}) is None
    assert gate_worse(before, {"passed": 8, "runs": 12, "seconds": 30})
    assert gate_worse(before, {"passed": 10, "runs": 12, "seconds": 40})
    assert gate_worse(before, {"passed": 11, "runs": 12, "seconds": 40}) is None


# Agent hooks ------------------------------------------------------------------------------------

@pytest.fixture
def mod_and_agent(tmp_path):
    mod = FakeMod()
    bridge = Bridge.connect(port=mod.port, timeout=5)

    def make(**config):
        return Agent(bridge, NoModel(), AgentConfig(data_dir=tmp_path / "agent", shared_dir=tmp_path, poll=0.05,
                                                    **config))

    yield mod, make, tmp_path
    bridge.close()


def test_failed_skill_scene_is_filed_not_shown(mod_and_agent):
    mod, make, tmp_path = mod_and_agent
    scene = pit_scene()
    mod.skill_handler = lambda m, name, args: (False, "made no headway near 1 2 3 for 12s", {"scene": scene, "x": 1})
    agent = make()
    result = agent._skill("goto", {"x": 5, "z": 5})
    assert not result.success and "scene" not in result.data and result.data == {"x": 1}
    records = agent.failures.records()
    assert len(records) == 1 and records[0]["tags"][0] == "in a pit" and records[0]["args"] == {"x": 5, "z": 5}
    sent = [r for r in mod.received if r["op"] == "skill"][-1]
    assert sent["tuning"] == {}
    agent.tuner.save()
    assert json.loads((tmp_path / "tuning.json").read_text())["knobs"]


def test_tuned_values_are_sent_and_explore_tries_others(mod_and_agent):
    mod, make, tmp_path = mod_and_agent
    tuner = Tuner(tmp_path / "tuning.json")
    tuner.knobs["mine.batch"] = {"best": 8}
    tuner.save()
    agent = make()
    agent._skill("mine", {"block": "stone", "count": 2})
    assert [r for r in mod.received if r["op"] == "skill"][-1]["tuning"] == {"mine.batch": 8}
    explorer = make(explore=True)
    explorer.tuner.rng = random.Random(3)
    for _ in range(12):
        explorer._skill("mine", {"block": "stone", "count": 2})
    sent = [r["tuning"] for r in mod.received if r["op"] == "skill"]
    assert any(set(t) - {"mine.batch"} or t.get("mine.batch") != 8 for t in sent)
    off = make(tune=False)
    off._skill("mine", {"block": "stone", "count": 2})
    assert "tuning" not in [r for r in mod.received if r["op"] == "skill"][-1]


def test_stall_events_are_filed_with_the_running_args(mod_and_agent):
    mod, make, tmp_path = mod_and_agent

    def stalling(m, name, args):
        m.send_event("stall", skill="goto", status="pillar up: jumping", seconds=15, scene=make_scene())
        m.send_event("stall_end", skill="goto", seconds=42)
        time.sleep(0.1)
        return True, "arrived", {}

    mod.skill_handler = stalling
    agent = make()
    agent._skill("goto", {"x": 9, "z": 9})
    time.sleep(0.2)
    agent.poll_events()
    stalls = [r for r in agent.failures.records() if r["kind"] == "stall"]
    assert len(stalls) == 1 and stalls[0]["seconds"] == 42 and stalls[0]["args"] == {"x": 9, "z": 9}


# Drills, replays, curriculum ---------------------------------------------------------------------

def obs_at(x, z, slots=()):
    return Observation(make_obs(x=x, z=z, slots=list(slots)))


def test_drill_checks():
    walk = DRILLS["walk"]
    assert check(walk, {"x": 10, "z": 0}, obs_at(0, 0), obs_at(10.5, 0.5))[0]
    ok, detail = check(walk, {"x": 10, "z": 0}, obs_at(0, 0), obs_at(2, 0))
    assert not ok and "blocks off" in detail
    logs = DRILLS["logs"]
    got = [{"slot": 0, "id": "minecraft:birch_log", "count": 6}]
    assert check(logs, {}, obs_at(0, 0), obs_at(0, 0, got))[0]
    assert not check(logs, {}, obs_at(0, 0), obs_at(0, 0, [{"slot": 0, "id": "minecraft:birch_log", "count": 2}]))[0]


def test_replay_rebuilds_the_scene_and_moves_the_args(mod_and_agent):
    mod, make, tmp_path = mod_and_agent
    mod.pos = (500.5, 70.0, -40.5)
    record = {"id": "r1", "cause": "goto: stuck [in a pit]", "skill": "goto", "args": {"x": 110, "z": 200},
              "scene": pit_scene()}
    assert replayable(record)
    assert not replayable({**record, "skill": "build"})
    agent = make()
    row = replay(agent, record, random.Random(0), do_reset=False)
    assert row["passed"] and row["kind"] == "replay"
    # Feet (100, 64, 200) now stand at (500, 70, -41): every x, y and z moves by the same amount.
    assert moved_args({"x": 110, "z": 200, "count": 3}, (400, 6, -241)) == {"x": 510, "z": -41, "count": 3}
    sent = [r for r in mod.received if r["op"] == "skill"][-1]
    assert sent["args"] == {"x": 510, "z": -41}
    assert any(c.startswith("setblock 501 70 -41 minecraft:stone") for c in mod.commands)
    assert mod.commands[-1].startswith("tp @s 500.5 70.0 -40.5 90.0 10.0")


def test_frontier_and_picker():
    tasks = [Task("a", "wood", "a"), Task("b", "wood", "b"), Task("c", "stone", "c"), Task("d", "iron", "d")]
    assert frontier({}, tasks) == "wood"
    mastered = {"a": (4, 4), "b": (3, 3)}
    assert frontier(mastered, tasks) == "stone"
    history = [{"kind": "task", "task": "a", "passed": True}] * 4 + [{"kind": "task", "task": "b", "passed": True}] * 3
    picker = Picker(random.Random(0), tasks=tasks, history=history, drill_share=0.0)
    weights = picker.task_weights()
    assert weights["c"] > weights["a"] and weights["c"] > weights["d"]
    assert isinstance(Picker(random.Random(0), tasks=tasks, llm=False).next(), Drill)
    drills = Picker(random.Random(0), history=[{"kind": "drill", "task": "walk", "passed": False}] * 5,
                    weak_skills=["mine"]).drill_weights()
    assert drills["walk"] > drills["climb_out"] and drills["logs"] > drills["climb_out"]


def test_failed_goals_come_from_experience(tmp_path):
    path = tmp_path / "experience.json"
    now = time.strftime("%Y-%m-%d %H:%M")
    path.write_text(json.dumps({"records": [
        {"goal": "build a tower", "outcome": "failed", "time": now, "world": "singleplayer:w"},
        {"goal": "make a pickaxe", "outcome": "success", "time": now, "world": "singleplayer:w"},
        {"goal": "raid a server", "outcome": "turn_limit", "time": now, "world": "server:x"},
        {"goal": "old", "outcome": "failed", "time": "2020-01-01 00:00", "world": "singleplayer:w"}]}))
    assert failed_goals(path) == ["build a tower"]
    assert failed_goals(tmp_path / "missing.json") == []


def test_cli_reports_and_keeps(tmp_path, capsys):
    log = fl.FailureLog(tmp_path / "failures")
    record = log.add_failure("goto", {"x": 1, "z": 1}, "made no headway", 20, pit_scene())
    assert main(["--data-dir", str(tmp_path), "failures"]) == 0
    assert "in a pit" in capsys.readouterr().out
    repo = tmp_path / "repo"
    (repo / "src" / "test" / "java").mkdir(parents=True)
    assert main(["--data-dir", str(tmp_path), "keep", record["id"], "--repo", str(repo)]) == 0
    assert (repo / "src" / "test" / "resources" / "scenes" / f"{record['id']}.scene").exists()
    assert main(["--data-dir", str(tmp_path), "tuning", "--reset", "mine.batch"]) == 0
    assert main(["--data-dir", str(tmp_path), "tuning", "--reset", "nope"]) == 2
    assert main(["--data-dir", str(tmp_path), "report"]) == 0
    assert main(["--data-dir", str(tmp_path), "keep", "missing"]) == 2
