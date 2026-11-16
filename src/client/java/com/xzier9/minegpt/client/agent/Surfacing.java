package com.xzier9.minegpt.client.agent;

import com.xzier9.minegpt.client.act.Action;
import com.xzier9.minegpt.client.act.Action.MoveKey;
import com.xzier9.minegpt.client.act.ActionExecutor;
import com.xzier9.minegpt.client.path.ClientBlockView;
import com.xzier9.minegpt.path.BlockView;
import com.xzier9.minegpt.path.Pathfinder;
import it.unimi.dsi.fastutil.longs.Long2LongOpenHashMap;
import it.unimi.dsi.fastutil.longs.LongArrayFIFOQueue;
import java.util.EnumSet;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;

/**
 * Swims the player to the nearest air when it is low on breath. Straight up is not always
 * the way: under a ledge or in a flooded cave the nearest air can be off to the side, so this
 * searches through the water around the head for the closest air the head can reach, and
 * swims along that route.
 */
final class Surfacing {
	private static final int HORIZONTAL = 10;
	private static final int VERTICAL = 16;
	private static final int MAX_CELLS = 4000;
	/** Ticks between searches; the route barely changes from one tick to the next. */
	private static final int REPLAN_TICKS = 10;
	/** How many cells along the route to steer at, so the player does not zigzag cell by cell. */
	private static final int LOOK_AHEAD = 2;

	private BlockPos aim;
	private int ticksSincePlan = REPLAN_TICKS;

	void reset() {
		aim = null;
		ticksSincePlan = REPLAN_TICKS;
	}

	/** Presses the keys for one tick of swimming towards air. */
	void tick(Minecraft mc, ActionExecutor executor) {
		LocalPlayer player = mc.player;
		BlockPos head = BlockPos.containing(player.getEyePosition());
		if (++ticksSincePlan >= REPLAN_TICKS || aim == null) {
			aim = route(new ClientBlockView(mc.level, player.getInventory()), head);
			ticksSincePlan = 0;
		}
		EnumSet<MoveKey> keys = EnumSet.noneOf(MoveKey.class);
		if (aim == null || aim.getY() > head.getY() && aim.getX() == head.getX() && aim.getZ() == head.getZ()) {
			// Straight up, or no air in reach to head for.
			keys.add(MoveKey.JUMP);
		} else {
			double dx = aim.getX() + 0.5 - player.getX();
			double dz = aim.getZ() + 0.5 - player.getZ();
			executor.lookToward(mc, aim.getX() + 0.5, aim.getY() + 0.5, aim.getZ() + 0.5);
			if (dx * dx + dz * dz > 0.09) {
				keys.add(MoveKey.FORWARD);
			}
			keys.add(aim.getY() < head.getY() ? MoveKey.SNEAK : MoveKey.JUMP);
		}
		executor.submit(mc, new Action.Move(keys, 1));
	}

	/**
	 * A breadth-first search through water from the head's cell to the nearest open air. Returns
	 * the cell a little way along the route to steer at, or null if no air is in reach.
	 */
	private static BlockPos route(BlockView view, BlockPos head) {
		long start = Pathfinder.pack(head.getX(), head.getY(), head.getZ());
		Long2LongOpenHashMap parent = new Long2LongOpenHashMap();
		LongArrayFIFOQueue queue = new LongArrayFIFOQueue();
		parent.put(start, start);
		queue.enqueue(start);
		while (!queue.isEmpty() && parent.size() < MAX_CELLS) {
			long cell = queue.dequeueLong();
			int x = Pathfinder.unpackX(cell);
			int y = Pathfinder.unpackY(cell);
			int z = Pathfinder.unpackZ(cell);
			// Up first, so that of equally near air the one overhead wins.
			for (Direction d : new Direction[] {Direction.UP, Direction.NORTH, Direction.SOUTH, Direction.EAST,
					Direction.WEST, Direction.DOWN}) {
				int nx = x + d.getStepX();
				int ny = y + d.getStepY();
				int nz = z + d.getStepZ();
				if (Math.abs(nx - head.getX()) > HORIZONTAL || Math.abs(nz - head.getZ()) > HORIZONTAL
						|| Math.abs(ny - head.getY()) > VERTICAL) {
					continue;
				}
				long next = Pathfinder.pack(nx, ny, nz);
				if (parent.containsKey(next)) {
					continue;
				}
				int c = view.cell(nx, ny, nz);
				if (c == BlockView.PASSABLE) {
					parent.put(next, cell);
					return along(parent, start, next);
				}
				if (c == BlockView.WATER) {
					parent.put(next, cell);
					queue.enqueue(next);
				}
			}
		}
		return null;
	}

	/** The cell {@link #LOOK_AHEAD} steps from the start along the route to {@code end}. */
	private static BlockPos along(Long2LongOpenHashMap parent, long start, long end) {
		long[] route = new long[parent.size() + 1];
		int length = 0;
		for (long cell = end; cell != start; cell = parent.get(cell)) {
			route[length++] = cell;
		}
		long pick = route[Math.max(0, length - LOOK_AHEAD)];
		return new BlockPos(Pathfinder.unpackX(pick), Pathfinder.unpackY(pick), Pathfinder.unpackZ(pick));
	}
}
