package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;
import com.xzier9.minegpt.build.Ledger;
import com.xzier9.minegpt.build.Turning;
import com.xzier9.minegpt.client.act.Action.MoveKey;
import com.xzier9.minegpt.path.Goal;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import java.util.HashSet;
import java.util.Set;
import java.util.function.Predicate;

/**
 * Places a block from the inventory, either at a given position or in a free spot next to
 * the player. It aims at a face of a neighbouring block and right-clicks while sneaking, so
 * clicking a chest or crafting table places against it instead of opening it. Grass, leaf
 * litter and the like in the spot are clicked directly, which replaces them as in vanilla.
 *
 * A block can be asked to face a way ({@link Want}): the game turns stairs, doors, chests and
 * beds by where the player looks when placing, and picks the top or bottom half of a slab by
 * where on the face the click lands, and lays a log along the axis of the face clicked. The
 * skill then stands and aims so that comes out right. If nowhere to stand allows it, the block
 * still goes in, but the skill then reports that it is turned wrong and how, so that a builder
 * can come back to it; it never passes a wrongly turned block off as done. A log with no axis
 * asked for is stood upright.
 *
 * Every block placed is noted in the ledger of the agent's own blocks ({@link OwnBlocks}).
 *
 * When a builder gives it the spots to keep clear, and no ground in reach gives a view of the
 * spot (a roof edge, the top of a tall wall), it picks a place beside the build to pillar up
 * from: goto stacks spare dirt or stone there and the builder takes it down again afterwards.
 */
public final class PlaceSkill extends Skill {
	private enum State {
		CHOOSE, AIM, VERIFY
	}

	/** Places to stand that are tried before giving up on a spot. */
	private static final int MAX_APPROACHES = 3;
	/** How far from the spot (in blocks, each way) to look for a place to stand. */
	private static final int VANTAGE_RADIUS = 3;
	/** Tallest pillar put up to get a view of a spot. */
	private static final int MAX_PILLAR = 6;
	/** Eye heights standing and sneaking: a vantage point must work for both, since placing sneaks. */
	private static final double[] EYE_HEIGHTS = {1.62, 1.27};
	private static final Direction[] SUPPORT_ORDER = {
			Direction.DOWN, Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST, Direction.UP};

	/**
	 * How a block should end up.
	 *
	 * @param look    the way the player must face when placing: stairs rise that way, doors and
	 *                beds point that way, chests and furnaces face back at the player; null for any
	 * @param top     true for the top half (slabs, upside-down stairs), false for the bottom; null for any
	 * @param against the side of the spot the block it is placed on must be: DOWN for a torch on the
	 *                floor, a wall's direction for a ladder; null for any
	 * @param axis    the axis a log or pillar lies along; null for upright (or along {@code against})
	 */
	public record Want(Direction look, Boolean top, Direction against, Direction.Axis axis) {
		public static final Want ANY = new Want(null, null, null, null);

		boolean any() {
			return look == null && top == null && against == null && axis == null;
		}

		/** The same request without the way to face, for when nowhere to stand allows it. */
		Want withoutLook() {
			return new Want(null, top, against, axis);
		}

		/**
		 * Parses "east", "top", "on:down", "axis:x" and combinations like "east,top". Null or empty
		 * is {@link #ANY}.
		 */
		public static Want parse(String text) {
			if (text == null || text.isBlank()) {
				return ANY;
			}
			Direction look = null;
			Boolean top = null;
			Direction against = null;
			Direction.Axis axis = null;
			for (String part : text.toLowerCase(java.util.Locale.ROOT).split("[,\\s]+")) {
				if (part.isEmpty()) {
					continue;
				}
				if (part.equals("top") || part.equals("bottom")) {
					top = part.equals("top");
				} else if (part.startsWith("axis:")) {
					axis = Direction.Axis.byName(part.substring(5));
					if (axis == null) {
						throw new IllegalArgumentException("unknown axis \"" + part.substring(5) + "\"; use axis:x, axis:y or axis:z");
					}
				} else if (part.startsWith("on:")) {
					against = Direction.byName(part.substring(3));
					if (against == null) {
						throw new IllegalArgumentException("unknown side \"" + part.substring(3) + "\"");
					}
				} else {
					look = Direction.byName(part);
					if (look == null || look.getAxis().isVertical()) {
						throw new IllegalArgumentException("unknown facing \"" + part
								+ "\"; use north, south, east, west, top, bottom, on:<side> or axis:<x|y|z>");
					}
				}
			}
			return new Want(look, top, against, axis);
		}
	}

