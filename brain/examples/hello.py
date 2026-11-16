"""Connects to the game and prints what MineGPT can see.

In game: /minegpt bridge start
Then:    python examples/hello.py
"""

from minegpt_brain.bridge import Bridge

with Bridge.connect() as mc:
    print(f"Connected to MineGPT {mc.info.get('version')} (mode {mc.info.get('mode')}, goal {mc.info.get('goal')!r})")
    obs = mc.observe()
    x, y, z = obs.pos
    print(f"Player at {x:.1f} {y:.1f} {z:.1f}, health {obs.health}, food {obs.food}")
    print(f"Looking at: {obs.target}")
    print(f"Carrying {len(obs.inventory)} stacks; {len(obs.entities)} entities nearby")
    logs = obs.find_blocks("oak_log")
    if logs:
        print(f"Nearest oak log in view range: {logs[0]}")
