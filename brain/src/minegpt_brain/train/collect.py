"""Gathering training episodes: the teacher model (Claude) plays a curriculum of goals.

Collection reuses the benchmark: each run resets the player with commands, gives the task's
kit, lets the agent work, then checks the result in the game. What differs is the mix: tasks
come in a shuffled, repeating curriculum, goals are worded in several ways so the trained model
does not learn one sentence per task, tasks that keep failing come up less often, and it stops
at a number of good episodes or a token budget. ``--goals`` adds your own goals (one per line),
which are checked by the agent's own goal check instead of a benchmark check.

Run it in a throwaway singleplayer world with cheats on, like the benchmark. Results go to
``collect/results.jsonl`` and episodes to ``collect/agent/episodes`` in the data folder, where
``python -m minegpt_brain.train export`` finds them.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from ..agent.loop import Agent, AgentConfig
from ..bench import TASKS, Task, get
from ..bench.__main__ import run_task
from ..bridge import Bridge

log = logging.getLogger("minegpt.collect")

#: Other ways to say each benchmark goal. The checks stay the same, so the numbers must match.
PHRASINGS: dict[str, list[str]] = {
    "logs": ["Chop down trees until you have 8 logs.", "Get 8 wood logs.", "I need 8 logs, any kind of wood."],
    "planks": ["Get 16 wooden planks.", "Craft 16 planks of any wood.", "Turn some logs into 16 planks."],
    "crafting_table": ["Craft a crafting table and keep it with you.", "I need a crafting table in my inventory."],
    "wooden_pickaxe": ["Craft a wooden pickaxe.", "Get yourself a wooden pickaxe."],
    "torches": ["Craft 8 torches.", "Make me 8 torches for lighting."],
    "cobblestone": ["Mine 16 cobblestone.", "Get 16 cobblestone blocks."],
    "stone_pickaxe": ["Craft a stone pickaxe.", "Upgrade to a stone pickaxe."],
    "furnace": ["Craft a furnace and keep it in your inventory.", "I need a furnace to carry with me."],
    "coal": ["Mine 5 coal.", "Find and mine coal until you have 5."],
    "raw_iron": ["Mine 3 raw iron.", "Find iron ore and get 3 raw iron."],
    "smelt_iron": ["Smelt your raw iron into 8 iron ingots.", "Turn the raw iron you have into 8 ingots."],
    "iron_pickaxe": ["Craft an iron pickaxe.", "Get an iron pickaxe, starting from nothing."],
    "shield": ["Craft a shield.", "Get a shield to block with."],
    "iron_armor": ["Make a full set of iron armor and put it on.", "Get all four pieces of iron armour and wear them."],
    "diamond": ["Find and mine a diamond.", "Get me one diamond."],
    "cooked_meat": ["Hunt some animals and cook 3 pieces of meat.", "Get 3 cooked meat."],
    "bread": ["Bake 2 bread with the wheat you have.", "Turn your wheat into 2 bread."],
    "bed": ["Craft a bed.", "Get a bed of any colour."],
}


def custom_task(goal: str, turns: int) -> Task:
    """A goal of your own: no setup or kit, and no benchmark check (the agent's goal check decides)."""
    return Task("custom", "custom", goal, turns)


class Curriculum:
    """Picks the next task: each task once per round in a shuffled order, tasks that failed often
    skipped some of the time so the budget goes where episodes come out good."""

    def __init__(self, tasks: list[Task], rng: random.Random, phrasings: bool = True):
        self.tasks = tasks
        self.rng = rng
        self.phrasings = phrasings
        self.tries: dict[str, int] = {}
        self.wins: dict[str, int] = {}
        self._round: list[Task] = []

    def next(self) -> Task:
        for _ in range(100):
            if not self._round:
                self._round = list(self.tasks)
                self.rng.shuffle(self._round)
            task = self._round.pop()
            if self.rng.random() < self.chance(task):
                return self.worded(task)
        return self.worded(task)

    @staticmethod
    def key(task: Task) -> str:
        # Rewordings count towards their task; custom goals each count on their own.
        return task.goal if task.name == "custom" else task.name

    def chance(self, task: Task) -> float:
        """How likely a task is to be run when its turn comes: its smoothed success rate, at least 0.2."""
        key = self.key(task)
        tries, wins = self.tries.get(key, 0), self.wins.get(key, 0)
        return max(0.2, (wins + 1) / (tries + 2)) if tries >= 2 else 1.0

    def worded(self, task: Task) -> Task:
        options = PHRASINGS.get(task.name) if self.phrasings else None
        if not options:
            return task
        return replace(task, goal=self.rng.choice([task.goal, *options]))

    def record(self, task: Task, passed: bool) -> None:
        key = self.key(task)
        self.tries[key] = self.tries.get(key, 0) + 1
        self.wins[key] = self.wins.get(key, 0) + passed


def collect(bridge: Bridge, agent: Agent, tasks: list[Task], *, data_dir: Path, label: str, target: int,
            max_runs: int, max_tokens: int, rng: random.Random, reset: bool = True,
            phrasings: bool = True) -> dict[str, Any]:
    """Runs tasks until ``target`` good episodes, ``max_runs`` runs or ``max_tokens`` tokens."""
    results = data_dir / "results.jsonl"
    results.parent.mkdir(parents=True, exist_ok=True)
    curriculum = Curriculum(tasks, rng, phrasings)
    good = runs = tokens = 0
    began = time.monotonic()
    while good < target and runs < max_runs and (not max_tokens or tokens < max_tokens):
        task = curriculum.next()
        record = run_task(agent, task, runs + 1, label, reset, rng)
        if record["outcome"] == "error" and record["turns"] == 0:
            log.error("Stopping: the model did not answer: %s", record["summary"])
            break
        runs += 1
        # Custom goals have no benchmark check; the agent's verified success stands in for it.
        if task.name == "custom":
            record["passed"] = record["outcome"] == "success" and _verified(record)
        tokens += sum(v for k, v in record.get("usage", {}).items() if k != "cache_read_input_tokens")
        good += bool(record["passed"])
        curriculum.record(task, bool(record["passed"]))
        with results.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        log.info("%s %s: %s in %.0fs, %d turns. %d good of %d runs, %d tokens", "GOOD" if record["passed"] else "BAD ",
                 task.name, record["outcome"], record["seconds"], record["turns"], good, runs, tokens)
    summary = {"good": good, "runs": runs, "tokens": tokens, "minutes": round((time.monotonic() - began) / 60, 1),
               "results": str(results)}
    log.info("Collected %d good episodes in %d runs (%d tokens, %.0f min). Results in %s", good, runs, tokens,
             summary["minutes"], results)
    return summary


def _verified(record: dict[str, Any]) -> bool:
    """Whether the agent's goal check held at the end, read from the episode log."""
    path = record.get("log")
    if not path:
        return False
    try:
        for line in reversed(Path(path).read_text(encoding="utf-8").splitlines()):
            event = json.loads(line)
            if event.get("event") == "end":
                return bool(event.get("verified"))
    except (OSError, json.JSONDecodeError):
        pass
    return False


def default_tasks(names: Optional[str]) -> list[Task]:
    """The benchmark tasks to collect on: names or tiers, or all of them."""
    return get([n for n in names.split(",") if n]) if names else list(TASKS)


def make_agent(bridge: Bridge, backend: Any, data_dir: Path, xray: bool) -> Agent:
    return Agent(bridge, backend, AgentConfig(data_dir=data_dir / "agent", jobs=False, xray=xray, resume=False,
                                        shared_dir=data_dir.parent))
