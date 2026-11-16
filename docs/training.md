# Training MineGPT's own model (Phase 6a)

The agent plays well with Claude, but every turn is an API call. Phase 6a trains a small open
model, MineGPT, to do the planner's job: read the goal and the state notes, call the skills.
It learns by imitation: Claude (the teacher) plays many goals, the episodes the game confirms
become training conversations, and a 4B model is fine-tuned on them with QLoRA on a free Colab
GPU. The result runs on your own PC through llama.cpp or Ollama, with no API key.

The skills (pathfinding, mining, crafting, building) stay in the mod. The model only has to
choose them well, which is why a small model can learn it from a few hundred episodes.

```
collect (Claude plays)  ->  export (dataset zip)  ->  Colab (fine-tune, GGUF)  ->  llama-server  ->  agent / bench --backend local
```

## 1. Collect episodes

Every goal the agent works on is already logged in `~/.minegpt/episodes/` (and benchmark runs
in `~/.minegpt/bench/agent/episodes/`). To see what you have:

```sh
cd brain
python -m minegpt_brain.train stats
```

A first fine-tune wants a few hundred usable episodes. Collect them with the teacher in a
throwaway singleplayer world with cheats on (it resets the player with commands, like the
benchmark):

```sh
python -m minegpt_brain.train collect --target 200 --max-tokens 20000000
```

It runs the benchmark tasks as a shuffled, repeating curriculum, words each goal in several
ways (so the model does not learn one sentence per task), runs tasks that keep failing less
often, and stops at `--target` good episodes, `--max-runs` runs or `--max-tokens` tokens (input
and output, not counting cache reads), whichever comes first. Ctrl+C stops it at any time; what
it collected so far is kept. Useful options:

- `--tasks wood,stone,iron` limits the curriculum to some tiers or tasks (`bench --list` names them).
- `--goals my_goals.txt` adds your own goals, one per line (lines starting with `#` are skipped).
  They have no benchmark check, so the agent's own goal check decides if they count.
  `--only-goals` runs just those.
- `--effort low` and `--model claude-sonnet-5-5` make collection cheaper; the teacher's quality
  is the ceiling for the student, so keep Opus for the hard tiers (iron, diamond).

Keep the game window able to run in the background (F3+P turns off pausing on lost focus). Each
task takes from under a minute (logs) to half an hour (iron armour), so a few hundred episodes is
an overnight run or two.

## 2. Export the dataset

```sh
python -m minegpt_brain.train export
```

This writes `minegpt-data/` with `train.jsonl`, `val.jsonl`, `info.json` and
`minegpt-data.zip` (those three plus `finetune.py`), and prints how many episodes it kept and why
it left the others out. It keeps only episodes that reached their goal as checked by the game (the
benchmark's check, or the agent's goal check), and drops repeats. Options: `--no-deaths`,
`--backend claude` (only teacher episodes), `--unverified`, `--max-turns`.

Each sample is one conversation in the OpenAI chat format with tools, exactly what the local
backend sends: system prompt, goal message, the model's tool calls, tool results and state notes.
Long episodes are cut into windows with the same rule the local backend uses to fit a small
context (old turns left out in steps of 8, past `--context-chars`, 40000 by default), so the model
is trained on exactly the history it will see when it plays. The model learns only its own
replies.

Since brain 0.6 the export also adds goal-to-plan examples to `train.jsonl`: one short sample per
goal the game confirmed, asking for the `set_plan` call from the goal and the starting situation
(see [planning.md](planning.md)). `--no-plans` leaves them out.

Item goals now take few turns, because `run_plan` does a whole chain in one. Collect on the
harder tiers to give the student something to learn: `--tasks iron,long,food,build,survival`.

## 3. Fine-tune on Colab

1. Open `brain/training/MineGPT_finetune.ipynb` in [Google Colab](https://colab.research.google.com)
   (File > Upload notebook).
2. Runtime > Change runtime type > **T4 GPU**.
3. Run the cells: it installs [Unsloth](https://github.com/unslothai/unsloth), asks for
   `minegpt-data.zip`, trains, and saves a `.gguf` file to your Google Drive (`MyDrive/minegpt`).

Defaults: `unsloth/Qwen3-4B-Instruct-2507`, 4-bit QLoRA, rank 16, 2 epochs, samples up to 16k tokens.
The 4B model is the right size for running on a laptop iGPU; `unsloth/Qwen3-8B` with `MAX_SEQ =
12288` is the step up if you have more memory and patience. `finetune.py` also runs on any machine
with an NVIDIA GPU: `pip install unsloth`, then `python finetune.py --help`.

Free Colab sessions time out after a few hours and the GPU is not always available; if that
gets in the way, the same notebook runs on a paid Colab tier or a rented GPU.

## 4. Run it

The most faithful way is llama.cpp's server, because `--jinja` uses the chat template stored in
the GGUF, the same one the model was trained with. On Windows with an AMD GPU, download the
**Vulkan** build from [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases)
(`llama-...-bin-win-vulkan-x64.zip`), then:

```sh
llama-server -m minegpt-q4_k_m.gguf --jinja -c 16384 -ngl 99 --port 8080
```

and in `brain/`:

```sh
python -m minegpt_brain.agent --backend local --model minegpt --base-url http://localhost:8080/v1
```

`-c 16384` fits the default history budget plus the tool list; if you raise the context, raise
`--context-chars` with it (about 4 characters per token).

Ollama works too: `ollama create minegpt -f Modelfile` with `FROM ./minegpt-q4_k_m.gguf` in the
Modelfile (use the Modelfile Unsloth writes next to the GGUF if there is one, which carries the
chat template), then `--backend local --model minegpt`.

## 5. Score it

Run the benchmark with the trained model and compare it with Claude's runs:

```sh
python -m minegpt_brain.bench --backend local --model minegpt --base-url http://localhost:8080/v1 --label minegpt-v1 --tasks wood,stone,storage
python -m minegpt_brain.bench --report
```

The report puts each label in its own column. Goals the student fails are the ones to collect
more teacher episodes for, then export and train again (a new run on all the data is simplest).
Episodes the student itself gets right are kept as training data too, if you want them: they are
already logged.

## What is in the code

- `brain/src/minegpt_brain/train/episodes.py`: reads episode logs back into conversations.
  Logs from before brain 0.3 lack a few messages, which it fills in the way the agent wrote them.
- `train/dataset.py`: filters, windows, splits and writes the dataset.
- `train/collect.py`: the curriculum collector.
- `train/finetune.py`: the training script (stands alone, uploaded with the data).
- `llm/history.py`: the context-fitting rule shared by the local backend and the exporter.
