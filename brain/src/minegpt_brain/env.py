"""A Gymnasium environment around the bridge, for imitation and reinforcement learning.

Needs the ``env`` extra: ``pip install -e ".[env]"``.

The game keeps running between steps (it cannot be paused from a client, and servers never
wait), so each :meth:`MineGPTEnv.step` holds its action for ``frame_skip`` ticks and returns
what the game looks like afterwards. Lock-step training against a server you control comes
later, with the headless training server.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Optional

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from .bridge import Bridge
from .observation import Observation

#: Camera turn for each yaw choice, in degrees per step. Pitch uses half of these.
CAMERA_BINS = (-90.0, -45.0, -20.0, -10.0, -5.0, 0.0, 5.0, 10.0, 20.0, 45.0, 90.0)
INVENTORY_SLOTS = 41  # hotbar, main, armor, offhand
MAX_BLOCK_IDS = 2048
MAX_ITEM_IDS = 4096
PLAYER_FEATURES = 16

RewardFn = Callable[[Optional[Observation], Observation], float]


class Vocabulary:
    """Gives each block or item id a stable small integer, in the order they are first seen.

    0 means unknown or not loaded, 1 is air. Save :attr:`ids` with a trained model so the
    numbers mean the same thing later.
    """

    def __init__(self, size: int, ids: Optional[list[str]] = None):
        self.size = size
        self.ids: list[str] = list(ids) if ids else ["<unknown>", "minecraft:air"]
        self._index = {name: i for i, name in enumerate(self.ids)}

    def __call__(self, name: Optional[str]) -> int:
        if name is None:
            return 0
        index = self._index.get(name)
        if index is None:
            if len(self.ids) >= self.size:
                return 0
            index = len(self.ids)
            self.ids.append(name)
            self._index[name] = index
        return index


class MineGPTEnv(gym.Env):
    """Controls the player through the mod's bridge.

    Actions are a ``MultiDiscrete`` vector:

    ===== ======================================== =======
    index meaning                                  choices
    ===== ======================================== =======
    0     forward / back                           none, forward, back
    1     strafe                                   none, left, right
    2     jump                                     off, on
    3     sneak                                    off, on
    4     sprint                                   off, on
    5     attack (hold left mouse)                 off, on
    6     use (hold right mouse)                   off, on
    7     turn left/right                          :data:`CAMERA_BINS`
    8     look up/down                             :data:`CAMERA_BINS` halved
    9     hotbar                                   keep, slot 0..8
    ===== ======================================== =======
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        bridge: Optional[Bridge] = None,
        frame_skip: int = 4,
        max_steps: int = 2000,
        reward_fn: Optional[RewardFn] = None,
        blocks: Optional[Vocabulary] = None,
        items: Optional[Vocabulary] = None,
    ):
        self.bridge = bridge or Bridge.connect()
        self.frame_skip = frame_skip
        self.max_steps = max_steps
        self.reward_fn = reward_fn or (lambda prev, obs: 0.0)
        self.blocks = blocks or Vocabulary(MAX_BLOCK_IDS)
        self.items = items or Vocabulary(MAX_ITEM_IDS)

        self.action_space = spaces.MultiDiscrete([3, 3, 2, 2, 2, 2, 2, len(CAMERA_BINS), len(CAMERA_BINS), 10])
        # Grid size comes from the mod; read it from a first observation.
        first = self.bridge.observe(grid=True)
        grid = first.data["grid"]
        side = 2 * grid["radius"] + 1
        self._grid_shape = (grid["below"] + grid["above"] + 1, side, side)
        self.observation_space = spaces.Dict(
            {
                "player": spaces.Box(-np.inf, np.inf, shape=(PLAYER_FEATURES,), dtype=np.float32),
                "grid": spaces.Box(0, MAX_BLOCK_IDS - 1, shape=self._grid_shape, dtype=np.int32),
                "inventory_items": spaces.Box(0, MAX_ITEM_IDS - 1, shape=(INVENTORY_SLOTS,), dtype=np.int32),
                "inventory_counts": spaces.Box(0.0, 1.0, shape=(INVENTORY_SLOTS,), dtype=np.float32),
            }
        )
        self._last: Optional[Observation] = None
        self._steps = 0

    def reset(self, *, seed: Optional[int] = None, options: Optional[dict[str, Any]] = None):
        super().reset(seed=seed)
        # A client cannot reset the world, so an episode starts wherever the player is now.
        self.bridge.release()
        self._steps = 0
        obs = self.bridge.observe(grid=True)
        self._last = obs
        return self.encode(obs), {"raw": obs}

    def step(self, action):
        actions = self.decode(action)
        obs = self.bridge.step(actions, ticks=self.frame_skip, grid=True)
        reward = float(self.reward_fn(self._last, obs))
        self._last = obs
        self._steps += 1
        terminated = obs.health <= 0
        truncated = self._steps >= self.max_steps
        return self.encode(obs), reward, terminated, truncated, {"raw": obs, "applied": obs.applied}

    def close(self):
        try:
            self.bridge.release()
        finally:
            self.bridge.close()

    # Conversions ----------------------------------------------------------------------

    def decode(self, action) -> list[dict[str, Any]]:
        """Turns an action vector into bridge actions that last ``frame_skip`` ticks."""
        a = [int(x) for x in action]
        ticks = self.frame_skip
        keys = []
        if a[0] == 1:
            keys.append("forward")
        elif a[0] == 2:
            keys.append("back")
        if a[1] == 1:
            keys.append("left")
        elif a[1] == 2:
            keys.append("right")
        for index, key in ((2, "jump"), (3, "sneak"), (4, "sprint")):
            if a[index]:
                keys.append(key)

        out: list[dict[str, Any]] = [{"type": "move", "keys": keys, "ticks": ticks}]
        if a[5]:
            out.append({"type": "attack", "ticks": ticks})
        if a[6]:
            out.append({"type": "use", "ticks": ticks})
        yaw, pitch = CAMERA_BINS[a[7]], CAMERA_BINS[a[8]] / 2
        if yaw or pitch:
            out.append({"type": "look", "yaw": yaw, "pitch": pitch, "ticks": ticks})
        if a[9]:
            out.append({"type": "hotbar", "slot": a[9] - 1})
        return out

    def encode(self, obs: Observation) -> dict[str, np.ndarray]:
        p = obs.player
        yaw = math.radians(p["yaw"])
        player = np.array(
            [
                p["pos"][1] / 256.0,
                *p["velocity"],
                math.sin(yaw),
                math.cos(yaw),
                p["pitch"] / 90.0,
                p["health"] / 20.0,
                p["food"] / 20.0,
                p["saturation"] / 20.0,
                p["air"] / max(1, p["maxAir"]),
                float(p["onGround"]),
                float(p["inWater"]),
                float(p["onFire"]),
                p["armor"] / 20.0,
                obs.data["inventory"]["selected"] / 8.0,
            ],
            dtype=np.float32,
        )

        grid = obs.data["grid"]
        lookup = np.array([self.blocks(name) for name in grid["palette"]] + [0], dtype=np.int32)
        cells = np.asarray(grid["cells"], dtype=np.int64)
        # -1 (unloaded) indexes the trailing 0 in lookup.
        blocks = lookup[cells].reshape(self._grid_shape)

        items = np.zeros(INVENTORY_SLOTS, dtype=np.int32)
        counts = np.zeros(INVENTORY_SLOTS, dtype=np.float32)
        for stack in obs.inventory:
            if stack["slot"] < INVENTORY_SLOTS:
                items[stack["slot"]] = self.items(stack["id"])
                counts[stack["slot"]] = min(1.0, stack["count"] / 64.0)

        return {"player": player, "grid": blocks, "inventory_items": items, "inventory_counts": counts}
