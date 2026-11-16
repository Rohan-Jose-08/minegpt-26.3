package com.xzier9.minegpt.path;

/**
 * Where a dropped item gets picked up, and how to close in on one. The game picks an item up
 * when its box touches the player's box grown by one block sideways and half a block up and
 * down, so sideways distance and height are checked apart: an item right overhead or down a
 * hole is close in a straight line and still out of reach, and walking at it only circles it.
 */
public final class Pickup {
	/** How far the pickup box reaches beyond the player's own, sideways. */
	public static final double SIDE = 1.0;
	/** And up and down. */
	public static final double VERTICAL = 0.5;
	private static final double PLAYER_HALF_WIDTH = 0.3;
	private static final double PLAYER_HEIGHT = 1.8;
	private static final double ITEM_HALF_WIDTH = 0.125;
	private static final double ITEM_HEIGHT = 0.25;
	/** Kept back from the edge of the box, where server and client may disagree. */
	private static final double MARGIN = 0.1;
	/** Sideways distance, on each axis, inside which an item at the right height is picked up. */
	public static final double SIDE_REACH = SIDE + PLAYER_HALF_WIDTH + ITEM_HALF_WIDTH - MARGIN;
	/** An item's feet may be this far above the player's and still walked up to: one step up. */
	private static final double STEP_UP = 1.25;
	/** Or this far below: down one step. */
	private static final double STEP_DOWN = 1.25;

	public enum Approach {
		/** Inside the pickup box: stand still, the game takes it once its pickup delay is over. */
		WAIT,
		/** Close and about level: walk straight at it. */
		WALK,
		/** Too far, or too far above or below: plan a path to a spot beside it. */
		PATH
	}

	private Pickup() {
	}

	/** Sideways distance: the larger of the gaps along x and z, as the pickup box is square. */
	public static double sideways(double px, double pz, double ix, double iz) {
		return Math.max(Math.abs(ix - px), Math.abs(iz - pz));
	}

	/** True if the item's height overlaps the pickup box of a player whose feet are at {@code py}. */
	public static boolean levelWith(double py, double iy) {
		return iy + ITEM_HEIGHT > py - VERTICAL + MARGIN && iy < py + PLAYER_HEIGHT + VERTICAL - MARGIN;
	}

	/** True if an item at ix, iy, iz is inside the pickup box of a player at px, py, pz (feet). */
	public static boolean inRange(double px, double py, double pz, double ix, double iy, double iz) {
		return sideways(px, pz, ix, iz) <= SIDE_REACH && levelWith(py, iy);
	}

	/**
	 * How to close in on an item.
	 *
	 * @param walkStraight sideways distance inside which walking straight at it is tried
	 */
	public static Approach approach(double px, double py, double pz, double ix, double iy, double iz,
			double walkStraight) {
		if (inRange(px, py, pz, ix, iy, iz)) {
			return Approach.WAIT;
		}
		double dy = iy - py;
		if (sideways(px, pz, ix, iz) <= walkStraight && dy <= STEP_UP && dy >= -STEP_DOWN) {
			return Approach.WALK;
		}
		return Approach.PATH;
	}
}
