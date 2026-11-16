import json
import threading

import pytest

from minegpt_brain.agent import Agent, AgentConfig
from minegpt_brain.agent import critic
from minegpt_brain.agent.memory import WorldMemory
from minegpt_brain.agent.routines import RoutineLibrary
from minegpt_brain.bridge import Bridge
from minegpt_brain.llm import ToolCall, Turn
from minegpt_brain.observation import Observation
from fake_mod import FakeMod, make_obs


class ScriptedConversation:
    def __init__(self, script):
        self.script = script
        self.messages = []

    def add_user(self, text):
        self.messages.append(("user", text))

    def add_results(self, results, note=None):
        self.messages.append(("results", list(results), note))

    def next(self):
        step = self.script.pop(0)
        if callable(step):
            step = step(self)
        calls = [ToolCall(f"call{len(self.messages)}_{i}", name, args) for i, (name, args) in enumerate(step)]
        return Turn("", calls, "tool_use" if calls else "end_turn", usage={"input_tokens": 10, "output_tokens": 5})

    def results(self, index=-1):
        """The tool results sent back for one turn."""
        return [m for m in self.messages if m[0] == "results"][index]


class ScriptedBackend:
    """Plays back a fixed list of turns; each turn is a list of (tool name, args)."""

    name = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.conversations = []

    def start(self, system, tools):
        self.tools = tools
        conversation = ScriptedConversation(self.script)
        self.conversations.append(conversation)
        return conversation


def crafting_world(mod, name, args):
    """Skills that change the fake inventory the way the real ones would."""
    if name == "mine":
        mod.give("oak_log", args.get("count", 1))
        return True, f"mined {args.get('count', 1)} {args['block']}", {"broken": args.get("count", 1)}
    if name == "craft":
        item = args["item"] if ":" in args["item"] else "minecraft:" + args["item"]
        if item == "minecraft:oak_planks":
            mod.take("oak_log", 1)
            mod.give(item, args.get("count", 1))
            return True, f"crafted {args.get('count', 1)} {item}", {"crafted": args.get("count", 1)}
        return False, f"no unlocked recipe makes {item} (recipes unlock as you pick up their ingredients)", {}
    if name == "eat":
        mod.food = 20
        return True, "ate bread", {"food": 20}
    if name == "goto":
        return True, "arrived", {"x": args.get("x"), "y": 64, "z": args.get("z")}
    return True, "done", {}


@pytest.fixture
def setup(tmp_path):
    mod = FakeMod()
    mod.skill_handler = crafting_world
    mod.goal = "make planks"
    bridge = Bridge.connect(port=mod.port, timeout=5)
    made = []

    def make(script, **config):
        backend = ScriptedBackend(script)
        agent = Agent(bridge, backend, AgentConfig(data_dir=tmp_path, poll=0.05, **config))
        made.append(agent)
        return agent, backend

    yield mod, bridge, make, tmp_path
    bridge.close()


def test_reaches_a_goal_the_game_confirms(setup):
    mod, _, make, tmp_path = setup
    agent, backend = make([
        [("set_plan", {"steps": [{"step": "get logs", "done_when": {"have": {"oak_log": 10}}},
                                 {"step": "craft planks", "done_when": {"have": {"*_planks": 4}}}],
                       "goal_check": {"have": {"oak_planks": 4}}})],
        [("mine", {"block": "#minecraft:logs", "count": 2}), ("craft", {"item": "oak_planks", "count": 4})],
        [("goal_done", {"summary": "made 4 planks"})],
    ])
    result = agent.run_goal("make planks")

    assert result.outcome == "success" and result.verified
    assert result.usage["input_tokens"] == 30
    conversation = backend.conversations[0]
    first = conversation.messages[0][1]
    assert first.startswith("Goal: make planks") and "oak_log 8" in first
    _, results, note = conversation.results(1)
    assert results[0].content.startswith("ok: mined 2") and "+2 oak_log" in results[0].content
    assert "+4 oak_planks" in results[1].content
    assert "[x] 1. get logs" in note and "[x] 2. craft planks" in note
    # The game was told, and the goal cleared so it does not start again.
    assert mod.goal is None and mod.status == "Done: make planks"
    assert agent.goal is None
    # The calls that worked are kept as a routine for next time.
    routines = json.loads((tmp_path / "routines.json").read_text())["routines"]
    assert [s["tool"] for s in routines["make planks"]["steps"]] == ["mine", "craft"]
    events = [json.loads(line)["event"] for line in result.log_path.read_text().splitlines()]
    assert events[0] == "start" and events[-1] == "end" and "tool" in events and "skill" in events


