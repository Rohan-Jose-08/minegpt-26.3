# Practice: MineGPT learning from its own play (Phase 20)

Practice is how MineGPT gets better without you typing goals. It runs in a throwaway world,
tries things, lets the game grade every attempt, and changes what it does based on the results.
Three parts work together:

1. **Practice mode** picks what to practise, resets the world between tries, and runs drills
   (single skills, no model), replays of earlier failures, and goals for the model.
2. **The failure miner** files every skill failure and every stall with the blocks round the player,
   groups them by cause and ranks the causes by minutes lost.
3. **Self-tuning skills**: about twenty limits and counts in the skills try nearby values during
   practice and keep the ones that make skills succeed more and finish sooner.

Normal goals, the benchmark and collection runs also feed the failure miner and use the tuned
settings; only practice tries new values.

## Running a night of practice

Use a throwaway singleplayer world with cheats on, like the benchmark: practice moves the player
to fresh land, clears its inventory and builds small test spots with commands. Start the game,
load the world, then:

```sh
cd brain
python -m minegpt_brain.practice run --hours 8 --no-llm   # drills and replays only: free
python -m minegpt_brain.practice run --hours 8            # drills, replays and goals (Claude by default)
```

`--no-llm` needs no API key and is the best first run: it gives the tuner hundreds of skill calls
and tells you where the skills get stuck. With the model, 60% of runs are still drills
(`--drill-share`), and goals stop at the token budget (`--max-tokens`, 20 million by default).
The usual backend options work (`--backend local --base-url ...` for your own model).

A night goes like this:

1. **Gate before.** Twelve fixed drills (six kinds, twice each) from the same starting spots every
   night, with the settings in force.
2. **Replays.** The three worst failure causes of the last week are rebuilt in game and tried
   again (`--replays`).
3. **Practice** until the time, run or token limit: drills and goals picked by the curriculum.
4. **Gate after.** The same twelve drills. If one more drill fails than before, or the passed
   drills take a fifth longer, every setting change made that night is undone.
5. **Progress.** One line in `~/.minegpt/practice/progress.jsonl`, and a summary printed.

Stop early with Ctrl+C. Results of every run are in `~/.minegpt/practice/results.jsonl`.

## What it practises

**Drills** run one skill without the model and check the result in the game:

| drill | what it does | passes when |
|---|---|---|
| walk | goto 40 to 80 blocks over open land | within 3 blocks |
| climb_out | goto out of a 3-deep stone pit | within 3 blocks of a spot 8 away |
| swim_out | goto out of a walled pool | within 3 blocks of a spot 9 away |
| pillar_up | pillar 6 up from short grass, torches and a flower | 6 higher |
| logs | mine 6 logs | 6 logs gained |
| stone | mine 12 stone with a wooden pickaxe | 12 cobblestone gained |
| coal | find and mine 3 coal | 3 coal gained |
| iron | find and mine 3 iron, tunnelling | 3 raw iron gained |

**Replays** rebuild a failure's 9 x 6 x 9 scene on fresh land, put the player back where it stood,
facing the same way, and run the same skill again. A replay passes if the skill works without
stalling. Only goto, mine, collect and place failures in the overworld can be replayed.

**Goals** come from the benchmark tasks: tasks of the first tier not yet mastered (wood, then
stone, iron, long jobs; mastered means each task passed 70% of at least two tries) are picked most,
and tasks that fail often are picked more. Goals from normal play that were given up or ran out of
turns in the last two weeks are retried now and then.

## The failure miner

```sh
python -m minegpt_brain.practice failures            # every cause, worst first
python -m minegpt_brain.practice failures --days 7
python -m minegpt_brain.practice report              # failures, settings and nights together
```

Each line is a cause: the skill, the gist of its message, and what the scene shows (in water,
under water, in a pit, boxed in, in a dead end, plant or torch in the way, in leaves, lava or fire
near, in the air, on a pillar). For example:

