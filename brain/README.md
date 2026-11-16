# MineGPT brain

Python side of MineGPT. It talks to the mod over the bridge (docs/protocol.md).

- `minegpt_brain.bridge.Bridge`: connect, observe, act, step, and receive goal and pause events
- `minegpt_brain.observation.Observation`: helpers such as `block_at`, `find_blocks`, `count`
- `minegpt_brain.skills.Skills`: the mod's skills (goto, mine, collect, craft, smelt, place, equip, eat, fight), one blocking call each
- `minegpt_brain.env.MineGPTEnv`: a Gymnasium environment for learning (needs the `env` extra)
- `minegpt_brain.agent`: the goal-driven agent, where a language model plans and calls skills (see below)
- `minegpt_brain.llm`: the model backends the agent uses, Claude or a local model
- `minegpt_brain.train`: collecting episodes, exporting a fine-tuning dataset, and the fine-tune script (see below)

## Setup

```sh
cd brain
python -m venv .venv
.venv\Scripts\activate        # Windows (use `source .venv/bin/activate` elsewhere)
pip install -e ".[dev]"
pytest
```

## Trying it against the game

1. Start the game (`./gradlew runClient`), join a world, and run `/minegpt bridge start`.
2. In `brain/` with the virtual environment active:

```sh
python examples/hello.py              # prints what the agent sees
python examples/walk_to.py 100 -40    # walks to x=100 z=-40 in a straight line, with raw actions
python examples/random_agent.py       # random actions through the Gymnasium env
python examples/iron_pickaxe.py       # gets an iron pickaxe from scratch with skills
```

Skills from Python:

```python
from minegpt_brain.bridge import Bridge
from minegpt_brain.skills import Skills

with Bridge.connect() as mc:
    skills = Skills(mc)
    result = skills.mine("#minecraft:logs", count=4)
    print(result.success, result.message, result.data)
    skills.craft("#minecraft:planks", count=16)
    skills.goto(100, 64, -40)
```

Press F8 in game to pause the agent at any time.

## The agent

The agent takes a goal in plain words, such as `full iron armor`, and works on it until it is
done. A language model reads the situation, writes a plan, and calls the skills one turn at a
time; the agent runs them in game and reports back what happened. It also:

- checks plan steps and the goal itself against the game (items carried, armour worn, place
  reached), so `goal_done` is refused until the game agrees;
- adds a hint to failed skills, and points out when the same call keeps failing the same way;
- eats when food runs low, fights mobs that come close, and picks up drops lying within 8 blocks,
  without asking the model; the state note after each turn lists items still on the ground;
- notices deaths, remembers where and probably why, and waits for you to respawn and press F8;
- remembers things per world (base, ore spots, chests, deaths), and saves skill sequences that
  worked as routines that later, similar goals are offered;
- logs every goal as an episode, the training data for Phase 6.

### Running it with Claude

```sh
pip install -e ".[agent]"
set ANTHROPIC_API_KEY=sk-ant-...      # Windows cmd; PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
python -m minegpt_brain.agent
```

Then in game, with the bridge started, type `/minegpt goal full iron armor`. Or give the goal
on the command line: `python -m minegpt_brain.agent --goal "full iron armor" --once`.

It uses `claude-opus-5-5` with medium effort by default. `--effort high` thinks harder per turn
(slower and costlier), `--model` picks another Claude model. Refusal fallbacks are on: if the
API declines a request, it retries it on Anthropic's recommended fallback model in the same
call (`--no-fallbacks` turns that off). A long goal can take 50 to 80 turns, which is a few
dollars of API use with Opus.

### Running it with a local model

