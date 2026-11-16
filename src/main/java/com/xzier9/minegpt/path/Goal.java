package com.xzier9.minegpt.path;

/** Where a path should end. Positions are the block the player's feet are in. */
public interface Goal {
	boolean reached(int x, int y, int z);

	/** Estimated distance in blocks. The search scales it into ticks and heads for the smallest. */
	double distance(int x, int y, int z);

	/** Stand on this exact block. */
	record Block(int x, int y, int z) implements Goal {
		@Override
		public boolean reached(int px, int py, int pz) {
			return px == x && py == y && pz == z;
		}

		@Override
		public double distance(int px, int py, int pz) {
			return Math.sqrt(sq(px - x) + sq(py - y) + sq(pz - z));
		}
	}

	/** Get within {@code radius} blocks of a point. */
	record Near(int x, int y, int z, double radius) implements Goal {
		@Override
		public boolean reached(int px, int py, int pz) {
			return sq(px - x) + sq(py - y) + sq(pz - z) <= radius * radius;
		}

		@Override
		public double distance(int px, int py, int pz) {
			return Math.max(0, Math.sqrt(sq(px - x) + sq(py - y) + sq(pz - z)) - radius);
		}
	}

	/**
	 * Stand where the player picks up an item lying in this block: in it or in a block next to
	 * it, level with it or one below (pickup reaches about a block sideways and half a block up).
	 */
	record Touch(int x, int y, int z) implements Goal {
		@Override
		public boolean reached(int px, int py, int pz) {
			return Math.abs(px - x) <= 1 && Math.abs(pz - z) <= 1 && (py == y || py == y - 1);
		}

		@Override
		public double distance(int px, int py, int pz) {
			double dy = py > y ? py - y : py < y - 1 ? y - 1 - py : 0;
			double dx = Math.max(0, Math.abs(px - x) - 1);
			double dz = Math.max(0, Math.abs(pz - z) - 1);
			return Math.sqrt(dx * dx + dy * dy + dz * dz);
		}
	}

	/** Reach a column, at any height. */
	record XZ(int x, int z) implements Goal {
		@Override
		public boolean reached(int px, int py, int pz) {
			return px == x && pz == z;
		}

		@Override
		public double distance(int px, int py, int pz) {
			return Math.sqrt(sq(px - x) + sq(pz - z));
		}
	}

	/** Get to this height or below, anywhere. Used to dig down. */
	record BelowY(int y) implements Goal {
		/**
		 * Going down usually means digging, which costs several blocks' worth of walking per
		 * block. Counting each block of height as more keeps the search digging down instead of
		 * wandering around the surface looking for a cheaper way.
		 */
		static final double DEPTH_WEIGHT = 4;

		@Override
		public boolean reached(int px, int py, int pz) {
			return py <= y;
		}

		@Override
		public double distance(int px, int py, int pz) {
			return Math.max(0, py - y) * DEPTH_WEIGHT;
		}
	}

	/** Get to this height or above, anywhere. Used to climb out of a mine or up a cliff. */
	record AboveY(int y) implements Goal {
		@Override
		public boolean reached(int px, int py, int pz) {
			return py >= y;
		}

		@Override
		public double distance(int px, int py, int pz) {
			// Weighted like BelowY, so the search climbs where it stands instead of wandering.
			return Math.max(0, y - py) * BelowY.DEPTH_WEIGHT;
		}
	}

	/**
	 * Stand where a block is within arm's reach, measured from the eyes to the block's centre,
	 * without standing in it or on top of it. Used before mining, placing or opening something.
	 */
	record Reach(int x, int y, int z, double reach) implements Goal {
		static final double EYE_HEIGHT = 1.62;

		@Override
		public boolean reached(int px, int py, int pz) {
			if (px == x && pz == z && py >= y - 1 && py <= y + 1) {
				return false;
			}
			double dx = px + 0.5 - (x + 0.5);
			double dy = py + EYE_HEIGHT - (y + 0.5);
			double dz = pz + 0.5 - (z + 0.5);
			return dx * dx + dy * dy + dz * dz <= reach * reach;
		}

		@Override
		public double distance(int px, int py, int pz) {
			double dx = px - x;
			double dy = py + EYE_HEIGHT - 0.5 - y;
			double dz = pz - z;
			return Math.max(0, Math.sqrt(dx * dx + dy * dy + dz * dz) - reach);
		}
	}

	/**
	 * Like {@link Reach}, but standing at least two blocks away sideways, or two or more below, so
	 * the player's body is clear of the block and its faces can be seen. Used when placing fails
	 * from close by.
	 */
	record Apart(int x, int y, int z, double reach) implements Goal {
		@Override
		public boolean reached(int px, int py, int pz) {
			boolean close = Math.max(Math.abs(px - x), Math.abs(pz - z)) < 2 && py > y - 2;
			return !close && new Reach(x, y, z, reach).reached(px, py, pz);
		}

		@Override
		public double distance(int px, int py, int pz) {
			double d = new Reach(x, y, z, reach).distance(px, py, pz);
			return Math.max(Math.abs(px - x), Math.abs(pz - z)) < 2 && py > y - 2 ? d + 1 : d;
		}
	}

	/**
	 * Any one of several goals, whichever is cheapest to get to. One search over this finds the
	 * candidate with the shortest real route, instead of the one nearest in a straight line.
	 */
	record AnyOf(java.util.List<Goal> goals) implements Goal {
		public AnyOf {
			goals = java.util.List.copyOf(goals);
		}

		@Override
		public boolean reached(int px, int py, int pz) {
			return which(px, py, pz) >= 0;
		}

		@Override
		public double distance(int px, int py, int pz) {
			double best = Double.MAX_VALUE;
			for (Goal goal : goals) {
				best = Math.min(best, goal.distance(px, py, pz));
			}
			return goals.isEmpty() ? 0 : best;
		}

		/** The index of the first goal reached from here, or -1. */
		public int which(int px, int py, int pz) {
			for (int i = 0; i < goals.size(); i++) {
				if (goals.get(i).reached(px, py, pz)) {
					return i;
				}
			}
			return -1;
		}
	}

	private static double sq(double v) {
		return v * v;
	}
}