def test_goal_done_is_refused_until_the_check_holds(setup):
    _, _, make, _ = setup
    agent, backend = make([
        [("set_plan", {"steps": [{"step": "smelt iron"}], "goal_check": {"have": {"iron_ingot": 3}}})],
        [("goal_done", {"summary": "done, I think"})],
        [("goal_failed", {"reason": "no iron here"})],
    ])
    result = agent.run_goal("make planks")

    refused = backend.conversations[0].results(1)[1][0]
    assert refused.is_error and "have 0 of 3 iron_ingot" in refused.content
    assert result.outcome == "failed" and result.summary == "no iron here"


def test_a_failed_skill_skips_the_rest_of_the_turn_and_gets_a_hint(setup):
    mod, _, make, _ = setup
    agent, backend = make([
        [("craft", {"item": "stone_pickaxe"}), ("craft", {"item": "oak_planks", "count": 4}),
         ("look_around", {})],
        [("craft", {"item": "stone_pickaxe"})],
        [("goal_failed", {"reason": "stuck"})],
    ])
    agent.run_goal("make planks")

    first, skipped, looked = backend.conversations[0].results(0)[1]
    assert first.is_error and "Hint: Recipes unlock" in first.content
    assert skipped.is_error and "skipped because craft failed" in skipped.content
    assert not looked.is_error and "Inventory:" in looked.content
    again = backend.conversations[0].results(1)[1][0]
    assert "failed 2 times the same way" in again.content
    assert not any(s["id"] == "minecraft:oak_planks" for s in mod.slots)


def test_a_new_goal_in_game_replaces_the_current_one(setup):
    mod, _, make, _ = setup

    def interrupt(mod, name, args):
        mod.set_goal("build a hut")
        return True, "arrived", {}

    mod.skill_handler = interrupt
    agent, _ = make([[("goto", {"x": 5, "y": 64, "z": 5}), ("goto", {"x": 9, "y": 64, "z": 9})]])
    result = agent.run_goal("make planks")

    assert result.outcome == "replaced"
    # The new goal is left in place for the next episode.
    assert mod.goal == "build a hut" and agent.goal == "build a hut"


def test_stopping_in_game_ends_the_episode(setup):
    mod, _, make, _ = setup

    def stop(mod, name, args):
        mod.set_goal(None)
        return False, "cancelled: stopped by the player", {}

    mod.skill_handler = stop
    agent, _ = make([[("mine", {"block": "#minecraft:logs"})]])
    assert agent.run_goal("make planks").outcome == "stopped"


def test_mine_can_be_asked_to_dig_a_tunnel(setup):
    mod, _, make, _ = setup
    agent, backend = make([[("mine", {"block": "minecraft:stone", "count": 40, "tunnel": True})],
                           [("goal_failed", {"reason": "test over"})]])
    mod.goal = agent.goal = "make planks"
    agent.run_goal("make planks")
    sent = [r["args"] for r in mod.received if r["op"] == "skill" and r["name"] == "mine"]
    assert sent and sent[0]["tunnel"] is True and sent[0]["count"] == 40


