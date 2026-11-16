"""Gets an iron pickaxe from scratch using the skill library, the way a player would:

    logs -> planks -> crafting table and sticks -> wooden pickaxe -> cobblestone
    -> stone pickaxe and furnace -> dig down -> iron ore -> iron ingots -> iron pickaxe

Start in survival near some trees, then run:

    python examples/iron_pickaxe.py

It prints each step and shows it in the game's HUD. Press F8 in game to pause, or type
/minegpt stop to cancel the running step. It digs a shaft down to y=16 and tunnels there,
so expect a hole in the ground.
"""

import sys

from minegpt_brain.bridge import Bridge
from minegpt_brain.skills import SkillFailed, Skills

LOGS = "#minecraft:logs"
PLANKS = "#minecraft:planks"
# Stone drops cobblestone and deepslate drops cobbled deepslate; both make tools and furnaces.
STONE = ["minecraft:stone", "minecraft:cobblestone", "minecraft:deepslate", "minecraft:cobbled_deepslate"]
COBBLE = ("minecraft:cobblestone", "minecraft:cobbled_deepslate", "minecraft:blackstone")
IRON_ORE = "#minecraft:iron_ores"
IRON_LEVEL = 16


def count(mc: Bridge, *item_ids: str) -> int:
    obs = mc.observe(grid=False)
    return sum(obs.count(item_id) for item_id in item_ids)


def count_tag(mc: Bridge, suffix: str) -> int:
    """Counts items whose id ends with a suffix, such as every kind of log."""
    obs = mc.observe(grid=False)
    return sum(s["count"] for s in obs.inventory if s["id"].endswith(suffix))


def look_after_yourself(mc: Bridge) -> None:
    """Eats when hungry and fights back when hurt, between steps. Failures here are fine."""
    skills = Skills(mc)
    obs = mc.observe(grid=False)
    if obs.food <= 14:
        skills.eat()
    if obs.health < 14 and any(e.get("hostile") and e["distance"] < 8 for e in obs.entities):
        skills.fight(radius=8)


def main() -> int:
    with Bridge.connect() as mc:
        skills = Skills(mc, raise_on_failure=True)

        def step(text: str) -> None:
            print(f"-> {text}")
            mc.set_status(text)
            look_after_yourself(mc)

        try:
            if count(mc, "minecraft:iron_pickaxe"):
                print("Already have an iron pickaxe")
                return 0

            # Wood: a table (4 planks), 8 sticks (4), a wooden pickaxe (3), fuel (2), and enough
            # spare for a second table down in the mine.
            logs = count_tag(mc, "_log") + count_tag(mc, "_stem")
            if logs < 7:
                step("Chopping trees")
                skills.mine(LOGS, count=7 - logs)
            step("Crafting planks")
            logs = count_tag(mc, "_log") + count_tag(mc, "_stem")
            skills.craft(PLANKS, count=4 * logs)
            step("Crafting a crafting table and sticks")
            skills.craft("minecraft:crafting_table")
            skills.craft("minecraft:stick", count=8)
            step("Crafting a wooden pickaxe")
            skills.craft("minecraft:wooden_pickaxe")

            # Stone: 3 for the pickaxe, 8 for the furnace.
            skills.equip(tool="pickaxe")
            if count(mc, *COBBLE) < 11:
                step("Mining stone")
                found = Skills(mc).mine(STONE, count=11 - count(mc, *COBBLE), radius=24, explore=1)
                if not found and count(mc, *COBBLE) < 11:
                    # No stone in sight: dig a few blocks down, where there always is some.
                    step("Digging down to stone")
                    skills.goto(y=int(mc.observe(grid=False).pos[1]) - 5, allow_break=True, timeout=300)
                    if count(mc, *COBBLE) < 11:
                        skills.mine(STONE, count=11 - count(mc, *COBBLE), timeout=600)
            step("Crafting a stone pickaxe and a furnace")
            skills.craft("minecraft:stone_pickaxe")
            skills.craft("minecraft:furnace")

            # Iron is common around y=16. Dig down there, then look for ore, tunnelling if none
            # is in sight; the tunnel walls show new ore as it goes.
            skills.equip(tool="pickaxe")
            obs = mc.observe(grid=False)
            if obs.pos[1] > IRON_LEVEL + 4:
                step(f"Digging down to y={IRON_LEVEL}")
                skills.goto(y=IRON_LEVEL, allow_break=True, timeout=900)
            raw = count(mc, "minecraft:raw_iron")
            if raw < 3:
                step("Mining iron ore")
                skills.mine(IRON_ORE, count=3 - raw, explore=12, timeout=900)

            step("Smelting iron")
            skills.smelt("minecraft:raw_iron", count=3)
            step("Crafting an iron pickaxe")
            if count(mc, "minecraft:stick") < 2:
                skills.craft("minecraft:stick", count=2)
            skills.craft("minecraft:iron_pickaxe")
            skills.equip(tool="pickaxe")
        except SkillFailed as e:
            print(f"Stopped: {e}")
            mc.set_status(f"Stopped: {e.result.message}")
            return 1

        print("Got an iron pickaxe")
        mc.finish_goal("Got an iron pickaxe")
        return 0


if __name__ == "__main__":
    sys.exit(main())
