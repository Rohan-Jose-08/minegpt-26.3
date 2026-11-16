import json
import threading
import time

import pytest

from minegpt_brain.agent import Agent, AgentConfig
from minegpt_brain.automation import Automation, commands
from minegpt_brain.automation.jobs import AT_WINDOW, DAY_TICKS, MAX_FAILURES, JobBook, parse
from minegpt_brain.bridge import Bridge
from fake_mod import FakeMod
from test_agent import ScriptedBackend, crafting_world


# Schedules ---------------------------------------------------------------------------------

def test_parses_game_and_real_time_schedules():
    sched, goal = parse("every day at dawn: harvest and replant the wheat farm")
    assert (sched.clock, sched.interval, sched.at) == ("game", DAY_TICKS, 0)
    assert goal == "harvest and replant the wheat farm" and sched.text == "every day at dawn"

    sched, goal = parse("every 2 days, smelt the iron in the chest")
    assert (sched.clock, sched.interval, sched.at) == ("game", 2 * DAY_TICKS, None) and goal.startswith("smelt")

    sched, goal = parse("every 30 minutes - top up the furnaces")
    assert (sched.clock, sched.interval) == ("real", 1800) and goal == "top up the furnaces"

    sched, _ = parse("nightly: sleep")
    assert sched.at == 13000
    sched, _ = parse("Hourly: check the farm")
    assert (sched.clock, sched.interval) == ("real", 3600)


@pytest.mark.parametrize("text, problem", [
    ("harvest the farm", "start with when"),
    ("every week: harvest", "unknown unit"),
    ("every day at teatime: harvest", "unknown time"),
    ("every 10 minutes at dusk: harvest", "only works with days"),
    ("every day:", "say what to do"),
    ("every 0.5 minutes: harvest", "at most once a minute"),
])
def test_rejects_bad_schedules(text, problem):
    with pytest.raises(ValueError, match=problem):
        parse(text)


