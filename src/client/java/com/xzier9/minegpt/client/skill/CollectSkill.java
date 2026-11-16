package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;
import com.xzier9.minegpt.client.act.Action.MoveKey;
import com.xzier9.minegpt.client.path.ClientBlockView;
import com.xzier9.minegpt.path.Blacklist;
import com.xzier9.minegpt.path.BlockView;
import com.xzier9.minegpt.path.Commitment;
import com.xzier9.minegpt.path.Goal;
import com.xzier9.minegpt.path.Pickup;
import com.xzier9.minegpt.path.StuckWatch;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.EnumSet;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

/**
 * Walks over dropped items to pick them up, and reports what ended up in the inventory.
 * Items floating in deep water or in lava are not worth going in after; items in shallow
 * water, where the player can stand, are.
 *
 * Which item to go for is decided by the route to it, not by how near it looks: one search
 * over the nearest few finds the one that is quickest to walk to, and items with no way to
 * them are left alone for a while, by this and by later collect calls. Once it has set off for
 * an item it keeps to it unless another turns up much closer. Next to the item it checks the
 * sideways gap and the height apart, the way the game does: inside the pickup box it stands
 * still and waits instead of circling, and an item overhead or down a hole gets a path to a
 * spot beside it instead of being walked at.
 */
public final class CollectSkill extends Skill {
	/** Ticks spent walking at an item that will not get picked up before moving on. */
	private static final int CLOSE_LIMIT = 40;
	/** Ticks spent standing in pickup range before deciding the game will not hand it over. */
	private static final int WAIT_LIMIT = 50;
	/** Sideways, closer than this the player walks straight at the item instead of planning a path. */
	private static final double WALK_STRAIGHT = 2.5;
	/** Items compared by route at a time, nearest first. */
	private static final int CANDIDATES = 8;
	private static final int PICK_NODES = 3000;
	/** Turned further than this from the item, the player turns on the spot before walking. */
	private static final float FACING_DEGREES = 50;
	/** How long an item that could not be reached is left alone at first: 30 seconds. */
	private static final long OUT_OF_REACH_TICKS = 600;
	/** Items (entity ids) no collect call should go for again just yet. */
	static final Blacklist<Integer> OUT_OF_REACH = new Blacklist<>(OUT_OF_REACH_TICKS);

	private final ItemMatcher item;
	private final Vec3 around;
	private final double radius;
	private final int settleTicks;
	private final Set<Integer> skip = new HashSet<>();
	/** Rivals already weighed against a target, so each interrupts a walk at most once. */
	private final Set<Integer> compared = new HashSet<>();
	private final Commitment commitment = new Commitment(40, 4);
	private final StuckWatch watch = new StuckWatch(Tuning.COLLECT_STILL_TICKS.i(), Integer.MAX_VALUE, 2);
	private Map<Item, Integer> before;
	private ItemEntity target;
	private TargetPicker<ItemEntity> picker;
	private boolean pickerDigs;
	private int closeTicks;
	private int waitTicks;
	private int trips;
	private int missed;
	private boolean full;

	/**
	 * @param item        only items like this, or null for any
	 * @param around      where to look, or null for around the player
	 * @param settleTicks how long to wait for drops to appear, as just after breaking a block
	 */
	public CollectSkill(ItemMatcher item, Vec3 around, double radius, int settleTicks, int maxTicks) {
		super("collect", maxTicks);
		this.item = item;
		this.around = around;
		this.radius = radius;
		this.settleTicks = settleTicks;
	}

	static CollectSkill fromArgs(Args args) {
		ItemMatcher item = args.has("item") ? ItemMatcher.parse(args.string("item")) : null;
		double radius = Math.max(1, Math.min(32, args.decimal("radius", 16)));
		return new CollectSkill(item, null, radius, 0, args.ticks("timeout", 60));
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		if (before == null) {
			before = contents(ctx.player.getInventory());
		}
		long now = ctx.level.getGameTime();
		SkillResult walked = childResult();
		if (walked != null && !walked.success() && target != null) {
			giveUp(now);
		}
		if (target != null && target.isRemoved()) {
			target = null;
		}
		if (target == null) {
			SkillResult done = choose(ctx, now);
			if (done != null || target == null) {
				return done;
			}
		}

		LocalPlayer player = ctx.player;
		Pickup.Approach how = Pickup.approach(player.getX(), player.getY(), player.getZ(),
				target.getX(), target.getY(), target.getZ(), WALK_STRAIGHT);
		BlockPos at = target.blockPosition();
		BlockPos feet = ctx.feet();
		Goal beside = new Goal.Touch(at.getX(), at.getY(), at.getZ());
		if (how == Pickup.Approach.PATH && beside.reached(feet.getX(), feet.getY(), feet.getZ())) {
			// Already on the spot a path would lead to: all that is left is to close the gap.
			how = Pickup.Approach.WALK;
		}
		switch (how) {
			case PATH -> {
				if (++trips > Tuning.COLLECT_TRIPS.i()) {
					giveUp(now);
					return null;
				}
				watch.reset();
				startChild(GotoSkill.to(beside, "dropped item", GotoSkill.Dig.FALLBACK, 4000, 200));
			}
			case WAIT -> {
				// In the pickup box. New drops cannot be picked up for half a second, and walking on
				// now would only carry the player past the item and round it.
				ctx.lookAt(target.position().add(0, player.getEyeHeight() * 0.5, 0));
				if (++waitTicks > WAIT_LIMIT) {
					if (!roomFor(player.getInventory(), target.getItem())) {
						full = true;
					}
					giveUp(now);
				}
			}
			case WALK -> walkAt(ctx, now, feet);
		}
		return null;
	}