	/** Where to click to put a block at {@code pos}. {@code support} is {@code pos} itself when replacing grass and the like. */
	private record Placement(BlockPos pos, BlockPos support, Direction face, Vec3 aim) {
		boolean replaces() {
			return support.equals(pos);
		}
	}

	private final ItemMatcher item;
	private final BlockPos requested;
	/** How the block should face; relaxed step by step when no place to stand allows it. */
	private Want want;
	/** How it was asked for, which is what the result is checked against. */
	private Want asked;
	/** Why the block is being placed, for the ledger. */
	private Ledger.Purpose purpose = Ledger.Purpose.BUILD;
	private State state = State.CHOOSE;
	private Placement placement;
	private Block block;
	/** Walks taken to get a clear view of the spot. */
	private int approaches;
	/** Places to stand already tried for this spot. */
	private final Set<BlockPos> triedVantages = new HashSet<>();
	/** Spots a pillar must not stand in, from a builder; null when pillaring for a view is not wanted. */
	private final Predicate<BlockPos> keepClear;
	private int stateTicks;
	/** True while a child skill knocks a soft block out of the way. */
	private boolean knocking;
	/** Soft blocks knocked out of the way so far. */
	private int knocked;
	private static final int MAX_KNOCKED = 4;
	/** Ticks in a row the crosshair has rested on the face to click. */
	private int steady;
	private int tries;

	/** @param requested where to put the block, or null for anywhere next to the player */
	public PlaceSkill(ItemMatcher item, BlockPos requested, int maxTicks) {
		this(item, requested, Want.ANY, maxTicks);
	}

	public PlaceSkill(ItemMatcher item, BlockPos requested, Want want, int maxTicks) {
		this(item, requested, want, maxTicks, null);
	}

	/** @param keepClear spots that must stay free of scaffolding: allows pillaring up for a view elsewhere */
	PlaceSkill(ItemMatcher item, BlockPos requested, Want want, int maxTicks, Predicate<BlockPos> keepClear) {
		super("place", maxTicks);
		this.item = item;
		this.requested = requested;
		this.want = requested == null ? Want.ANY : want;
		this.asked = this.want;
		this.keepClear = keepClear;
	}

	/** Says why the block is placed, which decides whether the agent may later take it down by itself. */
	PlaceSkill as(Ledger.Purpose purpose) {
		this.purpose = purpose;
		return this;
	}

