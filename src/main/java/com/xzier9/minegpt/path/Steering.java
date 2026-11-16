package com.xzier9.minegpt.path;

/**
 * Small pieces of control smoothing, so that movement driven tick by tick looks like a player's
 * and not a machine's: a camera that speeds up and eases off, and a jump key that is not
 * tapped at every brush against a wall.
 */
public final class Steering {
	private Steering() {
	}

	/**
	 * This tick's turn towards an angle {@code left} degrees away: a share of what is left, no
	 * faster than {@code max}, and no more than {@code acceleration} faster than last tick. It
	 * never turns further than what is left, so it cannot overshoot and swing back.
	 *
	 * @param last   the turn made last tick
	 * @param ease   share of the angle left covered per tick once the limits no longer bind
	 * @param settle closer than this it turns the rest in one go, so aim ends up exact
	 */
	public static float turnStep(float left, float last, float max, float acceleration, float ease, float settle) {
		if (Math.abs(left) <= settle) {
			return left;
		}
		float want = Math.max(-max, Math.min(max, left * ease));
		float limit = (Math.signum(last) == Math.signum(want) ? Math.abs(last) : 0) + acceleration;
		if (Math.abs(want) > limit) {
			want = Math.signum(want) * limit;
		}
		return want;
	}

	/**
	 * Counts how long something has been true without a break: {@code update} returns true once
	 * it has held for the given number of ticks in a row. Used so one tick of brushing a wall is
	 * not answered with a jump.
	 */
	public static final class Sustained {
		private final int ticks;
		private int held;

		public Sustained(int ticks) {
			this.ticks = ticks;
		}

		public boolean update(boolean now) {
			held = now ? held + 1 : 0;
			return held >= ticks;
		}
	}
}
