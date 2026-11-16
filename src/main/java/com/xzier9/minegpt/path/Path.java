package com.xzier9.minegpt.path;

import java.util.List;

/**
 * A route from the start (not included) to the last step.
 *
 * @param complete false when the search ran out of budget and this only leads to the
 *                 closest point found; follow it and search again from there
 */
public record Path(List<Step> steps, boolean complete, int nodesExplored) {
	public enum Move {
		WALK, DIAGONAL, ASCEND, DESCEND, SWIM,
		/** One block straight up or down a ladder, vine or scaffolding. */
		CLIMB,
		/** A jump over a gap, landing two or three blocks on in a straight line. */
		PARKOUR,
		/** Jump and put a block under the feet: one block up, in place. */
		PILLAR,
		/** Put a block where the floor is missing, against the block stood on, then walk onto it. */
		BRIDGE;

		/** True for moves that place a block at the cell under the step's feet. */
		public boolean places() {
			return this == PILLAR || this == BRIDGE;
		}
	}

	/**
	 * One move to the block at x, y, z (feet position). {@link Move#PILLAR} and {@link Move#BRIDGE}
	 * steps first place a block at x, y - 1, z.
	 *
	 * @param breaks blocks to break before making the move, packed with {@link Pathfinder#pack}
	 */
	public record Step(int x, int y, int z, Move move, long[] breaks) {
	}

	public boolean isEmpty() {
		return steps.isEmpty();
	}
}