	static PlaceSkill fromArgs(Args args) {
		BlockPos pos = null;
		if (args.has("x") || args.has("y") || args.has("z")) {
			pos = new BlockPos(args.integer("x"), args.integer("y"), args.integer("z"));
		}
		return new PlaceSkill(ItemMatcher.parse(args.string("item")), pos, Want.parse(args.string("facing", null)),
				args.ticks("timeout", 30));
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		SkillResult walked = childResult();
		if (knocking) {
			// That was a flower, grass or torch being knocked out of the line of sight, not a walk.
			knocking = false;
			walked = null;
		}
		if (walked != null && !walked.success()) {
			return SkillResult.failure("could not get close enough to place " + item + ": " + walked.message());
		}
		int slot = Inventories.find(ctx.player.getInventory(), item);
		if (slot < 0 && state != State.VERIFY) {
			return SkillResult.failure("no " + item + " in the inventory");
		}
		if (block == null) {
			ItemStack stack = ctx.player.getInventory().getItem(slot);
			if (!(stack.getItem() instanceof BlockItem blockItem)) {
				return SkillResult.failure(item + " is not a block");
			}
			block = blockItem.getBlock();
			if (requested != null && BlockStates.family(block) == Turning.Family.PILLAR && want.axis() == null) {
				// A log is a post unless told otherwise: click a face that stands it up, or one on the
				// side it was asked to be placed against.
				Direction.Axis axis = want.against() == null ? Direction.Axis.Y : want.against().getAxis();
				want = new Want(want.look(), want.top(), want.against(), axis);
				asked = want;
			}
		}

		switch (state) {
			case CHOOSE -> {
				if (ctx.mc.gui.screen() != null) {
					ctx.closeScreen();
					return null;
				}
				return choose(ctx);
			}
			case AIM -> {
				if (!Inventories.select(ctx, slot)) {
					ctx.closeScreen();
					return null;
				}
				ctx.lookAt(placement.aim());
				ctx.hold(MoveKey.SNEAK);
				BlockHitResult hit = ctx.blockTarget();
				boolean onTarget = hit != null && hit.getBlockPos().equals(placement.support())
						&& (placement.replaces() || hit.getDirection() == placement.face())
						&& (want.look() == null || ctx.player.getDirection() == want.look())
						&& (want.top() == null || placement.replaces() || placement.face().getAxis().isVertical()
								|| (hit.getLocation().y - placement.pos().getY() > 0.5) == want.top());
				// Click only once the camera has come to rest on the spot: while it is still turning, the
				// crosshair can slide onto the next face over by the time the click lands, and a log then
				// lies on its side or a stair faces the wrong way.
				steady = onTarget && settled(ctx, placement.aim()) ? steady + 1 : 0;
				if (steady >= 2 && ctx.player.isShiftKeyDown() && item.test(ctx.player.getMainHandItem())) {
					// Use the face that was just checked, rather than whatever the crosshair is on a tick
					// later: a log goes on its side if the click lands on the face next to it.
					ctx.useOn(hit);
					state = State.VERIFY;
					stateTicks = 0;
					steady = 0;
				} else if (++stateTicks > 30) {
					return retry("could not aim at a face to place against");
				}
				return null;
			}
			case VERIFY -> {
				ctx.hold(MoveKey.SNEAK);
				BlockState now = ctx.level.getBlockState(placement.pos());
				if (now.is(block)) {
					OwnBlocks.record(ctx, placement.pos(), purpose);
					JsonObject data = new JsonObject();
					data.addProperty("x", placement.pos().getX());
					data.addProperty("y", placement.pos().getY());
					data.addProperty("z", placement.pos().getZ());
					String turned = BlockStates.turned(now);
					if (!turned.isEmpty()) {
						data.addProperty("state", turned);
					}
					String wrong = BlockStates.mismatch(now, block, asked);
					if (wrong != null) {
						data.addProperty("turned", wrong);
						return SkillResult.failure("placed " + item + " at " + placement.pos().toShortString()
								+ " but it is turned wrong (" + wrong + ")", data);
					}
					return SkillResult.success("placed " + item + " at " + placement.pos().toShortString(), data);
				}
				if (++stateTicks > 10) {
					return retry("the block did not appear");
				}
				return null;
			}
		}
		return null;
	}

	@Override
	protected String describe() {
		return "placing " + item;
	}

	private SkillResult choose(SkillContext ctx) {
		if (requested != null) {
			BlockState there = ctx.level.getBlockState(requested);
			if (there.is(block)) {
				String wrong = BlockStates.mismatch(there, block, asked);
				if (wrong != null) {
					JsonObject data = new JsonObject();
					data.addProperty("turned", wrong);
					return SkillResult.failure(item + " is already at " + requested.toShortString()
							+ " but turned wrong (" + wrong + "); break it and place it again", data);
				}
				return SkillResult.success(item + " is already at " + requested.toShortString());
			}
			if (!there.canBeReplaced()) {
				return SkillResult.failure("something is already at " + requested.toShortString());
			}
			placement = placementFor(ctx, requested, want);
			if (placement == null && knocked < MAX_KNOCKED) {
				// Only grass, a flower or a torch hides the face to click: knock it off and look again.
				BlockPos soft = softBlocker(ctx, requested, want);
				if (soft != null) {
					knocked++;
					knocking = true;
					startChild(new BreakBlockSkill(soft));
					return null;
				}
			}
			if (placement == null) {
				// Walk to a place from which a face to place against can actually be seen and reached.
				// Being "within reach" is not enough: blocks already built (the near wall of a hut, say)
				// often hide the face, and the player may stand half in the spot.
				BlockPos stand = approaches < MAX_APPROACHES ? vantage(ctx, requested, want, triedVantages) : null;
				if (stand == null && approaches < MAX_APPROACHES && keepClear != null) {
					// No ground gives a view: somewhere to pillar up from, beside the build.
					stand = pillarVantage(ctx, requested, want, triedVantages, keepClear,
							Math.min(MAX_PILLAR, Scaffold.count(ctx.player.getInventory())));
				}
				if (stand == null && !want.any()) {
					// Nowhere to stand that turns the block the right way. Give up the way to face first,
					// then the rest, so that the block at least goes in; the check afterwards says so.
					want = want.look() != null && !want.withoutLook().any() ? want.withoutLook() : Want.ANY;
					approaches = 0;
					triedVantages.clear();
					return null;
				}
				if (stand == null) {
					boolean anySupport = false;
					for (Direction d : Direction.values()) {
						BlockPos n = requested.relative(d);
						anySupport |= ctx.level.getBlockState(n).isFaceSturdy(ctx.level, n, d.getOpposite());
					}
					return SkillResult.failure(anySupport
							? "no place to stand where a face next to " + requested.toShortString() + " can be clicked"
							: "nothing to place against at " + requested.toShortString());
				}
				triedVantages.add(stand);
				approaches++;
				if (stand.equals(ctx.feet())) {
					// Already there, but the aim failed (for instance while still moving): look again next tick.
					return null;
				}
				startChild(GotoSkill.to(new Goal.Block(stand.getX(), stand.getY(), stand.getZ()), "placing spot",
						GotoSkill.Dig.FALLBACK, 6_000, 30 * 20));
				return null;
			}
		} else {
			placement = freeSpot(ctx);
			if (placement == null) {
				return SkillResult.failure("no free spot to place " + item + " nearby");
			}
		}
		state = State.AIM;
		stateTicks = 0;
		return null;
	}

