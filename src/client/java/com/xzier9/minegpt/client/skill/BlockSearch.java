package com.xzier9.minegpt.client.skill;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Set;
import java.util.function.Predicate;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.LevelChunk;
import net.minecraft.world.level.chunk.LevelChunkSection;

/** Finds blocks near the player in the chunks the client has loaded. */
public final class BlockSearch {
	private BlockSearch() {
	}

	/**
	 * Matching blocks within {@code radius} of {@code center}, nearest first.
	 *
	 * @param exposedOnly only blocks with at least one face open to air, water or another
	 *                    see-through block, which is what a player could actually see. With
	 *                    false this also finds blocks buried in stone, which on a server
	 *                    amounts to x-ray, so it is off unless asked for
	 */
	public static List<BlockPos> find(ClientLevel level, BlockPos center, int radius, Predicate<BlockState> match,
			boolean exposedOnly, Set<BlockPos> exclude, int limit) {
		List<BlockPos> found = new ArrayList<>();
		long radiusSq = (long) radius * radius;
		int minY = Math.max(level.getMinY(), center.getY() - radius);
		int maxY = Math.min(level.getMaxY(), center.getY() + radius);
		BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();

		for (int cx = (center.getX() - radius) >> 4; cx <= (center.getX() + radius) >> 4; cx++) {
			for (int cz = (center.getZ() - radius) >> 4; cz <= (center.getZ() + radius) >> 4; cz++) {
				if (!level.hasChunk(cx, cz)) {
					continue;
				}
				LevelChunk chunk = level.getChunk(cx, cz);
				for (int sy = minY >> 4; sy <= maxY >> 4; sy++) {
					int index = level.getSectionIndexFromSectionY(sy);
					if (index < 0 || index >= chunk.getSections().length) {
						continue;
					}
					LevelChunkSection section = chunk.getSection(index);
					// The palette check skips whole sections that cannot contain the block.
					if (section.hasOnlyAir() || !section.maybeHas(match)) {
						continue;
					}
					for (int ly = 0; ly < 16; ly++) {
						int y = (sy << 4) + ly;
						if (y < minY || y > maxY) {
							continue;
						}
						for (int lx = 0; lx < 16; lx++) {
							int x = (cx << 4) + lx;
							for (int lz = 0; lz < 16; lz++) {
								int z = (cz << 4) + lz;
								if (center.distSqr(pos.set(x, y, z)) > radiusSq) {
									continue;
								}
								if (!match.test(section.getBlockState(lx, ly, lz))) {
									continue;
								}
								if (exclude.contains(pos) || (exposedOnly && !isExposed(level, pos))) {
									continue;
								}
								found.add(pos.immutable());
							}
						}
					}
				}
			}
		}
		found.sort(Comparator.comparingDouble(p -> cost(center, p)));
		return found.size() > limit ? new ArrayList<>(found.subList(0, limit)) : found;
	}

	/** True if a player could see at least one face of the block. */
	public static boolean isExposed(ClientLevel level, BlockPos pos) {
		BlockPos.MutableBlockPos next = new BlockPos.MutableBlockPos();
		for (Direction direction : Direction.values()) {
			next.setWithOffset(pos, direction);
			if (!level.getBlockState(next).isSolidRender()) {
				return true;
			}
		}
		return false;
	}

	/** Distance, with blocks high above counting extra since reaching them may need climbing. */
	private static double cost(BlockPos from, BlockPos to) {
		double distance = Math.sqrt(from.distSqr(to));
		int above = to.getY() - from.getY() - 3;
		return above > 0 ? distance + above * 3 : distance;
	}
}
