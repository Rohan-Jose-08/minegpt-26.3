"""Keeping a chat history inside a small model's context window.

Long goals (iron armour, diamonds) can run for a hundred turns, far more than a local model's
context holds. :func:`fit_history` leaves out the oldest turns once the history grows past a
budget, keeping the system prompt, the goal message and the newest turns. It drops whole turns
(a model reply with its tool results and state note) in steps of :data:`DROP_STEP`, so the
window only moves now and then: the server's prompt cache keeps hitting between moves, and
training data cut with the same function (``minegpt_brain.train``) shows the model exactly the
windows it will see when it plays.

Messages are OpenAI-style dicts (``role`` and ``content``, plus ``tool_calls`` on replies).
"""

from __future__ import annotations

import json
from typing import Any, Sequence

#: Characters of history (not counting the tool list) a local model gets by default, about 10k tokens.
DEFAULT_BUDGET = 40_000
#: Turns dropped at a time.
DROP_STEP = 8

TRIMMED_NOTE = ("({n} earlier turns are left out to save room. The plan and the latest state note are up to "
                "date; use look_around if you need to check something.)")


def size(message: dict[str, Any]) -> int:
    """A message's length in characters, roughly what it costs in context."""
    total = len(message.get("content") or "")
    for call in message.get("tool_calls") or ():
        function = call.get("function", {})
        arguments = function.get("arguments", "")
        total += len(function.get("name", "")) + len(arguments if isinstance(arguments, str) else json.dumps(arguments))
    return total


def turn_groups(messages: Sequence[dict[str, Any]], start: int = 2) -> list[list[dict[str, Any]]]:
    """Splits ``messages[start:]`` into turns: each starts with an assistant message and runs to the next."""
    groups: list[list[dict[str, Any]]] = []
    for message in messages[start:]:
        if message["role"] == "assistant" or not groups:
            groups.append([message])
        else:
            groups[-1].append(message)
    return groups


def dropped_turns(messages: Sequence[dict[str, Any]], budget: int = DEFAULT_BUDGET, step: int = DROP_STEP) -> int:
    """How many of the oldest turns :func:`fit_history` leaves out of this history."""
    if budget <= 0 or len(messages) <= 2:
        return 0
    head = sum(size(m) for m in messages[:2])
    sizes = [sum(size(m) for m in group) for group in turn_groups(messages)]
    total = head + sum(sizes)
    dropped = 0
    # Always keep at least the newest turn, even if it alone is over budget.
    while total > budget and dropped + 1 < len(sizes):
        chunk = min(step, len(sizes) - 1 - dropped)
        total -= sum(sizes[dropped:dropped + chunk])
        dropped += chunk
    return dropped


def fit_history(messages: Sequence[dict[str, Any]], budget: int = DEFAULT_BUDGET,
                step: int = DROP_STEP) -> list[dict[str, Any]]:
    """The messages to send: the first two (system prompt and goal), a note if turns were left
    out, then the newest turns. Returns the history unchanged when it fits."""
    dropped = dropped_turns(messages, budget, step)
    if not dropped:
        return list(messages)
    kept = [m for group in turn_groups(messages)[dropped:] for m in group]
    return [*messages[:2], {"role": "user", "content": TRIMMED_NOTE.format(n=dropped)}, *kept]
