package com.xzier9.minegpt.path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import it.unimi.dsi.fastutil.longs.Long2DoubleOpenHashMap;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;

class PathfinderTest {
	/** Flat ground: solid below y=0, air above, nothing loaded past 30 blocks out. */
	private static final class FakeWorld implements BlockView {
		final Map<Long, Integer> cells = new HashMap<>();
		final Map<Long, Float> breakTicks = new HashMap<>();
		final Set<Long> falling = new HashSet<>();
		final Set<Long> noPlacing = new HashSet<>();

		FakeWorld set(int x, int y, int z, int cell) {
			cells.put(Pathfinder.pack(x, y, z), cell);
			return this;
		}

		/** Fills a box, both corners included. */
		FakeWorld fill(int x1, int y1, int z1, int x2, int y2, int z2, int cell) {
			for (int x = x1; x <= x2; x++) {
				for (int y = y1; y <= y2; y++) {
					for (int z = z1; z <= z2; z++) {
						set(x, y, z, cell);
					}
				}
			}
			return this;
		}

		FakeWorld unbreakable(int x1, int y1, int z1, int x2, int y2, int z2) {
			for (int x = x1; x <= x2; x++) {
				for (int y = y1; y <= y2; y++) {
					for (int z = z1; z <= z2; z++) {
						breakTicks.put(Pathfinder.pack(x, y, z), -1f);
					}
				}
			}
			return this;
		}

		@Override
		public int cell(int x, int y, int z) {
			if (Math.abs(x) > 30 || Math.abs(z) > 30 || y < -40) {
				return UNLOADED;
			}
			Integer c = cells.get(Pathfinder.pack(x, y, z));
			if (c != null) {
				return c;
			}
			return y < 0 ? SOLID : PASSABLE;
		}

		@Override
		public float breakTicks(int x, int y, int z) {
			return breakTicks.getOrDefault(Pathfinder.pack(x, y, z), 10f);
		}

		@Override
		public int current(int x, int y, int z) {
			return falling.contains(Pathfinder.pack(x, y, z)) ? CURRENT_DOWN : CURRENT_NONE;
		}

		@Override
		public boolean placeable(int x, int y, int z) {
			return !noPlacing.contains(Pathfinder.pack(x, y, z));
		}

		FakeWorld noPlacing(int x1, int y1, int z1, int x2, int y2, int z2) {
			for (int x = x1; x <= x2; x++) {
				for (int y = y1; y <= y2; y++) {
					for (int z = z1; z <= z2; z++) {
						noPlacing.add(Pathfinder.pack(x, y, z));
					}
				}
			}
			return this;
		}
	}

	private static Path find(BlockView world, Goal goal, boolean allowBreak) {
		return new Pathfinder(world, Pathfinder.Options.DEFAULT.withBreaking(allowBreak)).find(0, 0, 0, goal);
	}

	private static Path findPlacing(BlockView world, int startY, Goal goal, int scaffold) {
		return new Pathfinder(world, Pathfinder.Options.DEFAULT.withScaffold(scaffold)).find(0, startY, 0, goal);
	}

	private static long count(Path path, Path.Move move) {
		return path.steps().stream().filter(s -> s.move() == move).count();
	}

	private static Path.Step last(Path path) {
		return path.steps().get(path.steps().size() - 1);
	}

	private static boolean anyBreaks(Path path) {
		return path.steps().stream().anyMatch(s -> s.breaks().length > 0);
	}

	private static boolean hasMove(Path path, Path.Move move) {
		return path.steps().stream().anyMatch(s -> s.move() == move);
	}

	@Test
	void walksStraightAcrossFlatGround() {
		Path path = find(new FakeWorld(), new Goal.Block(10, 0, 0), false);
		assertTrue(path.complete());
		assertEquals(10, path.steps().size());
		assertEquals(10, last(path).x());
		assertTrue(path.steps().stream().allMatch(s -> s.move() == Path.Move.WALK));
		assertFalse(anyBreaks(path));
	}

	@Test
	void walksDiagonally() {
		Path path = find(new FakeWorld(), new Goal.Block(5, 0, 5), false);
		assertTrue(path.complete());
		assertEquals(5, path.steps().size());
		assertTrue(path.steps().stream().allMatch(s -> s.move() == Path.Move.DIAGONAL));
	}

	@Test
	void doesNotCutCorners() {
		FakeWorld world = new FakeWorld().fill(1, 0, 0, 1, 1, 0, BlockView.SOLID);
		Path path = find(world, new Goal.Block(1, 0, 1), false);
		assertTrue(path.complete());
		assertEquals(2, path.steps().size());
		assertFalse(hasMove(path, Path.Move.DIAGONAL));
	}

