package com.xzier9.minegpt.build;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Which way a block ends up turned, worked out without the game: from the family a block
 * belongs to and how the builder asked for it, the block state it must have when it is right.
 *
 * Minecraft turns a block by how the player stands and where they click, not by a setting, so
 * the same request means different things for different blocks. Stairs, doors, beds and gates
 * point the way the player looks; chests and furnaces face back at the player; ladders and
 * wall torches face away from the block they hang on; a log lies along the axis of the face
 * that was clicked; a slab or upside-down stair takes its half from the half of the face.
 * This table is the one place that knowledge lives, so that placing, checking and reporting
 * all agree.
 *
 * Directions are the lower-case names north, south, east, west, up and down; axes are x, y, z.
 */
public final class Turning {
	/** How a kind of block takes its direction when a player places it. */
	public enum Family {
		/** Faces the way the player looks: stairs (they rise that way), doors, beds, fence gates. */
		WITH_LOOK,
		/** Faces back at the player: chests, furnaces, barrels and the like. */
		AGAINST_LOOK,
		/** Faces away from the block it hangs on: ladders, wall torches, wall signs. */
		HANGING,
		/** Lies along an axis: logs, pillars, hay, chains. Upright unless asked otherwise. */
		PILLAR,
		/** Has only a top or bottom half: slabs. */
		SLAB,
		/** Has no direction worth checking. */
		PLAIN
	}

	private Turning() {
	}

	/**
	 * The properties a rightly placed block has, by name: {@code facing}, {@code half} (top or
	 * bottom; for a slab the game calls it {@code type}, and it is given here as half too) and
	 * {@code axis}. Empty when nothing was asked for that this family can show.
	 *
	 * @param look    the way the player was to face, or null
	 * @param top     the half asked for, or null
	 * @param against the side of the spot its supporting block was to be on, or null
	 * @param axis    the axis asked for, or null
	 * @param hasHalf whether this block has a top and bottom half at all (stairs and trapdoors do, doors do not)
	 */
	public static Map<String, String> expected(Family family, String look, Boolean top, String against, String axis,
			boolean hasHalf) {
		Map<String, String> out = new LinkedHashMap<>();
		switch (family) {
			case WITH_LOOK -> {
				if (look != null) {
					out.put("facing", look);
				}
				if (top != null && hasHalf) {
					out.put("half", top ? "top" : "bottom");
				}
			}
			case AGAINST_LOOK -> {
				if (look != null) {
					out.put("facing", opposite(look));
				}
				if (top != null && hasHalf) {
					out.put("half", top ? "top" : "bottom");
				}
			}
			case HANGING -> {
				if (against != null && !against.equals("up") && !against.equals("down")) {
					out.put("facing", opposite(against));
				}
			}
			case PILLAR -> out.put("axis", pillarAxis(against, axis));
			case SLAB -> {
				if (top != null) {
					out.put("half", top ? "top" : "bottom");
				}
			}
			default -> {
			}
		}
		return out;
	}

	/**
	 * The axis a pillar block should lie along: the one asked for, else the axis of the side it
	 * was to be placed against, else upright. Posts, which are most logs in a build, are upright.
	 */
	public static String pillarAxis(String against, String axis) {
		if (axis != null) {
			return axis;
		}
		return against == null ? "y" : axisOf(against);
	}

	/**
	 * What is wrong with a block's state, or null when it is as asked. A double slab passes for
	 * either half, and a property the block does not have is not held against it.
	 *
	 * @param actual the block's own properties by name, with a slab's {@code type} given as {@code half}
	 */
	public static String mismatch(Map<String, String> expected, Map<String, String> actual) {
		StringBuilder wrong = new StringBuilder();
		for (Map.Entry<String, String> e : expected.entrySet()) {
			String is = actual.get(e.getKey());
			if (is == null || is.equals(e.getValue()) || (e.getKey().equals("half") && is.equals("double"))) {
				continue;
			}
			if (wrong.length() > 0) {
				wrong.append(", ");
			}
			wrong.append(e.getKey()).append(' ').append(is).append(" not ").append(e.getValue());
		}
		return wrong.length() == 0 ? null : wrong.toString();
	}

	public static String opposite(String direction) {
		return switch (direction) {
			case "north" -> "south";
			case "south" -> "north";
			case "east" -> "west";
			case "west" -> "east";
			case "up" -> "down";
			case "down" -> "up";
			default -> throw new IllegalArgumentException("unknown direction \"" + direction + "\"");
		};
	}

	public static String axisOf(String direction) {
		return switch (direction) {
			case "north", "south" -> "z";
			case "east", "west" -> "x";
			case "up", "down" -> "y";
			default -> throw new IllegalArgumentException("unknown direction \"" + direction + "\"");
		};
	}
}
