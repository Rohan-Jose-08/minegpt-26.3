package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;
import com.xzier9.minegpt.client.MineGPTClient;
import com.xzier9.minegpt.client.act.Action.MoveKey;
import com.xzier9.minegpt.client.safety.Safety;
import com.xzier9.minegpt.path.Goal;
import java.util.HashSet;
import java.util.Set;
import java.util.function.Predicate;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.tags.FluidTags;
import net.minecraft.tags.ItemTags;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.CropBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

/**
 * Tends a crop farm: harvests ripe crops (wheat, carrots, potatoes, beetroot and any other
 * {@link CropBlock}), picks up what they drop, and plants the same crop again on the farmland.
 * It can also plant empty farmland, and with {@code till} turn dirt and grass near water into
 * new farmland with a hoe. Unripe crops are never touched.
 */
public final class FarmSkill extends Skill {
	private enum Phase {
		NEXT, APPROACH, HARVEST, GATHER, USE, SWEEP
	}

	private enum Job {
		HARVEST, PLANT, TILL
	}

	private static final double REACH = 4.0;
	private static final int USE_TRIES = 3;
	/** Farmland further than this from water dries out, so tilling stays within it. */
	private static final int WATER_RANGE = 4;

	private final int radius;
	private final boolean replant;
	private final boolean plant;
	private final boolean till;
	private final int max;
	private BlockPos center;
	private Phase phase = Phase.NEXT;
	private Job job;
	/** The crop cell being worked on: the crop's block, or the air above the farmland or dirt. */
	private BlockPos spot;
	/** The seed for the crop that grew at {@link #spot}, to plant back after harvesting. */
	private Item seed;
	private int useTicks;
	private int useTries;
	private final Set<BlockPos> skipped = new HashSet<>();
	private int harvested;
	private int planted;
	private int tilled;
	private String lastProblem;

	public FarmSkill(int radius, boolean replant, boolean plant, boolean till, int max, int maxTicks) {
		super("farm", maxTicks);
		this.radius = radius;
		this.replant = replant;
		this.plant = plant;
		this.till = till;
		this.max = max;
	}

	static FarmSkill fromArgs(Args args) {
		int radius = Math.max(2, Math.min(32, args.integer("radius", 16)));
		int max = Math.max(1, Math.min(256, args.integer("max", 128)));
		return new FarmSkill(radius, args.bool("replant", true), args.bool("plant", true), args.bool("till", false), max,
				args.ticks("timeout", 60 + max * 4));
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		if (center == null) {
			center = ctx.feet();
		}
		SkillResult child = childResult();
		switch (phase) {
			case APPROACH -> {
				if (child != null && !child.success()) {
					skip("could not get there: " + child.message());
					return null;
				}
				if (job == Job.HARVEST) {
					phase = Phase.HARVEST;
					startChild(new BreakBlockSkill(spot));
				} else {
					startUse();
				}
				return null;
			}
			case HARVEST -> {
				if (child == null || !child.success()) {
					skip(child == null ? "harvest interrupted" : child.message());
					return null;
				}
				harvested++;
				phase = Phase.GATHER;
				startChild(new CollectSkill(null, Vec3.atCenterOf(spot), 3, 10, 6 * 20));
				return null;
			}
			case GATHER -> {
				if (replant && seed != null && Inventories.find(ctx.player.getInventory(), s -> s.is(seed)) >= 0) {
					job = Job.PLANT;
					startUse();
				} else {
					phase = Phase.NEXT;
				}
				return null;
			}
			case USE -> {
				return use(ctx);
			}
			case SWEEP -> {
				return finish(true);
			}
			case NEXT -> {
				return next(ctx);
			}
		}
		return null;
	}

	@Override
	protected String describe() {
		return "farming (" + harvested + " harvested, " + planted + " planted)";
	}

