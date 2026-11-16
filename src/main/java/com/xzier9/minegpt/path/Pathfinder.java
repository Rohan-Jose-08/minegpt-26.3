package com.xzier9.minegpt.path;

import it.unimi.dsi.fastutil.longs.Long2DoubleMap;
import it.unimi.dsi.fastutil.longs.Long2DoubleMaps;
import it.unimi.dsi.fastutil.longs.Long2FloatOpenHashMap;
import it.unimi.dsi.fastutil.longs.Long2IntOpenHashMap;
import it.unimi.dsi.fastutil.longs.Long2ObjectOpenHashMap;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * A* search over block cells for a player-sized walker.
 *
 * Moves: walk to a neighbour, walk diagonally, jump up one block, drop down up to
 * {@link #MAX_FALL} blocks (or much further into water), swim, climb ladders, vines and
 * scaffolding, go through wooden doors and gates, and jump over gaps one or two blocks wide. With {@link Options#allowBreak} the walker may also
 * break blocks in its way, which lets it tunnel, dig staircases, and dig straight down when
 * there is solid ground right under the block it digs out. With {@link Options#scaffold}
 * blocks to spare it may also place them: pillar up out of a hole or onto a ledge, and bridge
 * over water or a gap. Costs are in game ticks, so the cheapest path is roughly the fastest one.
 *
 * It stays out of trouble where it can. Deep water costs far more than walking, so the path
 * goes round a lake (or bridges it) unless that is much longer, and it never takes the head
 * under water except to come back up. Shallow water, where the feet touch the bottom, costs a
 * little extra; flowing water more, and falling water is never entered. Lava, fire and the like
 * are never entered, and standing beside them, beside water or beside a drop that would hurt
 * costs extra, so the path keeps a block clear of such edges when it can. {@link Options#penalties}
 * adds costs for cells the caller has learned to avoid, such as where the player got stuck.
 *
 * The search is weighted A*: the distance estimate is scaled up a little so the search heads
 * for the goal instead of fanning out, at the price of paths slightly longer than the best one.
 */
public final class Pathfinder {
	public static final double WALK = 4.6;
	public static final double DIAGONAL = 6.5;
	public static final double JUMP = 2.0;
	public static final double FALL_PER_BLOCK = 1.0;
	public static final double SWIM = 9.0;
	public static final double UNDERWATER = 5.0;
	/** Extra cost per broken block on top of its break time: turning to it, picking up drops. */
	public static final double BREAK_OVERHEAD = 4.0;
	public static final int MAX_FALL = 3;
	/** Extra cost of a cell in deep water: swimming is slow and drifts off the path, so going round is usually better. */
	public static final double IN_WATER = 25.0;
	/** Extra cost of a cell in shallow water, where the feet are on the bottom and the head stays out. */
	public static final double WADE = 3.0;
	/** Extra cost of a cell of water flowing sideways, which pushes the player off course. */
	public static final double IN_CURRENT = 15.0;
	/** Jumping and placing a block underneath, once the player is looking down. */
	public static final double PILLAR = 14.0;
	/** Sneaking to the edge, placing a block against it, and walking onto it. */
	public static final double BRIDGE = 18.0;
	/** Extra cost of a cell beside water or a long drop, where a slip ends in a swim or a fall. */
	public static final double NEAR_EDGE = 4.0;
	/** Extra cost of a cell beside lava, fire or similar. */
	public static final double NEAR_DANGER = 25.0;
	/** One block up a ladder, vine or scaffolding. */
	public static final double CLIMB_UP = 8.5;
	/** One block down one. */
	public static final double CLIMB_DOWN = 7.0;
	/** Opening a door or gate on the way through. */
	public static final double DOOR = 10.0;
	/** Lining up and jumping a gap, on top of the walking distance. */
	public static final double PARKOUR = 6.0;
	/** The same for a gap two blocks wide, which takes a sprint. */
	public static final double PARKOUR_LONG = 12.0;
	/** Longest fall when the landing is water, which breaks any fall. */
	public static final int MAX_WATER_FALL = 24;

	/** Ticks per block of distance in the A* estimate: a little over walking speed. */
	private static final double HEURISTIC = 5.0;
	/**
	 * The same when digging is allowed, where a block of progress through stone costs 15 to 50
	 * ticks. Weighting it more keeps searches through solid ground fast, but too much makes the
	 * path dig straight through a hill it could have walked round.
	 */
	private static final double HEURISTIC_DIGGING = 6.0;

	private static final long[] NO_BREAKS = new long[0];
	private static final int[][] CARDINALS = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};
	private static final int[][] DIAGONALS = {{1, 1}, {1, -1}, {-1, 1}, {-1, -1}};

	/**
	 * @param allowBreak may break natural blocks in the way
	 * @param maxNodes   search budget
	 * @param scaffold   blocks the player can spare for pillaring and bridging; 0 to place none
	 * @param penalties  extra cost of ending a move in a cell (by {@link #pack}), or infinity to
	 *                   never enter it
	 * @param parkour    may jump over gaps one or two blocks wide
	 */
	public record Options(boolean allowBreak, int maxNodes, int scaffold, Long2DoubleMap penalties, boolean parkour) {
		public static final Options DEFAULT = new Options(false, 20_000);

		public Options(boolean allowBreak, int maxNodes) {
			this(allowBreak, maxNodes, 0, Long2DoubleMaps.EMPTY_MAP, true);
		}

		public Options(boolean allowBreak, int maxNodes, int scaffold, Long2DoubleMap penalties) {
			this(allowBreak, maxNodes, scaffold, penalties, true);
		}

		public Options withBreaking(boolean allowBreak) {
			return new Options(allowBreak, maxNodes, scaffold, penalties, parkour);
		}

		public Options withScaffold(int scaffold) {
			return new Options(allowBreak, maxNodes, scaffold, penalties, parkour);
		}

		public Options withPenalties(Long2DoubleMap penalties) {
			return new Options(allowBreak, maxNodes, scaffold, penalties, parkour);
		}

		/** Whether to jump over gaps. */
		public Options withParkour(boolean parkour) {
			return new Options(allowBreak, maxNodes, scaffold, penalties, parkour);
		}
	}

	private static final class Node {
		final int x;
		final int y;
		final int z;
		double g = Double.POSITIVE_INFINITY;
		double f;
		double h;
		Node parent;
		Path.Move move;
		long[] breaks;
		/** Blocks placed on the way here. */
		int placed;
		boolean closed;
		/** Position in the open heap, or -1 when not queued. */
		int heapIndex = -1;

		Node(int x, int y, int z) {
			this.x = x;
			this.y = y;
			this.z = z;
		}
	}

	/**
	 * The open set: a binary heap ordered by f, with the node nearer the goal first on a tie,
	 * so the search runs down a corridor of equally good cells instead of filling it. A node
	 * that gets cheaper moves up in place, so nothing is queued twice.
	 */
	private static final class Heap {
		private Node[] items = new Node[1024];
		private int size;

		boolean isEmpty() {
			return size == 0;
		}

		private static boolean before(Node a, Node b) {
			return a.f < b.f || (a.f == b.f && a.h < b.h);
		}

		/** Adds the node, or moves it up if it is queued already and its f went down. */
		void push(Node node) {
			if (node.heapIndex < 0) {
				if (size == items.length) {
					items = Arrays.copyOf(items, size * 2);
				}
				node.heapIndex = size;
				items[size++] = node;
			}
			int i = node.heapIndex;
			while (i > 0) {
				int parent = (i - 1) >>> 1;
				Node p = items[parent];
				if (!before(node, p)) {
					break;
				}
				items[i] = p;
				p.heapIndex = i;
				i = parent;
			}
			items[i] = node;
			node.heapIndex = i;
		}

		Node pop() {
			Node top = items[0];
			top.heapIndex = -1;
			Node last = items[--size];
			items[size] = null;
			if (size > 0) {
				int i = 0;
				int half = size >>> 1;
				while (i < half) {
					int child = 2 * i + 1;
					if (child + 1 < size && before(items[child + 1], items[child])) {
						child++;
					}
					Node c = items[child];
					if (!before(c, last)) {
						break;
					}
					items[i] = c;
					c.heapIndex = i;
					i = child;
				}
				items[i] = last;
				last.heapIndex = i;
			}
			return top;
		}
	}

	private final BlockView world;
	private final Options options;
	private final double heuristic;
	private final Long2IntOpenHashMap cells = new Long2IntOpenHashMap();
	private final Long2FloatOpenHashMap breakTimes = new Long2FloatOpenHashMap();
	private final Long2FloatOpenHashMap edgeCosts = new Long2FloatOpenHashMap();
	/** The world through this search's caches, for {@link Hazards}. */
	private final BlockView cached = new BlockView() {
		@Override
		public int cell(int x, int y, int z) {
			return Pathfinder.this.cell(x, y, z);
		}

		@Override
		public float breakTicks(int x, int y, int z) {
			return Pathfinder.this.breakTicks(x, y, z);
		}
	};

	public Pathfinder(BlockView world, Options options) {
		this.world = world;
		this.options = options;
		this.heuristic = options.allowBreak() ? HEURISTIC_DIGGING : HEURISTIC;
		cells.defaultReturnValue(-1);
		breakTimes.defaultReturnValue(Float.NaN);
		edgeCosts.defaultReturnValue(Float.NaN);
	}

	/** Searches to the end in one go. */
	public Path find(int startX, int startY, int startZ, Goal goal) {
		Search search = start(startX, startY, startZ, goal);
		while (!search.advance(Integer.MAX_VALUE)) {
			// The budget in the options ends the loop.
		}
		return search.result();
	}

	/** Starts a search that can be run a slice at a time, so a long search does not freeze the game. */
	public Search start(int startX, int startY, int startZ, Goal goal) {
		return new Search(startX, startY, startZ, goal);
	}

	/** One search in progress. Call {@link #advance} until it returns true, then read {@link #result}. */
	public final class Search {
		private final Goal goal;
		private final Long2ObjectOpenHashMap<Node> nodes = new Long2ObjectOpenHashMap<>();
		private final Heap open = new Heap();
		private final List<Candidate> candidates = new ArrayList<>();
		private final Node start;
		private Node best;
		private Node found;
		private int explored;
		private boolean done;

		private Search(int startX, int startY, int startZ, Goal goal) {
			this.goal = goal;
			start = new Node(startX, startY, startZ);
			start.g = 0;
			start.h = goal.distance(startX, startY, startZ);
			start.f = start.h * heuristic;
			nodes.put(pack(startX, startY, startZ), start);
			open.push(start);
			best = start;
		}

		/** Explores up to {@code maxNodes} more nodes. Returns true once the search is over. */
		public boolean advance(int maxNodes) {
			int limit = explored + maxNodes;
			while (!done && explored < limit) {
				if (open.isEmpty() || explored >= options.maxNodes()) {
					done = true;
					break;
				}
				Node node = open.pop();
				node.closed = true;
				explored++;

				if (goal.reached(node.x, node.y, node.z)) {
					found = node;
					done = true;
					break;
				}
				if (node.h < best.h || (node.h == best.h && node.g < best.g)) {
					best = node;
				}
				expand(node);
			}
			return done;
		}

		/**
		 * Explores for about this long, in small batches. Returns true once the search is over.
		 * Lets a caller spend a fixed slice of each game tick on the search, whatever the machine.
		 */
		public boolean advanceFor(long nanos) {
			long end = System.nanoTime() + nanos;
			while (!advance(256)) {
				if (System.nanoTime() >= end) {
					return false;
				}
			}
			return true;
		}

		public int explored() {
			return explored;
		}

		public boolean done() {
			return done;
		}

		/** True if the goal itself was found, rather than only the closest point to it. */
		public boolean found() {
			return found != null;
		}

		/**
		 * True if the search ended with nowhere left to look and no way found: the goal cannot be
		 * reached at all with these options, as opposed to the search running out of budget.
		 */
		public boolean exhausted() {
			return done && found == null && open.isEmpty() && explored < options.maxNodes();
		}

		/** Cost in ticks of the route {@link #result} returns. */
		public double cost() {
			return found != null ? found.g : best.g;
		}

		/** The path found, or the way to the closest point reached if the goal was not found. */
		public Path result() {
			if (found != null) {
				return new Path(trace(found), true, explored);
			}
			return new Path(best == start ? List.of() : trace(best), false, explored);
		}

		private void expand(Node node) {
			candidates.clear();
			neighbours(node, goal.distance(node.x, node.y + 1, node.z) < node.h - 0.5, candidates);
			for (Candidate c : candidates) {
				double risk = risk(node, c);
				if (risk < 0) {
					continue;
				}
				long key = pack(c.x, c.y, c.z);
				Node next = nodes.get(key);
				if (next == null) {
					next = new Node(c.x, c.y, c.z);
					next.h = goal.distance(c.x, c.y, c.z);
					nodes.put(key, next);
				}
				double g = node.g + c.cost + risk;
				if (next.closed || g >= next.g) {
					continue;
				}
				next.g = g;
				next.f = g + next.h * heuristic;
				next.parent = node;
				next.move = c.move;
				next.breaks = c.breaks;
				next.placed = node.placed + (c.move.places() ? 1 : 0);
				open.push(next);
			}
		}
	}

	private record Candidate(int x, int y, int z, double cost, Path.Move move, long[] breaks) {
	}

	/** @param upward the goal lies well above this node, so pillaring in the open is worth a try */
	private void neighbours(Node n, boolean upward, List<Candidate> out) {
		int x = n.x;
		int y = n.y;
		int z = n.z;
		int here = cell(x, y, z);
		boolean inWater = here == BlockView.WATER;
		boolean onLadder = climbable(here);
		// A block placed by the move that got here counts as ground, though the world has none yet.
		boolean onGround = !inWater && (standableWithFloor(x, y, z) || (n.move != null && n.move.places()));
		boolean canPlace = onGround && !onLadder && n.placed < options.scaffold();

		for (int[] d : CARDINALS) {
			int nx = x + d[0];
			int nz = z + d[1];

			// Walk straight across.
			Breaks walk = new Breaks();
			if (clear(nx, y, nz, walk) && clear(nx, y + 1, nz, walk) && standable(nx, y, nz)) {
				double base = cell(nx, y, nz) == BlockView.WATER ? SWIM : WALK;
				out.add(walk.candidate(nx, y, nz, base + doorCost(nx, y, nz), Path.Move.WALK));
			}

			// Jump up onto the neighbouring block. From a ladder this is stepping off its top.
			if (cell(nx, y, nz) == BlockView.SOLID) {
				Breaks up = new Breaks();
				if (clear(x, y + 2, z, up) && clear(nx, y + 1, nz, up) && clear(nx, y + 2, nz, up)) {
					out.add(up.candidate(nx, y + 1, nz, WALK + JUMP + doorCost(nx, y + 1, nz), Path.Move.ASCEND));
				}
			}

			// Step or drop down off the edge.
			Breaks side = new Breaks();
			if (clear(nx, y + 1, nz, side) && clear(nx, y, nz, side)) {
				if (!standable(nx, y, nz)) {
					drop(nx, y, nz, side, WALK, out);
				} else if (options.allowBreak() && standableWithFloor(nx, y, nz)) {
					// Dig one stair down: break the block in front of the feet and step onto the one below it.
					Breaks dig = side.copy();
					if (!open(cell(nx, y - 1, nz)) && clear(nx, y - 1, nz, dig) && standableWithFloor(nx, y - 1, nz)) {
						out.add(dig.candidate(nx, y - 1, nz, WALK + FALL_PER_BLOCK, Path.Move.DESCEND));
					}
				}
			}

			if (options.parkour() && onGround && !onLadder) {
				jumps(n, d[0], d[1], out);
			}
		}

		// Dig straight down, but only onto solid ground: never into a cave, water or lava below.
		if (options.allowBreak() && !inWater && standableWithFloor(x, y - 1, z)) {
			Breaks dig = new Breaks();
			if (!open(cell(x, y - 1, z)) && clear(x, y - 1, z, dig)) {
				out.add(dig.candidate(x, y - 1, z, FALL_PER_BLOCK + 1, Path.Move.DESCEND));
			}
		}

		// Diagonals, only over dry, level ground with open space on both sides, so the player
		// neither clips a corner nor cuts across the corner of a hole.
		if (!inWater && !onLadder) {
			for (int[] d : DIAGONALS) {
				int nx = x + d[0];
				int nz = z + d[1];
				if (walkable(nx, y, z) && walkable(x, y, nz) && walkable(nx, y, nz)) {
					out.add(new Candidate(nx, y, nz, DIAGONAL, Path.Move.DIAGONAL, NO_BREAKS));
				}
			}
		}

		// Ladders, vines and scaffolding: straight up and down.
		if (onLadder && open(cell(x, y + 1, z)) && open(cell(x, y + 2, z)) && standable(x, y + 1, z)) {
			out.add(new Candidate(x, y + 1, z, CLIMB_UP, Path.Move.CLIMB, NO_BREAKS));
		}
		int under = cell(x, y - 1, z);
		if (climbable(under) && (onLadder || under == BlockView.SCAFFOLDING)) {
			out.add(new Candidate(x, y - 1, z, CLIMB_DOWN, Path.Move.CLIMB, NO_BREAKS));
		} else if (onLadder && open(under) && !standableWithFloor(x, y, z)) {
			// The ladder stops short of the ground: let go.
			drop(x, y, z, new Breaks(), 0, out);
		}

		if (canPlace) {
			// Pillar up: jump and put a block where the feet were. Gets out of holes and onto ledges.
			// Only beside a wall too high to step up, or when the goal is well overhead: pillaring
			// anywhere else leads nowhere and would make the search try every height.
			Breaks up = new Breaks();
			if ((upward || besideWall(x, y, z)) && here == BlockView.PASSABLE && clear(x, y + 2, z, up)
					&& world.placeable(x, y, z)) {
				out.add(up.candidate(x, y + 1, z, PILLAR, Path.Move.PILLAR));
			}
			// Bridge: put a block where the floor is missing in front, over water or a gap.
			for (int[] d : CARDINALS) {
				int nx = x + d[0];
				int nz = z + d[1];
				int floor = cell(nx, y - 1, nz);
				if ((floor == BlockView.PASSABLE || floor == BlockView.WATER) && cell(nx, y, nz) == BlockView.PASSABLE
						&& cell(nx, y + 1, nz) == BlockView.PASSABLE && !dangerBelow(nx, y - 1, nz)
						&& world.placeable(nx, y - 1, nz)) {
					out.add(new Candidate(nx, y, nz, BRIDGE, Path.Move.BRIDGE, NO_BREAKS));
				}
			}
		}

		// Swim up. There is no swimming down: the head never goes under on purpose.
		if (inWater && cell(x, y + 1, z) == BlockView.WATER && passable(x, y + 2, z)) {
			out.add(new Candidate(x, y + 1, z, SWIM + headWater(x, y + 1, z), Path.Move.SWIM, NO_BREAKS));
		}
	}

	/**
	 * Adds the fall down the column at x, z from just under y: onto ground or a ladder within
	 * {@link #MAX_FALL}, or into water up to {@link #MAX_WATER_FALL} below, which breaks the fall.
	 */
	private void drop(int x, int y, int z, Breaks breaks, double base, List<Candidate> out) {
		for (int fall = 1; fall <= MAX_WATER_FALL; fall++) {
			int ty = y - fall;
			int c = cell(x, ty, z);
			if (!open(c)) {
				return;
			}
			if (fall > MAX_FALL) {
				// Further than is safe onto ground: only the surface of water will do.
				if (c == BlockView.WATER) {
					if (widePool(x, ty, z)) {
						out.add(breaks.candidate(x, ty, z, base + fall * FALL_PER_BLOCK, Path.Move.DESCEND));
					}
					return;
				}
				if (c != BlockView.PASSABLE) {
					return;
				}
			} else if (standable(x, ty, z)) {
				out.add(breaks.candidate(x, ty, z, base + fall * FALL_PER_BLOCK, Path.Move.DESCEND));
				return;
			}
		}
	}

	/** True if at least three of the cells beside this water cell are water too: room to land in after a long fall. */
	private boolean widePool(int x, int y, int z) {
		int water = 0;
		for (int[] d : CARDINALS) {
			if (cell(x + d[0], y, z + d[1]) == BlockView.WATER) {
				water++;
			}
		}
		return water >= 3;
	}

	/**
	 * Jumps over a gap one or two blocks wide to ground on the same level. Only over gaps where
	 * missing the jump is safe: ground within {@link #MAX_FALL} or water below, never lava or a
	 * long drop. The two-block jump takes a sprint, so it needs a block of run-up behind.
	 */
	private void jumps(Node n, int dx, int dz, List<Candidate> out) {
		int x = n.x;
		int y = n.y;
		int z = n.z;
		if (cell(x + dx, y - 1, z + dz) == BlockView.SOLID || cell(x, y + 2, z) != BlockView.PASSABLE
				|| cell(x, y, z) != BlockView.PASSABLE) {
			return;
		}
		for (int gap = 1; gap <= 2; gap++) {
			int gx = x + dx * gap;
			int gz = z + dz * gap;
			if (cell(gx, y, gz) != BlockView.PASSABLE || cell(gx, y + 1, gz) != BlockView.PASSABLE
					|| cell(gx, y + 2, gz) != BlockView.PASSABLE || !safeToMiss(gx, y - 1, gz)) {
				return;
			}
			int lx = gx + dx;
			int lz = gz + dz;
			if (cell(lx, y - 1, lz) == BlockView.SOLID) {
				if (cell(lx, y, lz) != BlockView.PASSABLE || cell(lx, y + 1, lz) != BlockView.PASSABLE
						|| cell(lx, y + 2, lz) != BlockView.PASSABLE) {
					return;
				}
				if (gap == 2 && !walkable(x - dx, y, z - dz)) {
					return;
				}
				double cost = WALK * (gap + 1) + (gap == 1 ? PARKOUR : PARKOUR_LONG);
				out.add(new Candidate(lx, y, lz, cost, Path.Move.PARKOUR, NO_BREAKS));
				return;
			}
		}
	}

	/** True if falling into this cell ends on ground a short way down or in water, not in danger or a long drop. */
	private boolean safeToMiss(int x, int y, int z) {
		for (int fall = 0; fall <= 8; fall++) {
			int c = cell(x, y - fall, z);
			if (c == BlockView.WATER) {
				return world.current(x, y - fall, z) != BlockView.CURRENT_DOWN;
			}
			if (c == BlockView.SOLID) {
				return fall <= MAX_FALL;
			}
			if (c != BlockView.PASSABLE) {
				return false;
			}
		}
		return false;
	}

	private double doorCost(int x, int y, int z) {
		return cell(x, y, z) == BlockView.DOOR || cell(x, y + 1, z) == BlockView.DOOR ? DOOR : 0;
	}

	private static boolean climbable(int cell) {
		return cell == BlockView.CLIMBABLE || cell == BlockView.SCAFFOLDING;
	}

	/** True if the player's body can be in such a cell. */
	private static boolean open(int cell) {
		return cell == BlockView.PASSABLE || cell == BlockView.WATER || cell == BlockView.CLIMBABLE
				|| cell == BlockView.SCAFFOLDING || cell == BlockView.DOOR;
	}

	/**
	 * Extra cost of ending a move at the candidate's cell, or a negative number if the move
	 * must not be made: one that puts the head under water, unless it is on the way up; one into
	 * falling water; or one into a cell the caller ruled out.
	 */
	private double risk(Node from, Candidate to) {
		int x = to.x();
		int y = to.y();
		int z = to.z();
		boolean rising = to.move() == Path.Move.SWIM && y > from.y;
		if (cell(x, y + 1, z) == BlockView.WATER && !rising) {
			return -1;
		}
		double cost = 0;
		if (cell(x, y, z) == BlockView.WATER) {
			int current = world.current(x, y, z);
			if (current == BlockView.CURRENT_DOWN || world.current(x, y + 1, z) == BlockView.CURRENT_DOWN) {
				return -1;
			}
			cost += standableWithFloor(x, y, z) ? WADE : IN_WATER;
			if (current == BlockView.CURRENT_SIDEWAYS) {
				cost += IN_CURRENT;
			}
		}
		if (!options.penalties().isEmpty()) {
			double penalty = options.penalties().get(pack(x, y, z));
			if (penalty == Double.POSITIVE_INFINITY) {
				return -1;
			}
			cost += penalty;
		}
		return cost + edgeCost(x, y, z);
	}

	/** True if a neighbouring block blocks the way at head height. */
	private boolean besideWall(int x, int y, int z) {
		for (int[] d : CARDINALS) {
			int c = cell(x + d[0], y + 1, z + d[1]);
			if (c == BlockView.SOLID || c == BlockView.OBSTACLE) {
				return true;
			}
		}
		return false;
	}

	/** True if lava, fire or the like lies below this cell, under nothing but air and water. */
	private boolean dangerBelow(int x, int y, int z) {
		for (int dy = 0; dy <= 32; dy++) {
			int c = cell(x, y - dy, z);
			if (c == BlockView.DANGER) {
				return true;
			}
			if (c != BlockView.PASSABLE && c != BlockView.WATER) {
				return false;
			}
		}
		return false;
	}

	private double edgeCost(int x, int y, int z) {
		long key = pack(x, y, z);
		float cost = edgeCosts.get(key);
		if (Float.isNaN(cost)) {
			cost = switch (Hazards.beside(cached, x, y, z)) {
				case Hazards.DANGER -> (float) NEAR_DANGER;
				case Hazards.NONE -> 0f;
				default -> (float) NEAR_EDGE;
			};
			edgeCosts.put(key, cost);
		}
		return cost;
	}

	/** Blocks a candidate move has to break, and their total cost. */
	private static final class Breaks {
		// No move breaks more than three blocks.
		final long[] positions = new long[3];
		int count;
		double cost;

		void add(long position, double ticks) {
			positions[count++] = position;
			cost += ticks;
		}

		Breaks copy() {
			Breaks b = new Breaks();
			System.arraycopy(positions, 0, b.positions, 0, count);
			b.count = count;
			b.cost = cost;
			return b;
		}

		Candidate candidate(int x, int y, int z, double base, Path.Move move) {
			long[] packed = count == 0 ? NO_BREAKS : Arrays.copyOf(positions, count);
			return new Candidate(x, y, z, base + cost, move, packed);
		}
	}

	/** True if the player's body can occupy the cell, breaking it first if allowed. */
	private boolean clear(int x, int y, int z, Breaks breaks) {
		int c = cell(x, y, z);
		if (open(c)) {
			return true;
		}
		if (!options.allowBreak() || (c != BlockView.SOLID && c != BlockView.OBSTACLE)) {
			return false;
		}
		float ticks = breakTicks(x, y, z);
		if (ticks < 0) {
			return false;
		}
		breaks.add(pack(x, y, z), ticks + BREAK_OVERHEAD);
		return true;
	}

	private boolean passable(int x, int y, int z) {
		return open(cell(x, y, z));
	}

	/** Dry open space for the whole body, on solid ground. */
	private boolean walkable(int x, int y, int z) {
		return cell(x, y, z) == BlockView.PASSABLE && cell(x, y + 1, z) == BlockView.PASSABLE && standableWithFloor(x, y, z);
	}

	/** The player can stay here: ground underneath, floating in water, or holding on to a ladder. */
	private boolean standable(int x, int y, int z) {
		if (standableWithFloor(x, y, z)) {
			return true;
		}
		int c = cell(x, y, z);
		return c == BlockView.WATER || climbable(c);
	}

	/** Solid ground underneath, or the top of scaffolding. */
	private boolean standableWithFloor(int x, int y, int z) {
		int c = cell(x, y - 1, z);
		return c == BlockView.SOLID || c == BlockView.SCAFFOLDING;
	}

	private double headWater(int x, int y, int z) {
		return cell(x, y + 1, z) == BlockView.WATER ? UNDERWATER : 0;
	}

	private int cell(int x, int y, int z) {
		long key = pack(x, y, z);
		int cell = cells.get(key);
		if (cell < 0) {
			cell = world.cell(x, y, z);
			cells.put(key, cell);
		}
		return cell;
	}

	private float breakTicks(int x, int y, int z) {
		long key = pack(x, y, z);
		float ticks = breakTimes.get(key);
		if (Float.isNaN(ticks)) {
			ticks = world.breakTicks(x, y, z);
			breakTimes.put(key, ticks);
		}
		return ticks;
	}

	private static List<Path.Step> trace(Node end) {
		List<Path.Step> steps = new ArrayList<>();
		for (Node n = end; n.parent != null; n = n.parent) {
			steps.add(new Path.Step(n.x, n.y, n.z, n.move, n.breaks));
		}
		Collections.reverse(steps);
		return steps;
	}

	// 26 bits of x, 26 of z, 12 of y: covers the whole world border and build height.
	public static long pack(int x, int y, int z) {
		return ((long) x & 0x3FFFFFFL) << 38 | ((long) z & 0x3FFFFFFL) << 12 | (y & 0xFFFL);
	}

	public static int unpackX(long packed) {
		return (int) (packed >> 38);
	}

	public static int unpackY(long packed) {
		return (int) (packed << 52 >> 52);
	}

	public static int unpackZ(long packed) {
		return (int) (packed << 26 >> 38);
	}
}
