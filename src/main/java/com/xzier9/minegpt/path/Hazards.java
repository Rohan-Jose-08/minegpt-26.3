package com.xzier9.minegpt.path;

/**
 * What a player standing in a cell could slip or step into from there: the check behind the
 * pathfinder's edge costs, and behind the walker sneaking along risky edges.
 */
public final class Hazards {
	public static final int NONE = 0;
	/** A drop longer than {@link Pathfinder#MAX_FALL}, which would hurt. */
	public static final int CLIFF = 1;
	/** Water beside the cell, at the feet or below them. */
	public static final int WATER = 2;
	/** Lava, fire, cactus and the like beside the cell, at head height or anywhere a slip would land. */
	public static final int DANGER = 3;

	private static final int[][] CARDINALS = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};

	private Hazards() {
	}

	/** The worst of the four cells beside (x, y, z), one of the constants above. */
	public static int beside(BlockView world, int x, int y, int z) {
		int worst = NONE;
		for (int[] d : CARDINALS) {
			worst = Math.max(worst, column(world, x + d[0], y, z + d[1]));
			if (worst == DANGER) {
				break;
			}
		}
		return worst;
	}

	/** Looks down the column beside the player from head height to see where a slip would end. */
	private static int column(BlockView world, int x, int y, int z) {
		if (world.cell(x, y + 1, z) == BlockView.DANGER) {
			return DANGER;
		}
		for (int dy = 0; dy <= Pathfinder.MAX_FALL + 1; dy++) {
			switch (world.cell(x, y - dy, z)) {
				case BlockView.DANGER:
					return DANGER;
				case BlockView.WATER:
					return WATER;
				case BlockView.PASSABLE:
					break;
				default:
					// A wall beside the feet, or ground within a safe fall.
					return NONE;
			}
		}
		return CLIFF;
	}
}
