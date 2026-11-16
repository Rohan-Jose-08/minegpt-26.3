"""Runs the benchmark suite: ``python -m minegpt_brain.bench``.

Examples::

    python -m minegpt_brain.bench --list
    python -m minegpt_brain.bench --tasks wood,stone --repeat 3
    python -m minegpt_brain.bench --report

Resetting between tasks uses server commands, so run it in a singleplayer world with cheats
on (Open to LAN with cheats works too), not on a server you care about. ``--no-reset`` runs
each task from wherever the player happens to be.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import statistics
import sys
import time
from importlib import metadata
from pathlib import Path
from typing import Any, Optional

from ..agent.__main__ import add_backend_args, backend_from_args
from ..agent.loop import Agent, AgentConfig, default_data_dir
from ..agent.state import block_pos
from ..bridge import DEFAULT_HOST, DEFAULT_PORT, Bridge, BridgeError
from ..llm import BackendError
from . import Task, block_positions, check, get, TASKS

log = logging.getLogger("minegpt.bench")

#: How far apart fresh starting spots are, so each task starts on untouched land.
SPREAD = (3000, 20000)


def reset(bridge: Bridge, agent: Agent, task: Task, rng: random.Random) -> None:
    """Puts the player in a clean state for a task, with commands."""
    commands = ["gamemode survival @s", "clear @s", "effect clear @s", "weather clear", "difficulty easy"]
    if task.time is not None:
        commands.append(f"time set {task.time}")
    for command in commands:
        bridge.run_command(command)
    if task.fresh:
        _move_far(bridge, agent, rng)
    bridge.run_command("effect give @s minecraft:instant_health 1 10 true")
    bridge.run_command("effect give @s minecraft:saturation 1 10 true")
    bridge.run_command("spawnpoint @s ~ ~ ~")
    for command in task.setup:
        bridge.run_command(command)
    for item, count in task.kit.items():
        bridge.run_command(f"give @s {item if ':' in item else 'minecraft:' + item} {count}")
    time.sleep(1.5)  # let the inventory and blocks reach the client


def _move_far(bridge: Bridge, agent: Agent, rng: random.Random, tries: int = 5) -> None:
    """Moves the player to untouched land. spreadplayers refuses spots over water or lava without
    saying so to the brain, so this checks the player moved and tries somewhere else if not."""
    before = block_pos(agent.observe())
    for _ in range(tries):
        x = rng.choice((-1, 1)) * rng.randint(*SPREAD)
        z = rng.choice((-1, 1)) * rng.randint(*SPREAD)
        bridge.run_command(f"spreadplayers {x} {z} 0 64 false @s")
        _wait_for_ground(agent)
        now = block_pos(agent.observe())
        if abs(now[0] - before[0]) + abs(now[2] - before[2]) > 500:
            return
        log.info("spreadplayers found no dry land near %d %d; trying elsewhere", x, z)
    log.warning("Could not move the player to fresh land; the task starts where the last one ended")


def _wait_for_ground(agent: Agent, timeout: float = 20.0) -> None:
    """Waits for the chunks round a new spot to load and the player to land."""
    end = time.monotonic() + timeout
    time.sleep(2)
    while time.monotonic() < end:
        if agent.observe().player.get("onGround"):
            time.sleep(2)
            return
        time.sleep(1)


def run(args: argparse.Namespace) -> int:
    tasks = get([t for t in args.tasks.split(",") if t]) if args.tasks else list(TASKS)
    backend = backend_from_args(args)
    data_dir = args.data_dir or default_data_dir() / "bench"
    results = data_dir / "results.jsonl"
    label = args.label or f"brain {_version()} / {backend.name}"
    # Tuned skill settings and failure records are shared with normal goals, one folder up.
    config = AgentConfig(data_dir=data_dir / "agent", jobs=False, xray=args.xray, resume=False,
                         shared_dir=data_dir.parent)
    rng = random.Random(args.seed)
    bridge = Bridge.connect(args.host, args.port)
    with bridge:
        agent = Agent(bridge, backend, config)
        singleplayer = str(agent.observe().data.get("world", "")).startswith("singleplayer:")
        do_reset = args.reset if args.reset is not None else singleplayer
        if do_reset and not singleplayer:
            log.warning("Resetting with commands on a server: make sure this is a server you may do that on")
        log.info("Running %d task%s x %d as %r (reset %s)", len(tasks), "s" * (len(tasks) != 1), args.repeat, label,
                 "on" if do_reset else "off")
        passed = 0
        total = 0
        for task in tasks:
            for rep in range(1, args.repeat + 1):
                record = run_task(agent, task, rep, label, do_reset, rng)
                if record["outcome"] == "error" and record["turns"] == 0:
                    # The model never answered (no API key, server down): every other task would fail the same way.
                    log.error("Stopping: the model did not answer: %s", record["summary"])
                    print_report(results, labels=[label])
                    return 1
                total += 1
                passed += record["passed"]
                results.parent.mkdir(parents=True, exist_ok=True)
                with results.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
                log.info("%s %s #%d: %s in %.0fs, %d turns (%s)", "PASS" if record["passed"] else "FAIL", task.name,
                         rep, record["outcome"], record["seconds"], record["turns"], record["detail"])
        log.info("Passed %d of %d. Results in %s", passed, total, results)
    print_report(results, labels=[label])
    return 0 if passed == total else 1


def run_task(agent: Agent, task: Task, rep: int, label: str, do_reset: bool, rng: random.Random) -> dict[str, Any]:
    bridge = agent.bridge
    if do_reset:
        reset(bridge, agent, task, rng)
        # A task starts with no memories of the last one's places (routines and blueprints stay).
        memory = agent.config.data_dir / "memory.json"
        if memory.exists():
            memory.unlink()
    start = block_pos(agent.observe())
    goal = task.goal_text(start)
    agent.config.max_turns = task.turns
    bridge.set_goal(goal)
    agent.goal = goal
    began = time.monotonic()
    result = agent.run_goal(goal)
    seconds = time.monotonic() - began
    end = agent.observe()
    blocks = bridge.blocks(block_positions(task, start)) if task.blocks else None
    ok, detail = check(task, start, end, blocks)
    if agent.goal == goal:
        agent._safe(bridge.finish_goal, f"Benchmark task {task.name}: {'passed' if ok else 'failed'}")
        agent.goal = None
    return {"label": label, "task": task.name, "tier": task.tier, "rep": rep, "passed": ok, "detail": detail,
            "outcome": result.outcome, "summary": result.summary, "turns": result.turns,
            "seconds": round(seconds, 1), "usage": result.usage, "goal": goal,
            "log": str(result.log_path) if result.log_path else None, "time": time.strftime("%Y-%m-%d %H:%M:%S")}


def print_report(results: Path, labels: Optional[list[str]] = None) -> None:
    """A table of pass rate and median time per task, one column per label."""
    try:
        rows = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    except FileNotFoundError:
        print(f"No results yet in {results}")
        return
    if labels:
        rows = [r for r in rows if r["label"] in labels]
    if not rows:
        print("No matching results.")
        return
    seen_labels = list(dict.fromkeys(r["label"] for r in rows))
    order = {t.name: i for i, t in enumerate(TASKS)}
    task_names = sorted({r["task"] for r in rows}, key=lambda n: order.get(n, 999))
    width = max(len(n) for n in task_names) + 2
    print("".ljust(width) + "".join(l[:28].ljust(30) for l in seen_labels))
    for name in task_names:
        line = name.ljust(width)
        for label in seen_labels:
            runs = [r for r in rows if r["task"] == name and r["label"] == label]
            if not runs:
                line += "-".ljust(30)
                continue
            ok = [r for r in runs if r["passed"]]
            median = statistics.median(r["seconds"] for r in ok) if ok else None
            cell = f"{len(ok)}/{len(runs)}" + (f", {median / 60:.1f} min" if median is not None else "")
            line += cell.ljust(30)
        print(line)
    for label in seen_labels:
        runs = [r for r in rows if r["label"] == label]
        tokens = sum(sum(r.get("usage", {}).values()) for r in runs)
        print(f"{label}: {sum(r['passed'] for r in runs)}/{len(runs)} passed, {tokens} tokens")


def _version() -> str:
    try:
        return metadata.version("minegpt-brain")
    except metadata.PackageNotFoundError:
        return "dev"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m minegpt_brain.bench", description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", help="comma-separated task names or tiers (default all); see --list")
    parser.add_argument("--repeat", type=int, default=1, help="runs per task (1)")
    parser.add_argument("--label", help="name for this set of runs in the results (default brain version and model)")
    parser.add_argument("--reset", dest="reset", action="store_true", default=None,
                        help="reset the player with commands before each task (default: in singleplayer)")
    parser.add_argument("--no-reset", dest="reset", action="store_false")
    parser.add_argument("--seed", type=int, default=None, help="seed for the starting spots")
    parser.add_argument("--xray", action="store_true", help="let mine target hidden blocks")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help=f"where results and the agent's files go (default {default_data_dir() / 'bench'})")
    parser.add_argument("--list", action="store_true", help="list the tasks and exit")
    parser.add_argument("--report", action="store_true", help="print the results table and exit")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("-v", "--verbose", action="store_true")
    add_backend_args(parser)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.list:
        for t in TASKS:
            print(f"{t.tier:9} {t.name:16} {t.turns:4} turns  {t.goal}")
        return 0
    if args.report:
        print_report((args.data_dir or default_data_dir() / "bench") / "results.jsonl")
        return 0
    try:
        return run(args)
    except (ValueError, BackendError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except BridgeError as e:
        print(f"error: {e}", file=sys.stderr)
        if "cheats" in str(e):
            print("The benchmark resets the player with commands, so run it in a singleplayer world with cheats on, "
                  "or use --no-reset.", file=sys.stderr)
        return 1
    except (ConnectionRefusedError, OSError) as e:
        print(f"error: could not reach the game: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
