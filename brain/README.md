# MineGPT brain

Python side of MineGPT. In later phases this holds the bridge client, the Gymnasium
environment, the planner, the skill library and training code.

## Setup

```sh
cd brain
python -m venv .venv
.venv\Scripts\activate        # Windows (use `source .venv/bin/activate` elsewhere)
pip install -e ".[dev]"
pytest
```