def test_game_time_jobs_wait_a_day_and_for_their_time(tmp_path):
    book = JobBook(tmp_path / "jobs.json", "w")
    daily = book.add("every day at dusk: harvest")
    anytime = book.add("every day: smelt")
    # Never run: due straight away, but the dusk job only from dusk.
    assert not daily.due(game_time=0, day_time=6000, now=0) and anytime.due(0, 6000, 0)
    assert daily.due(0, 12100, 0) and not daily.due(0, 12000 + AT_WINDOW + 10, 0)

    book.record(daily, "success", "harvested", game_time=1000, now=0)
    assert not daily.due(1000 + DAY_TICKS // 2, 12100, 100)
    # A day later at dusk it is due again, a little early so it does not slip.
    assert daily.due(1000 + int(DAY_TICKS * 0.9), 12100, 100)
    assert daily.runs == 1 and daily.successes == 1


def test_failures_back_off_then_pause_the_job(tmp_path):
    book = JobBook(tmp_path / "jobs.json", "w")
    job = book.add("every 10 minutes: top up the furnaces")
    message = None
    for i in range(MAX_FAILURES):
        assert job.due(0, 0, now=1000.0 + i * 10_000)
        message = book.record(job, "failed", "no coal", game_time=0, now=1000.0 + i * 10_000)
        assert not job.due(0, 0, now=1001.0 + i * 10_000)
    assert not job.enabled and "paused" in message and "resume 1" in message
    # Saved, so a restart remembers it.
    again = JobBook(tmp_path / "jobs.json", "w").get(job.id)
    assert again is not None and not again.enabled and again.failures_in_row == MAX_FAILURES


def test_a_job_stopped_in_game_waits_for_its_next_turn(tmp_path):
    book = JobBook(tmp_path / "jobs.json", "w")
    job = book.add("every 10 minutes: tidy the chests")
    book.record(job, "stopped", "the player cleared the goal", game_time=0, now=100.0)
    assert not job.due(0, 0, now=200.0) and job.due(0, 0, now=100.0 + 600)
    book.record(job, "replaced", "the player set a new goal", game_time=0, now=800.0)
    assert job.runs == 1  # a replaced run does not count


def test_jobs_are_kept_per_world(tmp_path):
    JobBook(tmp_path / "jobs.json", "singleplayer:A").add("daily: harvest")
    assert JobBook(tmp_path / "jobs.json", "server:b.example").jobs == []
    assert len(JobBook(tmp_path / "jobs.json", "singleplayer:A").jobs) == 1


def test_job_commands(tmp_path):
    book = JobBook(tmp_path / "jobs.json", "w")
    assert "Added job #1 every day at dawn: harvest" in commands.handle("add every day at dawn: harvest", book)[0]
    assert "Could not add" in commands.handle("add whenever: harvest", book)[0]
    listed = commands.handle("list", book)
    assert listed[0] == "1 job:" and "#1 every day at dawn: harvest (not run yet)" in listed[1]
    assert commands.handle("pause 1", book) == ["Paused job #1."] and not book.get(1).enabled
    assert "start as soon" in commands.handle("run 1", book)[0]
    assert book.get(1).enabled and book.get(1).due(0, 6000, 0)  # run now ignores the dawn window
    assert "no job #7" in commands.handle("remove 7", book)[0].lower()
    assert commands.handle("off", book)[0].startswith("Jobs are off") and book.next_due(0, 0) is None
    commands.handle("on", book)
    assert commands.handle("remove 1", book) == ["Removed job #1."] and book.jobs == []
    assert commands.handle("", book) == commands.HELP


# The scheduler in the agent ----------------------------------------------------------------

@pytest.fixture
def world(tmp_path):
    mod = FakeMod()
    mod.skill_handler = crafting_world
    bridge = Bridge.connect(port=mod.port, timeout=5)
    yield mod, bridge, tmp_path
    bridge.close()


def _agent(bridge, tmp_path, script, **config):
    backend = ScriptedBackend(script)
    return Agent(bridge, backend, AgentConfig(data_dir=tmp_path, poll=0.05, **config)), backend


def _wait(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_job_commands_typed_in_game_reach_the_brain(world):
    mod, bridge, tmp_path = world
    agent, _ = _agent(bridge, tmp_path, [])
    mod.send_event("command", command="job", text="add every day: harvest the wheat")
    time.sleep(0.2)
    agent.poll_events()
    assert mod.notes and mod.notes[0].startswith("Added job #1")
    saved = json.loads((tmp_path / "jobs.json").read_text())
    assert saved["worlds"]["singleplayer:Test World"]["jobs"][0]["goal"] == "harvest the wheat"


def test_runs_a_due_job_when_there_is_no_goal(world):
    mod, bridge, tmp_path = world
    JobBook(tmp_path / "jobs.json", mod.world).add("every day: make planks")
    agent, backend = _agent(bridge, tmp_path, [
        [("craft", {"item": "oak_planks", "count": 4})],
        [("goal_done", {"summary": "made planks"})],
    ])
    agent.automation.tick()

    job = JobBook(tmp_path / "jobs.json", mod.world).get(1)
    assert job.runs == 1 and job.successes == 1 and job.last_outcome == "success"
    first = backend.conversations[0].messages[0][1]
    assert first.startswith("Goal: make planks") and "recurring job #1 (every day), run 1" in first
    assert mod.goal is None  # finished and cleared in game
    log = [json.loads(line) for line in (tmp_path / "automation.jsonl").read_text().splitlines()]
    assert [e["event"] for e in log] == ["job_start", "job_end"] and log[1]["outcome"] == "success"
    # Not due again until a day has passed.
    agent.automation._last_check = 0
    agent.automation.tick()
    assert len(backend.conversations) == 1


def test_a_goal_from_the_player_comes_before_the_job(world):
    mod, bridge, tmp_path = world
    JobBook(tmp_path / "jobs.json", mod.world).add("every day: make planks")

    def interrupt(conversation):
        mod.set_goal("build a house")
        time.sleep(0.2)
        return [("craft", {"item": "oak_planks"})]

    agent, _ = _agent(bridge, tmp_path, [interrupt])
    agent.automation.tick()
    job = JobBook(tmp_path / "jobs.json", mod.world).get(1)
    assert job.last_outcome == "replaced" and job.runs == 0 and job.enabled
    assert agent.goal == "build a house"


def test_sleeps_at_night_between_jobs(world):
    mod, bridge, tmp_path = world
    JobBook(tmp_path / "jobs.json", mod.world).add("every day at dawn: harvest")
    mod.day_time = 14000
    mod.blocks = [{"pos": [3, 64, 0], "block": "minecraft:red_bed", "tag": "#minecraft:beds", "distance": 3.0}]
    skills = []
    mod.skill_handler = lambda m, name, args: (skills.append(name) or True, "slept", {})
    agent, backend = _agent(bridge, tmp_path, [])
    agent.automation.tick()
    assert skills == ["sleep"] and backend.conversations == []
    assert "next job #1" in mod.status
    # Once a night.
    agent.automation._last_check = 0
    agent.automation.tick()
    assert skills == ["sleep"]


def test_does_nothing_between_jobs_in_a_world_without_jobs(world):
    mod, bridge, tmp_path = world
    mod.day_time = 14000
    mod.blocks = [{"pos": [3, 64, 0], "block": "minecraft:red_bed", "tag": "#minecraft:beds", "distance": 3.0}]
    skills = []
    mod.skill_handler = lambda m, name, args: (skills.append(name) or True, "slept", {})
    agent, _ = _agent(bridge, tmp_path, [])
    agent.automation.tick()
    assert skills == []


def test_walks_home_between_jobs(world):
    mod, bridge, tmp_path = world
    JobBook(tmp_path / "jobs.json", mod.world).add("every day at dusk: harvest")
    from minegpt_brain.agent.memory import WorldMemory
    WorldMemory(tmp_path / "memory.json", mod.world).add("base", "home", (100, 64, 100), "minecraft:overworld")
    calls = []
    mod.skill_handler = lambda m, name, args: (calls.append((name, args)) or True, "arrived", {})
    agent, _ = _agent(bridge, tmp_path, [])
    agent.automation.tick()
    assert calls == [("goto", {"x": 100, "y": 64, "z": 100, "radius": 4})]


def test_run_forever_picks_up_jobs(world):
    mod, bridge, tmp_path = world
    JobBook(tmp_path / "jobs.json", mod.world).add("every 10 minutes: make planks")
    agent, backend = _agent(bridge, tmp_path, [[("goal_done", {"summary": "ok"})]])
    thread = threading.Thread(target=agent.run_forever, daemon=True)
    thread.start()
    assert _wait(lambda: JobBook(tmp_path / "jobs.json", mod.world).get(1).runs == 1)
    assert len(backend.conversations) == 1


def test_safety_regions_and_blueprints_reach_the_model(world):
    mod, bridge, tmp_path = world
    mod.goal = "make planks"
    mod.regions = [{"name": "base", "dimension": "minecraft:overworld", "minX": -10, "minZ": -10, "maxX": 10,
                    "maxZ": 10, "farm": True}]
    agent, backend = _agent(bridge, tmp_path, [[("goal_failed", {"reason": "test"})]])
    agent.blueprints.save("hut", "a tiny hut", [["cc", "cc"]], {"c": "cobblestone"})
    agent.run_goal("make planks")
    first = backend.conversations[0].messages[0][1]
    assert "- base: x -10 to 10, z -10 to 10, but farming (harvest, replant, till) is allowed" in first
    assert "'hut' (2x1x2): a tiny hut Needs 4 cobblestone." in first