	@Test
	void jumpsUpOneBlock() {
		FakeWorld world = new FakeWorld().fill(3, 0, -30, 30, 0, 30, BlockView.SOLID);
		Path path = find(world, new Goal.Block(6, 1, 0), false);
		assertTrue(path.complete());
		assertTrue(hasMove(path, Path.Move.ASCEND));
		assertEquals(1, last(path).y());
	}

	@Test
	void cannotJumpTwoBlocks() {
		FakeWorld world = new FakeWorld().fill(3, 0, -30, 30, 1, 30, BlockView.SOLID);
		Path path = find(world, new Goal.Block(6, 2, 0), false);
		assertFalse(path.complete());
	}

	@Test
	void dropsUpToThreeBlocks() {
		// Start on a platform whose top is three blocks above the ground beside it.
		FakeWorld world = new FakeWorld().fill(-30, 0, -30, 0, 2, 30, BlockView.SOLID);
		Path path = new Pathfinder(world, Pathfinder.Options.DEFAULT).find(0, 3, 0, new Goal.Block(4, 0, 0));
		assertTrue(path.complete());
		assertTrue(hasMove(path, Path.Move.DESCEND));
	}

	@Test
	void refusesLongFalls() {
		FakeWorld world = new FakeWorld().fill(-30, 0, -30, 0, 4, 30, BlockView.SOLID);
		Path path = new Pathfinder(world, Pathfinder.Options.DEFAULT).find(0, 5, 0, new Goal.Block(4, 0, 0));
		assertFalse(path.complete());
	}

	@Test
	void wallBlocksWithoutBreaking() {
		FakeWorld world = new FakeWorld().fill(3, 0, -30, 3, 2, 30, BlockView.SOLID);
		Path path = find(world, new Goal.Block(6, 0, 0), false);
		assertFalse(path.complete());
		assertEquals(2, last(path).x());
	}

	@Test
	void tunnelsThroughWallWhenAllowed() {
		FakeWorld world = new FakeWorld().fill(3, 0, -30, 3, 2, 30, BlockView.SOLID);
		Path path = find(world, new Goal.Block(6, 0, 0), true);
		assertTrue(path.complete());
		assertTrue(anyBreaks(path));
		for (Path.Step step : path.steps()) {
			for (long b : step.breaks()) {
				assertEquals(3, Pathfinder.unpackX(b));
			}
		}
	}

	@Test
	void walksAroundRatherThanBreakingWhenCheaper() {
		FakeWorld world = new FakeWorld().fill(3, 0, -1, 3, 1, 1, BlockView.SOLID);
		Path path = find(world, new Goal.Block(6, 0, 0), true);
		assertTrue(path.complete());
		assertFalse(anyBreaks(path));
	}

	@Test
	void neverBreaksUnbreakableBlocks() {
		// Like bedrock: a wall all the way down, so there is no digging under it either.
		FakeWorld world = new FakeWorld().fill(3, 0, -30, 3, 2, 30, BlockView.SOLID).unbreakable(3, -40, -30, 3, 2, 30);
		Path path = find(world, new Goal.Block(6, 0, 0), true);
		assertFalse(path.complete());
	}

	@Test
	void digsStairsDownWhenStraightDownIsBlocked() {
		// Bedrock-like blocks under the start column force a staircase.
		FakeWorld world = new FakeWorld().unbreakable(0, -40, 0, 0, -1, 0);
		Path path = find(world, new Goal.BelowY(-5), true);
		assertTrue(path.complete());
		assertTrue(last(path).y() <= -5);
		assertTrue(path.steps().stream().allMatch(s -> s.move() == Path.Move.DESCEND));
		for (Path.Step step : path.steps()) {
			// Never break the block the player is about to stand on.
			for (long b : step.breaks()) {
				assertFalse(Pathfinder.unpackX(b) == step.x() && Pathfinder.unpackY(b) == step.y() - 1
						&& Pathfinder.unpackZ(b) == step.z());
			}
		}
	}

	@Test
	void digsStraightDownWhenItIsCheapest() {
		Path path = find(new FakeWorld(), new Goal.BelowY(-5), true);
		assertTrue(path.complete());
		for (Path.Step step : path.steps()) {
			assertEquals(0, step.x());
			assertEquals(0, step.z());
		}
	}

	@Test
	void neverDigsDownOntoLava() {
		FakeWorld world = new FakeWorld().set(0, -3, 0, BlockView.DANGER);
		Path path = find(world, new Goal.BelowY(-5), true);
		assertTrue(path.complete());
		for (Path.Step step : path.steps()) {
			assertTrue(world.cell(step.x(), step.y() - 1, step.z()) != BlockView.DANGER);
			for (long b : step.breaks()) {
				assertTrue(world.cell(Pathfinder.unpackX(b), Pathfinder.unpackY(b), Pathfinder.unpackZ(b)) != BlockView.DANGER);
			}
		}
	}

