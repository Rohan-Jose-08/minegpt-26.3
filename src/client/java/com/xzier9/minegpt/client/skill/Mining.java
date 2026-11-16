package com.xzier9.minegpt.client.skill;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.tags.BlockTags;
import net.minecraft.tags.FluidTags;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.BlockGetter;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;

/** Which blocks are safe to break or walk into, and which tool breaks them fastest. */
public final class Mining {
	private Mining() {
	}

	/**
	 * Natural terrain the pathfinder may dig through: stone, dirt, sand, gravel, ores, leaves and
	 * the like. Anything else (planks, glass, farmland, chests) could be someone's build, so paths
	 * go around it, which also keeps the agent from griefing on servers.
	 */
	public static boolean isNaturalTerrain(BlockState state) {
		if (state.is(Blocks.SUSPICIOUS_SAND) || state.is(Blocks.SUSPICIOUS_GRAVEL)) {
			// Breaking these destroys their archaeology loot.
			return false;
		}
		if (state.is(BlockTags.BASE_STONE_OVERWORLD) || state.is(BlockTags.BASE_STONE_NETHER)
				|| state.is(BlockTags.SUBSTRATE_OVERWORLD) || state.is(BlockTags.SAND) || state.is(BlockTags.LEAVES)
				|| state.is(BlockTags.BADLANDS_TERRACOTTA) || state.is(BlockTags.ORES)) {
			return true;
		}
		if (state.is(Blocks.GRAVEL) || state.is(Blocks.SANDSTONE) || state.is(Blocks.RED_SANDSTONE)
				|| state.is(Blocks.SNOW) || state.is(Blocks.SNOW_BLOCK) || state.is(Blocks.CLAY)
				|| state.is(Blocks.SOUL_SAND) || state.is(Blocks.SOUL_SOIL) || state.is(Blocks.BASALT)
				|| state.is(Blocks.SMOOTH_BASALT) || state.is(Blocks.CALCITE) || state.is(Blocks.DRIPSTONE_BLOCK)
				|| state.is(Blocks.SCULK) || state.is(Blocks.MAGMA_BLOCK)) {
			return true;
		}
		return BuiltInRegistries.BLOCK.getKey(state.getBlock()).getPath().endsWith("_ore");
	}

	/**
	 * What may be cleared off a building site: natural terrain, and what grows on it, such as
	 * flowers, saplings, mushrooms, vines and the logs of standing trees (see {@link #isTreeLog}).
	 * Wider than {@link #isNaturalTerrain}, which is what a path may dig through: a path should go
	 * round a tree, but a house cannot be built through one.
	 */
	public static boolean isClearable(BlockGetter level, BlockPos pos, BlockState state) {
		if (isNaturalTerrain(state)) {
			return true;
		}
		if (state.hasBlockEntity()) {
			return false;
		}
		if (state.is(BlockTags.OVERWORLD_NATURAL_LOGS)) {
			return isTreeLog(level, pos);
		}
		return state.is(BlockTags.FLOWERS) || state.is(BlockTags.SAPLINGS) || state.is(BlockTags.REPLACEABLE_BY_TREES)
				|| state.is(Blocks.BROWN_MUSHROOM) || state.is(Blocks.RED_MUSHROOM) || state.is(Blocks.SUGAR_CANE)
				|| state.is(Blocks.BAMBOO) || state.is(Blocks.PUMPKIN) || state.is(Blocks.MELON)
				|| state.is(Blocks.BROWN_MUSHROOM_BLOCK) || state.is(Blocks.RED_MUSHROOM_BLOCK)
				|| state.is(Blocks.MUSHROOM_STEM) || state.is(Blocks.LILY_PAD) || state.is(Blocks.MOSS_CARPET);
	}

	/** How far up a trunk to look for its leaves. */
	private static final int TRUNK_HEIGHT = 32;

	/**
	 * A small block that can be walked through and breaks at the first hit: short grass, ferns,
	 * flowers, saplings, torches, dead bushes. Its outline still stops the crosshair, so it can
	 * hide the block behind it or under it.
	 */
	public static boolean isSoft(BlockGetter level, BlockPos pos, BlockState state) {
		return !state.isAir() && state.getFluidState().isEmpty() && !state.hasBlockEntity()
				&& state.getCollisionShape(level, pos).isEmpty() && !state.getShape(level, pos).isEmpty()
				&& state.getDestroySpeed(level, pos) == 0;
	}

	/**
	 * True for a soft block ({@link #isSoft}) that may be knocked out of the way: a plant, or
	 * anything MineGPT put there itself, such as its own torch. A torch someone else placed is
	 * left alone.
	 */
	public static boolean mayKnockOff(net.minecraft.client.Minecraft mc, BlockGetter level, BlockPos pos, BlockState state) {
		if (!isSoft(level, pos, state) || touchesLava(level, pos)
				|| !com.xzier9.minegpt.client.MineGPTClient.safety().mayBreak(mc, pos)) {
			return false;
		}
		return isClearable(level, pos, state) || state.canBeReplaced() || OwnBlocks.owns(mc, pos, state) != null;
	}

