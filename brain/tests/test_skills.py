import pytest

from minegpt_brain.bridge import Bridge
from minegpt_brain.skills import SkillFailed, Skills
from fake_mod import FakeMod


@pytest.fixture
def mod_and_skills():
    mod = FakeMod()
    bridge = Bridge.connect(port=mod.port, timeout=1)
    yield mod, Skills(bridge)
    bridge.close()


def test_skill_result_comes_back(mod_and_skills):
    mod, skills = mod_and_skills
    result = skills.mine("#minecraft:logs", count=3)
    assert result
    assert result.skill == "mine"
    assert result.data == {"broken": 3}
    sent = mod.received[-1]
    assert sent["op"] == "skill" and sent["name"] == "mine"
    assert sent["args"] == {"block": "#minecraft:logs", "count": 3, "hidden": False}


def test_arguments_use_the_mods_names_and_skip_unset_ones(mod_and_skills):
    mod, skills = mod_and_skills
    skills.goto(y=16, allow_break=True, timeout=600)
    args = mod.received[-1]["args"]
    assert args == {"y": 16, "allowBreak": True, "timeout": 600}


def test_failure_is_falsy_or_raises(mod_and_skills):
    mod, skills = mod_and_skills
    result = skills.fight()
    assert not result
    assert "health" in result.message
    strict = Skills(skills.bridge, raise_on_failure=True)
    with pytest.raises(SkillFailed, match="fight failed"):
        strict.fight()


def test_long_skills_outlast_the_normal_reply_timeout(mod_and_skills):
    mod, skills = mod_and_skills
    mod.skill_delay = 1.5  # longer than the bridge's 1 s timeout for ordinary requests
    assert skills.craft("minecraft:stick", count=4)


def test_recipes_and_cancel(mod_and_skills):
    mod, skills = mod_and_skills
    recipes = skills.recipes("minecraft:stick")
    assert recipes[0]["count"] == 4
    assert skills.recipes("minecraft:diamond") == []
    skills.cancel()
    assert mod.received[-1]["op"] == "cancel_skill"