	@Test
	void searchCanRunInSlices() {
		Pathfinder.Search search = new Pathfinder(new FakeWorld(), Pathfinder.Options.DEFAULT)
				.start(0, 0, 0, new Goal.Block(20, 0, 7));
		int slices = 1;
		while (!search.advance(5)) {
			slices++;
		}
		assertTrue(slices > 1);
		assertTrue(search.result().complete());
		assertEquals(new Pathfinder(new FakeWorld(), Pathfinder.Options.DEFAULT).find(0, 0, 0, new Goal.Block(20, 0, 7))
				.steps().size(), search.result().steps().size());
	}

	@Test
	void digsStairsUp() {
		Path path = new Pathfinder(new FakeWorld(), Pathfinder.Options.DEFAULT.withBreaking(true))
				.find(0, -10, 0, new Goal.Block(5, -5, 0));
		assertTrue(path.complete());
		assertTrue(hasMove(path, Path.Move.ASCEND));
	}

	@Test
	void swimsAcrossAPool() {
		FakeWorld world = new FakeWorld().fill(2, -3, -30, 8, 0, 30, BlockView.WATER);
		Path path = find(world, new Goal.Block(10, 0, 0), false);
		assertTrue(path.complete());
		assertEquals(10, last(path).x());
	}

	@Test
	void swimsAtTheSurfaceOnly() {
		FakeWorld world = new FakeWorld().fill(2, -3, -30, 8, 0, 30, BlockView.WATER);
		Path path = find(world, new Goal.Block(10, 0, 0), false);
		assertTrue(path.complete());
		for (Path.Step step : path.steps()) {
			assertTrue(world.cell(step.x(), step.y() + 1, step.z()) != BlockView.WATER);
		}
	}

	@Test
	void goesRoundAPondRatherThanSwimming() {
		FakeWorld world = new FakeWorld().fill(3, -3, -2, 6, -1, 2, BlockView.WATER);
		Path path = find(world, new Goal.Block(10, 0, 0), false);
		assertTrue(path.complete());
		for (Path.Step step : path.steps()) {
			assertTrue(world.cell(step.x(), step.y(), step.z()) != BlockView.WATER);
		}
	}

	@Test
	void neverDivesForAGoalUnderWater() {
		FakeWorld world = new FakeWorld().fill(2, -6, -30, 8, -1, 30, BlockView.WATER);
		Path path = find(world, new Goal.Block(5, -6, 0), false);
		assertFalse(path.complete());
		for (Path.Step step : path.steps()) {
			assertTrue(world.cell(step.x(), step.y() + 1, step.z()) != BlockView.WATER);
		}
	}

	@Test
	void comesUpForAirWhenStartingUnderWater() {
		FakeWorld world = new FakeWorld().fill(-5, -6, -5, 5, -1, 5, BlockView.WATER);
		Path path = new Pathfinder(world, Pathfinder.Options.DEFAULT).find(0, -6, 0, new Goal.Block(10, 0, 0));
		assertTrue(path.complete());
		int y = -6;
		for (Path.Step step : path.steps()) {
			boolean headUnder = world.cell(step.x(), step.y() + 1, step.z()) == BlockView.WATER;
			assertTrue(!headUnder || (step.move() == Path.Move.SWIM && step.y() > y));
			y = step.y();
		}
		assertEquals(-1, path.steps().get(4).y());
	}

	@Test
	void keepsAClearBlockFromLava() {
		// A lava pool beside the straight line from start to goal.
		FakeWorld world = new FakeWorld().fill(1, -2, 1, 9, -1, 3, BlockView.DANGER);
		Path path = find(world, new Goal.Block(10, 0, 0), false);
		assertTrue(path.complete());
		for (Path.Step step : path.steps()) {
			assertTrue(Hazards.beside(world, step.x(), step.y(), step.z()) != Hazards.DANGER);
		}
	}

	@Test
	void diagonalsDoNotCutAcrossHoles() {
		FakeWorld world = new FakeWorld().fill(1, -6, 0, 1, -1, 0, BlockView.PASSABLE);
		Path path = find(world, new Goal.Block(1, 0, 1), false);
		assertTrue(path.complete());
		assertEquals(2, path.steps().size());
		assertFalse(hasMove(path, Path.Move.DIAGONAL));
	}

