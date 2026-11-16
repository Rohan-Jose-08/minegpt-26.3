package com.xzier9.minegpt.client.skill;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.LiquidBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

/**
 * Breaks one block within reach: picks the best tool, turns to a visible face and holds
 * the attack key until the block is gone. Used by goto (to dig) and by mine.
 */
final class BreakBlockSkill extends Skill {
	/** Ticks of aiming without the crosshair landing on the block before giving up. */
	private static final int AIM_LIMIT = 30;

	private final BlockPos target;
	private Vec3 aim;
	private int aimTicks;
	private int breakTicks;
	private int expected;
	/** The block being broken; it can change when sand or gravel falls into the gap. */
	private Block current;
	/** A soft block (grass, a flower, a torch) in the line of sight, knocked off first. */
	private BlockPos blocker;
	private int blockerTicks;
	/** Soft blocks knocked off so far; a few at most. */
	private int knocked;
	private static final int MAX_KNOCKED = 6;

	BreakBlockSkill(BlockPos target) {
		super("break", 0);
		this.target = target;
	}

	BlockPos target() {
		return target;
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		BlockState state = ctx.level.getBlockState(target);
		if (isGone(state)) {
			return SkillResult.success("broke " + target.toShortString());
		}
		float hardness = state.getDestroySpeed(ctx.level, target);
		if (hardness < 0) {
			return SkillResult.failure("cannot break " + blockName(state));
		}
		if (ctx.mc.gui.screen() != null) {
			ctx.closeScreen();
			return null;
		}
		if (blocker != null) {
			// Knock the grass, flower or torch in the way off first: it goes at the first hit.
			BlockState soft = ctx.level.getBlockState(blocker);
			if (isGone(soft) || soft.canBeReplaced() && soft.getShape(ctx.level, blocker).isEmpty()) {
				blocker = null;
				current = null;
				return null;
			}
			ctx.lookAt(softAim(ctx, blocker));
			if (ctx.isLookingAt(blocker)) {
				ctx.clickAttack();
			}
			if (++blockerTicks > 30) {
				return SkillResult.failure("cannot see " + blockName(state) + " at " + target.toShortString()
						+ ": " + blockName(soft) + " in the way would not come off");
			}
			return null;
		}
		if (state.getBlock() != current) {
			current = state.getBlock();
			int slot = Mining.bestToolSlot(ctx.player.getInventory(), state, hardness);
			Inventories.select(ctx, slot);
			expected = (int) Mining.bestBreakTicks(ctx.player.getInventory(), state, hardness);
			breakTicks = 0;
			aimTicks = 0;
			aim = aimPoint(ctx, target);
			if (aim == null) {
				BlockPos soft = knocked < MAX_KNOCKED ? softBlocker(ctx, target, ctx.player.getEyePosition()) : null;
				if (soft != null) {
					blocker = soft;
					blockerTicks = 0;
					knocked++;
					current = null;
					return null;
				}
				return SkillResult.failure("cannot see " + blockName(state) + " at " + target.toShortString());
			}
		}

		ctx.lookAt(aim);
		if (ctx.isLookingAt(target)) {
			ctx.holdAttack();
			breakTicks++;
			if (breakTicks > expected * 2 + 60) {
				return SkillResult.failure("breaking " + blockName(state) + " is taking too long");
			}
		} else if (++aimTicks > AIM_LIMIT) {
			return SkillResult.failure("cannot aim at " + blockName(state) + " at " + target.toShortString());
		}
		return null;
	}

	@Override
	protected String describe() {
		return "breaking block at " + target.toShortString();
	}

	/** Air or plain water/lava is what is left after breaking a block. */
	static boolean isGone(BlockState state) {
		return state.isAir() || state.getBlock() instanceof LiquidBlock;
	}

	static String blockName(BlockState state) {
		return BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString();
	}

	/** A point on the block the player can see from where they stand: the centre or a face. */
	/** A point on the block the crosshair can rest on from where the player stands, or null if none is in sight. */
	static Vec3 aimPoint(SkillContext ctx, BlockPos target) {
		return aimPoint(ctx, target, ctx.player.getEyePosition());
	}

	/** Like {@link #aimPoint(SkillContext, BlockPos)}, seen from {@code eye} instead of where the player is. */
	static Vec3 aimPoint(SkillContext ctx, BlockPos target, Vec3 eye) {
		Vec3 centre = Vec3.atCenterOf(target);
		if (hits(ctx, target, eye, centre)) {
			return centre;
		}
		// Flat things, such as petals on the ground or a carpet, have no outline at their centre.
		var shape = ctx.level.getBlockState(target).getShape(ctx.level, target);
		if (!shape.isEmpty()) {
			Vec3 middle = shape.bounds().getCenter().add(Vec3.atLowerCornerOf(target));
			if (hits(ctx, target, eye, middle)) {
				return middle;
			}
		}
		Vec3 best = null;
		double bestDistance = Double.MAX_VALUE;
		for (Direction face : Direction.values()) {
			Vec3 point = centre.add(face.getStepX() * 0.45, face.getStepY() * 0.45, face.getStepZ() * 0.45);
			double distance = eye.distanceToSqr(point);
			if (distance < bestDistance && hits(ctx, target, eye, point)) {
				best = point;
				bestDistance = distance;
			}
		}
		return best;
	}