def test_a_death_is_reported_and_remembered(setup):
    mod, _, make, tmp_path = setup

    def die(mod, name, args):
        if name != "goto":
            return True, "done", {}
        mod.health = 0.0
        mod.set_mode("paused")

        def respawn():
            mod.health = 20.0
            mod.set_mode("running")

        threading.Timer(0.3, respawn).start()
        return False, "cancelled: the player died", {}

    mod.skill_handler = die
    mod.entities = [{"id": 7, "type": "minecraft:zombie", "pos": [1, 64, 1], "distance": 2.0, "health": 20.0,
                     "hostile": True}]
    agent, backend = make([
        [("goto", {"x": 40, "y": 64, "z": 40})],
        [("goal_failed", {"reason": "died"})],
    ], reflexes=False)
    agent.run_goal("make planks")

    report = backend.conversations[0].results(0)[1][0].content
    assert "The player DIED at 0 64 0, probably killed by a zombie" in report
    memory = json.loads((tmp_path / "memory.json").read_text())
    entries = memory["worlds"]["singleplayer:Test World"]["entries"]
    assert entries[-1]["kind"] == "death" and entries[-1]["pos"] == [0, 64, 0]


def test_reflexes_eat_and_report_it(setup):
    mod, _, make, _ = setup
    mod.food = 10
    agent, backend = make([[("look_around", {})], [("goal_failed", {"reason": "test over"})]])
    agent.run_goal("make planks")

    note = backend.conversations[0].results(0)[2]
    assert "Reflex: food was low, so the player ate" in note
    assert mod.food == 20


def test_reflexes_pick_up_drops_once(setup):
    mod, _, make, _ = setup
    raw_iron = {"id": 7, "type": "minecraft:item", "item": "minecraft:raw_iron", "count": 3, "distance": 3.0,
                "pos": [3.5, 64.0, 0.5], "hostile": False}
    stuck = {"id": 8, "type": "minecraft:item", "item": "minecraft:cobblestone", "count": 2, "distance": 5.2,
             "pos": [5.5, 61.0, 0.5], "hostile": False}
    mod.entities = [raw_iron, stuck]
    collects = []

    def world(mod, name, args):
        if name == "collect":
            collects.append(args)
            mod.entities = [stuck]
            mod.give("raw_iron", 3)
            return True, "picked up 3 raw_iron; 1 out of reach", {"collected": 3}
        return crafting_world(mod, name, args)

    mod.skill_handler = world
    agent, backend = make([[("look_around", {})], [("look_around", {})], [("goal_failed", {"reason": "test over"})]])
    agent.run_goal("make planks")

    conversation = backend.conversations[0]
    assert "On the ground: raw_iron 3 (3m), cobblestone 2 (5m)." in conversation.messages[0][1]
    first = conversation.results(0)[2]
    assert "Reflex: picked up items lying nearby (+3 raw_iron)." in first
    assert "On the ground: cobblestone 2 (5m)." in first
    # The drop it could not reach is not tried again every turn.
    assert len(collects) == 1 and collects[0]["radius"] == 8.0
    assert "Reflex: picked up" not in conversation.results(1)[2]


def test_turn_limit_gives_up_and_clears_the_goal(setup):
    mod, _, make, _ = setup
    agent, _ = make([[("look_around", {})]] * 3, max_turns=2)
    result = agent.run_goal("make planks")
    assert result.outcome == "turn_limit" and result.turns == 2
    assert mod.goal is None


def test_waits_for_a_goal_set_in_game(tmp_path):
    mod = FakeMod()
    mod.announce = True
    with Bridge.connect(port=mod.port, timeout=5) as bridge:
        backend = ScriptedBackend([[("goal_done", {"summary": "nothing to it"})]])
        agent = Agent(bridge, backend, AgentConfig(data_dir=tmp_path, poll=0.05))
        threading.Timer(0.2, mod.set_goal, args=("look around",)).start()
        result = agent.run_forever(once=True)
    assert result.goal == "look around" and result.outcome == "success" and not result.verified