	@Test
	void hazardsLookDownTheEdge() {
		FakeWorld world = new FakeWorld()
				.fill(1, -6, 0, 1, -1, 0, BlockView.PASSABLE)
				.fill(-1, -2, 0, -1, -1, 0, BlockView.PASSABLE).set(-1, -3, 0, BlockView.DANGER)
				.set(0, -1, 1, BlockView.WATER);
		assertEquals(Hazards.NONE, Hazards.beside(world, 5, 0, 5));
		assertEquals(Hazards.CLIFF, Hazards.beside(world, 2, 0, 0));
		assertEquals(Hazards.WATER, Hazards.beside(world, 0, 0, 2));
		assertEquals(Hazards.DANGER, Hazards.beside(world, 0, 0, 0));
		// A drop the player can survive is not an edge to worry about.
		world.fill(5, -3, 0, 5, -1, 0, BlockView.PASSABLE);
		assertEquals(Hazards.NONE, Hazards.beside(world, 5, 0, 1));
	}

	@Test
	void avoidsLava() {
		FakeWorld world = new FakeWorld().fill(3, -1, -30, 3, -1, 30, BlockView.DANGER);
		Path path = find(world, new Goal.Block(6, 0, 0), false);
		assertFalse(path.complete());
		for (Path.Step step : path.steps()) {
			assertTrue(step.x() < 3);
		}
	}

	@Test
	void digsUnderLavaWithoutTouchingIt() {
		FakeWorld world = new FakeWorld().fill(3, -1, -30, 3, -1, 30, BlockView.DANGER);
		Path path = find(world, new Goal.Block(6, 0, 0), true);
		assertTrue(path.complete());
		for (Path.Step step : path.steps()) {
			for (int dy = -1; dy <= 1; dy++) {
				assertTrue(world.cell(step.x(), step.y() + dy, step.z()) != BlockView.DANGER);
			}
			for (long b : step.breaks()) {
				assertTrue(world.cell(Pathfinder.unpackX(b), Pathfinder.unpackY(b), Pathfinder.unpackZ(b)) != BlockView.DANGER);
			}
		}
	}

	@Test
	void treatsUnloadedChunksAsWalls() {
		Path path = find(new FakeWorld(), new Goal.Block(40, 0, 0), true);
		assertFalse(path.complete());
		assertEquals(30, last(path).x());
	}

	@Test
	void returnsPartialPathWhenBudgetRunsOut() {
		Path path = new Pathfinder(new FakeWorld(), new Pathfinder.Options(false, 10))
				.find(0, 0, 0, new Goal.Block(25, 0, 0));
		assertFalse(path.complete());
		assertFalse(path.isEmpty());
		assertTrue(last(path).x() > 0);
	}

	@Test
	void reachStopsWithinArmsLength() {
		Goal.Reach goal = new Goal.Reach(10, -1, 0, 4.5);
		Path path = find(new FakeWorld(), goal, false);
		assertTrue(path.complete());
		Path.Step end = last(path);
		assertTrue(goal.reached(end.x(), end.y(), end.z()));
		assertTrue(end.x() < 10);
		assertFalse(goal.reached(10, 0, 0));
	}

	@Test
	void apartStandsClearOfTheBlock() {
		Goal.Apart goal = new Goal.Apart(5, 0, 0, 4.5);
		assertTrue(new Goal.Reach(5, 0, 0, 4.5).reached(4, 0, 0));
		assertFalse(goal.reached(4, 0, 0));
		assertFalse(goal.reached(4, 0, 1));
		assertTrue(goal.reached(3, 0, 0));
		Path path = find(new FakeWorld(), goal, false);
		assertTrue(path.complete());
		Path.Step end = last(path);
		assertTrue(Math.max(Math.abs(end.x() - 5), Math.abs(end.z())) >= 2);
	}

	@Test
	void pillarsOutOfAHole() {
		// A one-wide shaft three deep, too deep to jump out of, with walls that cannot be broken.
		FakeWorld world = new FakeWorld().fill(0, -3, 0, 0, -1, 0, BlockView.PASSABLE);
		assertFalse(findPlacing(world, -3, new Goal.Block(5, 0, 0), 0).complete());
		Path path = findPlacing(world, -3, new Goal.Block(5, 0, 0), 8);
		assertTrue(path.complete());
		assertEquals(2, count(path, Path.Move.PILLAR));
		assertEquals(Path.Move.PILLAR, path.steps().get(0).move());
	}

	@Test
	void runsOutOfScaffold() {
		FakeWorld world = new FakeWorld().fill(0, -6, 0, 0, -1, 0, BlockView.PASSABLE);
		assertFalse(findPlacing(world, -6, new Goal.Block(5, 0, 0), 3).complete());
		assertTrue(findPlacing(world, -6, new Goal.Block(5, 0, 0), 5).complete());
	}

