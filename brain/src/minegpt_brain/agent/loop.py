"""The goal-driven agent: goal, state and memory in, skill calls out, checked by the game.

For each goal the agent starts a fresh conversation with the model, describes the situation,
and then loops: the model calls tools, the agent runs them, handles reflexes (eating, fending
off mobs) and deaths itself, and reports back with a state note. The loop ends when the model
calls ``goal_done`` (and the game agrees), calls ``goal_failed``, runs out of turns, or the
player stops or replaces the goal in game.
"""

from __future__ import annotations

import logging
import os
import queue
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from ..bridge import Bridge, BridgeError
from ..llm import Backend, BackendError, ToolResult
from ..observation import Observation
from ..practice.failures import FailureLog
from ..practice.tuner import Tuner
from ..skills import SkillResult, Skills
from . import contracts, critic, playbooks, prompts, state
from .blueprints import BlueprintLibrary
from .critic import FailureTracker, Unreachable
from .designs import DesignLibrary
from .memory import WorldMemory
from .experience import ExperiencePool, situation
from .routines import RoutineLibrary, describe_steps
from .skillstats import SkillStats
from .taskgraph import GoalStore
from .worldmap import WorldMap
from .tools import _ACTING_TOOLS, PLACE_BOUND, SKILLS, Outcome, Plan, Toolbox, tool_specs
from .trace import EpisodeLog, compact_obs

if TYPE_CHECKING:
    from ..automation import Job

log = logging.getLogger("minegpt.agent")


#: Sent when the model replies without calling a tool.
NUDGE = "Carry on by calling tools. Call goal_done when the goal is achieved, or goal_failed if it cannot be done."


def default_data_dir() -> Path:
    return Path(os.environ.get("MINEGPT_DATA_DIR") or Path.home() / ".minegpt")


@dataclass
class AgentConfig:
    max_turns: int = 80
    #: Extra turns granted each time the game checks off a plan step, so long jobs that keep
    #: making verified progress are not cut short, up to ``turn_cap`` in all.
    turns_per_step: int = 12
    turn_cap: int = 300
    data_dir: Path = field(default_factory=default_data_dir)
    #: Eat and fight between turns without asking the model.
    reflexes: bool = True
    eat_below: int = 14
    fight_within: float = 5.0
    #: Pick up dropped items this close between turns.
    pickup_within: float = 8.0
    #: Width in pixels of the pictures review_build shows the model. Wider costs more tokens.
    screenshot_width: int = 640
    #: Let mine target hidden blocks. Only for singleplayer or servers that allow it.
    xray: bool = False
    #: Seconds between checks while waiting for a goal or for the player to resume.
    poll: float = 0.5
    #: Run recurring jobs (``/minegpt job``) when there is no goal.
    jobs: bool = True
    #: Between jobs, fend off mobs, eat, sleep at night and walk home on its own.
    idle: bool = True
    #: Pick up the saved plan of a goal that was interrupted. Off for benchmark and collection runs,
    #: which start the same goal again from a reset player.
    resume: bool = True
    #: Send the tuned skill settings with each skill call (see practice.tuner).
    tune: bool = True
    #: Try other values of the settings and learn from how they do. Only practice turns this on.
    explore: bool = False
    #: Where the tuned settings and the failure records are kept, shared by goals, the benchmark and
    #: practice. None for ``data_dir``.
    shared_dir: Optional[Path] = None


@dataclass
class EpisodeResult:
    goal: str
    #: ``success``, ``failed``, ``turn_limit``, ``stopped`` (goal cleared in game),
    #: ``replaced`` (a new goal came in) or ``error``.
    outcome: str
    summary: str
    turns: int
    verified: bool = False
    usage: dict[str, int] = field(default_factory=dict)
    log_path: Optional[Path] = None