	/**
	 * A soft block ({@link Mining#isSoft}) that may be knocked off and is all that hides a face
	 * next to {@code pos} that could be clicked from where the player stands; null when there is none.
	 */
	private static BlockPos softBlocker(SkillContext ctx, BlockPos pos, Want want) {
		Vec3 eye = ctx.player.getEyePosition();
		double reach = ctx.player.blockInteractionRange();
		BlockState there = ctx.level.getBlockState(pos);
		if (!there.isAir() && Mining.mayKnockOff(ctx.mc, ctx.level, pos, there)) {
			// Something soft in the spot itself that clicking would not simply replace.
			if (!(want.any() && there.canBeReplaced())) {
				return pos;
			}
		}
		for (Direction direction : SUPPORT_ORDER) {
			BlockPos support = pos.relative(direction);
			if (!ctx.level.getBlockState(support).isFaceSturdy(ctx.level, support, direction.getOpposite())
					|| (want.against() != null && direction != want.against())
					|| (want.axis() != null && direction.getAxis() != want.axis())) {
				continue;
			}
			Vec3 aim = Vec3.atCenterOf(pos).add(direction.getStepX() * 0.49, direction.getStepY() * 0.49,
					direction.getStepZ() * 0.49);
			if (aim.distanceToSqr(eye) > reach * reach) {
				continue;
			}
			BlockPos soft = BreakBlockSkill.softOnRay(ctx, eye, aim, support);
			if (soft != null) {
				return soft;
			}
		}
		return null;
	}

	/** True when the player looks straight at {@code aim}, give or take the {@code PLACE_SETTLED_DEGREES} setting. */
	private static boolean settled(SkillContext ctx, Vec3 aim) {
		Vec3 eye = ctx.player.getEyePosition();
		Vec3 to = aim.subtract(eye);
		if (to.lengthSqr() < 1e-6) {
			return true;
		}
		double cos = to.normalize().dot(ctx.player.getViewVector(1.0F));
		return cos >= Math.cos(Math.toRadians(Tuning.PLACE_SETTLED_DEGREES.d()));
	}

	/** True if a block can be put at {@code pos} from where the player stands now, as {@code want} asks. */
	static boolean placeableFromHere(SkillContext ctx, BlockPos pos, Want want, Block block) {
		if (block != null && BlockStates.family(block) == Turning.Family.PILLAR && want.axis() == null) {
			want = new Want(want.look(), want.top(), want.against(),
					want.against() == null ? Direction.Axis.Y : want.against().getAxis());
		}
		return placementFor(ctx, pos, want) != null;
	}

	private SkillResult retry(String problem) {
		if (++tries >= Tuning.PLACE_TRIES.i()) {
			return SkillResult.failure("could not place " + item + ": " + problem);
		}
		state = State.CHOOSE;
		return null;
	}