	@Test
	void bridgesASeaItCannotGoRound() {
		FakeWorld world = new FakeWorld().fill(2, -4, -30, 20, -1, 30, BlockView.WATER);
		Path path = findPlacing(world, 0, new Goal.Block(23, 0, 0), 64);
		assertTrue(path.complete());
		// The last block of water can be jumped instead of bridged.
		assertTrue(count(path, Path.Move.BRIDGE) >= 18);
		for (Path.Step step : path.steps()) {
			assertTrue(world.cell(step.x(), step.y(), step.z()) != BlockView.WATER);
		}
	}

	@Test
	void neverPlacesWhereItIsNotAllowed() {
		// A protected strip across the sea: the bridge stops at it, and the path swims that part.
		FakeWorld world = new FakeWorld().fill(2, -4, -30, 20, -1, 30, BlockView.WATER)
				.noPlacing(10, -1, -30, 12, -1, 30);
		Path path = findPlacing(world, 0, new Goal.Block(23, 0, 0), 64);
		assertTrue(path.complete());
		assertTrue(hasMove(path, Path.Move.BRIDGE));
		for (Path.Step step : path.steps()) {
			if (step.move() == Path.Move.BRIDGE) {
				assertTrue(step.x() < 10 || step.x() > 12, "bridged at x=" + step.x());
			}
		}
	}

	@Test
	void walksRoundAPondRatherThanBridging() {
		FakeWorld world = new FakeWorld().fill(3, -3, -2, 6, -1, 2, BlockView.WATER);
		Path path = findPlacing(world, 0, new Goal.Block(10, 0, 0), 64);
		assertTrue(path.complete());
		assertFalse(hasMove(path, Path.Move.BRIDGE));
	}

	@Test
	void neverBridgesOverLava() {
		FakeWorld world = new FakeWorld().fill(2, -4, -30, 20, -1, 30, BlockView.DANGER);
		Path path = findPlacing(world, 0, new Goal.Block(23, 0, 0), 64);
		assertFalse(path.complete());
		assertFalse(hasMove(path, Path.Move.BRIDGE));
	}

	@Test
	void wadesThroughAFordRatherThanSwimming() {
		FakeWorld world = new FakeWorld().fill(2, -3, -30, 8, 0, 30, BlockView.WATER)
				.fill(2, -3, 5, 8, -1, 5, BlockView.SOLID);
		Path path = find(world, new Goal.Block(10, 0, 0), false);
		assertTrue(path.complete());
		for (Path.Step step : path.steps()) {
			if (world.cell(step.x(), step.y(), step.z()) == BlockView.WATER) {
				assertEquals(BlockView.SOLID, world.cell(step.x(), step.y() - 1, step.z()));
			}
		}
	}

	@Test
	void neverEntersFallingWater() {
		// A sheet of falling water across the way, with one gap at z=8.
		FakeWorld world = new FakeWorld();
		for (int z = -30; z <= 30; z++) {
			if (z != 8) {
				world.set(5, 0, z, BlockView.WATER);
				world.falling.add(Pathfinder.pack(5, 0, z));
			}
		}
		Path path = find(world, new Goal.Block(10, 0, 0), false);
		assertTrue(path.complete());
		for (Path.Step step : path.steps()) {
			assertFalse(world.falling.contains(Pathfinder.pack(step.x(), step.y(), step.z())));
		}
	}

	@Test
	void penaltiesSteerThePath() {
		Long2DoubleOpenHashMap penalties = new Long2DoubleOpenHashMap();
		penalties.put(Pathfinder.pack(5, 0, 0), 200);
		Path avoid = new Pathfinder(new FakeWorld(), Pathfinder.Options.DEFAULT.withPenalties(penalties))
				.find(0, 0, 0, new Goal.Block(10, 0, 0));
		assertTrue(avoid.complete());
		assertFalse(avoid.steps().stream().anyMatch(s -> s.x() == 5 && s.z() == 0));

		// A wall of forbidden cells with one way through.
		Long2DoubleOpenHashMap forbidden = new Long2DoubleOpenHashMap();
		for (int z = -30; z <= 30; z++) {
			if (z != 10) {
				forbidden.put(Pathfinder.pack(5, 0, z), Double.POSITIVE_INFINITY);
			}
		}
		Path through = new Pathfinder(new FakeWorld(), Pathfinder.Options.DEFAULT.withPenalties(forbidden))
				.find(0, 0, 0, new Goal.Block(10, 0, 0));
		assertTrue(through.complete());
		assertTrue(through.steps().stream().filter(s -> s.x() == 5).allMatch(s -> s.z() == 10));
	}

