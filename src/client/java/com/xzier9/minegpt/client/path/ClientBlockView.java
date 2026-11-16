package com.xzier9.minegpt.client.path;

import com.xzier9.minegpt.client.MineGPTClient;
import com.xzier9.minegpt.client.skill.Mining;
import com.xzier9.minegpt.path.BlockView;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.tags.BlockTags;
import net.minecraft.tags.FluidTags;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.level.BlockGetter;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.BubbleColumnBlock;
import net.minecraft.world.level.block.FallingBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.material.FlowingFluid;
import net.minecraft.world.level.material.FluidState;
import net.minecraft.world.phys.shapes.VoxelShape;

/**
 * The pathfinder's view of the client's world: what the player could walk through, stand
 * on, climb, open or dig, judged from the blocks the client has loaded. Unloaded chunks count as walls.
 */
public final class ClientBlockView implements BlockView {
	/** Collision lower than this (carpets, snow layers, pressure plates) is walked over like air. */
	private static final double FLAT_HEIGHT = 0.2;
	/** Water flowing into a dug hole slows things down. */
	private static final float WATER_PENALTY = 20;
	/** Sand or gravel above will fall into the hole and has to be dug again. */
	private static final float FALLING_PENALTY = 30;

	private final ClientLevel level;
	private final Inventory inventory;
	private final BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
	private final BlockPos.MutableBlockPos neighbour = new BlockPos.MutableBlockPos();

	public ClientBlockView(ClientLevel level, Inventory inventory) {
		this.level = level;
		this.inventory = inventory;
	}

	@Override
	public int cell(int x, int y, int z) {
		if (level.isOutsideBuildHeight(y) || !level.hasChunk(x >> 4, z >> 4)) {
			return UNLOADED;
		}
		pos.set(x, y, z);
		return classify(level, pos, level.getBlockState(pos));
	}

	public static int classify(BlockGetter level, BlockPos pos, BlockState state) {
		if (Mining.isDangerous(state)) {
			return DANGER;
		}
		if (state.is(Blocks.SCAFFOLDING)) {
			return SCAFFOLDING;
		}
		if (state.is(BlockTags.CLIMBABLE)) {
			return CLIMBABLE;
		}
		if (isDoor(state)) {
			return DOOR;
		}
		FluidState fluid = state.getFluidState();
		VoxelShape shape = state.getCollisionShape(level, pos);
		if (shape.isEmpty() || shape.max(Direction.Axis.Y) < FLAT_HEIGHT) {
			return fluid.is(FluidTags.WATER) ? WATER : PASSABLE;
		}
		if (state.is(BlockTags.LEAVES)) {
			// Tree tops are no place to walk: the path goes round trees, never over them.
			return OBSTACLE;
		}
		if (state.isCollisionShapeFullBlock(level, pos)) {
			return SOLID;
		}
		// Farmland, soul sand, top slabs and similar: full footprint and a top close to a full block.
		boolean fullFootprint = shape.min(Direction.Axis.X) <= 0 && shape.max(Direction.Axis.X) >= 1
				&& shape.min(Direction.Axis.Z) <= 0 && shape.max(Direction.Axis.Z) >= 1;
		double top = shape.max(Direction.Axis.Y);
		return fullFootprint && top >= 0.8 && top <= 1.0 ? SOLID : OBSTACLE;
	}

	/** Wooden doors and fence gates, which open by hand. Iron doors and trapdoors are left alone. */
	public static boolean isDoor(BlockState state) {
		return state.is(BlockTags.WOODEN_DOORS) || state.is(BlockTags.FENCE_GATES);
	}

	@Override
	public int current(int x, int y, int z) {
		if (level.isOutsideBuildHeight(y) || !level.hasChunk(x >> 4, z >> 4)) {
			return CURRENT_NONE;
		}
		pos.set(x, y, z);
		BlockState state = level.getBlockState(pos);
		if (state.getBlock() instanceof BubbleColumnBlock) {
			// A column over magma drags the player to the bottom; one over soul sand throws them up.
			return state.getValue(BubbleColumnBlock.DRAG_DOWN) ? CURRENT_DOWN : CURRENT_SIDEWAYS;
		}
		FluidState fluid = state.getFluidState();
		if (!fluid.is(FluidTags.WATER) || fluid.isSource()) {
			return CURRENT_NONE;
		}
		return fluid.getValueOrElse(FlowingFluid.FALLING, false) ? CURRENT_DOWN : CURRENT_SIDEWAYS;
	}

	@Override
	public boolean placeable(int x, int y, int z) {
		return !MineGPTClient.safety().isProtected(Minecraft.getInstance(), pos.set(x, y, z));
	}

	@Override
	public float breakTicks(int x, int y, int z) {
		pos.set(x, y, z);
		BlockState state = level.getBlockState(pos);
		if (!Mining.isNaturalTerrain(state) || state.hasBlockEntity()
				|| MineGPTClient.safety().isProtected(Minecraft.getInstance(), pos)) {
			return -1;
		}
		float hardness = state.getDestroySpeed(level, pos);
		if (hardness < 0) {
			return -1;
		}
		float ticks = Mining.bestBreakTicks(inventory, state, hardness);
		for (Direction direction : Direction.values()) {
			neighbour.setWithOffset(pos, direction);
			FluidState fluid = level.getBlockState(neighbour).getFluidState();
			if (fluid.is(FluidTags.LAVA) || (direction == Direction.UP && fluid.is(FluidTags.WATER))) {
				// Opening this block would let lava in, or pour water down onto the player.
				return -1;
			}
			if (fluid.is(FluidTags.WATER)) {
				ticks += WATER_PENALTY;
			}
		}
		neighbour.setWithOffset(pos, Direction.UP);
		if (level.getBlockState(neighbour).getBlock() instanceof FallingBlock) {
			ticks += FALLING_PENALTY;
		}
		return ticks;
	}
}
