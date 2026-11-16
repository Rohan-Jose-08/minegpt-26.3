package com.xzier9.minegpt.path;

/**
 * Notices a player that has stopped getting anywhere: either its block position has not
 * changed for a while, or it keeps moving but never leaves a small area, as when it circles
 * something it cannot get to. Fed the block position once per tick of trying to move.
 */
public final class StuckWatch {
	public enum State {
		MOVING,
		/** The block position has not changed for the still limit. */
		STILL,
		/** It has stayed within the radius of one spot for the loiter limit. */
		LOITERING
	}

	private final int stillLimit;
	private final int loiterLimit;
	private final double radiusSq;
	private boolean started;
	private int lastX;
	private int lastY;
	private int lastZ;
	private int anchorX;
	private int anchorY;
	private int anchorZ;
	private int stillTicks;
	private int loiterTicks;

	/**
	 * @param stillLimit   ticks on one block before it counts as stuck
	 * @param loiterLimit  ticks within {@code radius} blocks of one spot before it counts as going
	 *                     round in circles; should be the larger of the two
	 */
	public StuckWatch(int stillLimit, int loiterLimit, double radius) {
		this.stillLimit = stillLimit;
		this.loiterLimit = loiterLimit;
		this.radiusSq = radius * radius;
	}

	/** Counts one tick at this block position. */
	public State tick(int x, int y, int z) {
		if (!started) {
			started = true;
			lastX = anchorX = x;
			lastY = anchorY = y;
			lastZ = anchorZ = z;
		}
		if (x != lastX || y != lastY || z != lastZ) {
			lastX = x;
			lastY = y;
			lastZ = z;
			stillTicks = 0;
		} else {
			stillTicks++;
		}
		double dx = x - anchorX;
		double dy = y - anchorY;
		double dz = z - anchorZ;
		if (dx * dx + dy * dy + dz * dz > radiusSq) {
			anchorX = x;
			anchorY = y;
			anchorZ = z;
			loiterTicks = 0;
		} else {
			loiterTicks++;
		}
		if (loiterTicks >= loiterLimit) {
			return State.LOITERING;
		}
		return stillTicks >= stillLimit ? State.STILL : State.MOVING;
	}

	/** Starts over from wherever the next tick is, as after real progress of another kind. */
	public void reset() {
		started = false;
		stillTicks = 0;
		loiterTicks = 0;
	}

	/** Forgives standing still so far, but not staying in one area. */
	public void moved() {
		stillTicks = 0;
	}

	public int stillTicks() {
		return stillTicks;
	}

	public int loiterTicks() {
		return loiterTicks;
	}
}
