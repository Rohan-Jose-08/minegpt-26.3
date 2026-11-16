"""The plan as a tree the game checks: steps, their substeps, and other ways to do a step.

A long goal does not fit in a flat list. "Get iron armour" is a handful of phases, each of which
is its own list of things to do, and some steps have more than one way (mine the ore seen
earlier, or dig down and look). The tree keeps all of that outside the model's head:

- a step is done by doing **all** its substeps in order, or **any** one of them (alternatives,
  tried in order), or it is a leaf: one thing to do;
- any step may carry ``done_when``, a condition the game checks (``critic.py``), and once that
  holds the step and everything under it is done;
- a leaf that fails is marked, and after two failures the next alternative is taken; when a step
  has none left it is ``blocked``, and only that step needs a new breakdown (``expand_step``),
  not the whole plan.

The tree is saved to disk after every turn (``GoalStore``), so a goal that is interrupted (the
turn limit, a crash, a lost connection) carries on from where it stopped.

``Plan`` and ``PlanStep`` in ``tools.py`` are this tree and its nodes: a flat plan is a tree
whose steps have no substeps, and reads exactly as it did before.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import critic

ALL, ANY, DO = "all", "any", "do"
#: Failures after which a step is given up on, and the next alternative taken.
MAX_ATTEMPTS = 2
#: Deepest nesting accepted from the model.
MAX_DEPTH = 4
#: A saved plan older than this many hours is not picked up again.
RESUME_HOURS = 24.0


@dataclass
class PlanStep:
    text: str
    done_when: Optional[dict[str, Any]] = None
    done: bool = False
    #: ``all`` (every substep, in order), ``any`` (one of them) or ``do`` (a leaf).
    kind: str = DO
    children: list["PlanStep"] = field(default_factory=list)
    failed: bool = False
    attempts: int = 0
    last_error: str = ""
    #: For steps a planner made: the skill call that does it.
    call: Optional[dict[str, Any]] = None

    # State ------------------------------------------------------------------------------------

    @property
    def blocked(self) -> bool:
        """Cannot go on as planned: a leaf that failed, or a step whose ways are all used up."""
        if self.done:
            return False
        if not self.children:
            return self.failed
        if self.kind == ANY:
            return all(c.blocked for c in self.children)
        nxt = next((c for c in self.children if not c.done), None)
        return nxt is not None and nxt.blocked

    def update(self, obs, world: Optional[critic.World] = None) -> int:
        """Checks conditions against the game, bottom up. Returns how many steps were newly done."""
        if self.done:
            return 0
        newly = sum(c.update(obs, world) for c in self.children)
        if self.done_when:
            met = not critic.unmet(self.done_when, obs, world)
        elif self.children:
            met = any(c.done for c in self.children) if self.kind == ANY else all(c.done for c in self.children)
        else:
            met = False
        if met:
            self.finish()
            newly += 1
        return newly

    def finish(self) -> None:
        self.done = True
        self.failed = False

    def fail(self, error: str) -> None:
        self.attempts += 1
        self.last_error = error.strip().splitlines()[0][:160] if error.strip() else ""
        if self.attempts >= MAX_ATTEMPTS:
            self.failed = True

    def active(self) -> Optional["PlanStep"]:
        """The leaf to work on next under this step, or None when it is done or blocked."""
        if self.done:
            return None
        if not self.children:
            return None if self.failed else self
        if self.kind == ANY:
            for child in self.children:
                leaf = child.active()
                if leaf is not None:
                    return leaf
            return None
        nxt = next((c for c in self.children if not c.done), None)
        return None if nxt is None else nxt.active()

    def count(self) -> int:
        return 1 + sum(c.count() for c in self.children)

    # Text -------------------------------------------------------------------------------------

    def mark(self) -> str:
        return "x" if self.done else "!" if self.blocked else " "

    def line(self, label: str) -> str:
        text = f"[{self.mark()}] {label} {self.text}"
        if not self.done and self.attempts:
            state = "gave up" if self.failed else "failed once"
            text += f" ({state}: {self.last_error})" if self.last_error else f" ({state})"
        return text

    def describe(self, label: str, open_: bool) -> str:
        text = self.line(label)
        if not self.children:
            return text
        if not open_ or self.done:
            return text + f" ({sum(c.done for c in self.children)} of {len(self.children)} substeps done)"
        head = "one of" if self.kind == ANY else "in order"
        nxt = next((i for i, c in enumerate(self.children) if not c.done and not (self.kind == ANY and c.blocked)),
                   None)
        base = label.rstrip(".")
        parts = [c.describe(f"{base}.{i + 1}", i == nxt) for i, c in enumerate(self.children)]
        return f"{text} ({head}: " + "; ".join(parts) + ")"

    # Saving -----------------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"step": self.text}
        for key, value in (("done_when", self.done_when), ("done", self.done), ("failed", self.failed),
                           ("attempts", self.attempts), ("last_error", self.last_error), ("call", self.call)):
            if value:
                out[key] = value
        if self.children:
            out["kind"] = self.kind
            out["substeps"] = [c.to_dict() for c in self.children]
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PlanStep":
        step = cls(str(raw.get("step", "")), raw.get("done_when") or None, bool(raw.get("done")),
                   failed=bool(raw.get("failed")), attempts=int(raw.get("attempts", 0)),
                   last_error=str(raw.get("last_error", "")), call=raw.get("call") or None)
        step.children = [cls.from_dict(c) for c in raw.get("substeps", [])]
        if step.children:
            step.kind = ANY if raw.get("kind") == ANY else ALL
        return step


def parse_step(raw: Any, where: str, depth: int = 1) -> PlanStep:
    """A step as the model writes it: ``{"step", "done_when"?, "done"?, "substeps"?, "any"?}``."""
    if not isinstance(raw, dict) or not isinstance(raw.get("step"), str) or not raw["step"].strip():
        raise ValueError(f"step {where} needs a step text")
    step = PlanStep(raw["step"].strip(), critic.validate(raw.get("done_when")), bool(raw.get("done")))
    substeps = raw.get("substeps")
    if substeps:
        if not isinstance(substeps, list):
            raise ValueError(f"step {where}: substeps is a list of steps")
        if depth >= MAX_DEPTH:
            raise ValueError(f"step {where}: steps nest at most {MAX_DEPTH} deep")
        step.children = [parse_step(s, f"{where}.{i}", depth + 1) for i, s in enumerate(substeps, 1)]
        step.kind = ANY if raw.get("any") else ALL
    elif raw.get("any"):
        raise ValueError(f"step {where}: any needs substeps, the different ways to do the step")
    return step


@dataclass
class Plan:
    steps: list[PlanStep] = field(default_factory=list)
    goal_check: Optional[dict[str, Any]] = None
    #: Set when the plan was loaded from an earlier attempt at the same goal.
    resumed: bool = False

    def update(self, obs, world: Optional[critic.World] = None) -> int:
        """Checks off steps whose conditions hold. Once done, a step stays done. Returns how many
        top-level steps it checked off."""
        newly = 0
        for step in self.steps:
            was = step.done
            step.update(obs, world)
            newly += step.done and not was
        return newly

    def current(self) -> Optional[tuple[int, PlanStep]]:
        """The first unfinished step, as (number, the leaf under it to work on)."""
        for i, step in enumerate(self.steps, 1):
            if not step.done:
                return i, step.active() or step
        return None

    def stuck(self) -> Optional[tuple[str, PlanStep]]:
        """The step that cannot go on as planned and needs another way, with its number, if any.

        That is the failed leaf itself, or the step whose alternatives are all used up."""
        for i, step in enumerate(self.steps, 1):
            if step.done:
                continue
            label, node = str(i), step
            while node.blocked:
                if not node.children or node.kind == ANY:
                    return label, node
                at = next(n for n, c in enumerate(node.children) if not c.done)
                label, node = f"{label}.{at + 1}", node.children[at]
            return None
        return None

    def find(self, label: str) -> PlanStep:
        """The step numbered like ``2`` or ``2.1``."""
        try:
            path = [int(p) for p in str(label).strip().strip(".").split(".")]
        except ValueError:
            raise ValueError(f"no step {label!r}: steps are numbered like 2 or 2.1") from None
        steps, node = self.steps, None
        for n in path:
            if not 1 <= n <= len(steps):
                raise ValueError(f"no step {label}: the plan is {self.describe()}")
            node = steps[n - 1]
            steps = node.children
        assert node is not None
        return node

    def describe(self) -> str:
        if not self.steps:
            return "no plan yet (call set_plan)"
        nxt = next((i for i, s in enumerate(self.steps) if not s.done), None)
        # The step in hand is shown with its substeps, and so is any other step already begun.
        return "; ".join(s.describe(f"{i + 1}.", i == nxt or any(c.done or c.attempts for c in s.children))
                         for i, s in enumerate(self.steps))

    def outline(self, limit: int = 40) -> list[str]:
        """Every step on its own line, indented, for records."""
        lines: list[str] = []

        def walk(step: PlanStep, label: str, depth: int) -> None:
            if len(lines) < limit:
                lines.append("  " * depth + step.line(label))
                for i, child in enumerate(step.children, 1):
                    walk(child, f"{label}.{i}", depth + 1)

        for i, step in enumerate(self.steps, 1):
            walk(step, str(i), 0)
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {"steps": [s.to_dict() for s in self.steps], "goal_check": self.goal_check}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Plan":
        return cls([PlanStep.from_dict(s) for s in raw.get("steps", [])], raw.get("goal_check") or None)


class GoalStore:
    """Plans of unfinished goals, one file per world and goal."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def _path(self, world: str, goal: str) -> Path:
        key = hashlib.sha1(f"{world}\n{goal.strip().lower()}".encode()).hexdigest()[:16]
        return self.folder / f"{key}.json"

    def save(self, world: str, goal: str, plan: Plan, built: Optional[set[str]] = None) -> None:
        if not plan.steps:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(world, goal)
        data = {"world": world, "goal": goal, "saved": time.time(), "plan": plan.to_dict(),
                "built": sorted(built or ())}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(path)

    def load(self, world: str, goal: str) -> Optional[tuple[Plan, set[str]]]:
        path = self._path(world, goal)
        try:
            data = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        if time.time() - float(data.get("saved", 0)) > RESUME_HOURS * 3600:
            return None
        try:
            plan = Plan.from_dict(data.get("plan", {}))
            critic.validate(plan.goal_check)
        except (ValueError, TypeError, AttributeError):
            return None
        if not plan.steps:
            return None
        plan.resumed = True
        return plan, set(data.get("built", []))

    def clear(self, world: str, goal: str) -> None:
        try:
            self._path(world, goal).unlink()
        except FileNotFoundError:
            pass