	@Test
	void touchStopsBesideAnItemOnALedge() {
		// The item lies on top of a block, level with the player's head.
		FakeWorld world = new FakeWorld().set(5, 0, 0, BlockView.SOLID);
		Goal.Touch goal = new Goal.Touch(5, 1, 0);
		Path path = find(world, goal, false);
		assertTrue(path.complete());
		assertEquals(0, last(path).y());
		assertEquals(4, last(path).x());
		assertTrue(goal.reached(5, 1, 0));
		assertFalse(goal.reached(3, 0, 0));
		assertFalse(goal.reached(4, -1, 0));
	}

	@Test
	void packRoundTrips() {
		int[][] points = {{0, 0, 0}, {-1, -64, -1}, {29_999_999, 319, -29_999_999}, {-12345, 70, 6789}};
		for (int[] p : points) {
			long packed = Pathfinder.pack(p[0], p[1], p[2]);
			assertEquals(p[0], Pathfinder.unpackX(packed));
			assertEquals(p[1], Pathfinder.unpackY(packed));
			assertEquals(p[2], Pathfinder.unpackZ(packed));
		}
	}

	// Vertical moves ---------------------------------------------------------------------

	@Test
	void climbsALadderUpACliff() {
		// A wall 6 high at x=3 with a ladder on its near side.
		FakeWorld world = new FakeWorld().fill(3, 0, -30, 30, 5, 30, BlockView.SOLID)
				.fill(2, 0, 0, 2, 5, 0, BlockView.CLIMBABLE);
		Path path = find(world, new Goal.Block(5, 6, 0), false);
		assertTrue(path.complete());
		assertEquals(5, count(path, Path.Move.CLIMB));
		assertFalse(hasMove(path, Path.Move.PILLAR));
	}

	@Test
	void climbsDownALadderIntoAShaft() {
		// A shaft 8 deep, far too deep to drop into, with a ladder all the way down.
		FakeWorld world = new FakeWorld().fill(4, -8, 0, 4, -1, 0, BlockView.CLIMBABLE);
		Path path = find(world, new Goal.Block(4, -8, 0), false);
		assertTrue(path.complete());
		assertEquals(7, count(path, Path.Move.CLIMB));
		assertFalse(anyBreaks(path));
	}

	@Test
	void doesNotClimbPastTheTopOfALadder() {
		FakeWorld world = new FakeWorld().fill(2, 0, 0, 2, 3, 0, BlockView.CLIMBABLE);
		assertFalse(find(world, new Goal.Block(2, 6, 0), false).complete());
	}

	@Test
	void climbsScaffoldingAndStandsOnTop() {
		FakeWorld world = new FakeWorld().fill(2, 0, 0, 2, 4, 0, BlockView.SCAFFOLDING);
		Path up = find(world, new Goal.Block(2, 5, 0), false);
		assertTrue(up.complete());
		assertEquals(5, count(up, Path.Move.CLIMB));
		Path down = new Pathfinder(world, Pathfinder.Options.DEFAULT).find(2, 5, 0, new Goal.Block(6, 0, 0));
		assertTrue(down.complete());
		// Part of the way down, then a safe drop off the side.
		assertTrue(hasMove(down, Path.Move.CLIMB));
	}

	@Test
	void lettingGoOfALadderThatStopsShort() {
		// The ladder hangs from y=3 to y=5; below it is a two-block drop to the ground.
		FakeWorld world = new FakeWorld().fill(3, 0, -30, 30, 5, 30, BlockView.SOLID)
				.fill(2, 3, 0, 2, 5, 0, BlockView.CLIMBABLE);
		Path path = new Pathfinder(world, Pathfinder.Options.DEFAULT).find(4, 6, 0, new Goal.Block(0, 0, 0));
		assertTrue(path.complete());
		assertTrue(hasMove(path, Path.Move.CLIMB));
	}

	@Test
	void pillarsStraightUpWhenTheGoalIsOverhead() {
		Path path = findPlacing(new FakeWorld(), 0, new Goal.Block(0, 6, 0), 16);
		assertTrue(path.complete());
		assertEquals(6, count(path, Path.Move.PILLAR));
	}

	@Test
	void doesNotPillarInTheOpenForAGoalOnTheLevel() {
		Path path = findPlacing(new FakeWorld(), 0, new Goal.Block(12, 0, 0), 16);
		assertTrue(path.complete());
		assertFalse(hasMove(path, Path.Move.PILLAR));
		assertTrue(path.nodesExplored() < 60);
	}

