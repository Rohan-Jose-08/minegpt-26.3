"""Saved routines: skill sequences that worked, kept for reuse on later goals.

This is the skill library of Voyager, with skill calls in place of code. A routine is a named
list of skill calls. The agent saves one itself when it reaches a goal, and the model can
save its own with ``save_routine``. When a new goal comes in, the routines whose names and
descriptions are most like it (by TF-IDF cosine similarity) are offered to the model.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

_STOP_WORDS = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "get", "make", "some", "me",
               "my", "it", "from", "then", "at", "by", "up", "into", "full", "set"}


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower().replace("_", " "))
    # Plural and singular should match: "logs" and "log", "ingots" and "ingot".
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
            for w in words if w not in _STOP_WORDS]


class RoutineLibrary:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.routines: dict[str, dict[str, Any]] = json.loads(self.path.read_text()).get("routines", {})
        except FileNotFoundError:
            self.routines = {}
        except json.JSONDecodeError:
            self.path.replace(self.path.with_suffix(".broken"))
            self.routines = {}

    def save(self, name: str, description: str, steps: list[dict[str, Any]], source: str = "model") -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("a routine needs a name")
        if not steps:
            raise ValueError("a routine needs at least one step")
        old = self.routines.get(name, {})
        routine = {"name": name, "description": description.strip(), "steps": steps, "source": source,
                   "saved": time.strftime("%Y-%m-%d %H:%M"), "runs": old.get("runs", 0),
                   "successes": old.get("successes", 0)}
        self.routines[name] = routine
        self._write()
        return routine

    def get(self, name: str) -> Optional[dict[str, Any]]:
        if name in self.routines:
            return self.routines[name]
        lowered = name.strip().lower()
        return next((r for n, r in self.routines.items() if n.lower() == lowered), None)

    def record_run(self, name: str, success: bool) -> None:
        routine = self.routines.get(name)
        if routine is not None:
            routine["runs"] = routine.get("runs", 0) + 1
            routine["successes"] = routine.get("successes", 0) + int(success)
            self._write()

    def similar(self, query: str, k: int = 3, min_score: float = 0.1) -> list[tuple[float, dict[str, Any]]]:
        """The ``k`` routines most like ``query``, best first, with their similarity scores."""
        if not self.routines:
            return []
        docs = {name: Counter(tokens(f"{name} {r['description']}")) for name, r in self.routines.items()}
        df = Counter(word for words in docs.values() for word in words)
        n = len(docs)

        def vector(words: Counter) -> dict[str, float]:
            return {w: c * (math.log((1 + n) / (1 + df.get(w, 0))) + 1) for w, c in words.items()}

        q = vector(Counter(tokens(query)))
        q_norm = math.sqrt(sum(v * v for v in q.values()))
        if not q_norm:
            return []
        scored = []
        for name, words in docs.items():
            d = vector(words)
            d_norm = math.sqrt(sum(v * v for v in d.values()))
            if not d_norm:
                continue
            score = sum(q[w] * d.get(w, 0.0) for w in q) / (q_norm * d_norm)
            routine = self.routines[name]
            runs, wins = routine.get("runs", 0), routine.get("successes", 0)
            if runs >= 3 and wins == 0:
                continue  # replayed three times and never worked: stop offering it
            if score >= min_score:
                # Among routines that fit the wording, the ones that replay well come first.
                scored.append((score * (0.5 + 0.5 * (wins + 1) / (runs + 2)), routine))
        scored.sort(key=lambda pair: -pair[0])
        return scored[:k]

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"routines": self.routines}, indent=1))
        tmp.replace(self.path)


def describe_steps(steps: list[dict[str, Any]], limit: int = 12) -> str:
    parts = []
    for step in steps[:limit]:
        args = " ".join(f"{k}={v}" for k, v in step.get("args", {}).items())
        parts.append(f"{step['tool']}({args})")
    if len(steps) > limit:
        parts.append(f"... {len(steps) - limit} more")
    return ", ".join(parts)
