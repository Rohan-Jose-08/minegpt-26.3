"""Phase 5 automation: recurring jobs, and what the agent does on its own between them.

The agent works on goals the player sets first. When it has none, :class:`Automation` starts
the job that is due (set with ``/minegpt job add``), runs it as an ordinary goal, and records
how it went. Between jobs it looks after the player the way someone left playing would: it
fends off mobs that come close, eats when hungry, sleeps through the night when a bed is
near, and walks back to the base it remembers. All of this only happens in worlds that have
jobs, and ``/minegpt job off`` turns it off.
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from ..bridge import BridgeError
from ..observation import Observation
from . import commands
from .jobs import DAY_TICKS, Job, JobBook, parse

if TYPE_CHECKING:
    from ..agent.loop import Agent, EpisodeResult

__all__ = ["Automation", "Job", "JobBook", "parse"]

log = logging.getLogger("minegpt.automation")

#: Seconds between looks at the schedule while idle.
CHECK_EVERY = 5.0
#: Night, in ticks after sunrise, when beds can be used.
NIGHT = (12542, 23459)
#: Walk back to the base when further than this from it.
HOME_RADIUS = 32


class Automation:
    def __init__(self, agent: "Agent", data_dir: Path, idle: bool = True):
        self.agent = agent
        self.data_dir = Path(data_dir)
        self.idle_enabled = idle
        self._book: Optional[JobBook] = None
        self._last_check = 0.0
        self._status = ""
        #: The day number of the last night the agent tried to sleep, so it tries once a night.
        self._slept_day: Optional[int] = None
        self._went_home = False

    # Jobs -------------------------------------------------------------------------------

    def book(self, world: str) -> JobBook:
        if self._book is None or self._book.world != world:
            self._book = JobBook(self.data_dir / "jobs.json", world)
        return self._book

    def command(self, name: str, text: str) -> None:
        """Handles a command typed in game and passed on by the mod, such as ``/minegpt job list``."""
        if name != "job":
            self._notify(f"The brain does not know the command {name!r}.")
            return
        try:
            world = self.agent.observe().data.get("world", "unknown")
        except BridgeError as e:
            self._notify(f"Cannot change jobs right now: {e}")
            return
        for line in commands.handle(text, self.book(world)):
            self._notify(line)
        self._last_check = 0.0

    def tick(self) -> None:
        """Called while there is no goal: starts a due job, or looks after the player."""
        now = time.monotonic()
        if now - self._last_check < CHECK_EVERY:
            return
        self._last_check = now
        if self.agent.mode == "paused":
            return
        try:
            obs = self.agent.observe()
        except BridgeError:
            return  # no world loaded
        book = self.book(obs.data.get("world", "unknown"))
        game_time, day_time = _clock(obs)
        job = book.next_due(game_time, day_time)
        if job is not None:
            self.run_job(job)
            self._went_home = False
            return
        if book.has_work():
            self._show_next(book, game_time, day_time)
            if self.idle_enabled:
                self._idle(obs)

    def run_job(self, job: Job) -> "EpisodeResult":
        book = self._book
        log.info("Starting job #%d: %s", job.id, job.goal)
        self._write("job_start", job=job.id, goal=job.goal)
        self.agent.bridge.set_goal(job.goal)
        # The goal event from the mod follows; meanwhile the agent already knows its goal.
        self.agent.goal = job.goal
        result = self.agent.run_goal(job.goal, job=job)
        try:
            game_time, _ = _clock(self.agent.observe())
        except BridgeError:
            game_time = job.last_game or 0
        message = book.record(job, result.outcome, result.summary, game_time) if book else None
        self._write("job_end", job=job.id, outcome=result.outcome, summary=result.summary, turns=result.turns,
                    usage=result.usage)
        log.info("Job #%d %s: %s", job.id, result.outcome, result.summary)
        if message:
            log.warning(message)
            self._notify(message)
        self._status = ""
        return result

    def _show_next(self, book: JobBook, game_time: int, day_time: int) -> None:
        now = time.time()
        waits = [(w, j) for j in book.jobs if (w := j.wait(game_time, day_time, now)) is not None]
        if not waits:
            return
        wait, job = min(waits, key=lambda wj: wj[0])
        text = f"MineGPT: next job #{job.id} ({job.goal[:40]}) in about {_duration(wait)}"
        if text != self._status:
            self._status = text
            self._safe(self.agent.bridge.set_status, text)

    # Between jobs -----------------------------------------------------------------------

    def _idle(self, obs: Observation) -> None:
        cfg = self.agent.config
        threats = [e for e in obs.entities if e.get("hostile") and e.get("distance", 99) <= cfg.fight_within]
        if threats and obs.health > 8:
            self._skill("fight", radius=cfg.fight_within + 3, timeout=30)
            return
        if obs.food <= cfg.eat_below:
            self._skill("eat")
            return
        game_time, day_time = _clock(obs)
        day = game_time // DAY_TICKS
        tod = day_time % DAY_TICKS
        if NIGHT[0] <= tod <= NIGHT[1] and self._slept_day != day and obs.data.get("dimension") == "minecraft:overworld":
            self._slept_day = day
            if self._bed_near():
                self._skill("sleep")
                return
        base = self._base(obs)
        if base is not None and not self._went_home and obs.distance_to(*base) > HOME_RADIUS:
            self._went_home = True
            self._skill("goto", x=base[0], y=base[1], z=base[2], radius=4)

    def _bed_near(self) -> bool:
        try:
            return bool(self.agent.bridge.find("#minecraft:beds", radius=32, limit=1))
        except BridgeError:
            return False

    def _base(self, obs: Observation) -> Optional[tuple[int, int, int]]:
        from ..agent.memory import WorldMemory

        memory = WorldMemory(self.agent.config.data_dir / "memory.json", obs.data.get("world", "unknown"))
        base = next((e for e in memory.entries if e["kind"] == "base" and e.get("pos")
                     and e.get("dimension") in (None, obs.data.get("dimension"))), None)
        return tuple(base["pos"]) if base else None

    def _skill(self, name: str, **args: Any) -> None:
        log.info("Between jobs: %s %s", name, " ".join(f"{k}={v}" for k, v in args.items()))
        try:
            result = self.agent.skills.run(name, **args)
        except BridgeError as e:
            log.info("  %s: %s", name, e)
            return
        log.info("  %s", result.message)
        self._write("idle", skill=name, args=args, ok=result.success, message=result.message)

    # Plumbing ---------------------------------------------------------------------------

    def _notify(self, text: str) -> None:
        self._safe(self.agent.bridge.notify, text)

    def _write(self, event: str, **fields: Any) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with (self.data_dir / "automation.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event": event, "time": time.strftime("%Y-%m-%d %H:%M:%S"), **fields}, default=str)
                    + "\n")

    @staticmethod
    def _safe(function, *args) -> None:
        try:
            function(*args)
        except (BridgeError, ConnectionError, TimeoutError) as e:
            log.debug("%s failed: %s", getattr(function, "__name__", function), e)


def _clock(obs: Observation) -> tuple[int, int]:
    game_time = int(obs.data.get("gameTime") or 0)
    day_time = int(obs.data.get("dayTime") or 0)
    return game_time, day_time


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{max(1, math.ceil(seconds))}s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"