Any server with an OpenAI-style `/v1/chat/completions` endpoint and tool calling works, such as
[Ollama](https://ollama.com):

```sh
ollama pull qwen3:14b
python -m minegpt_brain.agent --backend local --model qwen3:14b
```

`--base-url` points it at another server (the default is Ollama's `http://localhost:11434/v1`).
Small models struggle with long goals; this is mostly the slot for the model trained in Phase 6.
Long histories are cut to fit a small context: `--context-chars` (40000, about 10k tokens) of
history are sent, and older turns are left out in steps of 8.

### Controls and files

- In game: `/minegpt goal <text>` sets a goal (a new one replaces the current one), `/minegpt stop`
  ends it, F8 pauses and resumes. The HUD shows what the agent is doing.
- `--max-turns` (80) caps model turns per goal, `--no-reflexes` turns off eating, fighting and picking up on
  its own, `--xray` lets mine target hidden ores (singleplayer only; it is cheating on a server).
- Memory, routines and episode logs live in `~/.minegpt/` (`--data-dir` or `MINEGPT_DATA_DIR`
  moves them): `memory.json`, `routines.json`, `blueprints.json`, `jobs.json`, `automation.jsonl`,
  and `episodes/*.jsonl`, one file per goal.
- `--no-jobs` turns off recurring jobs, `--no-idle` stops it looking after the player between jobs.

### Jobs, building and safety

Add recurring jobs in game: `/minegpt job add every day at dawn: harvest and replant the wheat`,
`every 2 days: smelt the iron in the chest`, `every 30 minutes: top up the furnaces`, `nightly: sleep`.
Day-based schedules follow game time, minutes and hours follow real time. A job runs as a normal
goal when nothing else is going on; a goal you set yourself always comes first. A job that fails
three times in a row is paused (`/minegpt job resume <n>`). Jobs are kept per world.

The model can build from a drawing (layers bottom up, rows north to south, a key of characters to
blocks), save it as a blueprint, and build it again by name. It can also farm, store and take from
chests, and sleep. Protected areas (`/minegpt protect`) are enforced by the mod, so the model cannot
dig or build in them whatever it decides; the mod also caps actions per minute and pauses at low health.

### Big jobs (brain 0.6)

`run_plan(items=...)` gets items the whole way, from logs to the finished tool, using the game's
own recipes and drops, which ship with the brain. Plans can have substeps and alternatives, are
saved so an interrupted goal carries on, and the agent keeps a map of ore it has seen, timings of
its skills and a record of every goal. See [docs/planning.md](../docs/planning.md).

```
python -m minegpt_brain.knowledge plan iron_pickaxe=1    # show a plan without the game
```

### Benchmark

```sh
python -m minegpt_brain.bench --list                 # the 28 tasks, by tier
python -m minegpt_brain.bench --tasks wood,stone --repeat 3
python -m minegpt_brain.bench --report               # pass rate and median time, per version
```

Run it in a throwaway singleplayer world with cheats on: between tasks it resets the player with
commands (survival, empty inventory, a fresh spot far away, the task's kit and fixtures). Each task is
checked by the benchmark itself, not by what the agent claims. Results go to `~/.minegpt/bench/results.jsonl`,
labelled with the brain version and model (`--label` to name a run). It takes the same backend options as the agent.

### Practice (Phase 20)

```sh
python -m minegpt_brain.practice run --hours 8 --no-llm   # drills and replays only: no tokens
python -m minegpt_brain.practice run --hours 8            # with goals for the model too
python -m minegpt_brain.practice report                   # worst failure causes, tuned settings, nights
```

MineGPT practises on its own in a throwaway world: set drills (walk, climb out of a pit, swim out of a
pool, pillar up through grass and torches, chop, mine), failure scenes rebuilt from its own records, and
goals aimed at its weakest tier. Every skill failure and every 15-second stall is filed with the blocks
round the player and ranked by minutes lost, and about twenty skill settings tune themselves from the
results, kept only if a fixed drill check does not get worse. See [docs/practice.md](../docs/practice.md).

### Training (Phase 6a)

```sh
python -m minegpt_brain.train stats                    # episodes so far, and how many are usable
python -m minegpt_brain.train collect --target 200     # Claude plays a curriculum (cheats-on test world)
python -m minegpt_brain.train export                   # minegpt-data/minegpt-data.zip for Colab
```

Then fine-tune with `training/MineGPT_finetune.ipynb` on a free Colab T4, run the GGUF it makes
with `llama-server --jinja`, and point the agent or benchmark at it with `--backend local`. The whole
loop, with options, is in [docs/training.md](../docs/training.md).
