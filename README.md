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

## Running the brain

See [brain/README.md](brain/README.md).

## License

CC0-1.0, from the Fabric example template. See [LICENSE](LICENSE).
