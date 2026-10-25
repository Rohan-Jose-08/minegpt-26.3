# MineGPT observation and action format

The mod and the Python brain exchange JSON. This file describes both directions.
Phase 1 produces and accepts these objects inside the game (`/minegpt obs`, `/minegpt act`,
recordings); Phase 2 sends the same objects over a local socket.

Everything is read from and sent through the normal game client, so it works the same in
singleplayer and on multiplayer servers. Nothing here asks the server for extra information.

## Observation (`schema: 1`)

```jsonc
{
  "schema": 1,
  "gameTime": 123456,          // ticks since the world was created
  "dayTime": 6000,             // default world clock, 0..24000 is one day
  "dimension": "minecraft:overworld",
  "biome": "minecraft:plains",
  "raining": false,
  "thundering": false,
  "multiplayer": true,         // false when playing a local singleplayer world

  "player": {
    "pos": [10.5, 64.0, -3.2], "yaw": 90.0, "pitch": 12.5,
    "velocity": [0.0, -0.08, 0.0],
    "onGround": true, "inWater": false, "onFire": false,
    "health": 20.0, "maxHealth": 20.0, "armor": 0,
    "food": 20, "saturation": 5.0, "air": 300, "maxAir": 300,
    "xpLevel": 0, "xpProgress": 0.0,
    "creative": false, "usingItem": false,
    "effects": [{ "id": "minecraft:speed", "amplifier": 0, "duration": 600 }]
  },

  "inventory": {
    "selected": 0,             // hotbar slot 0..8
    // Only non-empty slots. Player inventory indices: 0-8 hotbar, 9-35 main,
    // 36-39 armor (feet to head), 40 offhand, 41 body armor, 42 saddle.
    "slots": [{ "slot": 0, "id": "minecraft:oak_log", "count": 12 },
              { "slot": 1, "id": "minecraft:wooden_pickaxe", "count": 1, "damage": 3, "maxDamage": 59 }]
  },

  // What the crosshair is on.
  "target": { "type": "block", "pos": [11, 64, -3], "face": "north", "block": "minecraft:oak_log" },
  //    or  { "type": "entity", "entityId": 42, "entity": "minecraft:zombie" }
  //    or  { "type": "none" }

  // Up to 32 entities within 24 blocks, nearest first.
  "entities": [{ "id": 42, "type": "minecraft:zombie", "pos": [14.1, 64.0, -1.0], "distance": 4.2, "health": 20.0 }],

  "screen": {
    "open": true, "class": "ContainerScreen",
    // Present when a container other than the player's own inventory is open.
    "menu": {
      "containerId": 3, "type": "minecraft:generic_9x3",
      "slots": [{ "slot": 0, "id": "minecraft:cobblestone", "count": 64, "playerInventory": false }],
      "carried": { "id": "minecraft:coal", "count": 3 }
    }
  },

  // Only in some observations (every 20 ticks in recordings, always in /minegpt obs).
  "grid": {
    "origin": [10, 64, -4],    // the player's feet block
    "radius": 8, "below": 4, "above": 4,
    "palette": ["minecraft:air", "minecraft:grass_block", "minecraft:dirt"],
    // (2*radius+1)^2 * (below+above+1) = 2601 palette indices, -1 for unloaded chunks.
    // Order: y from origin.y-below up, then z from origin.z-radius, then x (fastest).
    "cells": [2, 2, 1, 0, ...]
  }
}
```

To find a block at offset `(dx, dy, dz)` from `origin`:

```python
side = 2 * radius + 1
index = (dy + below) * side * side + (dz + radius) * side + (dx + radius)
block = palette[cells[index]] if cells[index] >= 0 else None
```

## Actions

Each action is one JSON object with a `type`. Movement, camera, attack and use are separate
channels: sending a new `move` replaces the previous `move`, but does not cancel a `look` or
an `attack` that is still running. All durations are in game ticks (20 per second, at most 1200).

| type | fields | what it does |
|---|---|---|
| `move` | `keys`: list of `forward`, `back`, `left`, `right`, `jump`, `sneak`, `sprint`; `ticks` (default 1) | Holds those keys and releases the rest |
| `look` | `yaw`, `pitch` in degrees (relative), `ticks` (default 1) | Turns the camera, spread evenly over the ticks |
| `look_at` | `x`, `y`, `z`, `ticks` (default 1) | Turns to face a point |
| `attack` | `ticks` (default 0) | 0 is one left click; more holds the button, which is how blocks are mined |
| `use` | `ticks` (default 0) | 0 is one right click; more holds it (eating, bows, shields) |
| `hotbar` | `slot` 0..8 | Selects a hotbar slot |
| `click` | `slot`, `button` (0 left, 1 right), `mode` (`PICKUP`, `QUICK_MOVE`, `SWAP`, `THROW`, `QUICK_CRAFT`, `PICKUP_ALL`, `CLONE`) | Clicks a slot in the open container, using that container's slot numbers |
| `drop` | `all` (default false) | Drops one item, or the whole stack, from the selected slot |
| `open_inventory` | | Opens the player inventory |
| `close_screen` | | Closes whatever screen is open |
| `stop` | | Releases every key and cancels running actions |

Examples:

```
/minegpt act {"type":"move","keys":["forward","sprint"],"ticks":40}
/minegpt act {"type":"look","yaw":90,"pitch":0,"ticks":10}
/minegpt act {"type":"attack","ticks":60}
```

## Recordings

`/minegpt record start` writes `<game dir>/minegpt/recordings/<timestamp>.jsonl.gz`: one JSON
object per line, gzip-compressed.

- Tick lines: `{"t": 0, "agent": false, "input": {...}, "obs": {...}}`, where `agent` says
  whether MineGPT was controlling the player and `input` holds the keys that were down
  (`forward`, `back`, `left`, `right`, `jump`, `sneak`, `sprint`, `attack`, `use`), the
  selected `hotbar` slot and the camera change since the previous tick (`dYaw`, `dPitch`).
- Click lines: `{"t": 5, "event": "click", "containerId": 0, "slot": 36, "button": 0, "mode": "PICKUP"}`,
  one per inventory click, since clicks do not show up in key states.
