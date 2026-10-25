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
| `/minegpt goal <text>` | Sets the goal shown in the HUD (the brain will act on it from Phase 4) |
| `/minegpt pause`, `/minegpt resume` | Lets go of the controls, or takes them back. **F8** does the same |
| `/minegpt stop` | Clears the goal and releases every key |
| `/minegpt status` | Shows the mode, goal and recording state |
| `/minegpt act <json>` | Runs one low-level action, for example `{"type":"move","keys":["forward"],"ticks":40}` |
| `/minegpt obs` | Saves the current observation to `run/minegpt/observation.json` |
| `/minegpt record start`, `/minegpt record stop` | Records your gameplay to `run/minegpt/recordings/` |

The observation, action and recording formats are in [docs/protocol.md](docs/protocol.md).

## Running the brain

See [brain/README.md](brain/README.md).

## License

CC0-1.0, from the Fabric example template. See [LICENSE](LICENSE).
