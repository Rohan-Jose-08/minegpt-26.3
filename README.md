# MineGPT

An AI that plays Minecraft (Java Edition 26.3) on its own and works toward goals you give it.

The project has two halves:

| Folder | Language | Role |
|---|---|---|
| repo root (`src/`, `build.gradle`) | Java 25, Fabric | The mod: reads game state and carries out actions in the game |
| `brain/` | Python 3.12+ | Plans, remembers, and learns; drives the mod over a local socket |

## Versions

- Minecraft 26.3 (unobfuscated, Mojang names)
- Fabric Loader 0.20.1, Fabric API 0.165.0+26.3, Loom 1.19 (`net.fabricmc.fabric-loom`)
- Java 25, Gradle 9.8.2

## Running the mod

```sh
./gradlew runClient      # dev client with the mod loaded
./gradlew genSources     # decompiled Minecraft sources for reading in the IDE
./gradlew build          # jar in build/libs/
```

## In-game commands

All commands are client-side, so they work on any server.

| Command | What it does |
|---|---|
| `/minegpt goal <text>` | Sets a goal for the agent, such as `/minegpt goal full iron armor` |
| `/minegpt pause`, `/minegpt resume` | Lets go of the controls, or takes them back. **F8** does the same |
| `/minegpt stop` | Clears the goal, cancels the running skill and releases every key |
| `/minegpt status` | Shows the mode, goal and recording state |
| `/minegpt act <json>` | Runs one low-level action, for example `{"type":"move","keys":["forward"],"ticks":40}` |
| `/minegpt skill <name> [json]` | Runs a skill, for example `/minegpt skill mine {"block":"#minecraft:logs","count":4}` |
| `/minegpt obs` | Saves the current observation to `run/minegpt/observation.json` |
| `/minegpt record start`, `/minegpt record stop` | Records your gameplay to `run/minegpt/recordings/` |
| `/minegpt bridge start`, `stop`, `status` | Opens the local port the Python brain connects to (127.0.0.1:5577) |
| `/minegpt job add <when>: <goal>` | Adds a recurring job, such as `/minegpt job add every day at dawn: harvest and replant the wheat`. Also `job list`, `run <n>`, `pause <n>`, `resume <n>`, `remove <n>`, `off`, `on` |
| `/minegpt protect here <name> [radius]` | Protects the area round you (default radius 16) from the agent's digging and building |
| `/minegpt protect area <name> x1 z1 x2 z2` | Protects a rectangle, every height |
| `/minegpt protect farm <name> on\|off` | Lets the agent harvest, replant and till inside a protected area |
| `/minegpt protect list`, `remove <name>` | Lists or removes protected areas (kept per world) |

Settings are in `run/config/minegpt.properties`: `bridge.autostart`, `bridge.port`,
`bridge.allowCommands` (lets the brain run server commands outside singleplayer, default false),
`safety.maxActionsPerMinute` (150) and `safety.stopHealth` (the agent pauses at or below this
health, default 4).

The observation, action, recording and skill formats are in [docs/protocol.md](docs/protocol.md).

To let the agent keep playing while you use another window, turn off pausing on lost focus
(press F3+P in game, or set `pauseOnLostFocus:false` in `options.txt`).

## Running the brain

Start the bridge in game (`/minegpt bridge start`), then in `brain/`:

```sh
pip install -e ".[agent]"
set ANTHROPIC_API_KEY=sk-ant-...     # PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
python -m minegpt_brain.agent
```

and give it a goal in game with `/minegpt goal`. More, including running a local model instead,
in [brain/README.md](brain/README.md).

With the brain running, jobs added with `/minegpt job add` run on their own whenever there is no
goal, and the agent looks after itself between them (eats, fights, sleeps at night, goes home).
`python -m minegpt_brain.bench` runs the benchmark suite of 25 tasks and tracks pass rate and time
per task across versions.

## Training MineGPT's own model

`python -m minegpt_brain.train collect` lets Claude play a curriculum of goals to make training
episodes, `export` turns the ones the game confirmed into a dataset, and the Colab notebook in
`brain/training/` fine-tunes a 4B open model on them for free. The result runs on your PC with
llama.cpp or Ollama through `--backend local`. Step by step in [docs/training.md](docs/training.md).

## License

CC0-1.0, from the Fabric example template. See [LICENSE](LICENSE).
