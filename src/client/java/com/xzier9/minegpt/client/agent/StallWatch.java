package com.xzier9.minegpt.client.agent;

import com.google.gson.JsonObject;
import com.xzier9.minegpt.client.skill.Skill;
import java.util.Set;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.Vec3;

/**
 * Notices when a running skill makes no progress: the player has not moved a block and the
 * inventory has not changed for {@link #STALL_TICKS}. That is how time goes in a jump loop or a
 * hole even when the skill gets out in the end, so the brain hears of it as it happens: a
 * {@code stall} event with a {@link Scene} when the stall starts, and a {@code stall_end} event
 * with how long it lasted when the player moves again, the inventory changes or the skill ends.
 *
 * Skills that stand still by nature (crafting, smelting, eating, sleeping, fighting, chests,
 * farming) are not watched.
 */
public final class StallWatch {
	/** No progress for this long counts as a stall: 15 seconds. */
	public static final int STALL_TICKS = 300;
	private static final Set<String> STILL_SKILLS = Set.of("craft", "smelt", "eat", "sleep", "equip", "store", "take",
			"fight", "farm");

	private Skill watched;
	private Vec3 anchor;
	private int inventory;
	private int ticks;
	private boolean stalled;

	/** Call once a tick with the running skill (null when none, or while paused). Returns an event to send, or null. */
	public JsonObject tick(Minecraft mc, Skill skill) {
		LocalPlayer player = mc.player;
		if (skill != watched || player == null) {
			JsonObject end = ended();
			watched = player == null ? null : skill;
			restart(player);
			return end;
		}
		if (skill == null || STILL_SKILLS.contains(skill.name())) {
			return null;
		}
		int now = signature(player.getInventory());
		if (player.position().distanceTo(anchor) >= 1.0 || now != inventory) {
			JsonObject end = ended();
			restart(player);
			return end;
		}
		if (++ticks >= STALL_TICKS && !stalled) {
			stalled = true;
			JsonObject event = event("stall");
			event.addProperty("status", skill.status());
			event.addProperty("seconds", ticks / 20.0);
			JsonObject scene = Scene.capture(mc);
			if (scene != null) {
				event.add("scene", scene);
			}
			return event;
		}
		return null;
	}

	private JsonObject ended() {
		if (!stalled || watched == null) {
			return null;
		}
		JsonObject event = event("stall_end");
		event.addProperty("seconds", ticks / 20.0);
		return event;
	}

	private JsonObject event(String name) {
		JsonObject event = new JsonObject();
		event.addProperty("op", "event");
		event.addProperty("event", name);
		event.addProperty("skill", watched.name());
		return event;
	}

	private void restart(LocalPlayer player) {
		stalled = false;
		ticks = 0;
		if (player != null) {
			anchor = player.position();
			inventory = signature(player.getInventory());
		}
	}

	private static int signature(Inventory inventory) {
		int hash = 1;
		for (int i = 0; i < inventory.getContainerSize(); i++) {
			ItemStack stack = inventory.getItem(i);
			hash = 31 * hash + (stack.isEmpty() ? 0 : stack.getItem().hashCode() * 64 + stack.getCount());
		}
		return hash;
	}
}