@dataclass
class Episode:
    goal: str
    memory: WorldMemory
    log: EpisodeLog
    plan: Plan = field(default_factory=Plan)
    failures: FailureTracker = field(default_factory=FailureTracker)
    #: Skill calls that worked, in order, for the routine saved on success.
    successful_calls: list[dict[str, Any]] = field(default_factory=list)
    #: Notes for the next state report, such as reflexes that ran.
    notes: list[str] = field(default_factory=list)
    #: Dropped items (entity ids) the pickup reflex tried and failed to get, so it stops trying.
    unreachable_drops: set[int] = field(default_factory=set)
    #: Places goto could not get to lately, so the same trip is not tried again at once.
    unreachable: Unreachable = field(default_factory=Unreachable)
    #: Structures build_structure finished: ``kind`` and ``kind:part``.
    built: set[str] = field(default_factory=set)
    #: How many times review_build has looked at each build, by rough position.
    reviews: dict[tuple[int, int], int] = field(default_factory=dict)
    #: The style book theme picked for this goal's builds, once one has been.
    theme: str = ""
    #: Sites build_structure has cleared, by kind and origin, so a resumed build does not clear again.
    cleared: set[tuple[str, int, int, int]] = field(default_factory=set)
    #: build_structure calls that stopped, by their arguments: times in a row it stopped the same
    #: way, how, and how many skill calls had worked by then (so a fix made by hand lets it run again).
    build_stops: dict[str, tuple[int, str, int]] = field(default_factory=dict)
    #: Turns this goal may take; grows as verified plan steps complete.
    budget: int = 0
    #: Plan steps the game has checked off so far.
    milestones: int = 0
    #: The world id, and the map of what has been seen in it.
    world: str = "unknown"
    world_map: Optional[WorldMap] = None
    #: First lines of the skill failures met, for the record kept of this goal.
    problems: list[str] = field(default_factory=list)
    #: How the goal started (tools, place), and when.
    start: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)


@dataclass
class SkillRun:
    result: SkillResult
    changes: dict[str, int]
    after: Optional[Observation] = None
    #: Set when the player died during the skill: what to tell the model.
    death: Optional[str] = None


