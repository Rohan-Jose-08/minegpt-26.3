"""``python -m minegpt_brain.knowledge``: build or inspect the game knowledge file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m minegpt_brain.knowledge",
                                     description="Build or inspect MineGPT's recipe and drop data.")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="Read a Minecraft jar (or extracted data folder) and write the knowledge file.")
    b.add_argument("source", type=Path)
    b.add_argument("--version", default="26.3")
    b.add_argument("--out", type=Path, help="Default: the brain's own data folder, used automatically.")
    p = sub.add_parser("plan", help="Show the plan for some items from an empty inventory.")
    p.add_argument("items", nargs="+", help="item=count, e.g. iron_pickaxe=1 torch=16")
    args = parser.parse_args(argv)

    from . import build, graph, planner

    if args.command == "build":
        data = build.build(args.source, args.version)
        out = args.out or graph.DATA_DIR / f"minecraft-{args.version}.json.gz"
        build.write(data, out)
        print(f"Wrote {out}: {len(data['recipes'])} recipes, {len(data['blocks'])} block drops, "
              f"{len(data['mobs'])} mob drops, {len(data['item_tags'])} item tags.")
        return 0
    wanted = {}
    for spec in args.items:
        item, _, n = spec.partition("=")
        wanted[item] = int(n or 1)
    result = planner.plan_items(wanted, {}, graph.load())
    print(result.describe({}))
    print()
    for step in result.calls():
        print(step)
    return 0


if __name__ == "__main__":
    sys.exit(main())
