"""Recurring jobs: goals that run again on a schedule, like "every day at dawn: harvest the wheat".

A job's schedule is either in game time (``every day``, ``every 2 days at dusk``), which moves
with the world and so pauses while the game does, or in real time (``every 30 minutes``,
``every 2 hours``). Jobs are kept per world in ``jobs.json`` in the data folder, next to what
the agent remembers about that world.

A job that fails is retried after a while; after :data:`MAX_FAILURES` failures in a row it is
paused until the player resumes it, so a broken job does not burn turns all night.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

#: Game ticks in a Minecraft day (20 real minutes).
DAY_TICKS = 24000
#: Times of day for ``at``, in ticks after sunrise (dayTime 0 is about 6 in the morning).
TIMES = {"dawn": 0, "sunrise": 0, "morning": 1000, "noon": 6000, "midday": 6000, "afternoon": 9000,
         "evening": 11500, "dusk": 12000, "sunset": 12000, "night": 13000, "midnight": 18000}
#: How long after its time a job with ``at`` still starts, in ticks (two and a half real minutes).
AT_WINDOW = 3000
#: Failures in a row before a job is paused.
MAX_FAILURES = 3
#: Longest wait before retrying a failed job, in real seconds.
MAX_RETRY = 600.0
#: A game-time job counts as due a little early, so "every day at dawn" does not slip a day.
EARLY = 0.85

_UNITS = {
    "day": ("game", DAY_TICKS), "days": ("game", DAY_TICKS),
    "minute": ("real", 60), "minutes": ("real", 60), "min": ("real", 60), "mins": ("real", 60),
    "hour": ("real", 3600), "hours": ("real", 3600), "h": ("real", 3600),
}
_SCHEDULE = re.compile(
    r"^\s*(?:every\s+(?P<n>\d+(?:\.\d+)?\s+)?(?P<unit>[a-z]+)|(?P<word>daily|nightly|hourly))"
    r"(?:\s+at\s+(?P<at>[a-z]+))?\s*[:,\-]?\s*(?P<rest>.*)$", re.IGNORECASE)


@dataclass
class Schedule:
    #: ``game`` (interval in game ticks) or ``real`` (interval in seconds).
    clock: str
    interval: float
    #: Time of day (ticks after sunrise) to start at, for game-time schedules.
    at: Optional[int] = None
    text: str = ""

    def describe(self) -> str:
        return self.text


def parse(text: str) -> tuple[Schedule, str]:
    """Splits ``"every day at dawn: harvest the farm"`` into a schedule and the goal text.

    Raises ValueError with a readable message for anything it does not understand.
    """
    match = _SCHEDULE.match(text)
    if not match:
        raise ValueError("start with when, like 'every day at dawn: ...', 'every 2 days: ...', "
                         "'every 30 minutes: ...', 'daily: ...' or 'nightly: ...'")
    word = (match.group("word") or "").lower()
    at_name = (match.group("at") or "").lower() or None
    if word:
        clock, unit = ("real", 3600) if word == "hourly" else ("game", DAY_TICKS)
        n = 1.0
        if word == "nightly" and at_name is None:
            at_name = "night"
    else:
        unit_name = match.group("unit").lower()
        if unit_name not in _UNITS:
            raise ValueError(f"unknown unit {unit_name!r}; use days (game time), minutes or hours (real time)")
        clock, unit = _UNITS[unit_name]
        n = float(match.group("n") or 1)
    if n <= 0:
        raise ValueError("the interval must be more than zero")
    at = None
    if at_name is not None:
        if at_name not in TIMES:
            raise ValueError(f"unknown time {at_name!r}; use one of {', '.join(sorted(TIMES))}")
        if clock != "game":
            raise ValueError("'at' only works with days, like 'every day at dusk'")
        at = TIMES[at_name]
    if clock == "real" and n * unit < 60:
        raise ValueError("run real-time jobs at most once a minute")
    goal = match.group("rest").strip()
    if not goal:
        raise ValueError("say what to do after the schedule, like 'every day: harvest the wheat farm'")
    spoken = text[:match.start("rest")].strip().rstrip(":,-").strip()
    return Schedule(clock, n * unit, at, spoken), goal


@dataclass
class Job:
    id: int
    goal: str
    schedule: dict[str, Any]
    enabled: bool = True
    runs: int = 0
    successes: int = 0
    failures_in_row: int = 0
    last_game: Optional[int] = None
    last_real: Optional[float] = None
    retry_after: Optional[float] = None
    #: Set by ``/minegpt job run``: start as soon as possible, whatever the schedule says.
    run_now: bool = False
    last_outcome: Optional[str] = None
    last_summary: Optional[str] = None
    added: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M"))

    @property
    def when(self) -> Schedule:
        return Schedule(**self.schedule)

    def due(self, game_time: int, day_time: int, now: float) -> bool:
        if not self.enabled or (self.retry_after is not None and now < self.retry_after):
            return False
        if self.run_now:
            return True
        sched = self.when
        if sched.clock == "real":
            return self.last_real is None or now - self.last_real >= sched.interval
        if self.last_game is not None and 0 <= game_time - self.last_game < sched.interval * EARLY:
            return False
        if sched.at is None:
            return True
        return (day_time - sched.at) % DAY_TICKS < AT_WINDOW

    def wait(self, game_time: int, day_time: int, now: float) -> Optional[float]:
        """Roughly how many real seconds until the job is due, or None if it is paused."""
        if not self.enabled:
            return None
        if self.due(game_time, day_time, now):
            return 0.0
        waits = []
        if self.retry_after is not None:
            waits.append(self.retry_after - now)
        sched = self.when
        if sched.clock == "real":
            waits.append(sched.interval - (now - (self.last_real or now)))
        else:
            if self.last_game is not None:
                waits.append((sched.interval * EARLY - (game_time - self.last_game)) / 20)
            if sched.at is not None:
                waits.append(((sched.at - day_time) % DAY_TICKS) / 20)
        return max(0.0, max(waits)) if waits else 0.0

    def describe(self) -> str:
        state = "" if self.enabled else " [paused]"
        stats = f"{self.successes}/{self.runs} ok" if self.runs else "not run yet"
        last = f", last: {self.last_outcome}" if self.last_outcome else ""
        return f"#{self.id} {self.when.describe()}: {self.goal} ({stats}{last}){state}"


class JobBook:
    """The jobs of one world, saved to disk after every change."""

    def __init__(self, path: Path, world: str):
        self.path = Path(path)
        self.world = world
        self._data = _load(self.path)
        entry = self._data.setdefault("worlds", {}).setdefault(world, {"next_id": 1, "jobs": []})
        self._entry = entry
        self.jobs: list[Job] = [Job(**j) for j in entry["jobs"]]

    @property
    def active(self) -> bool:
        """False after ``/minegpt job off``: no jobs start and the agent does nothing between them."""
        return self._entry.get("active", True)

    @active.setter
    def active(self, value: bool) -> None:
        self._entry["active"] = value
        self.save()

    def has_work(self) -> bool:
        return self.active and any(j.enabled for j in self.jobs)

    def add(self, text: str) -> Job:
        schedule, goal = parse(text)
        job = Job(self._entry["next_id"], goal, asdict(schedule))
        self._entry["next_id"] += 1
        self.jobs.append(job)
        self.save()
        return job

    def get(self, job_id: int) -> Optional[Job]:
        return next((j for j in self.jobs if j.id == job_id), None)

    def remove(self, job_id: int) -> bool:
        before = len(self.jobs)
        self.jobs = [j for j in self.jobs if j.id != job_id]
        self.save()
        return len(self.jobs) < before

    def next_due(self, game_time: int, day_time: int, now: Optional[float] = None) -> Optional[Job]:
        """The due job that has waited longest (never-run jobs first)."""
        now = time.time() if now is None else now
        if not self.active:
            return None
        due = [j for j in self.jobs if j.due(game_time, day_time, now)]
        if not due:
            return None
        return min(due, key=lambda j: (j.last_real or 0.0, j.id))

    def record(self, job: Job, outcome: str, summary: str, game_time: int, now: Optional[float] = None) -> Optional[str]:
        """Notes how a run went. Returns a message for the player if the job was paused because of it."""
        now = time.time() if now is None else now
        job.last_outcome = outcome
        job.last_summary = summary[:300]
        job.run_now = False
        if outcome == "replaced":
            # The player set a goal of their own; the job runs once that is done.
            self.save()
            return None
        job.runs += 1
        message = None
        if outcome in ("success", "stopped"):
            # A job stopped in game counts as this run, so it does not start straight back up.
            job.last_game, job.last_real = game_time, now
            job.retry_after = None
            if outcome == "success":
                job.successes += 1
                job.failures_in_row = 0
        else:
            job.failures_in_row += 1
            wait = min(MAX_RETRY, job.when.interval / 20 if job.when.clock == "game" else job.when.interval)
            job.retry_after = now + wait * job.failures_in_row
            if job.failures_in_row >= MAX_FAILURES:
                job.enabled = False
                job.last_game, job.last_real = game_time, now
                message = (f"Job #{job.id} failed {job.failures_in_row} times in a row, so it is paused: {summary[:150]}. "
                           f"Fix what is wrong, then /minegpt job resume {job.id}.")
        self.save()
        return message

    def save(self) -> None:
        self._entry["jobs"] = [asdict(j) for j in self.jobs]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1))
        tmp.replace(self.path)


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        path.replace(path.with_suffix(".broken"))
        return {}
