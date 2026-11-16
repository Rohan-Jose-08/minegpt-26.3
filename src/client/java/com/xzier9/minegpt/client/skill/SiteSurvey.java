package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.xzier9.minegpt.client.MineGPTClient;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.core.BlockPos;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.levelgen.Heightmap;

/**
 * Looks for flat, open ground to build on: the bridge's {@code survey} op.
 *
 * It reads the height of the ground in every column around the player, looking through
 * leaves, logs and plants, and scores every rectangle of the asked size by how many blocks
 * would have to be dug away or filled in to level it. Water, lava, trees, blocks someone
 * built, and protected regions count against a site or rule it out.
 */
public final class SiteSurvey {
	private static final int MAX_RADIUS = 40;
	/** A column this far off the site's level rules the site out. */
	private static final int MAX_STEP = 3;

	private static final int OPEN = 0;
	private static final int TREE = 1;
	private static final int WATER = 2;
	private static final int BLOCKED = 3;

	private record Site(int x, int y, int z, int work, int trees, int water, double distance) {
	}

	private SiteSurvey() {
	}

	/** The best few sites of {@code width} (x) by {@code depth} (z) within {@code radius} of the player. */
	public static JsonArray find(Minecraft mc, int width, int depth, int radius, int limit) {
		ClientLevel level = mc.level;
		BlockPos feet = mc.player.blockPosition();
		radius = Math.max(Math.max(width, depth), Math.min(MAX_RADIUS, radius));
		int size = 2 * radius + 1;
		int minX = feet.getX() - radius;
		int minZ = feet.getZ() - radius;
		int[][] height = new int[size][size];
		int[][] kind = new int[size][size];
		BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
		for (int i = 0; i < size; i++) {
			for (int j = 0; j < size; j++) {
				int x = minX + i;
				int z = minZ + j;
				if (!level.hasChunk(x >> 4, z >> 4)) {
					kind[i][j] = BLOCKED;
					continue;
				}
				int y = level.getHeight(Heightmap.Types.WORLD_SURFACE, x, z) - 1;
				int column = OPEN;
				// Down through the tree, if any, to the ground under it.
				for (int steps = 0; steps < 40 && y > level.getMinY(); steps++, y--) {
					BlockState state = level.getBlockState(pos.set(x, y, z));
					if (state.is(BlockTags.LEAVES) || state.is(BlockTags.LOGS)) {
						column = TREE;
					} else if (!state.getFluidState().isEmpty()) {
						column = state.getFluidState().is(net.minecraft.tags.FluidTags.WATER) ? WATER : BLOCKED;
						break;
					} else if (!state.canBeReplaced() && !state.getCollisionShape(level, pos).isEmpty()) {
						if (!Mining.isNaturalTerrain(state) || state.hasBlockEntity() || Mining.isDangerous(state)) {
							// Something built, or something that hurts.
							column = BLOCKED;
						}
						break;
					}
				}
				if (column != BLOCKED && MineGPTClient.safety().isProtected(mc, pos.set(x, y, z))) {
					column = BLOCKED;
				}
				height[i][j] = y;
				kind[i][j] = column;
			}
		}

		List<Site> sites = new ArrayList<>();
		int[] histogram = new int[level.getMaxY() - level.getMinY() + 2];
		for (int i = 0; i + width <= size; i++) {
			for (int j = 0; j + depth <= size; j++) {
				Site site = score(level, height, kind, i, j, width, depth, histogram, minX, minZ, feet);
				if (site != null) {
					sites.add(site);
				}
			}
		}
		// Least work first; a block of walking counts as a quarter of a block of digging.
		sites.sort(Comparator.comparingDouble(s -> s.work() + 4 * s.trees() + s.distance() / 4));
		JsonArray out = new JsonArray();
		List<Site> chosen = new ArrayList<>();
		for (Site site : sites) {
			// Skip sites that mostly overlap one already listed.
			boolean overlaps = chosen.stream().anyMatch(c -> Math.abs(c.x() - site.x()) < width && Math.abs(c.z() - site.z()) < depth);
			if (overlaps) {
				continue;
			}
			chosen.add(site);
			JsonObject o = new JsonObject();
			o.addProperty("x", site.x());
			o.addProperty("y", site.y());
			o.addProperty("z", site.z());
			o.addProperty("work", site.work());
			o.addProperty("trees", site.trees());
			o.addProperty("water", site.water());
			o.addProperty("distance", Math.round(site.distance()));
			out.add(o);
			if (chosen.size() >= limit) {
				break;
			}
		}
		return out;
	}

	private static Site score(ClientLevel level, int[][] height, int[][] kind, int i0, int j0, int width, int depth,
			int[] histogram, int minX, int minZ, BlockPos feet) {
		int trees = 0;
		int water = 0;
		int lowest = Integer.MAX_VALUE;
		int highest = Integer.MIN_VALUE;
		for (int i = i0; i < i0 + width; i++) {
			for (int j = j0; j < j0 + depth; j++) {
				switch (kind[i][j]) {
					case BLOCKED -> {
						return null;
					}
					case TREE -> trees++;
					case WATER -> water++;
					default -> {
					}
				}
				lowest = Math.min(lowest, height[i][j]);
				highest = Math.max(highest, height[i][j]);
			}
		}
		if (highest - lowest > 2 * MAX_STEP || water * 5 > width * depth) {
			return null;
		}
		// The site's level is the most common ground height in it.
		int base = level.getMinY();
		for (int h = lowest; h <= highest; h++) {
			histogram[h - base] = 0;
		}
		for (int i = i0; i < i0 + width; i++) {
			for (int j = j0; j < j0 + depth; j++) {
				histogram[height[i][j] - base]++;
			}
		}
		int ground = lowest;
		for (int h = lowest; h <= highest; h++) {
			if (histogram[h - base] > histogram[ground - base]) {
				ground = h;
			}
		}
		int work = 0;
		for (int i = i0; i < i0 + width; i++) {
			for (int j = j0; j < j0 + depth; j++) {
				int off = Math.abs(height[i][j] - ground);
				if (off > MAX_STEP) {
					return null;
				}
				work += off + (kind[i][j] == WATER ? 2 : 0);
			}
		}
		int x = minX + i0;
		int z = minZ + j0;
		double dx = x + width / 2.0 - feet.getX();
		double dz = z + depth / 2.0 - feet.getZ();
		// y is where the floor goes: the first layer of air above the ground.
		return new Site(x, ground + 1, z, work, trees, water, Math.sqrt(dx * dx + dz * dz));
	}
}