	private SkillResult next(SkillContext ctx) {
		if (harvested + planted + tilled >= max) {
			return sweep();
		}
		Inventory inventory = ctx.player.getInventory();
		BlockPos ripe = nearest(ctx, s -> s.getBlock() instanceof CropBlock crop && crop.isMaxAge(s), false, false);
		if (ripe != null && MineGPTClient.safety().mayBreak(ctx.mc, ripe)) {
			job = Job.HARVEST;
			spot = ripe;
			seed = ctx.level.getBlockState(ripe).getBlock().asItem();
			return approach(ctx);
		}
		if (ripe != null) {
			skipped.add(ripe);
			return null;
		}
		if (plant && hasAnySeed(inventory)) {
			BlockPos empty = nearest(ctx, s -> s.is(Blocks.FARMLAND), true, false);
			if (empty != null) {
				job = Job.PLANT;
				spot = empty.above();
				seed = seedFor(ctx, spot);
				return approach(ctx);
			}
		}
		if (till && hasAnySeed(inventory) && Inventories.find(inventory, s -> s.is(ItemTags.HOES)) >= 0) {
			BlockPos dirt = nearest(ctx, s -> s.is(Blocks.DIRT) || s.is(Blocks.GRASS_BLOCK) || s.is(Blocks.COARSE_DIRT)
					|| s.is(Blocks.DIRT_PATH), true, true);
			if (dirt != null) {
				job = Job.TILL;
				spot = dirt.above();
				seed = null;
				return approach(ctx);
			}
		}
		return sweep();
	}

	/** Walks within reach of the spot, or starts at once if already there. */
	private SkillResult approach(SkillContext ctx) {
		BlockPos target = job == Job.HARVEST ? spot : spot.below();
		Goal reach = new Goal.Reach(target.getX(), target.getY(), target.getZ(), REACH);
		BlockPos feet = ctx.feet();
		phase = Phase.APPROACH;
		if (!reach.reached(feet.getX(), feet.getY(), feet.getZ())) {
			// Walking over the crops tramples nothing (only jumping on farmland does), but no digging here.
			startChild(GotoSkill.to(reach, "crops", GotoSkill.Dig.NEVER, 6_000, 30 * 20));
		}
		return null;
	}

	private void startUse() {
		phase = Phase.USE;
		useTicks = 0;
		useTries = 0;
	}

	/** Right-clicks the top of the block under {@link #spot}: plants a seed on farmland, or tills dirt with a hoe. */
	private SkillResult use(SkillContext ctx) {
		BlockPos ground = spot.below();
		BlockState state = ctx.level.getBlockState(ground);
		if (job == Job.PLANT && ctx.level.getBlockState(spot).getBlock() instanceof CropBlock) {
			planted++;
			phase = Phase.NEXT;
			return null;
		}
		if (job == Job.TILL && state.is(Blocks.FARMLAND)) {
			tilled++;
			// Plant the new farmland straight away.
			job = Job.PLANT;
			seed = seedFor(ctx, spot);
			startUse();
			return null;
		}
		if (job == Job.PLANT && (!state.is(Blocks.FARMLAND) || !ctx.level.getBlockState(spot).isAir())) {
			skip("nothing to plant on");
			return null;
		}
		Predicate<ItemStack> hold = job == Job.TILL ? s -> s.is(ItemTags.HOES) : s -> seed != null && s.is(seed);
		if (!Inventories.select(ctx, hold)) {
			if (ctx.player.containerMenu != ctx.player.inventoryMenu) {
				ctx.closeScreen();
				return null;
			}
			skip(job == Job.TILL ? "no hoe" : "no seeds for this crop");
			return null;
		}
		Vec3 top = Vec3.atCenterOf(ground).add(0, 0.5, 0);
		ctx.lookAt(top);
		BlockHitResult hit = ctx.blockTarget();
		boolean aimed = hit != null && hit.getBlockPos().equals(ground) && hit.getDirection() == Direction.UP;
		if (aimed && hold.test(ctx.player.getMainHandItem()) && useTicks % 8 == 0) {
			if (++useTries > USE_TRIES) {
				skip(job == Job.TILL ? "the ground would not till" : "the seed would not plant");
				return null;
			}
			ctx.clickUse();
		}
		if (++useTicks > 60) {
			skip("could not aim at the ground there");
		}
		// Standing still while aiming keeps the click on the block.
		ctx.hold(MoveKey.SNEAK);
		return null;
	}

