import pytest

pytest.importorskip("gymnasium")

from minegpt_brain.bridge import Bridge  # noqa: E402
from minegpt_brain.env import MineGPTEnv  # noqa: E402
from fake_mod import FakeMod  # noqa: E402


@pytest.fixture
def env():
    mod = FakeMod()
    e = MineGPTEnv(bridge=Bridge.connect(port=mod.port, timeout=5), frame_skip=4, max_steps=3)
    yield e
    e.close()


def test_spaces_match_observations(env):
    obs, _ = env.reset()
    assert env.observation_space.contains(obs)
    assert obs["grid"].shape == (3, 3, 3)


def test_step_and_truncation(env):
    env.reset()
    for i in range(3):
        obs, reward, terminated, truncated, info = env.step(env.action_space.sample())
    assert env.observation_space.contains(obs)
    assert truncated and not terminated


def test_decode_actions(env):
    actions = env.decode([1, 2, 1, 0, 1, 1, 0, 5, 5, 3])
    assert actions[0] == {"type": "move", "keys": ["forward", "right", "jump", "sprint"], "ticks": 4}
    assert {"type": "attack", "ticks": 4} in actions
    assert not any(a["type"] == "look" for a in actions)
    assert {"type": "hotbar", "slot": 2} in actions
