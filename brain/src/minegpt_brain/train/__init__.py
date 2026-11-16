"""Phase 6a: training MineGPT's own planner model on the agent's episodes.

The pipeline, end to end (see ``docs/training.md``):

1. ``collect``: the teacher (Claude through the API) plays a curriculum of benchmark goals in a
   throwaway world; every goal is an episode log.
2. ``export``: episodes the game confirmed become chat samples (``train.jsonl``, ``val.jsonl``)
   in the exact format the local backend sends, bundled with ``finetune.py`` in a zip.
3. ``finetune.py`` on a free Colab GPU: QLoRA on an open model (Qwen3 4B Instruct by default),
   saved as a GGUF file.
4. Serve the GGUF with llama.cpp or Ollama and play with ``--backend local``; score it with the
   benchmark next to Claude's results.
"""