	/** A spot beside the player, on the ground, at feet level first. */
	private Placement freeSpot(SkillContext ctx) {
		BlockPos feet = ctx.feet();
		for (int dy : new int[] {0, 1, -1}) {
			for (int r = 1; r <= 2; r++) {
				for (int dx = -r; dx <= r; dx++) {
					for (int dz = -r; dz <= r; dz++) {
						if (Math.max(Math.abs(dx), Math.abs(dz)) != r) {
							continue;
						}
						BlockPos pos = feet.offset(dx, dy, dz);
						BlockState state = ctx.level.getBlockState(pos);
						if (!state.canBeReplaced() || !state.getFluidState().isEmpty() || BuildSkill.inSite(pos)) {
							continue;
						}
						Placement p = placementFor(ctx, pos, Want.ANY);
						if (p != null && p.face() == Direction.UP) {
							return p;
						}
					}
				}
			}
		}
		return null;
	}

	/** A face next to {@code pos} that the player can see and click from where they stand. */
	private static Placement placementFor(SkillContext ctx, BlockPos pos, Want want) {
		if (ctx.player.getBoundingBox().intersects(new AABB(pos))) {
			return null;
		}
		return placementFrom(ctx, pos, want, ctx.player.getEyePosition(), ctx.player.blockInteractionRange());
	}

	/**
	 * The nearest place to stand (feet position) from which a face next to {@code pos} can be
	 * seen and clicked, standing or sneaking, or null. Only spots the player could stand in:
	 * two free cells over a solid floor, not overlapping {@code pos}.
	 */
	static BlockPos vantage(SkillContext ctx, BlockPos pos, Want want, Set<BlockPos> tried) {
		return vantage(ctx, pos, want, tried, null, 0);
	}

	/**
	 * Like {@link #vantage}, but for spots in the air that a pillar of at most {@code maxPillar}
	 * blocks would reach from the ground below, none of them where {@code keepClear} says no.
	 */
	static BlockPos pillarVantage(SkillContext ctx, BlockPos pos, Want want, Set<BlockPos> tried,
			Predicate<BlockPos> keepClear, int maxPillar) {
		return maxPillar < 1 ? null : vantage(ctx, pos, want, tried, keepClear, maxPillar);
	}

	private static BlockPos vantage(SkillContext ctx, BlockPos pos, Want want, Set<BlockPos> tried,
			Predicate<BlockPos> keepClear, int maxPillar) {
		BlockPos feet = ctx.feet();
		// Some slack under the real reach, for standing off the middle of the block.
		double reach = ctx.player.blockInteractionRange() - 0.4;
		BlockPos best = null;
		double bestScore = Double.MAX_VALUE;
		for (int dy = -2; dy <= 2; dy++) {
			for (int dx = -VANTAGE_RADIUS; dx <= VANTAGE_RADIUS; dx++) {
				for (int dz = -VANTAGE_RADIUS; dz <= VANTAGE_RADIUS; dz++) {
					BlockPos stand = pos.offset(dx, dy - 1, dz);
					if (tried.contains(stand) || stand.equals(pos) || stand.above().equals(pos)) {
						continue;
					}
					int pillar = 0;
					if (maxPillar == 0) {
						if (!standable(ctx, stand)) {
							continue;
						}
					} else {
						pillar = pillarHeight(ctx, stand, pos, keepClear, maxPillar);
						if (pillar <= 0) {
							continue;
						}
					}
					boolean ok = true;
					for (double height : EYE_HEIGHTS) {
						Vec3 eye = new Vec3(stand.getX() + 0.5, stand.getY() + height, stand.getZ() + 0.5);
						if (placementFrom(ctx, pos, want, eye, reach) == null) {
							ok = false;
							break;
						}
					}
					if (!ok) {
						continue;
					}
					// Prefer short walks, then standing a little away from the spot rather than right at it.
					// A pillar costs blocks and time, so low ones win over short walks.
					double score = Math.sqrt(feet.distSqr(stand)) + (Math.abs(dx) <= 1 && Math.abs(dz) <= 1 ? 0.5 : 0)
							+ pillar * 3.0;
					if (score < bestScore) {
						best = stand;
						bestScore = score;
					}
				}
			}
		}
		return best;
	}