	@Test
	void dropsALongWayIntoAPool() {
		// Standing on a 12-high plateau with a lake at its foot.
		FakeWorld world = new FakeWorld().fill(-30, 0, -30, 0, 11, 30, BlockView.SOLID)
				.fill(1, -3, -30, 12, -1, 30, BlockView.WATER);
		Path path = new Pathfinder(world, Pathfinder.Options.DEFAULT).find(0, 12, 0, new Goal.Block(20, 0, 0));
		assertTrue(path.complete());
		assertEquals(1, 0 + path.steps().get(0).x());
		assertEquals(-1, path.steps().get(0).y());
	}

	@Test
	void neverDropsALongWayIntoAPuddle() {
		FakeWorld world = new FakeWorld().fill(-30, 0, -30, 0, 11, 30, BlockView.SOLID)
				.set(1, -1, 0, BlockView.WATER);
		Path path = new Pathfinder(world, Pathfinder.Options.DEFAULT).find(0, 12, 0, new Goal.Block(20, 0, 0));
		assertFalse(path.complete());
	}

	@Test
	void jumpsAStreamRatherThanSwimming() {
		FakeWorld world = new FakeWorld().fill(4, -2, -30, 4, -1, 30, BlockView.WATER);
		Path path = find(world, new Goal.Block(10, 0, 0), false);
		assertTrue(path.complete());
		assertEquals(1, count(path, Path.Move.PARKOUR));
		assertFalse(hasMove(path, Path.Move.SWIM));
		for (Path.Step step : path.steps()) {
			assertTrue(world.cell(step.x(), step.y(), step.z()) != BlockView.WATER);
		}
	}

	@Test
	void jumpsATwoBlockDitchWithARunUp() {
		FakeWorld world = new FakeWorld().fill(4, -2, -30, 5, -1, 30, BlockView.PASSABLE);
		Path path = find(world, new Goal.Block(10, 0, 0), false);
		assertTrue(path.complete());
		assertEquals(1, count(path, Path.Move.PARKOUR));
		Path walking = new Pathfinder(world, Pathfinder.Options.DEFAULT.withParkour(false)).find(0, 0, 0, new Goal.Block(10, 0, 0));
		// The ditch is too deep to climb out of.
		assertFalse(walking.complete());
	}

	@Test
	void neverJumpsOverLavaOrADeepPit() {
		FakeWorld lava = new FakeWorld().fill(4, -1, -30, 4, -1, 30, BlockView.DANGER);
		assertFalse(hasMove(find(lava, new Goal.Block(10, 0, 0), false), Path.Move.PARKOUR));
		FakeWorld pit = new FakeWorld().fill(4, -20, -30, 4, -1, 30, BlockView.PASSABLE);
		assertFalse(hasMove(find(pit, new Goal.Block(10, 0, 0), false), Path.Move.PARKOUR));
	}

	@Test
	void doesNotJumpUnderALowCeiling() {
		FakeWorld world = new FakeWorld().fill(4, -2, -30, 4, -1, 30, BlockView.PASSABLE)
				.fill(3, 2, -30, 5, 2, 30, BlockView.SOLID);
		assertFalse(hasMove(find(world, new Goal.Block(10, 0, 0), false), Path.Move.PARKOUR));
	}

	@Test
	void goesThroughADoorInAWall() {
		FakeWorld world = new FakeWorld().fill(5, 0, -30, 5, 3, 30, BlockView.OBSTACLE)
				.fill(5, 0, 2, 5, 1, 2, BlockView.DOOR);
		Path path = find(world, new Goal.Block(10, 0, 0), false);
		assertTrue(path.complete());
		assertTrue(path.steps().stream().anyMatch(s -> s.x() == 5 && s.z() == 2));
		assertFalse(anyBreaks(path));
	}

	@Test
	void doorsCostALittleSoOpenGroundIsPreferred() {
		// A lone door standing in a field: walk round it.
		FakeWorld world = new FakeWorld().fill(5, 0, 0, 5, 1, 0, BlockView.DOOR);
		Path path = find(world, new Goal.Block(10, 0, 0), false);
		assertTrue(path.complete());
		assertFalse(path.steps().stream().anyMatch(s -> s.x() == 5 && s.z() == 0));
	}

	// Search ------------------------------------------------------------------------------

	@Test
	void straightRunsExploreFewNodes() {
		Path path = find(new FakeWorld(), new Goal.Block(25, 0, 0), false);
		assertTrue(path.complete());
		assertEquals(25, path.steps().size());
		assertTrue(path.nodesExplored() <= 30, "explored " + path.nodesExplored());
	}

