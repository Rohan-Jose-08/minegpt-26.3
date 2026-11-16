# How MineGPT breaks down big jobs

Long goals used to live in the model's head: it had to remember the recipe tree, the tool tiers,
how much iron a set of armour takes and where it saw ore, across a hundred turns, while old turns
were dropped from its context. Since brain 0.6 that knowledge lives in data the code keeps, and
the model chooses and fills gaps. Nothing here needs a GPU or any training.

| Piece | File | What it does |
| --- | --- | --- |
| Recipe graph | `knowledge/graph.py`, `knowledge/data/minecraft-26.3.json.gz` | Every recipe, block drop, mob drop and tool need in the game, with a cost in seconds for each way of getting an item |
| Item planner | `knowledge/planner.py` | Turns "I want these items" into ordered mine, hunt, craft and smelt steps, from what is carried |
| `run_plan` tool | `agent/tools.py` | Carries such a plan out in one model turn, each step checked by the game |
| Plan tree | `agent/taskgraph.py` | Steps with substeps and alternatives; a failed step is replanned alone; saved to disk and picked up again |
| Dry run | `agent/contracts.py` | Plays skill calls forward on paper and names the first that cannot work |
| Timings | `agent/skillstats.py` | Measures how long mining, hunting and crafting really take, and feeds that back to the planner |
| World map | `agent/worldmap.py` | Files what `look_around` and `find_blocks` see, so ore seen once can be found again (`recall`) |
| Goal records | `agent/experience.py` | Keeps each goal's plan, outcome and trouble; the most similar ones are shown at the start of a new goal |
| Plan examples | `train/plans.py` | Turns confirmed successes into short "goal and start to plan" training examples |

## Getting items: `run_plan`

```
run_plan(items={"iron_pickaxe": 1})
```

works out the chain (logs, planks, crafting table, sticks, wooden pickaxe, cobblestone, furnace,
stone pickaxe, iron ore, smelting, the pickaxe) from what the player carries, and runs it. It
stops at the first step that fails, usually "no iron ore in sight", says what is left and where
that ore was seen before, and carries on from the inventory when called again. To see a plan
without running it:

```
python -m minegpt_brain.knowledge plan iron_pickaxe=1 torch=16
```

Wood is kept loose ("any planks", "any logs") so the agent uses the trees it finds.

## The game data

The file for Minecraft 26.3 ships with the brain. After a game update, rebuild it from the jar
Loom downloads (any vanilla jar works):

```
python -m minegpt_brain.knowledge build .gradle/loom-cache/minecraftMaven/net/minecraft/minecraft-common-<hash>/<version>/minecraft-common-<hash>-<version>.jar --version <version>
```

It reads the jar's own recipes, loot tables and tags, so it needs no running game, and the same
file serves singleplayer and multiplayer. A server with changed recipes will differ from it; the
`recipe` tool still shows what the game itself allows.

## The plan tree

`set_plan` steps may carry `substeps`, and `any: true` makes them alternatives tried in order.
`expand_step(step="2", substeps=[...])` changes one step and leaves the rest. A call that fails
the same way repeatedly counts against the step in hand; after two such failures the step is
marked `[!]` and the next alternative is taken, or the model is asked to give that one step
another way. The plan is saved under `~/.minegpt/goals/` after every turn. If a goal ends by the
turn limit, a crash or a lost connection, setting the same goal again within a day picks the
plan up, with the steps the game had checked still ticked.

## Files in `~/.minegpt`

- `goals/` plans of unfinished goals
- `skillstats.json` how often and how fast each kind of skill call works
- `worldmap.json` what was seen where, per world
- `experience.json` one record per finished goal

## Training on plans

```
python -m minegpt_brain.train plans --out minegpt-data
```

writes `plans.jsonl`: one example per goal the game confirmed, with the goal and starting
situation as the question and the `set_plan` call as the answer. The examples are a few hundred
tokens each, in the same chat format as the episode export. `python -m minegpt_brain.train export`
adds them to `train.jsonl` by itself, so the Colab notebook needs no change. Records from
benchmark and collection runs are read too.

Benchmark and collection runs do not pick up saved plans (`resume=False`), because they start
the same goal again with the player reset.

## Measuring it

The benchmark has a `long` tier (`iron_kit`, `diamond_pickaxe`, `bed_and_bucket`):

```
python -m minegpt_brain.bench --tasks iron,long --label with-run-plan
```
