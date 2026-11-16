"""Walks the player to an x z coordinate in a straight line.

    python examples/walk_to.py 100 -40

This uses raw actions only, so it stops if it gets stuck; the goto skill (Skills.goto)
does the same with pathfinding. Press F8 in game to pause.
"""

import sys

from minegpt_brain.bridge import Bridge

target_x, target_z = float(sys.argv[1]), float(sys.argv[2])

with Bridge.connect() as mc:
    mc.set_status(f"Walking to {target_x:.0f} {target_z:.0f}")
    last_distance = None
    stuck_steps = 0
    while True:
        obs = mc.observe(grid=False)
        x, y, z = obs.pos
        distance = ((target_x - x) ** 2 + (target_z - z) ** 2) ** 0.5
        if distance < 1.0:
            print("Arrived")
            break
        if obs.mode == "paused":
            print("Paused in game, waiting")
            mc.step(ticks=20, grid=False)
            continue

        if last_distance is not None and last_distance - distance < 0.05:
            stuck_steps += 1
            if stuck_steps > 10:
                print("Stuck, giving up")
                break
        else:
            stuck_steps = 0
        last_distance = distance

        # Face the target at eye height, then sprint and jump over single blocks.
        actions = [
            {"type": "look_at", "x": target_x, "y": y + 1.62, "z": target_z, "ticks": 2},
            {"type": "move", "keys": ["forward", "sprint"] + (["jump"] if stuck_steps > 2 else []), "ticks": 5},
        ]
        mc.step(actions, ticks=5, grid=False)

    mc.release()
    mc.set_status("Idle")
