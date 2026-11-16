"""What to practise next: goals and drills aimed at what MineGPT is weakest at.

Three sources feed the choice:

* **The frontier.** Benchmark tiers come in an order (wood, stone, iron, long jobs). A tier counts
  as mastered once each of its tasks has passed at least 70% of at least two tries; tasks of the
  first tier not yet mastered get a bonus, and tiers past it are tried rarely.
* **Weak spots.** A task or drill that fails often is picked more often (its weight is how often
  it fails, smoothed), and drills of the skill behind the worst failure causes get a bonus.
* **Goals that failed before.** Goals given in normal play that were given up or ran out of turns
  are retried now and then, checked by the agent's own goal check.

Drills cost no tokens, so without a model (``--no-llm``) practice runs drills and replays only.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

from ..bench import TASKS, Task
from ..train.collect import Curriculum, custom_task
from .drills import DRILLS, Drill

#: Benchmark tiers in the order they build on each other.
LADDER = ["wood", "stone", "iron", "long"]
MASTERED = 0.7


def load_results(*paths: Path) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        try:
            rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def rates(rows: Iterable[Mapping[str, Any]]) -> dict[str, tuple[int, int]]:
    """(tries, passes) by task name."""
    out: dict[str, tuple[int, int]] = {}
    for row in rows:
        tries, wins = out.get(row["task"], (0, 0))
        out[row["task"]] = (tries + 1, wins + bool(row.get("passed")))
    return out


def smoothed(stats: tuple[int, int]) -> float:
    tries, wins = stats
    return (wins + 1) / (tries + 2)


def frontier(stats: Mapping[str, tuple[int, int]], tasks: list[Task]) -> Optional[str]:
    """The first tier on the ladder that is not mastered yet, or None when all are."""
    for tier in LADDER:
        members = [t for t in tasks if t.tier == tier]
        if not members:
            continue
        if not all(stats.get(t.name, (0, 0))[0] >= 2 and smoothed(stats[t.name]) >= MASTERED for t in members):
            return tier
    return None


class Picker:
    """Chooses the next drill or goal."""

    def __init__(self, rng: random.Random, tasks: Optional[list[Task]] = None, drills: Optional[list[Drill]] = None,
                 history: Iterable[Mapping[str, Any]] = (), retry_goals: Iterable[str] = (),
                 weak_skills: Iterable[str] = (), drill_share: float = 0.6, llm: bool = True):
        self.rng = rng
        self.tasks = list(TASKS) if tasks is None else tasks
        self.drills = list(DRILLS.values()) if drills is None else drills
        self.history = [dict(r) for r in history]
        self.retry = [custom_task(g, 60) for g in dict.fromkeys(retry_goals)][:5]
        self.weak_skills = list(weak_skills)
        self.drill_share = drill_share if llm else 1.0
        self.llm = llm
        self.words = Curriculum(self.tasks, rng)

    def record(self, row: Mapping[str, Any]) -> None:
        self.history.append(dict(row))

    def next(self) -> Union[Drill, Task]:
        if not self.llm or not self.tasks or self.rng.random() < self.drill_share:
            return self.next_drill()
        return self.next_task()

    def drill_weights(self) -> dict[str, float]:
        stats = rates(r for r in self.history if r.get("kind") == "drill")
        out = {}
        for d in self.drills:
            w = 0.3 + (1 - smoothed(stats.get(d.name, (0, 0))))
            if d.skill in self.weak_skills[:2]:
                w += 0.5
            out[d.name] = w
        return out

    def task_weights(self) -> dict[str, float]:
        stats = rates(r for r in self.history if r.get("kind", "task") == "task")
        edge = frontier(stats, self.tasks)
        reached = LADDER.index(edge) if edge in LADDER else len(LADDER)
        out = {}
        for t in self.tasks:
            w = 1 - smoothed(stats.get(t.name, (0, 0)))
            if t.tier == edge:
                w += 0.5
            elif t.tier in LADDER and LADDER.index(t.tier) > reached:
                w *= 0.2
            out[t.name] = max(0.05, w)
        for t in self.retry:
            out[t.goal] = 0.6 * (1 - smoothed(stats.get(t.goal, (0, 0))))
        return out

    def next_drill(self) -> Drill:
        weights = self.drill_weights()
        names = list(weights)
        return DRILLS.get(self.rng.choices(names, [weights[n] for n in names])[0]) or self.drills[0]

    def next_task(self) -> Task:
        weights = self.task_weights()
        names = list(weights)
        pick = self.rng.choices(names, [weights[n] for n in names])[0]
        for t in self.tasks:
            if t.name == pick:
                return self.words.worded(t)
        return next(t for t in self.retry if t.goal == pick)


def failed_goals(experience: Path, days: int = 14) -> list[str]:
    """Goals from normal play that were given up or ran out of turns lately."""
    try:
        records = json.loads(experience.read_text(encoding="utf-8")).get("records", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return []
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    return [r["goal"] for r in records if r.get("outcome") in ("failed", "turn_limit") and r.get("time", "") >= cutoff
            and not str(r.get("world", "")).startswith("server:")]
