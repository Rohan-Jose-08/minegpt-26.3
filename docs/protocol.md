# MineGPT observation and action format

The mod and the Python brain exchange JSON. This file describes both directions.
The same objects appear in the game (`/minegpt obs`, `/minegpt act`, recordings) and over the
bridge, the local socket the Python brain uses (see [Bridge](#bridge) below).

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
  "world": "server:mc.example.org", // or "singleplayer:<save folder>"; tells worlds apart

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

  // Up to 32 entities within 24 blocks, nearest first. Dropped items also give "item" and "count".
  "entities": [{ "id": 42, "type": "minecraft:zombie", "pos": [14.1, 64.0, -1.0], "distance": 4.2, "health": 20.0, "hostile": true },
               { "id": 57, "type": "minecraft:item", "pos": [12.3, 63.0, -2.6], "distance": 2.1, "hostile": false,
                 "item": "minecraft:raw_iron", "count": 2 }],

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

## Bridge

The bridge is a TCP server inside the mod, on `127.0.0.1` only, port 5577 by default.
Start it with `/minegpt bridge start`, or set `bridge.autostart=true` in
`config/minegpt.properties` (the port is set there too). One client can connect at a time.

Each message is one JSON object on one line, UTF-8, ending in `\n`. Requests carry an `id`,
which the reply repeats. Messages without an `id` are events the mod sends on its own.

The game keeps running between messages: a client cannot pause it, and multiplayer servers
never wait. Actions are applied at the start of the next client tick.

### Requests

| op | fields | reply |
|---|---|---|
| `hello` | `protocol` | `hello` with `protocol` (1), `schema`, mod `version`, `inWorld`, `skills`, `safety`, `mode`, `goal`, `status`, `skill`, `pauseReason`, `tick` |
| `observe` | `grid` (default true) | `obs` right away |
| `act` | `actions`: list of actions | `ack` with `applied` (false if the agent is paused) |
| `step` | `actions`, `ticks` (default 1), `grid` (default true) | `obs` after `ticks` client ticks, with `applied` |
| `subscribe` | `every` ticks (0 stops), `gridEvery` ticks | `ack`; then `obs` messages with `"stream": true` and no `id` |
| `status` | | `status` with `mode`, `goal`, `status`, `skill` (running skill or null), `pauseReason` (see [Safety](#safety)), `tick` |
| `set_status` | `text` | `ack`; the text appears in the HUD |
| `finish_goal` | `message` | `ack`; clears the goal, releases the keys, shows the message |
| `skill` | `name`, `args`, `tuning` (optional) | `skill_result` when the skill ends (see [Skills](#skills)). `tuning` is an object of skill settings by name (see [Skill settings](#skill-settings)); it replaces the values in force until the next call sends others |
| `tuning` | | `tuning`: `knobs`, a list of `{name, default, min, max, whole, value, about}` for every skill setting |
| `cancel_skill` | | `ack`; the running skill ends with a failed `skill_result` |
| `recipes` | `item` (id or `#tag`, optional), `craftable` (default false) | `recipes`: list of `{result, count, needsTable, craftable, ingredients}` |
| `find` | `block` (id, `#tag` or a list), `radius` (32, at most 64), `limit` (10, at most 64) | `found`: `blocks`, a list of `{pos, block, distance}`, nearest first; only blocks a player could see |
| `set_goal` | `goal` (string, or null to clear it) | `ack`; the same as `/minegpt goal`, so a `goal` event follows |
| `screenshot` | `width` (640, from 160 to 1280: the frame is shrunk by a whole factor to fit), `hideHud` (true) | `screenshot`, about four ticks later: `width`, `height`, `format` (`jpeg`, or `png` if the Java runtime has no JPEG writer) and `data`, the image file in base64. The HUD is hidden while the frame is taken and put back after |
| `survey` | `width`, `depth` (footprint in x and z, up to 48), `radius` (32, at most 40), `limit` (3, at most 8) | `survey`: `sites`, flat open ground for that footprint, best first: `{x, y, z, work, trees, water, distance}`. `x y z` is the west, north corner at walking level; `work` is the blocks to dig or fill to level it. Sites over something built, lava or a protected region are left out |
| `blocks` | `positions`: list of `[x, y, z]` (at most 4096), `states` (false) | `blocks`: the block id at each, or null where the chunk is not loaded. With `states`, the id is followed by how the block is turned, as in `minecraft:oak_stairs[facing=east,half=bottom]` |
| `audit` | `blocks`: list of `[x, y, z, item]` or `[x, y, z, item, facing]` as for the build skill (at most 8192, `air` for a spot that must be empty), `box` `[x1, y1, z1, x2, y2, z2]` (optional, at most 80000 cells), `ground` (optional, the footing item), `limit` (40, at most 400) | `audit`: counts `checked`, `right`, `unloaded`, `ground`, `missing`, `wrong`, `turned`, `blocked` and `extra`, and `problems`: up to `limit` of `{kind, x, y, z, want, is, own, purpose}`. `own` says whether the agent placed what stands there (from its saved record in `config/minegpt/ledger/`); `extra` are stray blocks elsewhere in the box |
| `safety` | | `safety`: `{regions, maxActionsPerMinute, stopHealth}`; each region is `{name, dimension, minX, minZ, maxX, maxZ, farm}` |
| `notify` | `text` | `ack`; shows `[MineGPT] text` in the player's chat, on their screen only |
| `run_command` | `command` (with or without the `/`) | `ack` once sent; runs a server command as the player. Only in singleplayer, or with `bridge.allowCommands=true`; an `error` if the player may not use that command (cheats off) |

An `obs` reply looks like `{"op": "obs", "id": 7, "tick": 1234, "mode": "running", "obs": {...}}`,
where `obs` is the observation above. Any request can instead get
`{"op": "error", "id": 7, "message": "..."}`; one bad action rejects the whole request.

### Events

| event | fields | when |
|---|---|---|
| `goal` | `goal` (string or null) | The user ran `/minegpt goal`, or the goal was cleared |
| `mode` | `mode`: `idle`, `running` or `paused`; `reason` when the agent paused itself (death, low health), else null | The agent started, the user paused or resumed (F8), or it stopped |
| `command` | `command` (`job`), `text` | The user typed `/minegpt job <text>`; the brain answers with `notify` |
| `stall` | `skill`, `status` (the skill's HUD line), `seconds`, `scene` (see [Scenes](#scenes)) | A skill that moves about (not craft, smelt, eat, sleep, equip, store, take, fight or farm) has neither moved a block nor changed the inventory for 15 seconds |
| `stall_end` | `skill`, `seconds` (how long it lasted in all) | The player moved or the inventory changed again, or the skill ended |

`goal` and `mode` are sent once right after connecting, so the client starts with the current state.

While a client is connected the agent stays in the running state between its steps, but only
holds the keys its current actions use, so the user can still move. If the client disconnects,
every key is released.

## Skills

Skills are behaviours that take many ticks, such as walking somewhere or crafting an item.
They run inside the mod and press the same keys a player would, so recordings of them look
like ordinary play, and they work on servers. One skill runs at a time.

Start one with `{"op": "skill", "id": 9, "name": "mine", "args": {"block": "#minecraft:logs", "count": 4}}`.
When it ends the reply is:

```json
{"op": "skill_result", "id": 9, "skill": "mine", "success": true, "message": "mined 4 #minecraft:logs",
 "data": {"broken": 4, "blocks": {"minecraft:oak_log": 4}, "explored": 0}}
```

A skill also ends, with `success: false` and a message starting `cancelled:`, when another
skill or a raw action (`act`, or `step` with actions) replaces it, when `cancel_skill` is
sent, when the user runs `/minegpt stop`, when the player dies, or when the brain
disconnects. Pausing (F8) freezes the skill until resumed. Bad arguments get an `error`
reply straight away. Every skill takes `timeout` in seconds. In game, `/minegpt skill <name> <json>`
runs one by hand, for example `/minegpt skill goto {"x": 100, "y": 64, "z": -40}`.

Item and block arguments take an id (`minecraft:oak_log`, or just `oak_log`) or a tag (`#minecraft:logs`).

A failed `skill_result` also carries `data.scene` (see [Scenes](#scenes)), unless the skill was
stopped by the brain, the player or a pause rather than by the world.

### Skill settings

About twenty limits and counts in the skills can be tuned by the brain (`Tuning.java`): for example
`goto.still_ticks` (100: ticks without moving before goto calls itself stuck), `mine.batch` (12: blocks
broken before picking up the drops) or `place.settled_degrees` (1.5). Each has a default and a range;
values outside it are clamped and unknown names ignored. The `tuning` op lists them all. Values sent
with a `skill` request stay in force for later skills, including ones started with `/minegpt skill`,
until the next request sends others (an empty object goes back to the defaults).

### Scenes

A scene is the blocks round the player when a skill failed or stalled: `feet` `[x, y, z]`, `exact`
position, `yaw`, `pitch`, `dimension`, `onGround`, `inWater`, `underWater`, `health`, `held` item,
and a box 9 wide, 6 high and 9 deep from 4 blocks west, 2 below and 4 north of the feet: `origin`,
`size` `[9, 6, 9]`, `palette` (block states with every property, as `/setblock` takes them),
`blocks` (palette indexes) and `cells` (one digit per cell, the pathfinder's view: 0 unloaded,
1 passable, 2 solid, 3 obstacle, 4 water, 5 danger, 6 climbable, 7 scaffolding, 8 door). Both
lists run x fastest, then z, then y.

| Skill | Arguments | Data |
|---|---|---|
| `goto` | `x`,`y`,`z` (with optional `radius`); or `x`,`z` for a column at any height; or only `y` to get to that height (down to it from above, up to it from below). `allowBreak` (left out: dig only when there is no other way; `true`: dig wherever quicker; `false`: never), `allowPlace` (true), `sprint` (true), `parkour` (true: jump gaps), `maxNodes` (20000), `timeout` (120) | `x`,`y`,`z` where it stopped, `broken`, `placed` |
| `mine` | `block` (one or a list), `count` (1), `radius` (32), `hidden` (false), `explore` (6), `timeout` (300) | `broken`, `blocks` by id, `explored`, `picked_up` by item |
| `collect` | `item` (any), `radius` (16), `timeout` (60) | `collected` items, `items` by item, `missed` |
| `craft` | `item`, `count` (1), `timeout` (120) | `crafted` |
| `smelt` | `item` (the input, such as `minecraft:raw_iron`), `count` (1), `fuel` (picked automatically), `timeout` | `smelted`, `result`, `leftovers` taken from an earlier batch |
| `place` | `item`, and `x`,`y`,`z` (or none for a free spot next to the player), `facing` (with a position; see below), `timeout` (30) | `x`,`y`,`z` |
| `equip` | one of `item`, `tool` (`pickaxe`, `axe`, `shovel`, `hoe`, `sword`), `block` (best tool for it), or `armor: true` | `slot`, or `worn` |
| `eat` | `item` (most filling safe food) | `food` level after |
| `fight` | `entity` (any hostile mob), `radius` (16), `minHealth` (6), `timeout` (60) | `kills`, `health`, `unreachable` |
| `build` | `blocks`: list of `[x, y, z, "item"]`, `[x, y, z, "item", "facing"]` or `{x, y, z, item, facing}` (at most 2048); the item `air` marks a spot to dig out. `clear` (false), `partial` (false), `fill` (true: put spare dirt or stone under blocks over a hole, up to 4 deep), `tidy` (true: take down the pillar and bridge blocks goto left while building), `foundation` (none: a block item to carry the lowest layer down to the ground with, where the ground falls away under it), `foundationDepth` (5, at most 8), `timeout` (60 + 12 per block) | `placed`, `present` (already there), `cleared`, `filled`, `footings`, `tidied`, `failed`, `failures` (first 12), `missing` by item |
| `farm` | `radius` (16), `replant` (true), `plant` (true), `till` (false), `max` (128), `timeout` | `harvested`, `planted`, `tilled`, `skipped` |
| `store` | `items` (everything but keepers), `keep`, `x`,`y`,`z` (nearest chest or barrel within 24), `timeout` (60) | `x`,`y`,`z`, `stored` by item, `contents` of the chest by item |
| `take` | `item`, `count` (64), `x`,`y`,`z`, `timeout` (60) | `x`,`y`,`z`, `took` by item, `contents` |
| `sleep` | `x`,`y`,`z` (nearest bed within 32), `timeout` (660) | `x`,`y`,`z` of the bed |

How they behave:

- **goto** plans with A* over the blocks the client has loaded and plans again when pushed
  off course, blocked, or at the end of a partial path. It walks, jumps one block, drops up to
  three, and swims. It never steps into lava, fire, cactus, magma, powder snow, cobwebs or
  portals, and never walks over tree tops. It may also dig, but only through
  natural terrain (stone, dirt, sand, gravel, ores, leaves and the like), never through blocks
  that could be part of someone's build, never next to lava, never under water, and never down
  into a drop: it digs straight down only onto solid ground. Left to itself it plans a route
  without digging first and digs only when there is none, or after getting stuck a few times.
  Searches are spread over a few ticks so the game does not stutter.
- **goto** places blocks when that is the way: it pillars up (jumps and places a block
  underneath) out of holes and onto ledges, and bridges over gaps and water by sneaking to the
  edge and placing against the side of the block it stands on. It only uses dirt, coarse dirt,
  netherrack, andesite, diorite, granite and tuff, plus cobblestone, cobbled deepslate and
  blackstone beyond the first 12 of each, and never bridges over lava.
- **Camera and keys.** Skills turn the camera through `ActionExecutor.lookToward`, which speeds
  up over a couple of ticks (at most 11 degrees a tick faster each tick), tops out at 32 degrees
  a tick sideways and 18 up and down, covers 45% of what is left once near, and settles exactly
  below 0.6 degrees, so it never overshoots. `ActionExecutor.smoothLook = false` restores the old
  half-the-angle-per-tick turn. The `look` and `look_at` actions sent over the bridge are
  unchanged. While walking, goto keeps its eyes on the ground about 3.5 blocks along the path
  instead of level at the next block, steers by where the next step is rather than where the
  camera points (forward, plus a sidestep on level ground while the camera is still more than
  35 degrees off), and hops over an unexpected obstacle only after three ticks pressed against it.
- **goto** remembers trouble for the rest of the walk: a spot where it got stuck costs more
  (and it backs off and jumps before trying again), and a spot where its head went under water
  for more than a moment is ruled out. After going under three times it gives up, since the
  way is flooded. On open, level ground it heads straight for a step up to eight ahead instead
  of following the block grid, and it sprints on straight stretches of three blocks or more.
  As a backstop it also watches the player's block position: five seconds on one block counts
  as stuck (it backs off and plans again), and twelve seconds of moving about without leaving
  a small area ends the goto with "made no headway", instead of circling until the timeout.
- **goto** stays out of water where it can. A block of swimming costs about as much as eight
  blocks of walking, so it goes round lakes (or bridges them) and only swims when there is no
  reasonable way round, and then only at the surface: it never dives. Wading through water
  with ground underfoot costs little more than walking, flowing water more, and it never enters
  falling water or a bubble column that drags down. Standing beside lava costs as much as
  five blocks of walking, and beside water or a drop of four or more about one, so paths keep
  a block clear of such edges when they can. Walking along one anyway, it sneaks, which stops
  the player at the edge.
- Any skill that ends up under water with less than half its air left is put on hold while the
  player swims to the nearest air and breathes back to full; the HUD shows "coming up for air".
  The nearest air is found by searching through the water around the head, so under a ledge or
  in a flooded cave it swims sideways to air instead of up into the ceiling.
- **goto** climbs ladders, vines and scaffolding (holding jump to go up, letting go or sneaking
  to come down), walks through wooden doors and fence gates, opening them and shutting them
  again behind it, and drops into water from up to 24 blocks when at least three of the
  blocks beside the landing are water too. With `parkour` it jumps gaps one block wide, and two
  blocks wide with a sprint and a block of run-up, but only where a miss lands on ground within
  three blocks or in water, never over lava or a long drop. With only `y`, it pillars up in
  the open as well as beside walls, so it can climb out of a shaft with spare blocks.
- **goto** plans the next stretch of a long trip while it is still walking the current one, and
  spends a fixed slice of each tick on searching (8 ms standing, 3 ms walking), so it neither
  stops to think between stretches nor freezes the game. Pushed off its path, it picks the path
  up again if it lands on a step close to where it was instead of planning afresh.
- **build** and **place** can turn blocks. `facing` is a compass direction the player must look
  in when placing (stairs rise that way, doors and beds point that way, chests and furnaces face
  back at the player), `top` or `bottom` for the half of a slab or stair, and `on:<side>` for the
  side of the spot its support must be on (`on:down` keeps a torch on the floor, `on:north` hangs
  a ladder on the wall to the north); join several with commas. The skill looks for a place to
  stand from which the click comes out that way, and places the block any way round if there is none.
- **mine** only targets blocks with at least one face a player could see (open to air,
  water or glass). Finding buried ores amounts to x-ray on a server, so `hidden: true` is
  only for singleplayer or servers that allow it. It skips blocks with nowhere to stand within
  reach (such as logs at the top of a tree) or with nowhere dry to stand (such as stone on a
  lake bed), and blocks that touch lava. It fails at once if no carried tool can harvest the
  block, and picks up the drops after each block (waiting a moment for them to appear) and any
  left nearby when it finishes. Blocks it could not reach are skipped for five minutes of game
  time, by later calls too. When nothing is in range it tunnels 24 blocks
  in the direction the player faces, up to `explore` times, which exposes new blocks.
  Among up to eight candidates it goes for the one with the cheapest route, digging included,
  rather than the nearest in a straight line. Blocks it could not get to are left alone for
  five minutes, doubling each time they fail again.
- **craft** uses the recipe book, the request the game sends when a player clicks a recipe
  there. The server only knows a recipe for the player once it is unlocked, which happens as
  the player picks up its ingredients. Recipes that need a 3x3 grid use a crafting table
  within 24 blocks, placing one from the inventory or crafting one first if there is none.
- **craft** and **smelt** aim at whatever part of a table or furnace is in sight to open it. If
  it will not open they walk right up to it and try again, then use another one or place one.
- **smelt** uses a furnace within 24 blocks, placing or crafting one if needed. Fuel is the
  first of coal, charcoal, coal block, blaze rod, dried kelp block, planks, logs and sticks
  that is carried (never the item being smelted) in a quantity that covers the whole batch, or
  else the one that covers the most; when the fuel slot runs empty with input left it adds more.
  Results left in the furnace from an earlier batch are taken out and reported, and it carries
  on with input already in the furnace even when the player has none left.
- **collect** leaves items in lava or in deep water, but goes into shallow water where the
  player can stand. It paths to stand where the item gets picked up, then walks the last
  few blocks straight at it, jumping onto steps, sneaking beside edges and stopping short of
  lava or fire. Its message lists what ended up in the inventory.
  It picks which item to go for by route, not by straight-line distance: one search over the
  nearest eight finds the one quickest to walk to, and items with no way to them are left alone
  for 30 seconds (doubling each time they fail again, and across collect calls). Next to an item
  it checks the sideways gap and the height apart, as the game's pickup box does (one block out
  to the sides, half a block above and below the player): inside the box it stands still and
  waits out the pickup delay instead of circling, and an item overhead or down a hole gets a
  path to a spot beside it. It keeps to the item it set off for unless another turns up at
  under half the distance, and each such rival is weighed once.
- **place** sneaks while clicking, so placing against a chest or crafting table does not open it.
- **build** with `foundation` looks under every block of the lowest layer in the call and adds
  that block in each open spot down to the first solid one, up to `foundationDepth` below. These
  count as part of the build (they are checked against the inventory and not tidied away), and
  the result gives their number as `footings`. Pass it only with the call that lays a
  structure's bottom layer, or upper parts grow legs.
- **build** places bottom layer first, and within a layer the nearest block that has a solid
  face beside it to be placed against, walking to each spot (through **place**). Blocks already
  in place are counted and skipped. Something in the way is dug out first only with `clear`,
  and only if it is natural terrain outside a protected region; anything else is left and
  reported. A block that fails is tried once more at the end. Without `partial` it fails at
  once, listing what is missing, when the inventory cannot cover the blocks still to place.
  Blocks without a `facing` face whichever way the player is facing.
- **farm** harvests ripe crops (any crop block at its last growth stage: wheat, carrots,
  potatoes, beetroot), waits for the drops and picks them up, and plants the same crop again on
  the farmland. With `plant` it also plants empty farmland, with the crop growing next to it or
  the seed carried most of; with `till` it hoes dirt and grass with air above and water within
  4 blocks, then plants it. It never walks with digging, and sweeps up drops at the end.
- **store** shift-clicks stacks into a chest, trapped chest or barrel. With no `items` it keeps
  tools, weapons, armour, food, torches, crafting tables, furnaces, shields and 32 scaffold
  blocks. **take** moves whole stacks, or part of one for the exact `count`. Both close the
  chest and list what it holds.
- **sleep** walks to a bed, clicks it until the player is in bed, and waits until the player
  wakes. In the nether or the end it refuses, since beds explode there. If the time runs out
  (other players on a server stayed up) it gets out of bed.
- **fight** leaves endermen, piglins, zombified piglins, the warden, the wither and the dragon
  alone unless named in `entity`, swings only at full attack strength, and backs off after
  hitting a creeper. When hunting a named `entity` it picks up the drops at the end.
  It stays on one mob until it is dead, turning to another only when that one is within 3.5
  blocks and in sight while the first is not, or is under half as far away once the first has
  had two seconds. A mob with no way to it, or one it has pushed towards for three seconds
  without changing block, is left alone for 10 seconds (doubling) unless it comes within
  reach; the result counts these as `unreachable`.

## Safety

These rails are enforced in the mod, below every skill and raw action, so they hold whatever
the brain asks for. They only apply while the agent is in control (running); a player
playing by hand is never blocked.

- **Protected regions.** `/minegpt protect here <name> [radius]` protects a square around the
  player (radius 16 by default), and `/minegpt protect area <name> <x1> <z1> <x2> <z2>` any
  rectangle. Regions cover every height, are stored per world in
  `config/minegpt/protected.json`, and are listed with `/minegpt protect list`. Inside one the
  agent cannot break blocks, place blocks, use buckets, flint and steel or fire charges, or till;
  opening chests, doors and beds still works. `/minegpt protect farm <name> on` lets it harvest
  ripe crops, plant on farmland and till inside that region. The pathfinder routes round
  protected blocks instead of digging or bridging through them.
- **Action cap.** Block breaks, placements and attacks count towards
  `safety.maxActionsPerMinute` (150 by default, 0 for none). At the cap the agent stands still
  until the oldest actions are a minute old; the HUD says so.
- **Low health.** At `safety.stopHealth` health or below (4, two hearts, by default; 0 turns it
  off) the agent cancels its skill and pauses with a reason, sent in the `mode` event. It does
  not stop again until health has been back above the line, so pressing F8 to carry on works.
- **Death** pauses the same way; the player respawns and presses F8.
- **Pause key.** F8 (or `/minegpt pause`) lets go of every key at once; `/minegpt stop` also drops the goal.

Settings live in `config/minegpt.properties`; settings added by a newer version are written into
the file the first time it runs.