	/**
	 * How many blocks would have to be stacked to stand at {@code feet}: the open cells between it
	 * and solid ground. Zero if it needs none (ordinary ground, not a pillar spot), -1 if it cannot
	 * be done: too high, something in the way, or a cell the builder needs kept clear.
	 */
	private static int pillarHeight(SkillContext ctx, BlockPos feet, BlockPos target, Predicate<BlockPos> keepClear,
			int maxPillar) {
		for (BlockPos cell : new BlockPos[] {feet, feet.above()}) {
			BlockState state = ctx.level.getBlockState(cell);
			if (!state.getCollisionShape(ctx.level, cell).isEmpty() || !state.getFluidState().isEmpty()) {
				return -1;
			}
		}
		BlockPos.MutableBlockPos at = new BlockPos.MutableBlockPos();
		for (int height = 0; height <= maxPillar; height++) {
			at.set(feet.getX(), feet.getY() - 1 - height, feet.getZ());
			BlockState state = ctx.level.getBlockState(at);
			if (state.isFaceSturdy(ctx.level, at, Direction.UP)) {
				return height;
			}
			if (!state.getCollisionShape(ctx.level, at).isEmpty() || !state.getFluidState().isEmpty()
					|| at.equals(target) || keepClear.test(at)) {
				return -1;
			}
		}
		return -1;
	}

	private static boolean standable(SkillContext ctx, BlockPos feet) {
		BlockPos floor = feet.below();
		BlockState below = ctx.level.getBlockState(floor);
		if (!below.isFaceSturdy(ctx.level, floor, Direction.UP)) {
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

	/** A face next to {@code pos} that can be seen and clicked from {@code eye}, within {@code reach}. */
	private static Placement placementFrom(SkillContext ctx, BlockPos pos, Want want, Vec3 eye, double reach) {
		Entity viewer = ctx.player;
		BlockState there = ctx.level.getBlockState(pos);
		if (want.any() && !there.isAir() && there.canBeReplaced() && there.getFluidState().isEmpty()
				&& !there.getShape(ctx.level, pos).isEmpty()) {
			// Clicking grass or leaf litter with a block replaces it, so aim at it directly.
			Vec3 aim = there.getShape(ctx.level, pos).bounds().getCenter().add(Vec3.atLowerCornerOf(pos));
			Vec3 end = eye.add(aim.subtract(eye).scale(1.05));
			BlockHitResult hit = ctx.level.clip(new ClipContext(eye, end, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE,
					viewer));
			if (hit.getType() == HitResult.Type.BLOCK && hit.getBlockPos().equals(pos)
					&& hit.getLocation().distanceToSqr(eye) <= reach * reach) {
				return new Placement(pos, pos, hit.getDirection(), aim);
			}
		}
		for (Direction direction : SUPPORT_ORDER) {
			BlockPos support = pos.relative(direction);
			Direction face = direction.getOpposite();
			if (!ctx.level.getBlockState(support).isFaceSturdy(ctx.level, support, face)) {
				continue;
			}
			if (want.against() != null && direction != want.against()) {
				continue;
			}
			if (want.axis() != null && direction.getAxis() != want.axis()) {
				// A log lies along the axis of the face it is clicked on.
				continue;
			}
			double lift = 0;
			if (want.top() != null) {
				// The half comes from the face clicked: the top of the block below gives a bottom half, the
				// underside of the block above a top half, and a side face whichever half the click lands in.
				if (direction.getAxis().isVertical()) {
					if ((direction == Direction.UP) != want.top()) {
						continue;
					}
				} else {
					lift = want.top() ? 0.3 : -0.3;
				}
			}
			Vec3 aim = Vec3.atCenterOf(pos).add(direction.getStepX() * 0.49, direction.getStepY() * 0.49 + lift,
					direction.getStepZ() * 0.49);
			if (want.look() != null && Direction.getApproximateNearest(aim.x - eye.x, 0, aim.z - eye.z) != want.look()) {
				continue;
			}
			// A little margin, so the player still faces that way from slightly off the middle of the block.
			if (want.look() != null) {
				double forward = (aim.x - eye.x) * want.look().getStepX() + (aim.z - eye.z) * want.look().getStepZ();
				double sideways = Math.abs((aim.x - eye.x) * want.look().getStepZ() + (aim.z - eye.z) * want.look().getStepX());
				if (forward < sideways + 0.4) {
					continue;
				}
			}
			Vec3 end = eye.add(aim.subtract(eye).scale(1.05));
			BlockHitResult hit = ctx.level.clip(new ClipContext(eye, end, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE,
					viewer));
			if (hit.getType() == HitResult.Type.BLOCK && hit.getBlockPos().equals(support) && hit.getDirection() == face
					&& hit.getLocation().distanceToSqr(eye) <= reach * reach) {
				return new Placement(pos, support, face, aim);
			}
		}
		return null;
	}
}