```
  12.5 min    9x  goto: stalled: pillar up [plant or torch in the way]
                     latest 20261117-021512-41 (2026-11-17 02:15:12): pillar up at 12 70 -4: jumping
```

Records are in `~/.minegpt/failures/<date>.jsonl`, one per failure or stall, scene included.

### Keeping a stuck spot as a test

A scene can become a JUnit test of the pathfinder:

```sh
python -m minegpt_brain.practice keep 20261117-021512-41                 # must be able to get out
python -m minegpt_brain.practice keep 20261117-021512-41 --expect "reach 4 2 8"
python -m minegpt_brain.practice keep 20261117-021512-41 --no-breaking   # without digging
```

This writes `src/test/resources/scenes/<id>.scene`; `gradlew test` then checks the pathfinder can
leave that spot (`escape`: get 4 blocks away) or reach the given cell of the box. A failing scene is
a stuck spot no change has fixed yet. Problems of steering rather than route finding (a jump loop
on a route that exists) do not show in this test; replay those in game instead:

```sh
python -m minegpt_brain.practice replay 20261117-021512-41
```

## Self-tuning skills

```sh
python -m minegpt_brain.practice tuning                     # every setting: default, in force, runs, best so far
python -m minegpt_brain.practice tuning --reset goto.idle_jumps
python -m minegpt_brain.practice tuning --reset             # all back to the defaults
```

Each setting has a default and a range (`Tuning.java` in the mod). In practice, about half the
calls of a skill try another value of one of its settings (60% to 150% of the default), chosen by
Thompson sampling: values that have done well are tried more, and none is ruled out on a few
unlucky runs. A value's score is its success rate divided by (1 + its seconds per unit of work over
the setting's usual pace). A value replaces the one in force once both have six runs and it scores
at least 5% better; the change is logged, and undone if the gate gets worse. Normal goals only use
the values in force. The settings are in `~/.minegpt/tuning.json`. `python -m minegpt_brain.agent
--no-tuning` runs with the mod's defaults.

| setting | default | what it is |
|---|---|---|
| goto.still_ticks | 100 | ticks without moving before goto calls itself stuck and replans |
| goto.loiter_ticks | 240 | ticks near one spot before goto gives up there |
| goto.walk_step_ticks | 60 | ticks one walking step may take |
| goto.place_step_ticks | 100 | ticks one pillar or bridge step may take |
| goto.idle_jumps | 3 | jumps with no block placed before a pillar spot is given up |
| goto.bridge_wait_ticks | 40 | ticks waited for a bridge click |
| goto.dig_after_failures | 3 | failed steps before goto may dig through |
| goto.submerged_ticks | 15 | ticks under water before goto swims up |
| goto.stuck_penalty | 60 | extra path cost on a cell where the player got stuck |
| goto.unstick_ticks | 8 | ticks spent backing off after getting stuck |
| mine.batch | 12 | blocks broken from one spot before picking up the drops |
| mine.drop_settle_ticks | 12 | ticks waited for drops to land |
| mine.pick_nodes | 4000 | path search size when choosing the next block |
| mine.seconds_per_block | 4 | time allowed per wanted block (at least 300 s) |
| collect.still_ticks | 25 | ticks without moving before collect gives up on an item |
| collect.trips | 2 | walks towards one item before it is given up |
| place.settled_degrees | 1.5 | how still the crosshair must be before clicking |
| place.tries | 3 | clicks on one spot before place gives up |
| build.uncover_tries | 5 | tries at clearing what hides a block |
| build.view_moves | 2 | moves to a better spot for a block that cannot be seen |

## Reading the progress

`practice report` ends with one line per night: runs passed, the gate before and after, minutes
lost to stalls per hour of practice, settings changed and tokens used. Over a week of nights,
the gate should stay level or climb, stall minutes per hour should fall, and the top failure causes
should change as old ones get fixed. The benchmark (`python -m minegpt_brain.bench`) is still the
measure for whole goals.