	@Test
	void timedSlicesFinishTheSearch() {
		FakeWorld world = new FakeWorld().fill(5, 0, -20, 5, 3, 20, BlockView.SOLID).unbreakable(5, 0, -20, 5, 3, 20);
		Pathfinder.Search search = new Pathfinder(world, Pathfinder.Options.DEFAULT).start(0, 0, 0, new Goal.Block(10, 0, 0));
		int slices = 0;
		while (!search.advanceFor(50_000)) {
			slices++;
			assertTrue(slices < 100_000);
		}
		assertTrue(search.done());
		assertTrue(search.result().complete());
	}

	@Test
	void anyOfPicksTheTargetWithARouteNotTheNearest() {
		// A wall right across the world between the start and the near target.
		FakeWorld world = new FakeWorld().fill(3, 0, -30, 3, 6, 30, BlockView.SOLID);
		Goal near = new Goal.Block(5, 0, 0);
		Goal far = new Goal.Block(0, 0, 12);
		Goal.AnyOf either = new Goal.AnyOf(java.util.List.of(near, far));
		Pathfinder.Search search = new Pathfinder(world, new Pathfinder.Options(false, 20_000)).start(0, 0, 0, either);
		while (!search.advance(500)) {
			// keep going
		}
		assertTrue(search.found());
		Path.Step end = last(search.result());
		assertEquals(1, either.which(end.x(), end.y(), end.z()));
		assertFalse(search.exhausted());
	}

	@Test
	void anyOfPrefersTheCheaperRouteOverTheShorterLine() {
		// The near target is across a wide ditch too deep to climb out of; the far one is a level walk.
		FakeWorld world = new FakeWorld().fill(2, -6, -30, 5, -1, 30, BlockView.PASSABLE);
		Goal.AnyOf either = new Goal.AnyOf(java.util.List.of(new Goal.Block(7, 0, 0), new Goal.Block(-10, 0, 0)));
		Pathfinder.Search search = new Pathfinder(world, new Pathfinder.Options(false, 20_000)).start(0, 0, 0, either);
		search.advance(20_000);
		assertTrue(search.found());
		Path.Step end = last(search.result());
		assertEquals(-10, end.x());
		assertEquals(10 * Pathfinder.WALK, search.cost(), 0.01);
	}

	@Test
	void aBoxedInSearchSaysNothingIsReachable() {
		FakeWorld world = new FakeWorld().fill(-2, 0, -2, 2, 3, 2, BlockView.SOLID).fill(-1, 0, -1, 1, 1, 1, BlockView.PASSABLE);
		Pathfinder.Search search = new Pathfinder(world, new Pathfinder.Options(false, 20_000)).start(0, 0, 0,
				new Goal.AnyOf(java.util.List.of(new Goal.Block(10, 0, 0), new Goal.Touch(0, 0, 10))));
		search.advance(20_000);
		assertFalse(search.found());
		assertTrue(search.exhausted());
	}

	@Test
	void aSearchThatRunsOutOfBudgetDoesNotClaimUnreachable() {
		FakeWorld world = new FakeWorld().fill(3, 0, -30, 3, 6, 30, BlockView.SOLID);
		Pathfinder.Search search = new Pathfinder(world, new Pathfinder.Options(false, 50)).start(0, 0, 0,
				new Goal.Block(5, 0, 0));
		search.advance(1000);
		assertTrue(search.done());
		assertFalse(search.found());
		assertFalse(search.exhausted());
	}

	@Test
	void walksOffASlabItStandsOn() {
		// Standing on a bottom slab: the feet are in the slab's own cell, which is an obstacle.
		FakeWorld world = new FakeWorld().set(0, 0, 0, BlockView.OBSTACLE);
		Path path = find(world, new Goal.Block(6, 0, 0), false);
		assertTrue(path.complete());
		assertEquals(6, last(path).x());
	}

	@Test
	void getsDownFromAFenceItStandsOn() {
		// On top of a fence the feet are one cell above it, with nothing solid underneath.
		FakeWorld world = new FakeWorld().set(0, 0, 0, BlockView.OBSTACLE);
		Path path = new Pathfinder(world, Pathfinder.Options.DEFAULT).find(0, 1, 0, new Goal.Block(6, 0, 0));
		assertTrue(path.complete());
		assertEquals(0, last(path).y());
	}

	@Test
	void walksAlongARidgeOfSlabsOntoTheRoof() {
		// A ridge of slabs with a solid roof one lower on each side.
		FakeWorld world = new FakeWorld().fill(-3, 0, -1, 3, 0, 1, BlockView.SOLID).fill(-3, 1, 0, 3, 1, 0, BlockView.OBSTACLE);
		Path path = new Pathfinder(world, Pathfinder.Options.DEFAULT).find(0, 1, 0, new Goal.Block(8, 0, 0));
		assertTrue(path.complete());
		assertEquals(8, last(path).x());
	}
}
