"""Command line entry point: ``python -m minegpt_brain.agent``.

Connects to the game, then works on each goal the player sets in game with
``/minegpt goal <text>``, or on ``--goal`` given here. With no goal to work on it runs the
world's recurring jobs (``/minegpt job``). Stop it with Ctrl+C.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from ..bridge import DEFAULT_HOST, DEFAULT_PORT, Bridge
from ..llm import BackendError, make_backend
from .loop import Agent, AgentConfig, default_data_dir


def add_backend_args(parser: argparse.ArgumentParser) -> None:
    """The options that choose and tune the model, shared with the benchmark runner."""
    env = os.environ.get
    parser.add_argument("--backend", choices=["claude", "local"], default=env("MINEGPT_BACKEND", "claude"),
                        help="claude (the Claude API, the default) or local (an OpenAI-style server like Ollama)")
    parser.add_argument("--model", default=env("MINEGPT_MODEL"),
                        help="model name; claude defaults to claude-opus-5-5, local needs one, e.g. qwen3:14b")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=env("MINEGPT_EFFORT"),
                        help="claude only: how hard the model thinks each turn (default medium)")
    parser.add_argument("--no-fallbacks", action="store_true",
                        help="claude only: turn off retrying declined requests on a fallback model")
    parser.add_argument("--base-url", default=env("MINEGPT_BASE_URL"),
                        help="local only: server address (default http://localhost:11434/v1, Ollama)")
    parser.add_argument("--context-chars", type=int, default=_int(env("MINEGPT_CONTEXT_CHARS")),
                        help="local only: characters of history to send before leaving out old turns "
                             "(default 40000, about 10k tokens; 0 sends everything)")


def _int(value):
    return int(value) if value else None


def backend_from_args(args: argparse.Namespace):
    """Raises BackendError if the backend cannot be set up (for example, no API key)."""
    return make_backend(args.backend, args.model, effort=args.effort, base_url=args.base_url,
                        fallbacks=not args.no_fallbacks, context_chars=args.context_chars)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m minegpt_brain.agent", description=__doc__.splitlines()[0])
    parser.add_argument("--goal", help="set this goal in game and start on it")
    parser.add_argument("--once", action="store_true", help="exit after one goal instead of waiting for more")
    add_backend_args(parser)
    parser.add_argument("--max-turns", type=int, default=80, help="model turns per goal before giving up (80)")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help=f"where memory, routines and episode logs go (default {default_data_dir()})")
    parser.add_argument("--no-reflexes", action="store_true", help="do not eat or fight between turns on its own")
    parser.add_argument("--no-jobs", action="store_true", help="do not run recurring jobs set with /minegpt job")
    parser.add_argument("--no-idle", action="store_true",
                        help="between jobs, do nothing instead of fighting, eating, sleeping and going home")
    parser.add_argument("--no-tuning", action="store_true",
                        help="use the mod's default skill settings, not the ones practice tuned")
    parser.add_argument("--xray", action="store_true",
                        help="let mine target blocks a player could not see; only for singleplayer or servers "
                             "that allow it")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    if not args.verbose:
        # The HTTP client logs every request at INFO.
        logging.getLogger("httpx").setLevel(logging.WARNING)
    log = logging.getLogger("minegpt.agent")

    try:
        backend = backend_from_args(args)
    except BackendError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.backend == "claude":
        log.info("Model %s, effort %s, refusal fallbacks %s", backend.model, backend.effort,
                 "on" if backend.fallbacks else "off")
    else:
        log.info("Model %s at %s", backend.model, backend.base_url)

    config = AgentConfig(max_turns=args.max_turns, reflexes=not args.no_reflexes, xray=args.xray,
                         jobs=not args.no_jobs, idle=not args.no_idle, tune=not args.no_tuning)
    if args.data_dir is not None:
        config.data_dir = args.data_dir
    try:
        bridge = Bridge.connect(args.host, args.port)
    except (ConnectionRefusedError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    with bridge:
        log.info("Connected to MineGPT %s", bridge.info.get("version", "?"))
        agent = Agent(bridge, backend, config)
        if args.goal:
            bridge.set_goal(args.goal)
        try:
            result = agent.run_forever(once=args.once)
        except KeyboardInterrupt:
            log.info("Stopped")
            return 130
        except ConnectionError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    return 0 if result is None or result.outcome == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
