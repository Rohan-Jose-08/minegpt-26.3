package com.xzier9.minegpt.path;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.HashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;

class EnclosureTest {
	/** Flat ground: solid below y=0, air above. */
	private static final class World implements BlockView {
		final Map<Long, Integer> cells = new HashMap<>();

		World set(int x, int y, int z, int cell) {
			cells.put(Pathfinder.pack(x, y, z), cell);
			return this;
		}

		/** A square ring of wall from (0,0) to (size-1,size-1), {@code height} high. */
		World ring(int size, int height) {
			for (int x = 0; x < size; x++) {
				for (int z = 0; z < size; z++) {
					if (x == 0 || z == 0 || x == size - 1 || z == size - 1) {
						for (int y = 0; y < height; y++) {
							set(x, y, z, SOLID);
						}
					}
				}
			}
			return this;
		}

		@Override
		public int cell(int x, int y, int z) {
			Integer c = cells.get(Pathfinder.pack(x, y, z));
			return c != null ? c : y < 0 ? SOLID : PASSABLE;
		}

		@Override
		public float breakTicks(int x, int y, int z) {
			return -1;
		}
	}

	private static final long NONE = Long.MIN_VALUE;

	private static boolean leaves(World world, long blocked) {
		return Enclosure.canLeave(world, 2, 0, 2, blocked, 0, 0, 4, 4, 2000);
	}

	@Test
	void openGroundIsNoTrap() {
		assertTrue(leaves(new World(), NONE));
	}

	@Test
	void aWallOneHighCanBeJumped() {
		assertTrue(leaves(new World().ring(5, 1), NONE));
	}

	@Test
	void aWallTwoHighShutsThePlayerIn() {
		assertFalse(leaves(new World().ring(5, 2), NONE));
	}

	@Test
	void aGapInTheWallIsAWayOut() {
		World world = new World().ring(5, 2).set(2, 0, 4, BlockView.PASSABLE).set(2, 1, 4, BlockView.PASSABLE);
		assertTrue(leaves(world, NONE));
		// Filling the lower half of the gap leaves a one-high step up, with head room: still a way out.
		assertTrue(leaves(world, Pathfinder.pack(2, 0, 4)));
	}

	@Test
	void theLastBlockOfTheSecondLayerIsTheOneThatTraps() {
		World world = new World().ring(5, 2).set(2, 1, 4, BlockView.PASSABLE);
		assertTrue(leaves(world, NONE));
		assertFalse(leaves(world, Pathfinder.pack(2, 1, 4)));
	}

	@Test
	void aDoorCountsAsOpen() {
		World world = new World().ring(5, 3).set(2, 0, 4, BlockView.DOOR).set(2, 1, 4, BlockView.DOOR);
		assertTrue(leaves(world, NONE));
	}

	@Test
	void aLadderLeadsOverTheWall() {
		World world = new World().ring(5, 2);
		world.set(2, 0, 3, BlockView.CLIMBABLE).set(2, 1, 3, BlockView.CLIMBABLE);
		assertTrue(leaves(world, NONE));
	}

	@Test
	void aPitThreeDeepWithSheerSidesIsATrap() {
		World world = new World();
		for (int x = 1; x <= 3; x++) {
			for (int z = 1; z <= 3; z++) {
				for (int y = -3; y < 0; y++) {
					world.set(x, y, z, BlockView.PASSABLE);
				}
			}
		}
		assertFalse(Enclosure.canLeave(world, 2, -3, 2, NONE, 0, 0, 4, 4, 2000));
		// A staircase of two steps makes it climbable.
		world.set(1, -3, 2, BlockView.SOLID).set(1, -2, 1, BlockView.SOLID).set(1, -3, 1, BlockView.SOLID);
		world.set(2, -1, 1, BlockView.SOLID).set(2, -2, 1, BlockView.SOLID).set(2, -3, 1, BlockView.SOLID);
		assertTrue(Enclosure.canLeave(world, 2, -3, 2, NONE, 0, 0, 4, 4, 2000));
	}

	@Test
	void aRoofOverAnOpenRoomDoesNotCount() {
		World world = new World().ring(5, 2).set(2, 0, 4, BlockView.DOOR).set(2, 1, 4, BlockView.DOOR);
		assertTrue(leaves(world, Pathfinder.pack(2, 2, 2)));
	}

	@Test
	void runningOutOfBudgetCountsAsAWayOut() {
		assertTrue(Enclosure.canLeave(new World().ring(5, 2), 2, 0, 2, NONE, -500, -500, 500, 500, 3));
	}
}
