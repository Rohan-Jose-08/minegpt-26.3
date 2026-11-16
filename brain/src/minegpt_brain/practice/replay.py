"""Rebuilding a failure's scene in game and running the same skill there again.

The scene comes from a failure record (:mod:`.failures`). The player is moved to fresh land, the
9 x 6 x 9 box of blocks is put back round it with ``/fill`` and ``/setblock``, the player is put
where it stood, facing the same way, and the skill that failed runs again with its arguments moved
by the same offset. It passes if the skill works and does not stall. No model is involved.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Any, Mapping

from ..agent import state
from ..bench import Task
from ..bench.__main__ import reset
from .failures import Box, scene_commands

if TYPE_CHECKING:  # pragma: no cover
    from ..agent.loop import Agent

#: Skills a scene can be replayed for: ones that move about and take short arguments.
REPLAYABLE = ("goto", "mine", "collect", "place")
#: What the player carries in a replay, since the scene does not say what it had.
KIT = {"dirt": 32, "cobblestone": 32, "stone_pickaxe": 1, "stone_shovel": 1, "stone_axe": 1, "torch": 8}


def replayable(record: Mapping[str, Any]) -> bool:
    scene = record.get("scene") or {}
    args = record.get("args") or {}
    return (record.get("skill") in REPLAYABLE and bool(scene.get("cells")) and bool(args)
            and not any(isinstance(v, str) and v.startswith("<") for v in args.values())
            and "overworld" in str(scene.get("dimension", "overworld")))


def moved_args(args: Mapping[str, Any], offset: tuple[int, int, int]) -> dict[str, Any]:
    out = dict(args)
    for i, axis in enumerate("xyz"):
        if isinstance(out.get(axis), (int, float)):
            out[axis] = int(out[axis]) + offset[i]
    return out


def replay(agent: "Agent", record: Mapping[str, Any], rng: random.Random, do_reset: bool = True) -> dict[str, Any]:
    if not replayable(record):
        raise ValueError(f"{record.get('id')}: this record cannot be replayed (skill {record.get('skill')}, "
                         "or no scene or arguments)")
    scene = record["scene"]
    bridge = agent.bridge
    if do_reset:
        reset(bridge, agent, Task("replay", "replay", "replay", 1, kit=KIT, time=6000), rng)
    feet = state.block_pos(agent.observe())
    box = Box(scene)
    origin = (feet[0] - box.fx, feet[1] - box.fy, feet[2] - box.fz)
    old = tuple(scene["origin"])
    offset = (origin[0] - old[0], origin[1] - old[1], origin[2] - old[2])
    for command in scene_commands(scene, origin):
        bridge.run_command(command)
    x, y, z = (scene["exact"][i] + offset[i] for i in range(3))
    bridge.run_command(f"tp @s {x} {y} {z} {scene.get('yaw', 0)} {scene.get('pitch', 0)}")
    time.sleep(1.5)
    stalls = agent.failures.stalls
    args = moved_args(record["args"], offset)
    began = time.monotonic()
    result = agent._skill(record["skill"], args)
    seconds = time.monotonic() - began
    agent.poll_events()
    stalled = agent.failures.stalls > stalls
    passed = result.success and not stalled
    detail = "ok" if passed else ("stalled again" if stalled and result.success else result.message[:160])
    return {"kind": "replay", "task": record["cause"], "id": record["id"], "skill": record["skill"], "passed": passed,
            "detail": detail, "seconds": round(seconds, 1), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
