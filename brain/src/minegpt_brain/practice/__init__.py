"""Practice: MineGPT improving itself from its own play.

* :mod:`.tuner` tunes the mod's skill settings from how skills do with them.
* :mod:`.failures` files every failure and stall with the blocks round the player, and ranks the
  causes by time lost.
* :mod:`.drills` and :mod:`.replay` run skills without the model: set drills, and failure scenes
  rebuilt in game.
* :mod:`.curriculum` picks what to practise next.
* ``python -m minegpt_brain.practice`` runs a night of practice and reports on it; see
  ``docs/practice.md``.
"""
