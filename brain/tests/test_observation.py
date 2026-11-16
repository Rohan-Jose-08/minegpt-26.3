from minegpt_brain.observation import Observation
from fake_mod import make_obs


def test_block_at_offsets():
    obs = Observation(make_obs())
    assert obs.block_at(0, -1, 0) == "minecraft:stone"
    assert obs.block_at(1, 0, 0) == "minecraft:oak_log"
    assert obs.block_at(0, 0, 0) == "minecraft:air"
    assert obs.block_at(5, 0, 0) is None


def test_find_blocks_returns_world_positions():
    obs = Observation(make_obs())
    assert obs.find_blocks("oak_log") == [(1, 64, 0)]
    assert obs.find_blocks("diamond_ore") == []


def test_inventory_helpers():
    obs = Observation(make_obs())
    assert obs.count("oak_log") == 8
    assert obs.find_slot("minecraft:oak_log") == 0
    assert obs.find_slot("stick") is None