	/**
	 * True if the log at {@code pos} is part of a standing tree and not of a build: following the
	 * logs straight up from it ends under or beside leaves. Log posts of a house end under a roof.
	 */
	public static boolean isTreeLog(BlockGetter level, BlockPos pos) {
		BlockPos.MutableBlockPos at = new BlockPos.MutableBlockPos().set(pos);
		BlockPos.MutableBlockPos next = new BlockPos.MutableBlockPos();
		for (int i = 0; i < TRUNK_HEIGHT; i++) {
			for (Direction direction : Direction.values()) {
				if (direction != Direction.DOWN && level.getBlockState(next.setWithOffset(at, direction)).is(BlockTags.LEAVES)) {
					return true;
				}
			}
			// Up the trunk, which may lean a block sideways, as acacias and big oaks do.
			BlockPos up = null;
			for (int dx = -1; dx <= 1 && up == null; dx++) {
				for (int dz = -1; dz <= 1 && up == null; dz++) {
					if (level.getBlockState(next.set(at.getX() + dx, at.getY() + 1, at.getZ() + dz)).is(BlockTags.LOGS)) {
						up = next.immutable();
					}
				}
			}
			if (up == null) {
				return false;
			}
			at.set(up);
		}
		return false;
	}

	/** Blocks that hurt, trap or teleport the player. Paths never enter or stand on them. */
	public static boolean isDangerous(BlockState state) {
		return state.getFluidState().is(FluidTags.LAVA)
				|| state.is(BlockTags.FIRE) || state.is(BlockTags.CAMPFIRES)
				|| state.is(Blocks.MAGMA_BLOCK) || state.is(Blocks.CACTUS) || state.is(Blocks.POWDER_SNOW)
				|| state.is(Blocks.SWEET_BERRY_BUSH) || state.is(Blocks.WITHER_ROSE) || state.is(Blocks.COBWEB)
				|| state.is(Blocks.NETHER_PORTAL) || state.is(Blocks.END_PORTAL) || state.is(Blocks.END_GATEWAY)
				|| state.is(Blocks.LAVA_CAULDRON) || state.is(Blocks.BUBBLE_COLUMN);
	}

	/** True if lava touches the block, so breaking it would let the lava out. */
	public static boolean touchesLava(BlockGetter level, BlockPos pos) {
		BlockPos.MutableBlockPos next = new BlockPos.MutableBlockPos();
		for (Direction direction : Direction.values()) {
			if (level.getFluidState(next.setWithOffset(pos, direction)).is(FluidTags.LAVA)) {
				return true;
			}
		}
		return false;
	}

	/** Ticks to break a block with this tool (or bare hands for an empty stack), ignoring enchantments. */
	public static float breakTicks(ItemStack tool, BlockState state, float hardness) {
		if (hardness <= 0) {
			return 1;
		}
		float speed = tool.isEmpty() ? 1.0F : tool.getDestroySpeed(state);
		boolean correct = !state.requiresCorrectToolForDrops() || (!tool.isEmpty() && tool.isCorrectToolForDrops(state));
		float progressPerTick = speed / hardness / (correct ? 30.0F : 100.0F);
		return (float) Math.ceil(1.0F / progressPerTick);
	}

	/**
	 * The inventory slot (0-35) of the best tool for a block, or -1 when bare hands are as good.
	 * A tool that gets the block's drops always beats one that does not, then faster wins.
	 */
	public static int bestToolSlot(Inventory inventory, BlockState state, float hardness) {
		int best = -1;
		float bestTicks = breakTicks(ItemStack.EMPTY, state, hardness);
		boolean bestHarvests = !state.requiresCorrectToolForDrops();
		for (int slot = 0; slot < Inventories.MAIN_SIZE; slot++) {
			ItemStack stack = inventory.getItem(slot);
			if (stack.isEmpty()) {
				continue;
			}
			boolean harvests = !state.requiresCorrectToolForDrops() || stack.isCorrectToolForDrops(state);
			float ticks = breakTicks(stack, state, hardness);
			if ((harvests && !bestHarvests) || (harvests == bestHarvests && ticks < bestTicks)) {
				best = slot;
				bestTicks = ticks;
				bestHarvests = harvests;
			}
		}
		return best;
	}

	/** Ticks with the best tool carried. */
	public static float bestBreakTicks(Inventory inventory, BlockState state, float hardness) {
		int slot = bestToolSlot(inventory, state, hardness);
		return breakTicks(slot < 0 ? ItemStack.EMPTY : inventory.getItem(slot), state, hardness);
	}

	/** True if breaking this block with something carried will drop it. */
	public static boolean canHarvest(Inventory inventory, BlockState state) {
		if (!state.requiresCorrectToolForDrops()) {
			return true;
		}
		for (int slot = 0; slot < Inventories.MAIN_SIZE; slot++) {
			ItemStack stack = inventory.getItem(slot);
			if (!stack.isEmpty() && stack.isCorrectToolForDrops(state)) {
				return true;
			}
		}
		return false;
	}

	/** The kind of tool a block wants, for error messages. */
	public static String toolKind(BlockState state) {
		if (state.is(BlockTags.MINEABLE_WITH_PICKAXE)) {
			return "pickaxe";
		}
		if (state.is(BlockTags.MINEABLE_WITH_AXE)) {
			return "axe";
		}
		if (state.is(BlockTags.MINEABLE_WITH_SHOVEL)) {
			return "shovel";
		}
		if (state.is(BlockTags.MINEABLE_WITH_HOE)) {
			return "hoe";
		}
		return "tool";
	}
}
