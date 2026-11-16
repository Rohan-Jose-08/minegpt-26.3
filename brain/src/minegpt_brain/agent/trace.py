"""Episode logs: one JSON object per line for everything that happens while chasing a goal.

These are Phase 6's training data. Each file holds one goal from start to end: the prompts,
every model turn, every tool call with the observation before and after it, reflexes, deaths
and the outcome. Observations are logged without the block grid to keep files small.

Event types: ``start``, ``prompt`` (system prompt, goal message and, since 0.3, the tool list),
``turn``, ``tool``, ``results`` (since 0.3: every tool result sent back, including calls that
were not run), ``skill``, ``plan``, ``reflex``, ``death``, ``note`` (the state note after the
results), ``user`` (any other message to the model) and ``end``. ``minegpt_brain.train``
rebuilds the conversation from these.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional


class EpisodeLog:
    def __init__(self, directory: Path, goal: str):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:40] or "goal"
        stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"
        self.path = directory / f"{stem}.jsonl"
        n = 1
        while self.path.exists():  # the same goal again within a second: one file per episode
            n += 1
            self.path = directory / f"{stem}-{n}.jsonl"
        self._file = self.path.open("x", encoding="utf-8")
        self._start = time.monotonic()

    def write(self, event: str, **fields: Any) -> None:
        record = {"event": event, "t": round(time.monotonic() - self._start, 2), **fields}
        self._file.write(json.dumps(record, default=str) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def compact_obs(data: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """An observation without its block grid."""
    if data is None:
        return None
    return {key: value for key, value in data.items() if key != "grid"}
