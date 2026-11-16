package com.xzier9.minegpt.path;

import it.unimi.dsi.fastutil.longs.LongArrayFIFOQueue;
import it.unimi.dsi.fastutil.longs.LongOpenHashSet;

/**
 * Answers one question for a builder: standing here, can the player still walk out of the
 * area being built on, or would the next block shut them in?
 *
 * It floods outward from the player's feet over cells a player can walk, step, climb or drop
 * between, without digging or placing anything, and stops as soon as it gets clear of the box.
 * Doors and gates count as open. It is a rough check, stricter than the pathfinder (no gap
 * jumps, no pillaring), so a "no" means "walk out first to be safe", not "impossible".
 */
public final class Enclosure {
	/** Longest drop the flood takes: the same height the player lands without harm. */
	private static final int MAX_DROP = 3;

	private Enclosure() {
	}

	/**
	 * @param blocked a cell (by {@link Pathfinder#pack}) to treat as a full block, or
	 *                {@code Long.MIN_VALUE} for none: the block about to be placed
	 * @param limit   cells to look at before giving up; running out counts as a way out,
	 *                since a space that big is not a trap
	 * @return true if a cell outside the box {@code minX..maxX, minZ..maxZ} can be reached
	 */
	public static boolean canLeave(BlockView view, int x, int y, int z, long blocked,
			int minX, int minZ, int maxX, int maxZ, int limit) {
		LongOpenHashSet seen = new LongOpenHashSet();
		LongArrayFIFOQueue queue = new LongArrayFIFOQueue();
		int[][] sides = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};
		long start = Pathfinder.pack(x, y, z);
		seen.add(start);
		queue.enqueue(start);
		int looked = 0;
		while (!queue.isEmpty()) {
			long at = queue.dequeueLong();
			int cx = Pathfinder.unpackX(at);
			int cy = Pathfinder.unpackY(at);
			int cz = Pathfinder.unpackZ(at);
			if (cx < minX || cx > maxX || cz < minZ || cz > maxZ) {
				return true;
			}
			if (++looked > limit) {
				return true;
			}
			int here = cell(view, cx, cy, cz, blocked);
			boolean climbing = here == BlockView.CLIMBABLE || here == BlockView.SCAFFOLDING || here == BlockView.WATER;
			// Straight up a ladder, vine or water column, and back down one.
			if (climbing && open(cell(view, cx, cy + 1, cz, blocked)) && open(cell(view, cx, cy + 2, cz, blocked))) {
				push(seen, queue, cx, cy + 1, cz);
			}
			int under = cell(view, cx, cy - 1, cz, blocked);
			if (under == BlockView.CLIMBABLE || under == BlockView.SCAFFOLDING || under == BlockView.WATER) {
				push(seen, queue, cx, cy - 1, cz);
			}
			boolean headroom = open(cell(view, cx, cy + 2, cz, blocked));
			for (int[] d : sides) {
				int nx = cx + d[0];
				int nz = cz + d[1];
				// One block up: a jump, which needs room over the head here.
				if (headroom && stand(view, nx, cy + 1, nz, blocked)) {
					push(seen, queue, nx, cy + 1, nz);
				}
				// Level, or down a drop: the body has to fit through the gap at this level first.
				if (!open(cell(view, nx, cy, nz, blocked)) || !open(cell(view, nx, cy + 1, nz, blocked))) {
					continue;
				}
				for (int down = 0; down <= MAX_DROP; down++) {
					int ny = cy - down;
					if (!open(cell(view, nx, ny, nz, blocked))) {
						break;
					}
					if (stand(view, nx, ny, nz, blocked)) {
						push(seen, queue, nx, ny, nz);
						break;
					}
				}
			}
		}
		return false;
	}

	private static void push(LongOpenHashSet seen, LongArrayFIFOQueue queue, int x, int y, int z) {
		long key = Pathfinder.pack(x, y, z);
		if (seen.add(key)) {
			queue.enqueue(key);
		}
	}

	/** Room for the body with something to stand on or hold on to. */
	private static boolean stand(BlockView view, int x, int y, int z, long blocked) {
		int feet = cell(view, x, y, z, blocked);
		if (!open(feet) || !open(cell(view, x, y + 1, z, blocked))) {
			return false;
		}
		if (feet == BlockView.WATER || feet == BlockView.CLIMBABLE || feet == BlockView.SCAFFOLDING) {
			return true;
		}
		int floor = cell(view, x, y - 1, z, blocked);
		return floor == BlockView.SOLID || floor == BlockView.OBSTACLE || floor == BlockView.SCAFFOLDING;
	}

	private static boolean open(int cell) {
		return cell == BlockView.PASSABLE || cell == BlockView.WATER || cell == BlockView.CLIMBABLE
				|| cell == BlockView.SCAFFOLDING || cell == BlockView.DOOR;
	}

	private static int cell(BlockView view, int x, int y, int z, long blocked) {
		return blocked != Long.MIN_VALUE && Pathfinder.pack(x, y, z) == blocked ? BlockView.SOLID : view.cell(x, y, z);
	}
}