class Agent:
    def __init__(self, bridge: Bridge, backend: Backend, config: Optional[AgentConfig] = None):
        self.bridge = bridge
        self.backend = backend
        self.config = config or AgentConfig()
        self.skills = Skills(bridge)
        self.routines = RoutineLibrary(self.config.data_dir / "routines.json")
        self.blueprints = BlueprintLibrary(self.config.data_dir / "blueprints.json")
        self.designs = DesignLibrary(self.config.data_dir / "designs.json")
        self.stats = SkillStats(self.config.data_dir / "skillstats.json")
        self.experience = ExperiencePool(self.config.data_dir / "experience.json")
        self.goals = GoalStore(self.config.data_dir / "goals")
        shared = self.config.shared_dir or self.config.data_dir
        self.tuner = Tuner(shared / "tuning.json")
        self.failures = FailureLog(shared / "failures")
        self._knowledge: Any = False
        self.mode: str = bridge.info.get("mode", "idle")
        self.goal: Optional[str] = bridge.info.get("goal")
        self.automation = None
        if self.config.jobs:
            from ..automation import Automation

            self.automation = Automation(self, self.config.data_dir, idle=self.config.idle)

    @property
    def knowledge(self):
        """The game's recipes and drops, with this player's measured timings; None if the data is missing."""
        if self._knowledge is False:
            try:
                from ..knowledge import load

                self._knowledge = load().with_costs(self.stats.measured())
            except Exception as e:  # noqa: BLE001 - the agent still works without it, with fewer aids
                log.warning("No game data, so run_plan is off and plan_materials knows fewer recipes: %s", e)
                self._knowledge = None
        return self._knowledge

    def check_calls(self, calls: list[dict[str, Any]], inventory: dict[str, int]) -> Optional[str]:
        """Plays skill calls forward on paper; the first step that could not work, or None."""
        if self.knowledge is None:
            return None
        try:
            return contracts.simulate(calls, inventory, self.knowledge).first()
        except Exception as e:  # noqa: BLE001 - a check that breaks must not stop the real thing
            log.debug("could not check calls: %s", e)
            return None

    def between_steps(self, episode: "Episode") -> None:
        """What happens between model turns, for tools that run many skills in one turn."""
        if self.config.reflexes and not self._check_goal(episode.goal):
            self._reflexes(episode)

    # Goals ------------------------------------------------------------------------------

    def run_forever(self, once: bool = False) -> Optional[EpisodeResult]:
        """Works on goals as the player sets them with ``/minegpt goal``. With ``once``, stops after one."""
        self.bridge.set_status("MineGPT ready: set a goal with /minegpt goal <text>")
        log.info("Waiting for a goal (in game: /minegpt goal <text>)")
        while True:
            self.poll_events(self.config.poll)
            if self.goal is None:
                if self.automation is not None:
                    try:
                        self.automation.tick()
                    except BridgeError as e:
                        log.warning("Automation paused: %s", e)
                        time.sleep(5)
                continue
            try:
                result = self.run_goal(self.goal)
            except BridgeError as e:
                # Usually no world is loaded, for example on the title screen.
                log.warning("Cannot work on the goal right now: %s", e)
                time.sleep(5)
                continue
            if once:
                return result
            if result.outcome == "error":
                time.sleep(5)

    def run_goal(self, goal: str, job: Optional["Job"] = None) -> EpisodeResult:
        """Works on one goal until it ends. ``job`` is set when the goal is a recurring job's."""
        obs = self.observe()
        self._knowledge = False  # load again, so the planner's timings include the last goal's
        world = obs.data.get("world", "unknown")
        memory = WorldMemory(self.config.data_dir / "memory.json", world)
        episode = Episode(goal, memory, EpisodeLog(self.config.data_dir / "episodes", goal))
        episode.budget = self.config.max_turns
        episode.world = world
        episode.world_map = WorldMap(self.config.data_dir / "worldmap.json", world)
        episode.start = situation(state.counts(obs), obs.pos[1], obs.data.get("dimension"))
        saved = self.goals.load(world, goal) if job is None and self.config.resume else None
        if saved is not None:
            episode.plan, episode.built = saved
            episode.plan.update(obs, self.world(episode))
        episode.log.write("start", goal=goal, world=world, backend=self.backend.name,
                          max_turns=self.config.max_turns, obs=compact_obs(obs.data),
                          job=None if job is None else job.id)
        log.info("Goal: %s (world %s, %s)", goal, world, self.backend.name)
        usage: dict[str, int] = {}
        result: Optional[EpisodeResult] = None
        turns = 0
        try:
            tools = tool_specs(vision=getattr(self.backend, "vision", False))
            conversation = self.backend.start(prompts.SYSTEM, tools)
            similar = self.routines.similar(goal)
            carried = state.counts(obs)
            first = prompts.first_message(
                goal, state.status_line(obs), state.inventory_line(obs),
                state.nearby_lines(self.scan(episode.world_map, obs)),
                memory.describe(obs.pos, obs.data.get("dimension")),
                [f"{r['name']!r} ({r.get('successes', 0)} of {r.get('runs', 0)} replays worked"
                 f"{self._routine_check(r, carried)}): "
                 f"{r['description']} Steps: {describe_steps(r['steps'])}" for _, r in similar],
                self.config.max_turns, ground=state.ground_line(obs),
                extra=self._map_lines(episode, obs) + self._experience_lines(episode) + self._resume_lines(episode)
                + self._safety_lines(obs) + self._blueprint_lines() + _guide_lines(goal) + _job_lines(job))
            conversation.add_user(first)
            episode.log.write("prompt", system=prompts.SYSTEM, text=first,
                              tools=[{"name": t.name, "description": t.description, "parameters": t.parameters}
                                     for t in tools])

            toolbox = Toolbox(self, episode)
            idle_turns = 0
            while result is None:
                if turns >= episode.budget:
                    result = EpisodeResult(goal, "turn_limit", f"ran out of turns ({turns})", turns)
                    break
                interrupted = self._check_goal(goal) or self.wait_while_paused(goal)
                if interrupted:
                    result = EpisodeResult(goal, interrupted, _interrupt_text(interrupted), turns)
                    break
                step = episode.plan.current()
                self.bridge.set_status(f"Thinking: {step[1].text}" if step else f"Thinking: {goal}")
                turns += 1
                turn = conversation.next()
                for key, value in turn.usage.items():
                    usage[key] = usage.get(key, 0) + value
                episode.log.write("turn", n=turns, model=turn.model, stop=turn.stop, text=turn.text,
                                  calls=[{"id": c.id, "name": c.name, "args": c.args} for c in turn.tool_calls],
                                  usage=turn.usage, raw=turn.raw)
                if turn.text.strip():
                    log.info("MineGPT: %s", turn.text.strip())
                if turn.stop == "refusal":
                    result = EpisodeResult(goal, "failed", "the model declined to continue", turns)
                    break
                if not turn.tool_calls:
                    idle_turns += 1
                    if idle_turns >= 3:
                        result = EpisodeResult(goal, "failed", "the model stopped calling tools", turns)
                        break
                    conversation.add_user(NUDGE)
                    episode.log.write("user", text=NUDGE)
                    continue
                idle_turns = 0

                results, end = self._run_calls(toolbox, episode, turn)
                # Exactly what goes back to the model, including calls that were not run.
                episode.log.write("results", results=[{"id": r.call_id, "content": r.content, "error": r.is_error}
                                                      for r in results])
                if end is not None:
                    outcome = end.end or "failed"
                    result = EpisodeResult(goal, outcome, end.summary, turns, verified=end.verified)
                    conversation.add_results(results)
                    break
                note = self._report(episode, turns)
                conversation.add_results(results, note)
                episode.log.write("note", text=note)
        except BackendError as e:
            log.error("Model error: %s", e)
            result = EpisodeResult(goal, "error", str(e), turns)
        except KeyboardInterrupt:
            episode.log.write("end", outcome="stopped", summary="stopped with Ctrl+C", turns=turns, usage=usage)
            episode.log.close()
            self._safe(self.bridge.set_status, "MineGPT brain stopped")
            raise
        except (ConnectionError, TimeoutError) as e:
            episode.log.write("end", outcome="disconnected", summary=str(e), turns=turns, usage=usage)
            episode.log.close()
            raise

        result.usage = usage
        result.log_path = episode.log.path
        self._finish(episode, result)
        return result

    def _routine_check(self, routine: dict[str, Any], carried: dict[str, int]) -> str:
        problem = self.check_calls(routine["steps"], carried)
        return f"; from what you carry now it would fail at {problem}" if problem else ""

    @staticmethod
    def _map_lines(episode: Episode, obs: Observation) -> list[str]:
        if episode.world_map is None:
            return []
        lines = episode.world_map.lines(obs.pos, obs.data.get("dimension"))
        return ["Ore seen earlier in this world, out of sight now (recall finds more):"] + \
            [f"- {line}" for line in lines] if lines else []

    def _experience_lines(self, episode: Episode) -> list[str]:
        records = self.experience.similar(episode.goal, episode.start)
        if not records:
            return []
        lines = ["Earlier goals like this one (use what worked, avoid what did not):"]
        for record in records:
            described = ExperiencePool.describe(record)
            lines += ["- " + described[0]] + described[1:]
        return lines

    @staticmethod
    def _resume_lines(episode: Episode) -> list[str]:
        if not episode.plan.resumed:
            return []
        return ["This goal was started before and interrupted. Its plan was kept, and steps the game checked "
                "then are still ticked: " + episode.plan.describe(),
                "Carry on from the first open step. Look at what you carry first: if things were lost since, "
                "set_plan again."]

    def _safety_lines(self, obs: Observation) -> list[str]:
        """The protected areas in this dimension, which skills will not break or build in."""
        try:
            safety = self.bridge.safety()
        except (BridgeError, KeyError):
            return []  # an older mod without the safety op
        regions = [r for r in safety.get("regions", []) if r.get("dimension") in (None, obs.data.get("dimension"))]
        if not regions:
            return []
        lines = ["Protected areas the player set (every height): skills cannot break or place blocks inside them."]
        for r in regions:
            lines.append(f"- {r['name']}: x {r['minX']} to {r['maxX']}, z {r['minZ']} to {r['maxZ']}"
                         + (", but farming (harvest, replant, till) is allowed" if r.get("farm") else ""))
        return lines

    def _blueprint_lines(self) -> list[str]:
        described = self.blueprints.describe()
        if not described:
            return []
        return ["Saved blueprints (build one with build(blueprint=..., x, y, z)):"] + [f"- {d}" for d in described]

    def _run_calls(self, toolbox: Toolbox, episode: Episode, turn) -> tuple[list[ToolResult], Optional[Outcome]]:
        results: list[ToolResult] = []
        end: Optional[Outcome] = None
        skipped_because: Optional[str] = None
        for call in turn.tool_calls:
            if turn.stop == "max_tokens":
                results.append(ToolResult(call.id, "not run: your reply hit the length limit. Think less per "
                                          "turn and make fewer calls.", True))
                continue
            if end is not None:
                results.append(ToolResult(call.id, "not run: the goal has already ended", True))
                continue
            if skipped_because and call.name in SKILLS + _ACTING_TOOLS:
                results.append(ToolResult(call.id, f"not run: skipped because {skipped_because} failed", True))
                continue
            if self._check_goal(episode.goal):
                results.append(ToolResult(call.id, "not run: the player changed or cleared the goal", True))
                continue
            log.info("> %s %s", call.name, _args_text(call.args))
            before = time.monotonic()
            outcome = toolbox.call(call.name, call.args)
            first_line = outcome.content.splitlines()[0] if outcome.content else ""
            log.info("  %s", first_line)
            episode.log.write("tool", id=call.id, name=call.name, args=call.args, ok=not outcome.is_error,
                              result=outcome.content, seconds=round(time.monotonic() - before, 1))
            results.append(ToolResult(call.id, outcome.content, outcome.is_error, images=outcome.images))
            if outcome.skill_failed:
                skipped_because = call.name
            if outcome.end is not None:
                end = outcome
        return results, end

    def _report(self, episode: Episode, turns: int) -> str:
        """Runs reflexes, then describes the state for the model's next turn."""
        if self.config.reflexes and not self._check_goal(episode.goal):
            self._reflexes(episode)
        obs = self.observe()
        self._update_plan(episode, obs)
        lines = ["State: " + state.status_line(obs), state.inventory_line(obs)]
        ground = state.ground_line(obs)
        if ground:
            lines.append(ground)
        lines.append("Plan: " + episode.plan.describe())
        stuck = episode.plan.stuck()
        if stuck is not None:
            label, step = stuck
            lines.append(f"Step {label} ({step.text}) is stuck"
                         + (f": {step.last_error}" if step.last_error else "")
                         + ". Give it another way with expand_step; the rest of the plan stays.")
        self._save_progress(episode)
        lines += episode.unreachable.lines()
        lines += episode.notes
        episode.notes.clear()
        left = episode.budget - turns
        if left <= 10:
            lines.append(f"Turns left: {left}.")
        return "\n".join(lines)

    def _save_progress(self, episode: Episode) -> None:
        """Keeps the plan and the map on disk, so an interrupted goal can carry on."""
        try:
            if self.config.resume:
                self.goals.save(episode.world, episode.goal, episode.plan, episode.built)
            if episode.world_map is not None:
                episode.world_map.save()
        except OSError as e:
            log.debug("could not save progress: %s", e)

    def _reflexes(self, episode: Episode) -> None:
        obs = self.observe()
        threats = [e for e in state.hostiles(obs, self.config.fight_within)
                   if state.short(e["type"]) not in ("enderman", "piglin", "zombified_piglin")]
        # Low on health, only fight what is already in arm's reach; the mod cancels work at low
        # health when a mob is close, and standing still would be worse.
        if threats and (obs.health > 8 or min(e.get("distance", 99) for e in threats) <= 3):
            run = self.run_skill("fight", {"radius": self.config.fight_within + 3, "timeout": 30}, episode)
            episode.notes.append(f"Reflex: a {state.short(threats[0]['type'])} came close, so the player fought: "
                                 f"{run.result.message}" + (f" {run.death}" if run.death else ""))
            episode.log.write("reflex", kind="fight", ok=run.result.success, message=run.result.message)
            obs = self.observe()
        if obs.food <= self.config.eat_below:
            run = self.run_skill("eat", {}, episode)
            if run.result.success:
                episode.notes.append(f"Reflex: food was low, so the player ate ({run.result.message}).")
            else:
                episode.notes.append(f"Food is low ({obs.food}/20) and the player has nothing safe to eat: get "
                                     "food soon.")
            episode.log.write("reflex", kind="eat", ok=run.result.success, message=run.result.message)
            obs = self.observe()
        self._pick_up(episode, obs)

    def _pick_up(self, episode: Episode, obs: Observation) -> None:
        """Picks up drops left lying close by, such as ones that bounced away from a mined block."""
        drops = {e["id"] for e in state.ground_items(obs, self.config.pickup_within) if "id" in e}
        drops -= episode.unreachable_drops
        if not drops or state.free_slots(obs) == 0:
            return
        run = self.run_skill("collect", {"radius": self.config.pickup_within, "timeout": 30}, episode)
        after = run.after or self.observe()
        left = {e["id"] for e in state.ground_items(after, self.config.pickup_within + 4) if "id" in e}
        episode.unreachable_drops |= drops & left
        if run.changes:
            episode.notes.append(f"Reflex: picked up items lying nearby ({state.delta_line(run.changes)}).")
        episode.log.write("reflex", kind="pickup", ok=run.result.success, message=run.result.message)

    def _finish(self, episode: Episode, result: EpisodeResult) -> None:
        log.info("Goal %s: %s (%s) after %d turns. Episode log: %s", result.outcome, result.goal, result.summary,
                 result.turns, episode.log.path)
        if result.usage:
            log.info("Tokens: %s", ", ".join(f"{k} {v}" for k, v in result.usage.items()))
        episode.log.write("end", outcome=result.outcome, summary=result.summary, turns=result.turns,
                          verified=result.verified, usage=result.usage)
        episode.log.close()
        if result.outcome == "success":
            self._save_routine(episode, result)
        self._record(episode, result)
        messages = {
            "success": f"Done: {result.goal}",
            "failed": f"Gave up on {result.goal}: {result.summary}",
            "turn_limit": f"Ran out of turns on {result.goal}",
            "error": f"Brain error: {result.summary}",
        }
        if result.outcome in messages:
            # Clearing the goal in game, so the same goal does not start again by itself.
            self._safe(self.bridge.finish_goal, messages[result.outcome][:200])
            if self.goal == result.goal:
                self.goal = None

    def _record(self, episode: Episode, result: EpisodeResult) -> None:
        """Files the goal's plan and outcome for later goals, and drops or keeps the saved plan."""
        try:
            if result.outcome in ("success", "failed", "replaced") or not self.config.resume:
                self.goals.clear(episode.world, episode.goal)
            else:
                self.goals.save(episode.world, episode.goal, episode.plan, episode.built)
            if episode.world_map is not None:
                episode.world_map.save()
            self.stats.save()
            self.tuner.save()
            if result.outcome in ("success", "failed", "turn_limit") and result.turns > 0:
                self.experience.add(episode.goal, episode.world, episode.start, result.outcome, result.verified,
                                    result.turns, time.monotonic() - episode.started,
                                    episode.plan.to_dict() if episode.plan.steps else None,
                                    episode.plan.outline(), episode.problems, result.summary, self.backend.name)
        except Exception as e:  # noqa: BLE001 - records are a convenience; the goal's result stands
            log.debug("could not record the goal: %s", e)

    def _save_routine(self, episode: Episode, result: EpisodeResult) -> None:
        # Positions only make sense in one place, so walking and placing at coordinates are left out.
        steps = [c for c in episode.successful_calls
                 if not (c["tool"] in PLACE_BOUND and any(k in c["args"] for k in "xyz"))]
        if len(steps) < 2:
            return
        plan = "; ".join(s.text for s in episode.plan.steps)
        description = f"Reached the goal {result.goal!r}." + (f" Plan: {plan}." if plan else "")
        self.routines.save(result.goal, description, steps, source="auto")
        log.info("Saved routine %r with %d steps", result.goal, len(steps))

    # Skills -----------------------------------------------------------------------------

    def run_skill(self, name: str, args: dict[str, Any], episode: Episode) -> SkillRun:
        """Runs a skill, waiting out a pause, and notices if the player died during it."""
        if self.wait_while_paused(episode.goal):
            return SkillRun(SkillResult(name, False, "cancelled: the goal changed"), {})
        before = self.observe()
        began = time.monotonic()
        result = self._skill(name, args)
        if result.message == "MineGPT is paused" and not self.wait_while_paused(episode.goal):
            began = time.monotonic()
            result = self._skill(name, args)
        self.stats.record(name, args, result.success, time.monotonic() - began, result.data)
        # Stall events that came in while the skill ran.
        self.poll_events()
        after = self.observe()
        changes = state.delta(state.counts(before), state.counts(after))
        episode.log.write("skill", name=name, args=args, success=result.success, message=result.message,
                          data=result.data, before=compact_obs(before.data), after=compact_obs(after.data))
        death = None
        if result.message.startswith("cancelled: the player died") or after.health <= 0:
            death = self._handle_death(episode, before, after)
            changes = {}
        else:
            # Checked after every skill, since a later skill may use up what an earlier step gathered.
            self._update_plan(episode, after)
        return SkillRun(result, changes, after, death)

    def _skill(self, name: str, args: dict[str, Any]) -> SkillResult:
        tuning, trial = self.tuner.settings(name, self.config.explore) if self.config.tune else (None, None)
        began = time.monotonic()
        self._running = (name, args)
        try:
            result = self.skills.run(name, tuning=tuning, **args)
        except BridgeError as e:
            # The mod refused the arguments before starting.
            return SkillResult(name, False, f"bad arguments: {e}")
        seconds = time.monotonic() - began
        # The scene of a failure is for the failure miner, not the model or the episode log.
        scene = result.data.pop("scene", None) if isinstance(result.data, dict) else None
        if result.message == "MineGPT is paused" or result.message.startswith("cancelled: cancelled by"):
            return result
        try:
            if self.config.tune:
                promoted = self.tuner.record(name, args, trial, result.success, seconds, result.data)
                if promoted:
                    log.info("Skill setting changed by practice: %s", promoted)
            if not result.success and scene:
                self.failures.add_failure(name, args, result.message, seconds, scene, self.goal)
        except Exception as e:  # noqa: BLE001 - learning from a run must never break the run
            log.debug("could not file the skill run: %s", e)
        return result

    def _handle_death(self, episode: Episode, before: Observation, after: Observation) -> str:
        where = state.block_pos(after)
        cause = _death_cause(before, after)
        dimension = after.data.get("dimension")
        episode.memory.add("death", f"died, probably {cause}", where, dimension)
        episode.log.write("death", pos=where, cause=cause, obs=compact_obs(after.data))
        log.warning("The player died at %s %s %s (%s). Respawn and press F8 to carry on.", *where, cause)
        self._safe(self.bridge.set_status, "MineGPT: died. Respawn, then press F8 to carry on")
        # The mod pauses on death; wait for the player to respawn and resume.
        self.wait_while_paused(episode.goal)
        while self._check_goal(episode.goal) is None and self.observe().health <= 0:
            self.poll_events(self.config.poll)
        respawn = state.block_pos(self.observe())
        return (f"The player DIED at {where[0]} {where[1]} {where[2]}, probably {cause}. The items carried dropped "
                f"there and vanish 5 minutes after death. Respawned at {respawn[0]} {respawn[1]} {respawn[2]}.")

    def world(self, episode: Episode) -> critic.World:
        """What plan conditions can look up beyond the observation: blocks in sight and finished builds."""
        return critic.World(find=lambda block, radius, limit: self.bridge.find(block, radius=radius, limit=limit),
                            built=episode.built)

    def _update_plan(self, episode: Episode, obs: Observation) -> None:
        """Checks plan steps against the game, and grants more turns for each one newly done."""
        newly = episode.plan.update(obs, self.world(episode))
        if newly:
            episode.milestones += newly
            grown = min(self.config.turn_cap, episode.budget + newly * self.config.turns_per_step)
            if grown > episode.budget:
                episode.notes.append(f"{newly} plan step{'s' if newly > 1 else ''} checked off by the game: "
                                     f"{grown - episode.budget} more turns granted.")
                episode.budget = max(episode.budget, grown)

    # Game state -------------------------------------------------------------------------

    def observe(self) -> Observation:
        return self.bridge.observe(grid=False)

    def scan(self, world_map: Optional[WorldMap] = None,
             obs: Optional[Observation] = None) -> dict[str, list[dict[str, Any]]]:
        """Notable blocks in sight, by heading, for look_around and the first message.

        With a map, what is seen is filed on it, and ore remembered here but gone is forgotten."""
        found = {}
        limits = {"trees": 8, "ores": 16, "stone": 4, "useful blocks": 6, "dangers": 3, "water": 2}
        for heading, specs in state.NOTABLE.items():
            try:
                found[heading] = self.bridge.find(specs, radius=24, limit=limits.get(heading, 6))
            except BridgeError as e:
                log.debug("find %s failed: %s", specs, e)
                continue
            if world_map is not None and obs is not None:
                dimension = obs.data.get("dimension")
                world_map.note_found(found[heading], dimension)
                if heading == "ores" and not found[heading]:
                    world_map.note_absent(["*_ore"], obs.pos, 24, dimension)
        return found

    def poll_events(self, timeout: float = 0.0) -> None:
        """Applies goal and mode events from the mod. Waits up to ``timeout`` for the first one."""
        block = timeout > 0
        while True:
            try:
                event = self.bridge.events.get(block=block, timeout=timeout if block else None)
            except queue.Empty:
                return
            block = False
            if event.get("op") != "event":
                continue  # streamed observations, which the agent does not ask for
            if event.get("event") == "goal":
                self.goal = event.get("goal")
            elif event.get("event") == "stall":
                running = getattr(self, "_running", None)
                args = running[1] if running and running[0] == event.get("skill") else None
                record = self.failures.add_stall(event, self.goal, args)
                log.info("Stalled: %s (%s)", record["cause"], ", ".join(record["tags"]) or "nothing telling in the scene")
            elif event.get("event") == "stall_end":
                self.failures.end_stall(event)
            elif event.get("event") == "mode":
                self.mode = event.get("mode", self.mode)
                if self.mode == "paused" and event.get("reason"):
                    log.warning("MineGPT paused itself in game: %s. Press F8 there to carry on.", event["reason"])
            elif event.get("event") == "command":
                if self.automation is not None:
                    self.automation.command(event.get("command", ""), event.get("text", ""))
                else:
                    self._safe(self.bridge.notify, "Jobs are turned off in the brain (it was started with --no-jobs).")

    def interrupted(self, goal: str) -> bool:
        return self._check_goal(goal) is not None

    def _check_goal(self, goal: str) -> Optional[str]:
        """``stopped`` if the goal was cleared in game, ``replaced`` if there is a new one, else None."""
        self.poll_events()
        if self.goal is None:
            return "stopped"
        if self.goal != goal:
            return "replaced"
        return None

    def wait_while_paused(self, goal: str) -> Optional[str]:
        """Waits while the player has MineGPT paused (F8). Returns why the goal ended, if it did."""
        announced = False
        while True:
            interrupted = self._check_goal(goal)
            if interrupted or self.mode != "paused":
                return interrupted
            if not announced:
                log.info("Paused in game; waiting for F8")
                announced = True
            self.poll_events(self.config.poll)

    @staticmethod
    def _safe(function, *args) -> None:
        try:
            function(*args)
        except (BridgeError, ConnectionError, TimeoutError) as e:
            log.debug("%s failed: %s", getattr(function, "__name__", function), e)


