"""Practice: ``python -m minegpt_brain.practice``.

Examples::

    python -m minegpt_brain.practice run --hours 8                # drills, replays and goals with the model
    python -m minegpt_brain.practice run --hours 8 --no-llm       # drills and replays only: no tokens
    python -m minegpt_brain.practice report                       # failure causes, tuning, nights so far
    python -m minegpt_brain.practice tuning [--reset [KNOB]]      # the skill settings and what practice found
    python -m minegpt_brain.practice failures [--days 7]          # causes ranked by minutes lost
    python -m minegpt_brain.practice keep <id> [--expect escape]  # a failure scene as a JUnit test
    python -m minegpt_brain.practice replay <id>                  # rebuild a failure scene and try again

Practice resets the player with commands, like the benchmark, so run it in a throwaway
singleplayer world with cheats on, never on a server.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Optional

from ..agent.loop import Agent, AgentConfig, default_data_dir
from ..bridge import DEFAULT_HOST, DEFAULT_PORT, Bridge, BridgeError
from .curriculum import Picker, failed_goals, load_results
from .drills import DRILLS, GATE, GATE_SEED, Drill, run_drill
from .failures import FailureLog, fixture, report, stall_minutes
from .replay import replay, replayable
from .tuner import KNOBS, Tuner

log = logging.getLogger("minegpt.practice")

#: How much worse the gate may get before the night's setting changes are undone: one more failure
#: in the twelve drills, or a fifth slower on the drills that passed.
GATE_SLACK_FAILS = 1
GATE_SLACK_TIME = 0.2


class NoModel:
    """Stands in for a model when practice runs drills and replays only."""

    name = "none"


def gate(agent: Agent, label: str) -> dict[str, Any]:
    """The fixed drill set, same spots each time, with the settings in force and no trying."""
    explore, agent.config.explore = agent.config.explore, False
    rng = random.Random(GATE_SEED)
    rows = []
    try:
        for name in GATE:
            row = run_drill(agent, DRILLS[name], rng)
            rows.append(row)
            log.info("gate %s %s: %s in %.0fs (%s)", label, name, "PASS" if row["passed"] else "FAIL", row["seconds"],
                     row["detail"])
    finally:
        agent.config.explore = explore
    passed = [r for r in rows if r["passed"]]
    return {"passed": len(passed), "runs": len(rows),
            "seconds": round(statistics.median(r["seconds"] for r in passed), 1) if passed else None,
            "by_drill": {name: [r["passed"] for r in rows if r["task"] == name] for name in dict.fromkeys(GATE)}}


def gate_worse(before: dict[str, Any], after: dict[str, Any]) -> Optional[str]:
    """Why the gate after the night is worse than before it, or None."""
    if after["passed"] < before["passed"] - GATE_SLACK_FAILS:
        return f"gate passed {after['passed']} of {after['runs']} after, {before['passed']} before"
    if (after["passed"] <= before["passed"] and before["seconds"] and after["seconds"]
            and after["seconds"] > before["seconds"] * (1 + GATE_SLACK_TIME)):
        return f"gate drills took {after['seconds']:.0f}s (median) after, {before['seconds']:.0f}s before"
    return None


def run(args: argparse.Namespace) -> int:
    root = args.data_dir or default_data_dir()
    folder = root / "practice"
    folder.mkdir(parents=True, exist_ok=True)
    results = folder / "results.jsonl"
    llm = not args.no_llm
    if llm:
        from ..agent.__main__ import backend_from_args

        backend = backend_from_args(args)
    else:
        backend = NoModel()
    config = AgentConfig(data_dir=folder / "agent", jobs=False, xray=args.xray, resume=False, explore=True,
                         shared_dir=root)
    rng = random.Random(args.seed)
    deadline = time.monotonic() + args.hours * 3600
    began_wall = time.time()
    began = time.strftime("%Y-%m-%d %H:%M:%S")
    bridge = Bridge.connect(args.host, args.port)
    with bridge:
        agent = Agent(bridge, backend, config)
        world = str(agent.observe().data.get("world", ""))
        if not world.startswith("singleplayer:") and not args.server_ok:
            log.error("Practice resets the player with commands: run it in a singleplayer world with cheats on "
                      "(this is %s). --server-ok overrides this.", world or "an unknown world")
            return 2
        from ..bench.__main__ import run_task

        log.info("Practice for %.1f h (%s), data in %s", args.hours, "drills, replays and goals" if llm else
                 "drills and replays only", folder)
        before = gate(agent, "before") if args.gate else None
        weak = [g.skill for g in report(agent.failures.records(days=7))]
        picker = Picker(rng, history=load_results(results, root / "bench" / "results.jsonl"),
                        retry_goals=failed_goals(root / "experience.json") if llm else (),
                        weak_skills=list(dict.fromkeys(weak)), drill_share=args.drill_share, llm=llm)
        rows: list[dict[str, Any]] = []
        tokens = 0

        def file(row: dict[str, Any]) -> None:
            rows.append(row)
            picker.record(row)
            with results.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")

        # Replays first: the worst causes from the last week, each tried again in its own scene.
        for group in report(agent.failures.records(days=7))[:args.replays]:
            record = next((r for r in reversed(agent.failures.records(days=7))
                           if r["cause"] == group.cause and replayable(r)), None)
            if record is None or time.monotonic() > deadline:
                continue
            try:
                row = replay(agent, record, rng)
            except (ValueError, BridgeError) as e:
                log.info("Could not replay %s: %s", group.cause, e)
                continue
            file(row)
            log.info("replay %s: %s (%s)", group.cause, "PASS" if row["passed"] else "FAIL", row["detail"])

        while time.monotonic() < deadline and len(rows) < args.max_runs and (not args.max_tokens or
                                                                             tokens < args.max_tokens):
            item = picker.next()
            if isinstance(item, Drill):
                row = run_drill(agent, item, rng)
            else:
                row = run_task(agent, item, 1, f"practice / {backend.name}", True, rng)
                row["kind"] = "task"
                if item.name == "custom":
                    # A goal of your own has no benchmark check: the agent's verified success stands in.
                    from ..train.collect import _verified

                    row["task"] = item.goal
                    row["passed"] = row["outcome"] == "success" and _verified(row)
                if row["outcome"] == "error" and row["turns"] == 0:
                    log.error("Stopping goals: the model did not answer: %s", row["summary"])
                    picker.llm, picker.drill_share = False, 1.0
                    continue
                tokens += sum(v for k, v in row.get("usage", {}).items() if k != "cache_read_input_tokens")
            file(row)
            log.info("%s %s: %s in %.0fs (%s). %d runs, %d tokens", row.get("kind"), row["task"],
                     "PASS" if row["passed"] else "FAIL", row["seconds"], row.get("detail", ""), len(rows), tokens)

        after = gate(agent, "after") if args.gate else None
        reverted: list[str] = []
        why = gate_worse(before, after) if before and after else None
        if why:
            reverted = agent.tuner.revert_since(began_wall)
            log.warning("The gate got worse (%s): undid %d setting change%s", why, len(reverted),
                        "" if len(reverted) == 1 else "s")
        agent.tuner.save()
        kept = [h for h in agent.tuner.history if h["time"] >= began_wall and not h.get("reverted")]
        hours = max(0.01, (time.time() - began_wall) / 3600)
        night = {"began": began, "ended": time.strftime("%Y-%m-%d %H:%M:%S"), "hours": round(hours, 2),
                 "runs": len(rows), "passed": sum(bool(r["passed"]) for r in rows),
                 "drills": _rate(rows, "drill"), "replays": _rate(rows, "replay"), "tasks": _rate(rows, "task"),
                 "tokens": tokens, "stall_minutes_per_hour": round(stall_minutes(agent.failures.records(days=2),
                                                                               began) / hours, 2),
                 "gate_before": before, "gate_after": after, "gate_worse": why,
                 "settings_changed": [f"{h['knob']}: {h['from']:g} -> {h['to']:g}" for h in kept],
                 "settings_undone": reverted, "backend": backend.name}
        with (folder / "progress.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(night) + "\n")
    print_night(night)
    return 0


def _rate(rows: list[dict[str, Any]], kind: str) -> str:
    mine = [r for r in rows if r.get("kind") == kind]
    return f"{sum(bool(r['passed']) for r in mine)}/{len(mine)}"


def print_night(night: dict[str, Any]) -> None:
    print(f"Practice {night['began']} to {night['ended']} ({night['hours']} h): {night['passed']}/{night['runs']} "
          f"passed. Drills {night['drills']}, replays {night['replays']}, goals {night['tasks']}, "
          f"{night['tokens']} tokens.")
    print(f"Stalls: {night['stall_minutes_per_hour']} minutes lost per hour of practice.")
    for label in ("gate_before", "gate_after"):
        g = night.get(label)
        if g:
            print(f"{label.replace('_', ' ')}: {g['passed']}/{g['runs']} drills, median {g['seconds']}s")
    if night.get("gate_worse"):
        print(f"Gate got worse ({night['gate_worse']}); undone: {', '.join(night['settings_undone']) or 'nothing'}")
    print("Settings changed: " + (", ".join(night["settings_changed"]) or "none"))


def print_failures(log_: FailureLog, days: Optional[int], limit: int) -> None:
    records = log_.records(days=days)
    groups = report(records)
    if not groups:
        print("No failures or stalls filed yet.")
        return
    total = sum(g.seconds for g in groups) / 60
    print(f"{len(records)} failures and stalls, {total:.0f} minutes lost, in {len(groups)} causes "
          f"({'last ' + str(days) + ' days' if days else 'all time'}). Worst first:")
    for g in groups[:limit]:
        print(f"  {g.minutes:6.1f} min  {g.count:3d}x  {g.cause}")
        print(f"                     latest {g.ids[-1]} ({g.last}): {g.example}")


def print_tuning(tuner: Tuner) -> None:
    print(f"{'setting':26} {'default':>8} {'in force':>9} {'runs':>5}  best so far")
    for row in tuner.table():
        mark = " *" if row["value"] != row["default"] else ""
        leader = "-" if row["leader"] is None else f"{row['leader']:g}"
        print(f"{row['knob']:26} {row['default']:>8g} {row['value']:>9g}{mark:2} {row['runs']:>4}  {leader}")
    changes = [h for h in tuner.history if not h.get("reverted")][-8:]
    if changes:
        print("Latest changes: " + "; ".join(f"{h['when']} {h['knob']} {h['from']:g} -> {h['to']:g}" for h in changes))


def print_progress(path: Path, last: int = 10) -> None:
    rows = load_results(path)
    if not rows:
        print("No practice nights yet.")
        return
    print("Nights (newest last):")
    for n in rows[-last:]:
        g0, g1 = n.get("gate_before") or {}, n.get("gate_after") or {}
        gates = f"gate {g0.get('passed', '-')}->{g1.get('passed', '-')}/{g1.get('runs', '-')}" if g0 else "no gate"
        print(f"  {n['began'][:16]}  {n['hours']:4} h  {n['passed']}/{n['runs']} passed  {gates}  "
              f"stalls {n['stall_minutes_per_hour']} min/h  {len(n['settings_changed'])} settings changed  "
              f"{n['tokens']} tokens")


def repo_root(given: Optional[Path]) -> Path:
    if given:
        return given
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "src" / "test" / "java").is_dir():
            return parent
    raise SystemExit("could not find the mod's folder; pass --repo")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m minegpt_brain.practice", description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None, help=f"data folder (default {default_data_dir()})")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="practise for a while")
    p.add_argument("--hours", type=float, default=8.0, help="how long to practise (8)")
    p.add_argument("--max-runs", type=int, default=100000)
    p.add_argument("--max-tokens", type=int, default=20_000_000, help="token budget for goals (20M, 0 for none)")
    p.add_argument("--no-llm", action="store_true", help="drills and replays only, no model and no tokens")
    p.add_argument("--drill-share", type=float, default=0.6, help="share of runs that are drills (0.6)")
    p.add_argument("--replays", type=int, default=3, help="worst failure causes replayed at the start (3)")
    p.add_argument("--no-gate", dest="gate", action="store_false",
                   help="skip the drill check before and after (and undoing changes that made it worse)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--xray", action="store_true")
    p.add_argument("--server-ok", action="store_true", help="allow running on a server (it resets with commands)")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    from ..agent.__main__ import add_backend_args

    add_backend_args(p)

    p = sub.add_parser("report", help="failure causes, tuning and nights so far")
    p.add_argument("--days", type=int, default=7)
    p = sub.add_parser("failures", help="failure and stall causes ranked by minutes lost")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--limit", type=int, default=15)
    p = sub.add_parser("tuning", help="the skill settings and what practice found")
    p.add_argument("--reset", nargs="?", const="*", help="put a setting (or every one) back to its default")
    p = sub.add_parser("keep", help="keep a failure scene as a JUnit test (src/test/resources/scenes)")
    p.add_argument("id")
    p.add_argument("--expect", default="escape", help="escape, 'reach X Y Z' (cells of the box), or none (kept for replays only)")
    p.add_argument("--no-breaking", action="store_true", help="the test may not dig its way out")
    p.add_argument("--repo", type=Path, default=None, help="the mod's folder (found from here by default)")
    p = sub.add_parser("replay", help="rebuild a failure's scene in game and run the skill again")
    p.add_argument("id")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    root = args.data_dir or default_data_dir()
    failures = FailureLog(root / "failures")
    try:
        if args.command == "run":
            return run(args)
        if args.command == "failures":
            print_failures(failures, args.days, args.limit)
        elif args.command == "report":
            print_failures(failures, args.days, 8)
            print()
            print_tuning(Tuner(root / "tuning.json"))
            print()
            print_progress(root / "practice" / "progress.jsonl")
        elif args.command == "tuning":
            tuner = Tuner(root / "tuning.json")
            if args.reset:
                if args.reset != "*" and args.reset not in KNOBS:
                    print(f"error: unknown setting {args.reset}; known: {', '.join(KNOBS)}", file=sys.stderr)
                    return 2
                tuner.reset(None if args.reset == "*" else args.reset)
                print(f"Reset {'every setting' if args.reset == '*' else args.reset} to the default.")
            print_tuning(tuner)
        elif args.command == "keep":
            record = failures.get(args.id)
            if record is None:
                print(f"error: no failure record {args.id}", file=sys.stderr)
                return 2
            folder = repo_root(args.repo) / "src" / "test" / "resources" / "scenes"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{record['id']}.scene"
            path.write_text(fixture(record, args.expect, breaking=not args.no_breaking), encoding="utf-8")
            print(f"Wrote {path}. The mod's tests (gradlew test) now check the pathfinder can {args.expect} there.")
        elif args.command == "replay":
            record = failures.get(args.id)
            if record is None:
                print(f"error: no failure record {args.id}", file=sys.stderr)
                return 2
            with Bridge.connect(args.host, args.port) as bridge:
                agent = Agent(bridge, NoModel(), AgentConfig(data_dir=root / "practice" / "agent", jobs=False,
                                                             resume=False, shared_dir=root))
                row = replay(agent, record, random.Random())
            print(f"{'PASS' if row['passed'] else 'FAIL'} {row['task']} in {row['seconds']}s: {row['detail']}")
            return 0 if row["passed"] else 1
        return 0
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except BridgeError as e:
        print(f"error: {e}", file=sys.stderr)
        if "cheats" in str(e):
            print("Practice resets the player with commands: use a singleplayer world with cheats on.", file=sys.stderr)
        return 1
    except (ConnectionRefusedError, OSError) as e:
        print(f"error: could not reach the game: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