def test_saved_routines_replay_step_by_step(setup):
    mod, _, make, _ = setup
    agent, backend = make([
        [("save_routine", {"name": "planks from logs", "description": "mine logs and turn them into planks",
                           "steps": [{"tool": "mine", "args": {"block": "#minecraft:logs", "count": 1}},
                                     {"tool": "craft", "args": {"item": "oak_planks", "count": 4}}]})],
        [("run_routine", {"name": "Planks from logs"})],
        [("goal_failed", {"reason": "test over"})],
    ])
    agent.run_goal("make planks")

    ran = backend.conversations[0].results(1)[1][0]
    assert not ran.is_error and "All steps succeeded" in ran.content
    assert agent.routines.get("planks from logs")["successes"] == 1
    # A second goal like it is offered the routine.
    agent2, backend2 = make([[("goal_failed", {"reason": "just looking"})]])
    agent2.run_goal("craft some planks")
    assert "'planks from logs'" in backend2.conversations[0].messages[0][1]


def test_find_and_recipe_tools(setup):
    mod, _, make, _ = setup
    mod.blocks = [{"pos": [3, 60, -2], "block": "minecraft:iron_ore", "distance": 6.2, "tag": "#minecraft:iron_ores"}]
    agent, backend = make([
        [("find_blocks", {"block": "#iron_ores"}), ("recipe", {"item": "stick"}), ("recipe", {"item": "diamond"})],
        [("goal_failed", {"reason": "test over"})],
    ])
    agent.run_goal("make planks")

    found, stick, diamond = backend.conversations[0].results(0)[1]
    assert found.content == "iron_ore at 3 60 -2 (6m)"
    assert stick.content == "4 stick from 2 oak_planks; no table needed; you can craft it now"
    assert "No unlocked recipe makes diamond" in diamond.content
    assert "ores: iron_ore 1 (nearest 3 60 -2, 6m)" in backend.conversations[0].messages[0][1]


# Pieces on their own -----------------------------------------------------------------------


def test_conditions():
    obs = Observation(make_obs(slots=[{"slot": 0, "id": "minecraft:spruce_planks", "count": 3},
                                      {"slot": 1, "id": "minecraft:oak_planks", "count": 2},
                                      {"slot": 38, "id": "minecraft:iron_chestplate", "count": 1}]))
    assert critic.unmet({"have": {"*_planks": 5}, "wearing": ["iron_chestplate"]}, obs) == []
    assert critic.unmet({"have": {"oak_planks": 3}, "wearing": ["minecraft:iron_helmet"],
                         "near": {"x": 10, "y": 64, "z": 0}}, obs) == [
        "have 2 of 3 oak_planks", "not wearing iron_helmet", "10 blocks from 10 64 0, not within 3"]
    with pytest.raises(ValueError, match="unknown condition keys"):
        critic.validate({"holding": "sword"})


def test_hints():
    assert critic.hint("need a pickaxe that can mine minecraft:iron_ore") .startswith("Craft a better pickaxe")
    assert critic.hint("something odd") is None


def test_memory_is_kept_per_world(tmp_path):
    path = tmp_path / "memory.json"
    home = WorldMemory(path, "singleplayer:A")
    home.add("base", "starter house", (0, 64, 0))
    home.add("base", "bigger house", (100, 70, 0))
    home.add("ore", "iron in a cave", (90, 30, 5), "minecraft:overworld")
    home.add("ore", "coal on a cliff", (-200, 80, 0), "minecraft:overworld")
    WorldMemory(path, "server:example.org").add("chest", "spawn chest", (5, 70, 5))

    again = WorldMemory(path, "singleplayer:A")
    kinds = [(e["kind"], e["note"]) for e in again.relevant((95, 60, 0), "minecraft:overworld")]
    assert kinds == [("base", "bigger house"), ("ore", "iron in a cave"), ("ore", "coal on a cliff")]
    assert "spawn chest" not in again.describe()


def test_routine_search_prefers_similar_names(tmp_path):
    library = RoutineLibrary(tmp_path / "routines.json")
    library.save("make a stone pickaxe", "logs to planks to sticks, then cobblestone", [{"tool": "mine", "args": {}}])
    library.save("build a dirt hut", "walls and a roof out of dirt", [{"tool": "place", "args": {}}])
    library.save("smelt iron ingots", "mine iron ore and smelt it", [{"tool": "smelt", "args": {}}])
    best = [r["name"] for _, r in library.similar("get a stone pickaxe")]
    assert best[0] == "make a stone pickaxe"
    assert [r["name"] for _, r in library.similar("iron armor")] == ["smelt iron ingots"]
    assert library.similar("fly to the moon") == []