	/** Where to aim at a soft block: the middle of its outline, which can be low (a flower) or off centre. */
	static Vec3 softAim(SkillContext ctx, BlockPos pos) {
		var shape = ctx.level.getBlockState(pos).getShape(ctx.level, pos);
		return shape.isEmpty() ? Vec3.atCenterOf(pos) : shape.bounds().getCenter().add(Vec3.atLowerCornerOf(pos));
	}

	/**
	 * A soft block ({@link Mining#isSoft}) that may be knocked off and is all that hides
	 * {@code target} from {@code eye}: the crosshair stops on it, but with it gone a point of the
	 * target would be in sight and in reach. Null when there is none.
	 */
	static BlockPos softBlocker(SkillContext ctx, BlockPos target, Vec3 eye) {
		Vec3 centre = Vec3.atCenterOf(target);
		java.util.List<Vec3> points = new java.util.ArrayList<>();
		points.add(centre);
		for (Direction face : Direction.values()) {
			points.add(centre.add(face.getStepX() * 0.45, face.getStepY() * 0.45, face.getStepZ() * 0.45));
		}
		double reach = ctx.player.blockInteractionRange();
		for (Vec3 point : points) {
			if (eye.distanceToSqr(point) > reach * reach) {
				continue;
			}
			BlockPos soft = softOnRay(ctx, eye, point, target);
			if (soft != null) {
				return soft;
			}
		}
		return null;
	}

	/**
	 * The soft block the crosshair would stop on looking from {@code eye} at {@code point}, if a
	 * look past soft blocks reaches {@code want} there; null otherwise.
	 */
	static BlockPos softOnRay(SkillContext ctx, Vec3 eye, Vec3 point, BlockPos want) {
		Vec3 end = eye.add(point.subtract(eye).scale(1.05));
		BlockHitResult seen = ctx.level.clip(new ClipContext(eye, end, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE,
				ctx.player));
		if (seen.getType() != HitResult.Type.BLOCK || seen.getBlockPos().equals(want)) {
			return null;
		}
		BlockPos soft = seen.getBlockPos().immutable();
		if (!Mining.mayKnockOff(ctx.mc, ctx.level, soft, ctx.level.getBlockState(soft))) {
			return null;
		}
		// Past soft things: what the ray meets if only solid blocks stop it.
		BlockHitResult past = ctx.level.clip(new ClipContext(eye, end, ClipContext.Block.COLLIDER, ClipContext.Fluid.NONE,
				ctx.player));
		return past.getType() == HitResult.Type.BLOCK && past.getBlockPos().equals(want) ? soft : null;
	}

	/** Eye heights standing and sneaking: a view counts only if it holds for both. */
	private static final double[] EYE_HEIGHTS = {1.62, 1.27};

	/**
	 * The nearest place to stand (feet position) on solid ground from which some face of
	 * {@code target} is in sight and in reach, or null. Spots in {@code avoid} are skipped, and so
	 * are spots inside or right under the block itself.
	 */
	static BlockPos vantage(SkillContext ctx, BlockPos target, java.util.Set<BlockPos> avoid) {
		BlockPos feet = ctx.feet();
		BlockPos best = null;
		double bestScore = Double.MAX_VALUE;
		for (int dy = -3; dy <= 1; dy++) {
			for (int dx = -4; dx <= 4; dx++) {
				for (int dz = -4; dz <= 4; dz++) {
					BlockPos stand = target.offset(dx, dy, dz);
					if (avoid.contains(stand) || (dx == 0 && dz == 0 && dy >= -2) || !standable(ctx, stand)) {
						continue;
					}
					boolean seen = true;
					for (double height : EYE_HEIGHTS) {
						Vec3 eye = new Vec3(stand.getX() + 0.5, stand.getY() + height, stand.getZ() + 0.5);
						if (aimPoint(ctx, target, eye) == null) {
							seen = false;
							break;
						}
					}
					if (!seen) {
						continue;
					}
					double score = Math.sqrt(feet.distSqr(stand));
					if (score < bestScore) {
						best = stand;
						bestScore = score;
					}
				}
			}
		}
		return best;
	}

	private static boolean standable(SkillContext ctx, BlockPos feet) {
		BlockPos floor = feet.below();
		if (!ctx.level.getBlockState(floor).isFaceSturdy(ctx.level, floor, Direction.UP)) {
			return false;
		}
		for (BlockPos cell : new BlockPos[] {feet, feet.above()}) {
			BlockState state = ctx.level.getBlockState(cell);
			if (!state.getCollisionShape(ctx.level, cell).isEmpty() || !state.getFluidState().isEmpty()) {
				return false;
			}
		}
		return true;
	}

	private static boolean hits(SkillContext ctx, BlockPos target, Vec3 eye, Vec3 point) {
		// Same ray the crosshair uses: block outlines, fluids ignored.
		Vec3 end = eye.add(point.subtract(eye).scale(1.05));
		BlockHitResult hit = ctx.level.clip(new ClipContext(eye, end, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, ctx.player));
		double reach = ctx.player.blockInteractionRange();
		return hit.getType() == HitResult.Type.BLOCK && hit.getBlockPos().equals(target)
				&& hit.getLocation().distanceToSqr(eye) <= reach * reach;
	}
}