	/** Walks straight at the target, which is close and about level with the player. */
	private void walkAt(SkillContext ctx, long now, BlockPos feet) {
		LocalPlayer player = ctx.player;
		ctx.lookAt(target.position().add(0, player.getEyeHeight() * 0.5, 0));
		double dx = target.getX() - player.getX();
		double dz = target.getZ() - player.getZ();
		float wanted = (float) (Mth.atan2(dz, dx) * Mth.RAD_TO_DEG) - 90;
		EnumSet<MoveKey> keys = EnumSet.noneOf(MoveKey.class);
		// Walking while still turning curves the player round the item, so face it first.
		if (Math.abs(Mth.wrapDegrees(wanted - player.getYRot())) <= FACING_DEGREES) {
			keys.add(MoveKey.FORWARD);
			if (player.horizontalCollision && player.onGround()) {
				// The item is up on a step.
				keys.add(MoveKey.JUMP);
			}
		}
		Footing.guard(ctx, keys);
		ctx.hold(keys);
		boolean blocked = watch.tick(feet.getX(), feet.getY(), feet.getZ()) == StuckWatch.State.STILL
				&& Pickup.sideways(player.getX(), player.getZ(), target.getX(), target.getZ()) > Pickup.SIDE_REACH;
		if (++closeTicks > CLOSE_LIMIT || blocked) {
			if (!roomFor(player.getInventory(), target.getItem())) {
				full = true;
			}
			giveUp(now);
		}
	}

	/**
	 * Picks the next item to go for. Leaves {@link #target} null while still deciding. Returns
	 * the skill's result when there is nothing left to pick up.
	 */
	private SkillResult choose(SkillContext ctx, long now) {
		if (picker == null) {
			List<ItemEntity> found = candidates(ctx, now);
			if (found.isEmpty()) {
				if (ticks() < settleTicks) {
					// Drops take a moment to show up after a block breaks.
					return null;
				}
				return finish(ctx);
			}
			LocalPlayer player = ctx.player;
			for (ItemEntity entity : found) {
				if (Pickup.approach(player.getX(), player.getY(), player.getZ(), entity.getX(), entity.getY(),
						entity.getZ(), WALK_STRAIGHT) != Pickup.Approach.PATH && player.hasLineOfSight(entity)) {
					// Right here and in plain view: no route to work out.
					lock(entity, now);
					return null;
				}
			}
			if (!player.onGround() && !player.isInWater() && !player.onClimbable()) {
				// Mid-air: a route from here would start from nowhere.
				return null;
			}
			pickerDigs = false;
			picker = new TargetPicker<>(ctx, options(found), false, PICK_NODES);
		}
		if (!picker.advance()) {
			return null;
		}
		TargetPicker<ItemEntity> picked = picker;
		picker = null;
		ItemEntity chosen = picked.chosen();
		if (chosen != null) {
			if (chosen.isAlive()) {
				lock(chosen, now);
			}
		} else if (picked.noneReachable() && !pickerDigs) {
			// No way on foot. Before writing them off, see if digging would get to one, as from the
			// bottom of a shaft the player has just dug.
			pickerDigs = true;
			picker = new TargetPicker<>(ctx, picked.options(), true, PICK_NODES);
		} else if (picked.noneReachable()) {
			for (TargetPicker.Option<ItemEntity> option : picked.options()) {
				target = option.target();
				giveUp(now);
			}
		} else {
			// The search ran out before finding a way to any of them: a way, if there is one, is long and
			// winding, and walking it would cost more than the items are worth. Leave them for now.
			for (TargetPicker.Option<ItemEntity> option : picked.options()) {
				target = option.target();
				giveUp(now);
			}
		}
		return null;
	}

	private void lock(ItemEntity entity, long now) {
		target = entity;
		closeTicks = 0;
		waitTicks = 0;
		trips = 0;
		watch.reset();
		commitment.commit(now);
	}