@pytest.mark.parametrize("distance, fights", [(2.0, True), (4.5, False)])
def test_low_on_health_fights_only_what_is_in_reach(setup, distance, fights):
    mod, _, make, _ = setup
    mod.health = 5.0
    mod.entities = [{"id": 7, "type": "minecraft:zombie", "pos": [1, 64, 1], "distance": distance, "health": 20.0,
                     "hostile": True}]
    skills = []
    mod.skill_handler = lambda m, name, args: (skills.append(name) or True, "done", {})
    agent, _ = make([[("look_around", {})], [("goal_failed", {"reason": "test over"})]])
    agent.run_goal("make planks")
    assert ("fight" in skills) == fights


def test_a_goto_with_no_way_is_not_repeated_straight_away(setup):
    mod, _, make, _ = setup
    trips = []

    def world(mod, name, args):
        if name == "goto":
            trips.append(args)
            if args.get("x") == 30:
                return False, "no path to 30 64 30", {}
        return crafting_world(mod, name, args)

    mod.skill_handler = world
    agent, backend = make([
        [("goto", {"x": 30, "y": 64, "z": 30})],
        [("goto", {"x": 31, "y": 64, "z": 30})],
        [("goto", {"x": 5, "y": 64, "z": 5})],
        [("goal_failed", {"reason": "test over"})],
    ])
    agent.run_goal("make planks")

    conversation = backend.conversations[0]
    first = conversation.results(0)
    assert first[1][0].is_error and "no path to" in first[1][0].content
    assert "Out of reach lately (do not head there again yet): x=30 y=64 z=30." in first[2]
    again = conversation.results(1)[1][0]
    assert again.is_error and again.content.startswith("not run: x=30 y=64 z=30 could not be reached")
    # The repeat never reached the game; a trip somewhere else did.
    assert [t["x"] for t in trips] == [30, 5]
    assert not conversation.results(2)[1][0].is_error


def test_unreachable_places_come_back():
    now = [0.0]
    places = critic.Unreachable(seconds=100, clock=lambda: now[0])
    here = (0, 64, 0)
    assert not places.note({"x": 9, "y": 64, "z": 9}, "arrived late", here)
    assert not places.note({"x": 9, "y": 64, "z": 9}, "cancelled: the goal changed", here)
    assert places.note({"x": 9, "y": 64, "z": 9, "allow_break": False}, "no path to 9 64 9 without digging", here)
    assert places.blocked({"x": 10, "y": 64, "z": 8, "allow_break": False}, here)
    # Somewhere else, a different trip, or one that may now dig, is let through.
    assert places.blocked({"x": 40, "y": 64, "z": 9}, here) is None
    assert places.blocked({"x": 9, "z": 9}, here) is None
    assert places.blocked({"x": 9, "y": 64, "z": 9}, here) is None
    assert places.blocked({"x": 9, "y": 64, "z": 9, "allow_break": False}, (20, 64, 0)) is None
    now[0] = 101
    assert places.blocked({"x": 9, "y": 64, "z": 9, "allow_break": False}, here) is None and not places.lines()
    # Failing again soon after doubles the wait; getting there clears it.
    assert places.note({"x": 9, "y": 64, "z": 9}, "made no headway near 4 64 4 for 12s", here)
    now[0] = 250
    assert places.blocked({"x": 9, "y": 64, "z": 9}, here) and places.lines()
    places.reached({"x": 9, "y": 64, "z": 9})
    assert places.blocked({"x": 9, "y": 64, "z": 9}, here) is None


def test_no_headway_gets_a_hint():
    assert critic.hint("made no headway near 4 64 4 for 12s on the way to 9 64 9").startswith("The player kept moving")

