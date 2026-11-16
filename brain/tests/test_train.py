import json
import random
import uuid
import zipfile

import pytest

from minegpt_brain.agent import Agent, AgentConfig
from minegpt_brain.bench import TASKS
from minegpt_brain.bridge import Bridge
from minegpt_brain.llm import ToolCall, Turn
from minegpt_brain.llm.history import TRIMMED_NOTE, dropped_turns, fit_history
from minegpt_brain.llm.local import LocalBackend, LocalConversation
from minegpt_brain.train import dataset, episodes, finetune
from minegpt_brain.train.__main__ import main
from minegpt_brain.train.collect import PHRASINGS, Curriculum
from fake_mod import FakeMod
from test_agent import crafting_world


class ScriptedLocal(LocalConversation):
    """The local backend's conversation, with replies played back instead of asked for."""

    def __init__(self, script, *args):
        super().__init__(*args)
        self.script = script
        self.sent = []

    def next(self):
        self.sent.append(fit_history(self.messages, self._backend.context_chars))
        calls = [ToolCall(f"c_{uuid.uuid4().hex[:8]}", name, args) for name, args in self.script.pop(0)]
        assistant = {"role": "assistant", "content": ""}
        if calls:
            assistant["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.name, "arguments": json.dumps(c.args)}} for c in calls]
        self.messages.append(assistant)
        return Turn("", calls, "tool_use" if calls else "end_turn", raw=assistant)


class ScriptedLocalBackend(LocalBackend):
    def __init__(self, script, context_chars=None):
        super().__init__("scripted", context_chars=context_chars)
        self.script = list(script)
        self.conversations = []

    def start(self, system, tools):
        real = super().start(system, tools)
        conversation = ScriptedLocal(self.script, self, system, real._tools)
        self.conversations.append(conversation)
        return conversation


def _plain(messages):
    """Messages with tool call arguments as objects, for comparing."""
    out = []
    for m in messages:
        m = {k: v for k, v in m.items() if k != "train"}
        if m.get("tool_calls"):
            m["tool_calls"] = [{**c, "function": {**c["function"], "arguments": (
                json.loads(c["function"]["arguments"]) if isinstance(c["function"]["arguments"], str)
                else c["function"]["arguments"])}} for c in m["tool_calls"]]
        out.append(m)
    return out


@pytest.fixture
def game(tmp_path):
    mod = FakeMod()
    mod.skill_handler = crafting_world
    mod.goal = "make planks"
    bridge = Bridge.connect(port=mod.port, timeout=5)
    yield mod, bridge, tmp_path
    bridge.close()


PLANKS = [
    [("set_plan", {"steps": [{"step": "craft planks", "done_when": {"have": {"*_planks": 4}}}],
                   "goal_check": {"have": {"oak_planks": 4}}})],
    [],  # a reply with no tool calls: the agent nudges
    [("craft", {"item": "stone_pickaxe"}), ("craft", {"item": "oak_planks", "count": 4})],  # second one skipped
    [("craft", {"item": "oak_planks", "count": 4})],
    [("goal_done", {"summary": "made 4 planks"})],
]


def test_episode_logs_rebuild_the_conversation_the_local_backend_sent(game):
    mod, bridge, tmp_path = game
    backend = ScriptedLocalBackend(PLANKS)
    result = Agent(bridge, backend, AgentConfig(data_dir=tmp_path, poll=0.05)).run_goal("make planks")
    assert result.outcome == "success" and result.verified

    episode = episodes.load(result.log_path)
    conversation = backend.conversations[0]
    assert episode.exact and episode.good and episode.turns == 5
    # Everything up to the last reply matches what the model was given, message for message.
    last_reply = max(i for i, m in enumerate(conversation.messages) if m["role"] == "assistant")
    assert _plain(episode.messages[:last_reply + 1]) == _plain(conversation.messages[:last_reply + 1])
    assert any("skipped because craft failed" in m["content"] for m in episode.messages if m["role"] == "tool")
    assert episode.tools and episode.tools[0]["name"] == conversation._tools[0]["function"]["name"]

    sample, = dataset.samples(episode)
    assert sample["messages"][-1]["role"] == "assistant"
    assert sample["tools"] == conversation._tools
    assert _plain(sample["messages"]) == _plain(conversation.messages[:last_reply + 1])


def test_old_logs_without_results_are_filled_in(game):
    mod, bridge, tmp_path = game
    backend = ScriptedLocalBackend(PLANKS)
    result = Agent(bridge, backend, AgentConfig(data_dir=tmp_path, poll=0.05)).run_goal("make planks")
    # Make it look like a log from brain 0.2: no results, user or tool list.
    lines = [json.loads(l) for l in result.log_path.read_text().splitlines()]
    old = [l for l in lines if l["event"] not in ("results", "user")]
    for l in old:
        l.pop("tools", None)
    path = tmp_path / "old.jsonl"
    path.write_text("\n".join(json.dumps(l) for l in old))

    episode = episodes.load(path)
    new = episodes.load(result.log_path)
    assert not episode.exact and episode.tools is None
    assert _plain(episode.messages) == _plain(new.messages)
    assert dataset.samples(episode)[0]["tools"] == dataset.openai_tools(None)


def test_long_episodes_are_cut_into_the_windows_the_backend_used(game):
    mod, bridge, tmp_path = game
    script = [[("look_around", {})] for _ in range(30)] + [[("goal_failed", {"reason": "test"})]]
    backend = ScriptedLocalBackend(script, context_chars=14000)
    result = Agent(bridge, backend, AgentConfig(data_dir=tmp_path, poll=0.05, max_turns=40)).run_goal("make planks")
    conversation = backend.conversations[0]
    assert any(m["content"].startswith("(") and "earlier turns" in m["content"]
               for sent in conversation.sent for m in sent if m["role"] == "user")

    episode = episodes.load(result.log_path)
    windows = dataset.samples(episode, budget=14000)
    assert len(windows) > 1
    # Each reply is learnt exactly once, from exactly the history it was produced from.
    learnt = 0
    replies = [m for m in conversation.messages if m["role"] == "assistant"]
    for window in windows:
        messages = window["messages"]
        assert messages[-1]["role"] == "assistant" and messages[-1].get("train", True)
        for i, m in enumerate(messages):
            if m["role"] == "assistant" and m.get("train", True):
                assert _plain(messages[:i]) == _plain(conversation.sent[learnt])
                assert _plain([m]) == _plain([replies[learnt]])
                learnt += 1
    assert learnt == len(replies)


def test_fit_history_drops_old_turns_in_steps():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "goal"}]
    drops = []
    for n in range(40):
        messages += [{"role": "assistant", "content": "", "tool_calls": [
            {"id": str(n), "type": "function", "function": {"name": "look_around", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": str(n), "content": "x" * 100}, {"role": "user", "content": "state"}]
        drops.append(dropped_turns(messages, budget=2000, step=8))
    assert drops == sorted(drops) and set(drops) <= {0, 8, 16, 24, 32}
    fitted = fit_history(messages, budget=2000, step=8)
    assert fitted[:2] == messages[:2] and fitted[2]["content"] == TRIMMED_NOTE.format(n=drops[-1])
    assert fitted[3]["role"] == "assistant" and fitted[-1] == messages[-1]
    assert fit_history(messages[:5], budget=2000) == messages[:5]


def test_selection_keeps_only_confirmed_successes(tmp_path):
    def episode(name, **fields):
        e = episodes.Episode(tmp_path / name, goal=name, turns=1, backend="claude:x",
                             messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "g"},
                                       {"role": "assistant", "content": name}], **fields)
        return e

    picked = dataset.select([
        episode("good", outcome="success", verified=True),
        episode("bench", outcome="success", verified=False, passed=True),
        episode("unchecked", outcome="success", verified=False),
        episode("bench_fail", outcome="success", verified=True, passed=False),
        episode("failed", outcome="failed"),
        episode("died", outcome="success", verified=True, deaths=1),
    ], dataset.Filters(deaths=False))
    assert [e.goal for e in picked.kept] == ["good", "bench"]
    assert picked.skipped["benchmark check failed"] == 1 and picked.skipped["died"] == 1


def test_export_writes_a_colab_bundle(game):
    mod, bridge, tmp_path = game
    for _ in range(6):
        backend = ScriptedLocalBackend(PLANKS)
        mod.slots = [{"slot": 0, "id": "minecraft:oak_log", "count": 5}]
        mod.goal = "make planks"
        Agent(bridge, backend, AgentConfig(data_dir=tmp_path / "data", poll=0.05)).run_goal("make planks")
    assert main(["export", "--data-dir", str(tmp_path / "data"), "--out", str(tmp_path / "out")]) == 0
    info = json.loads((tmp_path / "out" / "info.json").read_text())
    # The runs that got going all played out the same way, so all but one are duplicates.
    assert info["kept"] == 1 and info["skipped"]["duplicate"] >= 1
    with zipfile.ZipFile(tmp_path / "out" / "minegpt-data.zip") as z:
        assert set(z.namelist()) == {"train.jsonl", "val.jsonl", "info.json", "finetune.py"}
        assert "def assistant_spans" in z.read("finetune.py").decode()
    assert main(["stats", "--data-dir", str(tmp_path / "data")]) == 0


def test_bench_results_mark_episodes_by_file_name(tmp_path):
    (tmp_path / "results.jsonl").write_text(json.dumps(
        {"log": "C:\\Users\\me\\.minegpt\\bench\\agent\\episodes\\a.jsonl", "passed": True, "task": "logs"}) + "\n")
    index = episodes.bench_index([tmp_path / "results.jsonl"])
    assert index["a.jsonl"]["task"] == "logs"


def test_curriculum_rewords_goals_and_backs_off_failing_tasks():
    tasks = [t for t in TASKS if t.name in ("logs", "planks")]
    curriculum = Curriculum(tasks, random.Random(1))
    seen = {curriculum.next().goal for _ in range(40)}
    assert set(PHRASINGS["logs"]) & seen and "Collect 8 logs." in seen
    for _ in range(10):
        curriculum.record(tasks[0], False)
        curriculum.record(tasks[1], True)
    assert curriculum.chance(tasks[0]) == 0.2 and curriculum.chance(tasks[1]) > 0.9
    picks = [curriculum.next().name for _ in range(200)]
    assert picks.count("planks") > 2 * picks.count("logs")


# The fine-tune script's tokenizing, with a small ChatML tokenizer standing in for Qwen's. ------

CHATML = (
    "{%- if tools %}<|im_start|>system\n{{ messages[0].content }}\n\n<tools>{% for t in tools %}\n{{ t | tojson }}"
    "{% endfor %}\n</tools><|im_end|>\n{%- endif %}"
    "{%- for m in messages %}"
    "{%- if m.role == 'system' %}{% if not tools %}<|im_start|>system\n{{ m.content }}<|im_end|>\n{% endif %}"
    "{%- elif m.role == 'assistant' %}<|im_start|>assistant\n{{ m.content }}"
    "{%- for c in m.tool_calls or [] %}\n<tool_call>\n{\"name\": \"{{ c.function.name }}\", \"arguments\": "
    "{{ c.function.arguments | tojson }}}\n</tool_call>{% endfor %}<|im_end|>\n"
    "{%- elif m.role == 'tool' %}<|im_start|>user\n<tool_response>\n{{ m.content }}\n</tool_response><|im_end|>\n"
    "{%- else %}<|im_start|>{{ m.role }}\n{{ m.content }}<|im_end|>\n{%- endif %}{%- endfor %}"
    "{%- if add_generation_prompt %}<|im_start|>assistant\n{% endif %}")


@pytest.fixture(scope="module")
def tokenizer():
    pytest.importorskip("transformers")
    return chatml_tokenizer()


def chatml_tokenizer():
    from tokenizers import Tokenizer, models, pre_tokenizers, decoders, trainers
    from transformers import PreTrainedTokenizerFast

    raw = Tokenizer(models.BPE())
    raw.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    raw.decoder = decoders.ByteLevel()
    corpus = ["craft planks oak_log tool_call arguments name goal state", "<tool_response> Error: ok mined"] * 50
    raw.train_from_iterator(corpus, trainers.BpeTrainer(vocab_size=400, special_tokens=["<|im_start|>", "<|im_end|>"],
                                                        initial_alphabet=pre_tokenizers.ByteLevel.alphabet()))
    tok = PreTrainedTokenizerFast(tokenizer_object=raw, eos_token="<|im_end|>", pad_token="<|im_end|>")
    tok.chat_template = CHATML
    return tok


def test_finetune_learns_only_the_replies_it_should(tokenizer):
    sample = {"tools": dataset.openai_tools(None)[:2], "messages": [
        {"role": "system", "content": "You play."}, {"role": "user", "content": "Goal: planks"},
        {"role": "assistant", "content": "", "train": False, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "mine", "arguments": {"block": "oak_log"}}}]},
        {"role": "tool", "tool_call_id": "a", "content": "ok: mined"},
        {"role": "user", "content": "State: fine"},
        {"role": "assistant", "content": "Now craft.", "tool_calls": [
            {"id": "b", "type": "function", "function": {"name": "craft", "arguments": "{\"item\": \"oak_planks\"}"}}]},
    ]}
    encoded = finetune.encode(tokenizer, sample, finetune.CHATML_START, finetune.CHATML_END)
    learnt = tokenizer.decode([t for t, l in zip(encoded["input_ids"], encoded["labels"]) if l != -100])
    assert learnt.startswith("Now craft.") and '"arguments": {"item": "oak_planks"}' in learnt
    assert learnt.endswith("<|im_end|>") and "mine" not in learnt and "State" not in learnt