	private static List<TargetPicker.Option<ItemEntity>> options(List<ItemEntity> entities) {
		List<TargetPicker.Option<ItemEntity>> options = new ArrayList<>();
		for (ItemEntity entity : entities) {
			BlockPos at = entity.blockPosition();
			options.add(new TargetPicker.Option<>(entity, new Goal.Touch(at.getX(), at.getY(), at.getZ())));
		}
		return options;
	}

	@Override
	protected boolean interruptChild(SkillContext ctx) {
		if (target == null) {
			return false;
		}
		if (target.isRemoved()) {
			// Someone else took it, or it burned or fell away: no point walking to where it was.
			return true;
		}
		if (ticks() % 10 != 0) {
			return false;
		}
		// Keep to the item being walked to, unless another has turned up much closer. Each rival
		// gets one look, so two items about as far away as each other cannot pull it to and fro.
		long now = ctx.level.getGameTime();
		double left = ctx.player.distanceTo(target);
		for (ItemEntity rival : candidates(ctx, now)) {
			if (rival == target || compared.contains(rival.getId())) {
				continue;
			}
			if (commitment.shouldSwitch(now, left, 0, ctx.player.distanceTo(rival), 0)) {
				compared.add(rival.getId());
				// Let go of the target; the choice that follows compares both by route.
				target = null;
				return true;
			}
		}
		return false;
	}

	@Override
	protected String describe() {
		return "picking up " + (item == null ? "items" : item.toString());
	}

	/** Leaves the target alone from now on, and for a while in later collect calls too. */
	private void giveUp(long now) {
		skip.add(target.getId());
		if (!full) {
			OUT_OF_REACH.add(target.getId(), now);
		}
		target = null;
		missed++;
	}

	private SkillResult finish(SkillContext ctx) {
		Map<Item, Integer> after = contents(ctx.player.getInventory());
		JsonObject items = new JsonObject();
		int total = 0;
		for (Map.Entry<Item, Integer> entry : after.entrySet()) {
			int gained = entry.getValue() - before.getOrDefault(entry.getKey(), 0);
			if (gained > 0) {
				items.addProperty(BuiltInRegistries.ITEM.getKey(entry.getKey()).getPath(), gained);
				total += gained;
			}
		}
		JsonObject data = new JsonObject();
		data.addProperty("collected", total);
		data.add("items", items);
		data.addProperty("missed", missed);
		String picked = total == 0 ? "picked up nothing" : "picked up " + items.entrySet().stream()
				.map(e -> e.getValue().getAsInt() + " " + e.getKey())
				.collect(Collectors.joining(", "));
		String left = missed == 0 ? "" : full ? "; the inventory is full" : "; " + missed + " out of reach";
		return SkillResult.success(picked + left, data);
	}

	/** Items worth going for, nearest in a straight line first, at most {@link #CANDIDATES}. */
	private List<ItemEntity> candidates(SkillContext ctx, long now) {
		Vec3 centre = around != null ? around : ctx.player.position();
		AABB box = new AABB(BlockPos.containing(centre)).inflate(radius);
		BlockView view = new ClientBlockView(ctx.level, ctx.player.getInventory());
		return ctx.level.getEntitiesOfClass(ItemEntity.class, box, e -> e.isAlive() && !skip.contains(e.getId())
						&& !OUT_OF_REACH.contains(e.getId(), now)
						&& !e.isInLava() && (!e.isInWater() || shallow(view, e.blockPosition()))
						&& e.position().distanceTo(centre) <= radius && (item == null || item.test(e.getItem())))
				.stream()
				.sorted(Comparator.comparingDouble(e -> e.distanceToSqr(ctx.player)))
				.limit(CANDIDATES)
				.collect(Collectors.toList());
	}

	/** Water the player can stand in with the head out: ground right under the item, air above it. */
	private static boolean shallow(BlockView view, BlockPos pos) {
		return view.cell(pos.getX(), pos.getY() - 1, pos.getZ()) == BlockView.SOLID
				&& view.cell(pos.getX(), pos.getY() + 1, pos.getZ()) == BlockView.PASSABLE;
	}

	private static Map<Item, Integer> contents(Inventory inventory) {
		Map<Item, Integer> counts = new LinkedHashMap<>();
		for (int slot = 0; slot < Inventories.MAIN_SIZE; slot++) {
			ItemStack stack = inventory.getItem(slot);
			if (!stack.isEmpty()) {
				counts.merge(stack.getItem(), stack.getCount(), Integer::sum);
			}
		}
		return counts;
	}

	private static boolean roomFor(Inventory inventory, ItemStack stack) {
		return inventory.getFreeSlot() >= 0 || inventory.getSlotWithRemainingSpace(stack) >= 0;
	}
}
