package com.xzier9.minegpt.client.act;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.EnumSet;
import java.util.Locale;
import java.util.Set;
import net.minecraft.world.inventory.ContainerInput;

/**
 * A low-level action, the same set of things a human can do with keyboard and mouse.
 * Parsed from JSON so the debug command and the Python bridge share one format
 * (see docs/protocol.md).
 */
public sealed interface Action {
	/** Movement keys that can be held. */
	enum MoveKey {
		FORWARD, BACK, LEFT, RIGHT, JUMP, SNEAK, SPRINT
	}

	/** Hold the given movement keys for a number of ticks. Keys not listed are released. */
	record Move(Set<MoveKey> keys, int ticks) implements Action {
	}

	/** Turn the camera by a relative amount, spread evenly over a number of ticks. */
	record Look(float yaw, float pitch, int ticks) implements Action {
	}

	/** Turn the camera to face a point in the world. */
	record LookAt(double x, double y, double z, int ticks) implements Action {
	}

	/** Left mouse. ticks == 0 is a single click; more holds the button (for mining). */
	record Attack(int ticks) implements Action {
	}

	/** Right mouse. ticks == 0 is a single click; more holds the button (for eating, bows, shields). */
	record Use(int ticks) implements Action {
	}

	record Hotbar(int slot) implements Action {
	}

	/** Click a slot in the open container (or the player inventory). */
	record Click(int slot, int button, ContainerInput mode) implements Action {
	}

	record Drop(boolean wholeStack) implements Action {
	}

	record OpenInventory() implements Action {
	}

	record CloseScreen() implements Action {
	}

	/** Release every key and cancel queued actions. */
	record Stop() implements Action {
	}

	static Action fromJson(JsonObject json) {
		String type = string(json, "type", null);
		if (type == null) {
			throw new IllegalArgumentException("action needs a \"type\"");
		}
		return switch (type) {
			case "move" -> new Move(moveKeys(json), ticks(json, 1));
			case "look" -> new Look((float) number(json, "yaw", 0), (float) number(json, "pitch", 0), ticks(json, 1));
			case "look_at" -> new LookAt(number(json, "x", 0), number(json, "y", 0), number(json, "z", 0), ticks(json, 1));
			case "attack" -> new Attack(ticks(json, 0));
			case "use" -> new Use(ticks(json, 0));
			case "hotbar" -> new Hotbar(range(json, "slot", 0, 8));
			case "click" -> new Click(
					(int) number(json, "slot", 0),
					(int) number(json, "button", 0),
					ContainerInput.valueOf(string(json, "mode", "PICKUP").toUpperCase(Locale.ROOT)));
			case "drop" -> new Drop(json.has("all") && json.get("all").getAsBoolean());
			case "open_inventory" -> new OpenInventory();
			case "close_screen" -> new CloseScreen();
			case "stop" -> new Stop();
			default -> throw new IllegalArgumentException("unknown action type \"" + type + "\"");
		};
	}

	private static Set<MoveKey> moveKeys(JsonObject json) {
		Set<MoveKey> keys = EnumSet.noneOf(MoveKey.class);
		JsonElement element = json.get("keys");
		if (element instanceof JsonArray array) {
			for (JsonElement key : array) {
				keys.add(MoveKey.valueOf(key.getAsString().toUpperCase(Locale.ROOT)));
			}
		}
		return keys;
	}

	private static int ticks(JsonObject json, int fallback) {
		return Math.max(0, Math.min(20 * 60, (int) number(json, "ticks", fallback)));
	}

	private static int range(JsonObject json, String key, int min, int max) {
		int value = (int) number(json, key, min);
		if (value < min || value > max) {
			throw new IllegalArgumentException(key + " must be between " + min + " and " + max);
		}
		return value;
	}

	private static double number(JsonObject json, String key, double fallback) {
		return json.has(key) ? json.get(key).getAsDouble() : fallback;
	}

	private static String string(JsonObject json, String key, String fallback) {
		return json.has(key) ? json.get(key).getAsString() : fallback;
	}
}
