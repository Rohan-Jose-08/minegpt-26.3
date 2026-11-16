package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The skill settings the brain may tune: limits and counts that used to be fixed numbers in
 * the skills, such as how long goto waits before calling itself stuck or how many blocks mine
 * breaks before picking up the drops.
 *
 * Each setting has a default and a range. The brain sends values with each skill call (the
 * {@code tuning} field of a {@code skill} request); they stay in force until the next call
 * sends others, so skills started from in-game commands use what the brain last chose. Values
 * outside the range are clamped, and unknown names are ignored. The brain's tuner picks the
 * values from how skills did with them (see {@code minegpt_brain.practice.tuner}).
 */
public final class Tuning {
	/** One setting. Skills read it with {@link #i()} or {@link #d()} each time they need it. */
	public record Knob(String name, double base, double min, double max, boolean whole, String about) {
		public double d() {
			Double value = OVERRIDES.get(name);
			return value == null ? base : Math.max(min, Math.min(max, value));
		}

		public int i() {
			return (int) Math.round(d());
		}
	}

	private static final Map<String, Knob> KNOBS = new LinkedHashMap<>();
	private static volatile Map<String, Double> OVERRIDES = Map.of();

	// goto ---------------------------------------------------------------------------------------
	public static final Knob GOTO_STILL_TICKS = whole("goto.still_ticks", 100, 40, 200,
			"ticks without moving before goto calls itself stuck and replans");
	public static final Knob GOTO_LOITER_TICKS = whole("goto.loiter_ticks", 240, 120, 480,
			"ticks spent within a few blocks of one spot before goto gives up there");
	public static final Knob GOTO_WALK_STEP_TICKS = whole("goto.walk_step_ticks", 60, 30, 120,
			"ticks one walking step may take before it counts as failed");
	public static final Knob GOTO_PLACE_STEP_TICKS = whole("goto.place_step_ticks", 100, 40, 200,
			"ticks one pillar or bridge step may take before it counts as failed");
	public static final Knob GOTO_IDLE_JUMPS = whole("goto.idle_jumps", 3, 2, 6,
			"jumps with no block placed before a pillar spot is given up");
	public static final Knob GOTO_BRIDGE_WAIT_TICKS = whole("goto.bridge_wait_ticks", 40, 20, 80,
			"ticks waited for a bridge click before the spot is given up");
	public static final Knob GOTO_DIG_AFTER_FAILURES = whole("goto.dig_after_failures", 3, 1, 6,
			"failed steps before goto may dig through instead of walking round");
	public static final Knob GOTO_SUBMERGED_TICKS = whole("goto.submerged_ticks", 15, 5, 40,
			"ticks under water before goto swims up instead of following the path");
	public static final Knob GOTO_STUCK_PENALTY = number("goto.stuck_penalty", 60, 20, 200,
			"extra path cost put on a cell where the player got stuck");
	public static final Knob GOTO_UNSTICK_TICKS = whole("goto.unstick_ticks", 8, 4, 16,
			"ticks spent backing off after getting stuck");
	// mine ---------------------------------------------------------------------------------------
	public static final Knob MINE_BATCH = whole("mine.batch", 12, 4, 24,
			"blocks broken from where the player stands before picking up the drops");
	public static final Knob MINE_DROP_SETTLE_TICKS = whole("mine.drop_settle_ticks", 12, 4, 30,
			"ticks waited for drops to land before collecting them");
	public static final Knob MINE_PICK_NODES = whole("mine.pick_nodes", 4000, 1000, 8000,
			"path search size when choosing which block to mine next");
	public static final Knob MINE_SECONDS_PER_BLOCK = number("mine.seconds_per_block", 4, 2, 8,
			"time allowed per wanted block before mine times out (at least 300 s in all)");
	// collect ------------------------------------------------------------------------------------
	public static final Knob COLLECT_STILL_TICKS = whole("collect.still_ticks", 25, 10, 60,
			"ticks without moving before collect gives up on an item");
	public static final Knob COLLECT_TRIPS = whole("collect.trips", 2, 1, 4,
			"walks towards one item before it is given up");
	// place and build ----------------------------------------------------------------------------
	public static final Knob PLACE_SETTLED_DEGREES = number("place.settled_degrees", 1.5, 0.5, 3,
			"how still the crosshair must be on the face before clicking");
	public static final Knob PLACE_TRIES = whole("place.tries", 3, 2, 5,
			"clicks on one spot before place gives up");
	public static final Knob BUILD_UNCOVER_TRIES = whole("build.uncover_tries", 5, 2, 8,
			"tries at clearing what hides a block before build skips it");
	public static final Knob BUILD_VIEW_MOVES = whole("build.view_moves", 2, 0, 4,
			"moves to a better standing spot for a block that cannot be seen");

	private Tuning() {
	}

	private static Knob whole(String name, double base, double min, double max, String about) {
		return add(new Knob(name, base, min, max, true, about));
	}

	private static Knob number(String name, double base, double min, double max, String about) {
		return add(new Knob(name, base, min, max, false, about));
	}

	private static Knob add(Knob knob) {
		KNOBS.put(knob.name(), knob);
		return knob;
	}

	/** Every setting, in a fixed order. */
	public static List<Knob> knobs() {
		return Collections.unmodifiableList(new ArrayList<>(KNOBS.values()));
	}

	/**
	 * Puts these values in force, replacing the last ones; null or empty goes back to the defaults.
	 * Returns the names that were not known, so the caller can mention them.
	 */
	public static List<String> apply(JsonObject values) {
		List<String> unknown = new ArrayList<>();
		Map<String, Double> next = new HashMap<>();
		if (values != null) {
			for (Map.Entry<String, JsonElement> entry : values.entrySet()) {
				Knob knob = KNOBS.get(entry.getKey());
				if (knob == null || !entry.getValue().isJsonPrimitive() || !entry.getValue().getAsJsonPrimitive().isNumber()) {
					unknown.add(entry.getKey());
					continue;
				}
				next.put(knob.name(), entry.getValue().getAsDouble());
			}
		}
		OVERRIDES = Map.copyOf(next);
		return unknown;
	}

	/** The settings with their ranges and the values in force, for the {@code tuning} bridge op. */
	public static JsonArray describe() {
		JsonArray out = new JsonArray();
		for (Knob knob : KNOBS.values()) {
			JsonObject o = new JsonObject();
			o.addProperty("name", knob.name());
			o.addProperty("default", knob.base());
			o.addProperty("min", knob.min());
			o.addProperty("max", knob.max());
			o.addProperty("whole", knob.whole());
			o.addProperty("value", knob.d());
			o.addProperty("about", knob.about());
			out.add(o);
		}
		return out;
	}
}
