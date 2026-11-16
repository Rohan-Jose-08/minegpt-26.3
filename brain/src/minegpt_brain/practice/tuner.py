"""Self-tuning skill settings: tries nearby values during practice and keeps what works better.

The mod has about twenty settings that used to be fixed numbers in the skills: how long goto
waits before calling itself stuck, how many blocks mine breaks before picking up the drops, how
still the crosshair must be before a block is placed. Each is a *knob* with a default and a range
(``Tuning.java`` in the mod; :data:`KNOBS` here mirrors it).

Every skill call carries the values in force. During practice (``explore``), about half the calls
also try one other value for one knob that belongs to that skill, chosen by Thompson sampling:
each value's record (how often the skill worked with it, and how many seconds per unit of work it
took) is turned into a random guess at how good it is, and the best guess is tried. Values that do
well are tried more, poor ones less, and none is ruled out for good on a few unlucky runs.

A value replaces the one in force (is *promoted*) only once it and the one in force each have
:data:`MIN_RUNS` runs and its expected score is at least :data:`MARGIN` better. Promotions are kept
in a history with the time they were made, so a night of practice that made the benchmark worse
can be undone with :meth:`Tuner.revert_since`, and any knob can go back to its default with
:meth:`Tuner.reset`. Normal goals never explore; they only use the values in force.
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

#: Runs each value needs before it can be compared.
MIN_RUNS = 6
#: How much better (as a share) a value's score must be to replace the one in force.
MARGIN = 0.05
#: Share of practice calls that try another value.
EXPLORE_RATE = 0.5
#: The value steps tried round each default.
STEPS = (0.6, 0.8, 1.0, 1.25, 1.5)


@dataclass(frozen=True)
class Knob:
    name: str
    default: float
    low: float
    high: float
    whole: bool
    about: str

    @property
    def skill(self) -> str:
        return self.name.split(".", 1)[0]

    def clamp(self, value: float) -> float:
        value = max(self.low, min(self.high, value))
        return float(round(value)) if self.whole else round(value, 2)

    def candidates(self) -> list[float]:
        """The values tried: the default and steps either side of it, inside the range."""
        return sorted({self.clamp(self.default * s) for s in STEPS})


#: The mod's settings (src/client/java/.../skill/Tuning.java). A test checks the two lists agree.
KNOBS: dict[str, Knob] = {k.name: k for k in [
    Knob("goto.still_ticks", 100, 40, 200, True, "ticks without moving before goto calls itself stuck and replans"),
    Knob("goto.loiter_ticks", 240, 120, 480, True, "ticks within a few blocks of one spot before goto gives up there"),
    Knob("goto.walk_step_ticks", 60, 30, 120, True, "ticks one walking step may take"),
    Knob("goto.place_step_ticks", 100, 40, 200, True, "ticks one pillar or bridge step may take"),
    Knob("goto.idle_jumps", 3, 2, 6, True, "jumps with no block placed before a pillar spot is given up"),
    Knob("goto.bridge_wait_ticks", 40, 20, 80, True, "ticks waited for a bridge click before the spot is given up"),
    Knob("goto.dig_after_failures", 3, 1, 6, True, "failed steps before goto may dig through"),
    Knob("goto.submerged_ticks", 15, 5, 40, True, "ticks under water before goto swims up"),
    Knob("goto.stuck_penalty", 60, 20, 200, False, "extra path cost on a cell where the player got stuck"),
    Knob("goto.unstick_ticks", 8, 4, 16, True, "ticks spent backing off after getting stuck"),
    Knob("mine.batch", 12, 4, 24, True, "blocks broken from one spot before picking up the drops"),
    Knob("mine.drop_settle_ticks", 12, 4, 30, True, "ticks waited for drops to land"),
    Knob("mine.pick_nodes", 4000, 1000, 8000, True, "path search size when choosing the next block"),
    Knob("mine.seconds_per_block", 4, 2, 8, False, "time allowed per wanted block before mine times out"),
    Knob("collect.still_ticks", 25, 10, 60, True, "ticks without moving before collect gives up on an item"),
    Knob("collect.trips", 2, 1, 4, True, "walks towards one item before it is given up"),
    Knob("place.settled_degrees", 1.5, 0.5, 3, False, "how still the crosshair must be before clicking"),
    Knob("place.tries", 3, 2, 5, True, "clicks on one spot before place gives up"),
    Knob("build.uncover_tries", 5, 2, 8, True, "tries at clearing what hides a block before build skips it"),
    Knob("build.view_moves", 2, 0, 4, True, "moves to a better spot for a block that cannot be seen"),
]}


def _key(value: float) -> str:
    return f"{value:g}"


def units(skill: str, args: Mapping[str, Any], data: Optional[Mapping[str, Any]]) -> float:
    """How much work a call did, so seconds compare across calls of different sizes."""
    if skill == "mine":
        return float((data or {}).get("broken") or args.get("count") or 1)
    if skill in ("smelt", "craft", "collect"):
        return float(args.get("count") or 1)
    if skill == "build":
        return float((data or {}).get("placed") or len(args.get("blocks") or []) or 1)
    return 1.0


class Tuner:
    def __init__(self, path: Path, rng: Optional[random.Random] = None):
        self.path = Path(path)
        self.rng = rng or random.Random()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            raw = {}
        self.knobs: dict[str, dict[str, Any]] = raw.get("knobs", {})
        self.history: list[dict[str, Any]] = raw.get("history", [])
        self._unsaved = 0

    # Values ---------------------------------------------------------------------------------

    def value(self, name: str) -> float:
        """The value in force for a knob."""
        knob = KNOBS[name]
        best = self.knobs.get(name, {}).get("best")
        return knob.clamp(best) if best is not None else knob.default

    def in_force(self) -> dict[str, float]:
        """Every knob whose value in force is not its default (the mod uses defaults for the rest)."""
        return {name: self.value(name) for name in KNOBS if self.value(name) != KNOBS[name].default}

    def settings(self, skill: str, explore: bool = False) -> tuple[dict[str, float], Optional[tuple[str, float]]]:
        """The values to send with a call of ``skill``, and the one knob being tried, if any."""
        values = self.in_force()
        owned = [k for k in KNOBS.values() if k.skill == skill]
        if not explore or not owned or self.rng.random() >= EXPLORE_RATE:
            return values, None
        # The knob with the fewest tries goes first, so every knob gets looked at.
        knob = min(owned, key=lambda k: (self._tries(k.name), self.rng.random()))
        value = max(knob.candidates(), key=lambda v: self._sample(knob.name, v))
        if value == self.value(knob.name):
            return values, None
        values = dict(values)
        values[knob.name] = value
        return values, (knob.name, value)

    # Learning -------------------------------------------------------------------------------

    def record(self, skill: str, args: Mapping[str, Any], trial: Optional[tuple[str, float]], success: bool,
               seconds: float, data: Optional[Mapping[str, Any]] = None) -> Optional[str]:
        """Files how a call went. Returns a line saying so when a value was promoted."""
        work = max(1.0, units(skill, args, data))
        names = [trial[0]] if trial else [k.name for k in KNOBS.values() if k.skill == skill]
        promoted = None
        for name in names:
            value = trial[1] if trial else self.value(name)
            arm = self.knobs.setdefault(name, {}).setdefault("arms", {}).setdefault(
                _key(value), {"runs": 0, "ok": 0, "seconds": 0.0, "units": 0.0})
            arm["runs"] += 1
            if success:
                arm["ok"] += 1
                arm["seconds"] = round(arm["seconds"] + max(0.0, seconds), 1)
                arm["units"] = round(arm["units"] + work, 1)
            promoted = self._maybe_promote(name) or promoted
        self._unsaved += 1
        if promoted or self._unsaved >= 5:
            self.save()
        return promoted

    def score(self, name: str, value: float) -> Optional[float]:
        """Expected score of a value: success rate over (1 + seconds per unit / the knob's typical)."""
        arm = self._arm(name, value)
        if not arm or not arm["runs"]:
            return None
        rate = (arm["ok"] + 1) / (arm["runs"] + 2)
        return rate / (1 + self._pace(arm, name) / self._typical(name))

    def _sample(self, name: str, value: float) -> float:
        arm = self._arm(name, value) or {"runs": 0, "ok": 0, "seconds": 0.0, "units": 0.0}
        rate = self.rng.betavariate(arm["ok"] + 1, arm["runs"] - arm["ok"] + 1)
        typical = self._typical(name)
        pace = self._pace(arm, name)
        # Fewer timed runs, wider guess at the pace.
        spread = typical / math.sqrt(arm["ok"] + 1)
        pace = max(0.0, self.rng.gauss(pace, spread))
        return rate / (1 + pace / typical)

    def _maybe_promote(self, name: str) -> Optional[str]:
        current = self.value(name)
        base = self._arm(name, current)
        if not base or base["runs"] < MIN_RUNS:
            return None
        base_score = self.score(name, current) or 0.0
        best_value, best_score = current, base_score
        for key, arm in self.knobs[name].get("arms", {}).items():
            if arm["runs"] >= MIN_RUNS:
                s = self.score(name, float(key)) or 0.0
                if s > best_score:
                    best_value, best_score = float(key), s
        if best_value == current or best_score < base_score * (1 + MARGIN):
            return None
        self.knobs[name]["best"] = best_value
        self.history.append({"time": time.time(), "when": time.strftime("%Y-%m-%d %H:%M"), "knob": name,
                             "from": current, "to": best_value, "score_from": round(base_score, 3),
                             "score_to": round(best_score, 3)})
        return f"{name}: {current:g} -> {best_value:g} (score {base_score:.2f} -> {best_score:.2f})"

    def revert_since(self, since: float) -> list[str]:
        """Undoes the promotions made since a time (newest first). Returns what was undone."""
        undone = []
        for entry in reversed([h for h in self.history if h["time"] >= since and not h.get("reverted")]):
            self.knobs.setdefault(entry["knob"], {})["best"] = entry["from"]
            entry["reverted"] = True
            undone.append(f"{entry['knob']}: back to {entry['from']:g}")
        if undone:
            self.save()
        return undone

    def reset(self, name: Optional[str] = None) -> None:
        """Puts one knob, or every knob, back to its default and forgets its runs."""
        for key in [name] if name else list(self.knobs):
            self.knobs.pop(key, None)
        self.save()

    # Reading --------------------------------------------------------------------------------

    def table(self) -> list[dict[str, Any]]:
        """One row per knob: default, value in force, runs and the best-scoring value so far."""
        rows = []
        for knob in KNOBS.values():
            arms = self.knobs.get(knob.name, {}).get("arms", {})
            scored = [(self.score(knob.name, float(k)) or 0.0, float(k), a["runs"]) for k, a in arms.items()]
            leader = max(scored)[1] if scored else None
            rows.append({"knob": knob.name, "default": knob.default, "value": self.value(knob.name),
                         "runs": sum(a["runs"] for a in arms.values()), "leader": leader,
                         "values": {k: {"runs": a["runs"], "ok": a["ok"],
                                        "pace": round(self._pace(a, knob.name), 2)} for k, a in sorted(arms.items())},
                         "about": knob.about})
        return rows

    def save(self) -> None:
        self._unsaved = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"knobs": self.knobs, "history": self.history}, indent=1, sort_keys=True),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    # Helpers --------------------------------------------------------------------------------

    def _arm(self, name: str, value: float) -> Optional[dict[str, Any]]:
        return self.knobs.get(name, {}).get("arms", {}).get(_key(value))

    def _tries(self, name: str) -> int:
        return sum(a["runs"] for a in self.knobs.get(name, {}).get("arms", {}).values())

    def _typical(self, name: str) -> float:
        """Seconds per unit across all of a knob's runs: the yardstick for its pace."""
        arms = self.knobs.get(name, {}).get("arms", {}).values()
        seconds = sum(a["seconds"] for a in arms)
        work = sum(a["units"] for a in arms)
        return max(0.5, seconds / work) if work else 10.0

    def _pace(self, arm: Mapping[str, Any], name: str) -> float:
        return arm["seconds"] / arm["units"] if arm.get("units") else self._typical(name)
