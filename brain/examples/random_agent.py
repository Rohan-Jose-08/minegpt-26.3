"""Runs random actions through the Gymnasium environment for a few seconds.

Needs: pip install -e ".[env]"
"""

from minegpt_brain.env import MineGPTEnv

env = MineGPTEnv(frame_skip=4, max_steps=100)
obs, info = env.reset()
total = 0.0
for _ in range(100):
    obs, reward, terminated, truncated, info = env.step(env.action_space.sample())
    total += reward
    if terminated or truncated:
        break
print(f"Done. Blocks seen: {len(env.blocks.ids)}, items seen: {len(env.items.ids)}")
env.close()