def test_finetune_encodes_exported_samples(game, tokenizer):
    mod, bridge, tmp_path = game
    backend = ScriptedLocalBackend(PLANKS)
    result = Agent(bridge, backend, AgentConfig(data_dir=tmp_path, poll=0.05)).run_goal("make planks")
    sample, = dataset.samples(episodes.load(result.log_path))
    encoded = finetune.encode(tokenizer, sample, finetune.CHATML_START, finetune.CHATML_END)
    assert encoded is not None
    learnt = tokenizer.decode([t for t, l in zip(encoded["input_ids"], encoded["labels"]) if l != -100])
    assert learnt.count("<|im_end|>") == 5 and "goal_done" in learnt and "Goal:" not in learnt


def test_collect_runs_goals_until_enough_are_good(game):
    from minegpt_brain.train.collect import collect, custom_task, make_agent

    mod, bridge, tmp_path = game
    script = []
    for _ in range(2):
        script += [[("set_plan", {"steps": [{"step": "craft planks"}], "goal_check": {"have": {"oak_planks": 4}}})],
                   [("craft", {"item": "oak_planks", "count": 4})], [("goal_done", {"summary": "made planks"})]]
    agent = make_agent(bridge, ScriptedLocalBackend(script), tmp_path, xray=False)
    summary = collect(bridge, agent, [custom_task("make planks", 10)], data_dir=tmp_path, label="test", target=2,
                      max_runs=5, max_tokens=0, rng=random.Random(0), reset=False)
    assert summary["good"] == 2 and summary["runs"] == 2
    records = [json.loads(l) for l in (tmp_path / "results.jsonl").read_text().splitlines()]
    assert all(r["passed"] and r["task"] == "custom" for r in records)
    found = episodes.load_all(tmp_path)
    assert sum(e.good for e in found) == 2