	/**
	 * The nearest matching block in range not already given up on. With {@code airAbove}, only
	 * ones with air on top, outside protected regions other than farms; with {@code nearWater},
	 * only ones close enough to water to stay farmland.
	 */
	private BlockPos nearest(SkillContext ctx, Predicate<BlockState> match, boolean airAbove, boolean nearWater) {
		for (BlockPos pos : BlockSearch.find(ctx.level, center, radius, match, true, skipped, 64)) {
			if (airAbove) {
				Safety.Region region = MineGPTClient.safety().regionAt(ctx.mc, pos);
				if (!ctx.level.getBlockState(pos.above()).isAir() || (region != null && !region.farm())) {
					continue;
				}
			}
			if (nearWater && !nearWater(ctx, pos)) {
				continue;
			}
			return pos;
		}
		return null;
	}

	private static boolean nearWater(SkillContext ctx, BlockPos pos) {
		for (BlockPos p : BlockPos.betweenClosed(pos.offset(-WATER_RANGE, -1, -WATER_RANGE), pos.offset(WATER_RANGE, 0, WATER_RANGE))) {
			if (ctx.level.getFluidState(p).is(FluidTags.WATER)) {
				return true;
			}
		}
		return false;
	}

	private static boolean isSeed(ItemStack stack) {
		return stack.getItem() instanceof BlockItem item && item.getBlock() instanceof CropBlock;
	}

	private static boolean hasAnySeed(Inventory inventory) {
		return Inventories.find(inventory, FarmSkill::isSeed) >= 0;
	}

	/** The seed to plant at a spot: what grows next to it, else the seed carried most of. */
	private static Item seedFor(SkillContext ctx, BlockPos spot) {
		Inventory inventory = ctx.player.getInventory();
		for (Direction direction : Direction.Plane.HORIZONTAL) {
			BlockState neighbour = ctx.level.getBlockState(spot.relative(direction));
			if (neighbour.getBlock() instanceof CropBlock) {
				Item item = neighbour.getBlock().asItem();
				if (Inventories.find(inventory, s -> s.is(item)) >= 0) {
					return item;
				}
			}
		}
		Item best = null;
		int most = 0;
		for (int slot = 0; slot < Inventories.MAIN_SIZE; slot++) {
			ItemStack stack = inventory.getItem(slot);
			if (isSeed(stack)) {
				Item item = stack.getItem();
				int count = Inventories.count(inventory, s -> s.is(item));
				if (count > most) {
					best = item;
					most = count;
				}
			}
		}
		return best;
	}

	private void skip(String problem) {
		lastProblem = problem;
		if (spot != null) {
			skipped.add(job == Job.HARVEST ? spot : spot.below());
		}
		phase = Phase.NEXT;
	}

	private SkillResult sweep() {
		phase = Phase.SWEEP;
		startChild(new CollectSkill(null, Vec3.atCenterOf(center), radius, 0, 30 * 20));
		return null;
	}

	private SkillResult finish(boolean done) {
		JsonObject data = new JsonObject();
		data.addProperty("harvested", harvested);
		data.addProperty("planted", planted);
		data.addProperty("tilled", tilled);
		data.addProperty("skipped", skipped.size());
		String message = "harvested " + harvested + ", planted " + planted + (tilled > 0 ? ", tilled " + tilled : "");
		if (harvested + planted + tilled == 0) {
			return SkillResult.failure("nothing to do: no ripe crops" + (plant ? " or empty farmland" : "")
					+ " within " + radius + " blocks" + (lastProblem != null ? " (last problem: " + lastProblem + ")" : ""),
					data);
		}
		return SkillResult.success(message + (skipped.isEmpty() ? "" : "; skipped " + skipped.size()
				+ (lastProblem != null ? " (" + lastProblem + ")" : "")), data);
	}
}
