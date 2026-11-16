"""MineGPT's goal-driven agent (Phase 4): a language model plans, skills act, the game checks.

Run it with ``python -m minegpt_brain.agent`` and set goals in game with ``/minegpt goal``.
"""

from .loop import Agent, AgentConfig, EpisodeResult

__all__ = ["Agent", "AgentConfig", "EpisodeResult"]