def _death_cause(before: Observation, after: Observation) -> str:
    player = after.player
    if player.get("air", 300) <= 0 or (before.player.get("inWater") and before.player.get("air", 300) < 100):
        return "drowned"
    if player.get("onFire") or before.player.get("onFire"):
        return "burned in lava or fire"
    if after.pos[1] < -64:
        return "fell out of the world"
    threats = state.hostiles(after, 8) or state.hostiles(before, 8)
    if threats:
        return f"killed by a {state.short(threats[0]['type'])}"
    if before.food <= 0:
        return "starved"
    return "from a fall or an unseen attack"


def _job_lines(job: Optional["Job"]) -> list[str]:
    if job is None:
        return []
    lines = [f"This goal is recurring job #{job.id} ({job.when.describe()}), run {job.runs + 1}. It runs again "
             "later, so finish cleanly: put what you gathered where it belongs (store it in the chest it goes "
             "in) and leave the place ready for next time."]
    if job.last_outcome:
        lines.append(f"Last run: {job.last_outcome}: {job.last_summary}")
    return lines


def _guide_lines(goal: str) -> list[str]:
    """The guides that match the goal's wording, in full, and the names of the rest."""
    topics = playbooks.relevant(goal)
    lines = []
    for topic in topics:
        lines += [f"Guide that looks relevant ({topic}):", playbooks.get(topic), ""]
    others = [t for t in playbooks.names() if t not in topics]
    lines.append("Other guides to read with the guide tool when useful: " + ", ".join(others) + ".")
    return lines


def _interrupt_text(reason: str) -> str:
    return "the player cleared the goal in game" if reason == "stopped" else "the player set a new goal"


def _args_text(args: dict[str, Any]) -> str:
    return " ".join(f"{k}={v}" for k, v in args.items())
