"""Training tools: ``python -m minegpt_brain.train stats|export|plans|collect``.

Examples::

    python -m minegpt_brain.train stats
    python -m minegpt_brain.train collect --target 200 --max-tokens 20000000
    python -m minegpt_brain.train export --out minegpt-data
    python -m minegpt_brain.train plans --out minegpt-data

``plans`` writes short "goal and start to plan" examples from the record of finished goals
(``experience.json``). ``stats`` and ``export`` read every episode log under the data folder (the agent's own,
benchmark runs and collection runs). ``collect`` needs the game running with the mod, in a
singleplayer world with cheats on.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

from ..agent.loop import default_data_dir
from ..llm.history import DEFAULT_BUDGET
from . import dataset, episodes


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", type=Path, default=None, help=f"data folder (default {default_data_dir()})")
    parser.add_argument("--episodes", type=Path, nargs="*", default=[], help="extra episode log files to read")


def _filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--unverified", action="store_true",
                        help="also keep successes the game did not confirm")
    parser.add_argument("--no-deaths", action="store_true", help="leave out episodes in which the player died")
    parser.add_argument("--backend", action="append", default=[],
                        help="only episodes from this backend (e.g. claude, or claude:claude-opus-5-5); repeatable")
    parser.add_argument("--max-turns", type=int, default=0, help="leave out episodes longer than this")


def _make_filters(args: argparse.Namespace) -> dataset.Filters:
    return dataset.Filters(unverified=args.unverified, deaths=not args.no_deaths, backends=tuple(args.backend),
                           max_turns=args.max_turns)


def stats(args: argparse.Namespace) -> int:
    found = episodes.load_all(args.data_dir or default_data_dir(), args.episodes)
    if not found:
        print(f"No episode logs under {args.data_dir or default_data_dir()}")
        return 1
    outcomes = Counter(e.outcome for e in found)
    print(f"{len(found)} episodes: " + ", ".join(f"{n} {k}" for k, n in outcomes.most_common()))
    selection = dataset.select(found, _make_filters(args))
    print(f"{len(selection.kept)} usable for training, {sum(e.turns for e in selection.kept)} model turns")
    if selection.skipped:
        print("Left out: " + ", ".join(f"{n} {k}" for k, n in selection.skipped.most_common()))
    by_goal = Counter((e.task or e.goal) for e in selection.kept)
    if by_goal:
        print("Usable episodes per task or goal:")
        for goal, n in by_goal.most_common():
            print(f"  {n:4}  {goal[:70]}")
    print("A first fine-tune wants a few hundred usable episodes; collect more with "
          "python -m minegpt_brain.train collect")
    return 0


def export(args: argparse.Namespace) -> int:
    found = episodes.load_all(args.data_dir or default_data_dir(), args.episodes)
    plan_rows = []
    if not args.no_plans:
        from . import plans as plan_data

        plan_rows, _ = plan_data.examples(args.data_dir or default_data_dir(), unverified=args.unverified)
    info = dataset.export(found, args.out, _make_filters(args), val_fraction=args.val, budget=args.context_chars,
                          thinking=args.thinking, seed=args.seed, plan_rows=plan_rows)
    print(f"Kept {info.kept} of {info.episodes} episodes: {info.train_episodes} for training, "
          f"{info.val_episodes} for validation")
    if info.skipped:
        print("Left out: " + ", ".join(f"{n} {k}" for k, n in info.skipped.items()))
    print(f"{info.train_samples} training and {info.val_samples} validation samples, {info.replies} replies to learn, "
          f"about {info.approx_tokens} tokens (longest about {info.longest_tokens})")
    if info.plan_samples:
        print(f"{info.plan_samples} of the training samples are goal-to-plan examples from the goals on record")
    if info.inexact:
        print(f"{info.inexact} episodes come from logs older than brain 0.3; parts of them were filled in")
    if "zip" in info.files:
        print(f"Upload {info.files['zip']} to Colab (see docs/training.md)")
    return 0 if info.train_samples else 1


def collect(args: argparse.Namespace) -> int:
    from ..agent.__main__ import backend_from_args
    from ..bridge import Bridge, BridgeError
    from ..llm import BackendError
    from .collect import collect as run, custom_task, default_tasks, make_agent

    try:
        tasks = default_tasks(args.tasks) if not args.only_goals else []
        if args.goals:
            lines = [l.strip() for l in args.goals.read_text(encoding="utf-8").splitlines()]
            tasks += [custom_task(l, args.goal_turns) for l in lines if l and not l.startswith("#")]
        if not tasks:
            print("error: no tasks to run", file=sys.stderr)
            return 2
        backend = backend_from_args(args)
    except (ValueError, OSError, BackendError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    data_dir = args.data_dir or default_data_dir() / "collect"
    label = args.label or f"collect / {backend.name}"
    try:
        bridge = Bridge.connect(args.host, args.port)
    except OSError as e:
        print(f"error: could not reach the game: {e}", file=sys.stderr)
        return 1
    with bridge:
        agent = make_agent(bridge, backend, data_dir, args.xray)
        world = str(agent.observe().data.get("world", ""))
        if not world.startswith("singleplayer:") and not args.allow_server:
            print("error: collection resets the player with commands, so it runs in singleplayer only "
                  "(pass --allow-server for a server you may do that on)", file=sys.stderr)
            return 2
        try:
            summary = run(bridge, agent, tasks, data_dir=data_dir, label=label, target=args.target,
                          max_runs=args.max_runs, max_tokens=args.max_tokens, rng=random.Random(args.seed),
                          phrasings=not args.no_phrasings)
        except BridgeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            return 130
    return 0 if summary["good"] else 1


def plans(args: argparse.Namespace) -> int:
    from . import plans as plan_data

    counts = plan_data.export(args.data_dir or default_data_dir(), args.out, unverified=args.unverified)
    print(f"{counts['kept']} plan examples written to {args.out / 'plans.jsonl'} from {counts['records']} goals on record")
    left_out = ", ".join(f"{n} {why}" for why, n in counts.items() if why not in ("kept", "records") and n)
    if left_out:
        print("Left out: " + left_out)
    return 0 if counts["kept"] else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m minegpt_brain.train", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("stats", help="count the episodes and how many are usable for training")
    _common(p)
    _filters(p)
    p.set_defaults(func=stats)

    p = sub.add_parser("export", help="write the training dataset and the Colab bundle")
    _common(p)
    _filters(p)
    p.add_argument("--out", type=Path, default=Path("minegpt-data"), help="output folder (minegpt-data)")
    p.add_argument("--val", type=float, default=0.1, help="share of episodes kept for validation (0.1)")
    p.add_argument("--context-chars", type=int, default=DEFAULT_BUDGET,
                   help="history budget, the same as the local backend's --context-chars (40000)")
    p.add_argument("--thinking", action="store_true",
                   help="include the teacher's summarized thinking as reasoning_content (for thinking models)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--no-plans", action="store_true",
                   help="leave out the goal-to-plan examples made from the goals on record")
    p.set_defaults(func=export)

    p = sub.add_parser("plans", help="write goal-to-plan training examples from the record of finished goals")
    p.add_argument("--data-dir", type=Path, default=None, help=f"data folder (default {default_data_dir()})")
    p.add_argument("--out", type=Path, default=Path("minegpt-data"), help="output folder (minegpt-data)")
    p.add_argument("--unverified", action="store_true", help="also keep successes the game did not confirm")
    p.set_defaults(func=plans)

    p = sub.add_parser("collect", help="let the teacher model play a curriculum of goals to make episodes")
    from ..agent.__main__ import add_backend_args
    from ..bridge import DEFAULT_HOST, DEFAULT_PORT
    p.add_argument("--data-dir", type=Path, default=None,
                   help=f"where results and episodes go (default {default_data_dir() / 'collect'})")
    p.add_argument("--tasks", help="benchmark task names or tiers (default all)")
    p.add_argument("--goals", type=Path, help="a text file of your own goals, one per line")
    p.add_argument("--only-goals", action="store_true", help="run only the --goals file, no benchmark tasks")
    p.add_argument("--goal-turns", type=int, default=60, help="turn limit for --goals goals (60)")
    p.add_argument("--target", type=int, default=100, help="stop after this many good episodes (100)")
    p.add_argument("--max-runs", type=int, default=1000, help="stop after this many runs (1000)")
    p.add_argument("--max-tokens", type=int, default=0,
                   help="stop after about this many tokens, not counting cache reads (default no limit)")
    p.add_argument("--no-phrasings", action="store_true", help="always use the benchmark's own goal wording")
    p.add_argument("--label", help="label in the results (default collect / <model>)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--xray", action="store_true")
    p.add_argument("--allow-server", action="store_true", help="run on a multiplayer server you may reset players on")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    add_backend_args(p)
    p.set_defaults(func=collect)

    p.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
