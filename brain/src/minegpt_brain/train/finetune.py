#!/usr/bin/env python
"""Fine-tunes an open model on MineGPT episodes: QLoRA with Unsloth, sized for a free Colab T4.

This file stands alone (it does not import minegpt_brain), so it can be uploaded to Colab
with the dataset. ``python -m minegpt_brain.train export`` puts it in ``minegpt-data.zip``
next to ``train.jsonl`` and ``val.jsonl``. In Colab (GPU runtime)::

    !pip install unsloth
    !python finetune.py                       # Qwen3 4B Instruct, 2 epochs, GGUF q4_k_m at the end
    !python finetune.py --model unsloth/Qwen3-8B --max-seq 12288   # bigger, slower, tighter on a T4

The model learns only the assistant replies (tool calls and any text), not the prompts, tool
results or state notes, and skips replies marked ``"train": false`` (context from an earlier
window). Targets ChatML templates (the Qwen family); for other templates pass
``--assistant-start`` and ``--assistant-end``.

Outputs, under ``--out`` (default ``minegpt``): ``lora/`` (the adapter), and with ``--gguf``
a quantized ``.gguf`` file to run with llama.cpp's ``llama-server --jinja`` or Ollama.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_MODEL = "unsloth/Qwen3-4B-Instruct-2507"
CHATML_START = "<|im_start|>assistant\n"
CHATML_END = "<|im_end|>"


# Pure helpers (tested without a GPU) --------------------------------------------------------

def normalize(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[bool]]:
    """Messages ready for a chat template, and for each assistant message whether to learn it."""
    out, learn = [], []
    for message in messages:
        message = dict(message)
        if message["role"] == "assistant":
            learn.append(message.pop("train", True) is not False)
            calls = []
            for call in message.get("tool_calls") or []:
                function = dict(call["function"])
                if isinstance(function.get("arguments"), str):
                    try:
                        function["arguments"] = json.loads(function["arguments"] or "{}")
                    except json.JSONDecodeError:
                        pass
                calls.append({**call, "function": function})
            if calls:
                message["tool_calls"] = calls
            else:
                message.pop("tool_calls", None)
        out.append(message)
    return out, learn


def assistant_spans(text: str, start: str = CHATML_START, end: str = CHATML_END) -> list[tuple[int, int]]:
    """Character spans of each assistant reply in a rendered conversation, from just after
    ``start`` through ``end`` (so the model learns to stop)."""
    spans = []
    for match in re.finditer(re.escape(start), text):
        begin = match.end()
        close = text.find(end, begin)
        if close < 0:
            spans.append((begin, len(text)))
        else:
            spans.append((begin, close + len(end)))
    return spans


def labels_for(input_ids: list[int], offsets: list[tuple[int, int]], spans: list[tuple[int, int]],
               learn: list[bool]) -> list[int]:
    """Labels for causal LM training: the token id inside learnt spans, -100 elsewhere."""
    chosen = [span for span, keep in zip(spans, learn) if keep]
    labels = [-100] * len(input_ids)
    j = 0
    for k, (token, (s, e)) in enumerate(zip(input_ids, offsets)):
        while j < len(chosen) and chosen[j][1] <= s:
            j += 1
        if j < len(chosen) and e > chosen[j][0] and s < chosen[j][1] and e > s:
            labels[k] = token
    return labels


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def encode(tokenizer: Any, sample: dict[str, Any], start: str, end: str) -> Optional[dict[str, list[int]]]:
    """Tokenizes one sample with its labels, or None if the template did not render it as expected."""
    messages, learn = normalize(sample["messages"])
    text = tokenizer.apply_chat_template(messages, tools=sample.get("tools") or None, tokenize=False)
    spans = assistant_spans(text, start, end)
    if len(spans) != len(learn):
        return None
    encoded = tokenizer(text, return_offsets_mapping=True, add_special_tokens=False)
    ids = list(encoded["input_ids"])
    labels = labels_for(ids, [tuple(o) for o in encoded["offset_mapping"]], spans, learn)
    return {"input_ids": ids, "attention_mask": [1] * len(ids), "labels": labels}


def encode_all(tokenizer: Any, rows: Iterable[dict[str, Any]], max_len: int, start: str, end: str,
               name: str) -> list[dict[str, list[int]]]:
    out, too_long, bad = [], 0, 0
    for row in rows:
        encoded = encode(tokenizer, row, start, end)
        if encoded is None:
            bad += 1
        elif len(encoded["input_ids"]) > max_len:
            too_long += 1
        elif any(label != -100 for label in encoded["labels"]):
            out.append(encoded)
    print(f"{name}: {len(out)} samples" + (f", {too_long} longer than {max_len} tokens left out" if too_long else "")
          + (f", {bad} the chat template did not render as expected (check --assistant-start)" if bad else ""))
    return out


# Training ------------------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--train", type=Path, default=Path("train.jsonl"))
    parser.add_argument("--val", type=Path, default=Path("val.jsonl"))
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"base model on Hugging Face (default {DEFAULT_MODEL})")
    parser.add_argument("--out", type=Path, default=Path("minegpt"))
    parser.add_argument("--max-seq", type=int, default=16384, help="longest sample in tokens (16384)")
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--rank", type=int, default=16, help="LoRA rank (16)")
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--seed", type=int, default=3407)
    parser.add_argument("--gguf", default="q4_k_m", help="GGUF quantization to save at the end, or 'none'")
    parser.add_argument("--assistant-start", default=CHATML_START)
    parser.add_argument("--assistant-end", default=CHATML_END)
    args = parser.parse_args(argv)

    train_rows, val_rows = read_jsonl(args.train), read_jsonl(args.val)
    if not train_rows:
        print(f"No training samples in {args.train}. Export some with: python -m minegpt_brain.train export",
              file=sys.stderr)
        return 1

    from unsloth import FastLanguageModel, is_bfloat16_supported  # import first: it patches transformers
    import torch
    from transformers import Trainer, TrainingArguments

    model, tokenizer = FastLanguageModel.from_pretrained(model_name=args.model, max_seq_length=args.max_seq,
                                                         load_in_4bit=True, dtype=None)
    tokenizer = getattr(tokenizer, "tokenizer", tokenizer)  # some models come with a processor
    model = FastLanguageModel.get_peft_model(
        model, r=args.rank, lora_alpha=args.rank, lora_dropout=0, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth", random_state=args.seed)

    train = encode_all(tokenizer, train_rows, args.max_seq, args.assistant_start, args.assistant_end, "train")
    val = encode_all(tokenizer, val_rows, args.max_seq, args.assistant_start, args.assistant_end, "val")
    if not train:
        print("Nothing left to train on.", file=sys.stderr)
        return 1
    learnt = sum(sum(1 for l in s["labels"] if l != -100) for s in train)
    print(f"Training on {learnt} reply tokens of {sum(len(s['input_ids']) for s in train)} in total")

    pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id

    def collate(batch: list[dict[str, list[int]]]) -> dict[str, Any]:
        width = max(len(b["input_ids"]) for b in batch)
        def padded(key: str, value: int) -> Any:
            return torch.tensor([b[key] + [value] * (width - len(b[key])) for b in batch])
        return {"input_ids": padded("input_ids", pad), "attention_mask": padded("attention_mask", 0),
                "labels": padded("labels", -100)}

    steps_per_epoch = math.ceil(len(train) / (args.batch * args.grad_accum))
    training_args = TrainingArguments(
        output_dir=str(args.out / "checkpoints"), per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=1, gradient_accumulation_steps=args.grad_accum, num_train_epochs=args.epochs,
        learning_rate=args.lr, warmup_steps=max(1, steps_per_epoch // 10), lr_scheduler_type="cosine",
        weight_decay=0.01, optim="adamw_8bit", logging_steps=max(1, steps_per_epoch // 10),
        eval_strategy="epoch" if val else "no", save_strategy="epoch", save_total_limit=1,
        bf16=is_bfloat16_supported(), fp16=not is_bfloat16_supported(), seed=args.seed, report_to="none",
        remove_unused_columns=False)
    trainer = Trainer(model=model, args=training_args, train_dataset=train, eval_dataset=val or None,
                      data_collator=collate)
    trainer.train()
    if val:
        print("Validation:", trainer.evaluate())

    lora = args.out / "lora"
    model.save_pretrained(str(lora))
    tokenizer.save_pretrained(str(lora))
    print(f"Saved the adapter to {lora}")

    _show_one(model, tokenizer, val_rows or train_rows, FastLanguageModel)

    if args.gguf and args.gguf != "none":
        gguf_dir = args.out / "gguf"
        model.save_pretrained_gguf(str(gguf_dir), tokenizer, quantization_method=args.gguf)
        files = sorted(Path(".").glob("**/*.gguf"))
        print("GGUF files:", *[str(f) for f in files], sep="\n  ")
    return 0


def _show_one(model: Any, tokenizer: Any, rows: list[dict[str, Any]], fast: Any) -> None:
    """Prints what the trained model replies to the start of one sample, as a quick sanity check."""
    try:
        fast.for_inference(model)
        row = rows[0]
        messages, _ = normalize(row["messages"][:2])
        prompt = tokenizer.apply_chat_template(messages, tools=row.get("tools") or None, tokenize=False,
                                               add_generation_prompt=True)
        inputs = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to(model.device)
        output = model.generate(**inputs, max_new_tokens=300, do_sample=False)
        print("Goal:", row["messages"][1]["content"].splitlines()[0])
        print("Reply:", tokenizer.decode(output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=False))
    except Exception as e:  # a sanity check only
        print(f"(Skipped the sample reply: {e})")


if __name__ == "__main__":
    sys.exit(main())
