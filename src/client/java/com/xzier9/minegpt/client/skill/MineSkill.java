package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;
import com.xzier9.minegpt.client.path.ClientBlockView;
import com.xzier9.minegpt.path.Blacklist;
import com.xzier9.minegpt.path.BlockView;
import com.xzier9.minegpt.path.Goal;
import com.xzier9.minegpt.path.Hazards;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.Vec3;

/**
 * Finds, reaches and breaks blocks until {@code count} have been broken, picking up the
 * drops after each one. When nothing matching is in range it explores, tunnelling through
 * terrain in one direction, which also exposes new ores in the tunnel walls.
 *
 * It leaves alone blocks that touch lava, and blocks it could only mine from in the water,
 * such as stone on a lake bed. Among the blocks in range it goes for the one with the cheapest
 * route, digging included, which is often not the one nearest in a straight line: a log across
 * a ravine or behind a wall loses to one a few steps further along the ground. Blocks it failed
 * to reach are remembered for a few minutes, longer each time, so the next mine call does not
 * walk back into the same flooded cave. Once done it sweeps up any drops it missed nearby.
 *
 * It breaks every wanted block it can reach and see from where it stands before moving on, and
 * picks up the drops of such a group in one go instead of after each block. With {@code tunnel}
 * it digs a straight tunnel two blocks high through natural ground instead of looking for
 * blocks, counting the ones that match: the quickest way to a lot of stone. It turns at lava,
 * water, a drop or anything built, and stops when every way is blocked.
 */
public final class MineSkill extends Skill {
	private enum State {
		SEARCH, PICK, APPROACH, BREAK, COLLECT, EXPLORE, SWEEP, TUNNEL_BREAK, TUNNEL_STEP
	}

	/** Turns in a row with no way on before a tunnel gives up. */
	private static final int MAX_TURNS = 4;

	/** How close the eyes get to a block before mining it; a little under survival reach. */
	private static final double REACH = 4.0;
	private static final int EXPLORE_DISTANCE = 24;
	private static final int CANDIDATES = 16;
	private static final int SWEEP_RADIUS = 8;
	/** How long a block that could not be reached is left alone: five minutes of game time. */
	private static final long UNREACHABLE_TICKS = 6000;
	/** Blocks that could not be reached, left alone until their time is up. */
	static final Blacklist<BlockPos> UNREACHABLE = new Blacklist<>(UNREACHABLE_TICKS);
	/** Blocks compared by route at a time, nearest first. */
	private static final int PICK_OPTIONS = 8;

	private final BlockMatcher match;
	private final int wanted;
	private final int radius;
	private final boolean hidden;
	private final int maxExplores;
	private final boolean tunnel;

	private final Set<BlockPos> skip = new HashSet<>();
	private final JsonObject blocks = new JsonObject();
	private final JsonObject picked = new JsonObject();
	private State state = State.SEARCH;
	private TargetPicker<BlockPos> picker;
	private BlockPos target;
	/** The block about to be broken, remembered so the result can name it after it is gone. */
	private BlockState lastState;
	private int broken;
	private int explores;
	private Direction exploreDirection;
	/** Blocks broken since the drops were last picked up. */
	private int sinceCollect;
	private Direction tunnelDirection;
	private int turns;
	/** Steps in a row into open space, with nothing to dig. */
	private int openSteps;
	private static final int MAX_OPEN_STEPS = 6;

	public MineSkill(BlockMatcher match, int wanted, int radius, boolean hidden, int maxExplores, int maxTicks) {
		this(match, wanted, radius, hidden, maxExplores, false, maxTicks);
	}

	public MineSkill(BlockMatcher match, int wanted, int radius, boolean hidden, int maxExplores, boolean tunnel,
			int maxTicks) {
		super("mine", maxTicks);
		this.match = match;
		this.wanted = wanted;
		this.radius = radius;
		this.hidden = hidden;
		this.maxExplores = maxExplores;
		this.tunnel = tunnel;
	}

	static MineSkill fromArgs(Args args) {
		int count = Math.max(1, args.integer("count", 1));
		// A big order takes longer: five minutes, or a minute and four seconds a block, whichever is more.
		return new MineSkill(BlockMatcher.parse(args.strings("block")),
				count,
				Math.max(4, Math.min(64, args.integer("radius", 32))),
				args.bool("hidden", false),
				Math.max(0, args.integer("explore", 6)),
				args.bool("tunnel", false),
				args.ticks("timeout", Math.max(300, 60 + (int) Math.round(Tuning.MINE_SECONDS_PER_BLOCK.d() * count))));
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		SkillResult child = childResult();
		switch (state) {
			case PICK -> {
				return pick(ctx);
			}
			case APPROACH -> {
				if (child != null && child.success()) {
					state = State.BREAK;
					startChild(new BreakBlockSkill(target));
					return null;
				}
				unreachable(ctx, target);
			}
			case BREAK -> {
				if (child != null && child.success()) {
					count(lastState);
					if (broken < wanted && sinceCollect < Tuning.MINE_BATCH.i()) {
						// Another one in reach and in sight: break it from here, before moving or picking up.
						BlockPos next = nextInReach(ctx);
						if (next != null) {
							target = next;
							lastState = ctx.level.getBlockState(next);
							startChild(new BreakBlockSkill(next));
							return null;
						}
					}
					return collectGroup(ctx);
				}
				unreachable(ctx, target);
			}
			case TUNNEL_BREAK -> {
				if (child != null && child.success()) {
					openSteps = 0;
					if (match.test(lastState)) {
						count(lastState);
					}
				} else {
					turn();
				}
			}
			case TUNNEL_STEP -> {
				if (child != null && child.success()) {
					turns = 0;
					openSteps++;
				} else {
					turn();
				}
			}
			case COLLECT -> addPicked(child);
			case SWEEP -> {
				addPicked(child);
				return SkillResult.success("mined " + broken + " " + match + pickedSummary(), data());
			}
			case EXPLORE -> {
				if (child != null && !child.success()) {
					// Could not go that way; try the next direction.
					exploreDirection = exploreDirection.getClockWise();
				}
			}
			default -> {
			}
		}
		state = State.SEARCH;
		if (sinceCollect > 0 && (broken >= wanted || !tunnel || sinceCollect >= Tuning.MINE_BATCH.i())) {
			return collectGroup(ctx);
		}
		if (broken >= wanted) {
			// Pick up drops that bounced away or were left while mining the next block.
			state = State.SWEEP;
			startChild(new CollectSkill(null, null, SWEEP_RADIUS, 0, 200));
			return null;
		}
		return tunnel ? tunnel(ctx) : search(ctx);
	}

	private void count(BlockState state) {
		broken++;
		sinceCollect++;
		String id = BreakBlockSkill.blockName(state);
		blocks.addProperty(id, blocks.has(id) ? blocks.get(id).getAsInt() + 1 : 1);
	}

	/** Picks up the drops of the blocks just broken, all round where the player stands. */
	private SkillResult collectGroup(SkillContext ctx) {
		state = State.COLLECT;
		startChild(new CollectSkill(null, ctx.player.position(), 8, Tuning.MINE_DROP_SETTLE_TICKS.i(), 120 + 20 * sinceCollect));
		sinceCollect = 0;
		return null;
	}

	/** The nearest wanted block the player can mine from where it stands, without a step: or null. */
	private BlockPos nextInReach(SkillContext ctx) {
		if (!ctx.player.onGround() && !ctx.player.isInWater()) {
			return null;
		}
		skipUnreachable(ctx);
		BlockPos feet = ctx.feet();
		for (BlockPos pos : BlockSearch.find(ctx.level, feet, (int) Math.ceil(REACH) + 2, match, !hidden, skip, CANDIDATES)) {
			BlockState there = ctx.level.getBlockState(pos);
			if (!Mining.canHarvest(ctx.player.getInventory(), there) || Mining.touchesLava(ctx.level, pos)
					|| !com.xzier9.minegpt.client.MineGPTClient.safety().mayBreak(ctx.mc, pos)) {
				continue;
			}
			Goal reach = new Goal.Reach(pos.getX(), pos.getY(), pos.getZ(), REACH);
			if (reach.reached(feet.getX(), feet.getY(), feet.getZ()) && BreakBlockSkill.aimPoint(ctx, pos) != null) {
				return pos;
			}
		}
		return null;
	}

	private void turn() {
		tunnelDirection = tunnelDirection.getClockWise();
		turns++;
	}

	/** Digs the next step of a straight tunnel two blocks high, or steps into it once it is open. */
	private SkillResult tunnel(SkillContext ctx) {
		if (tunnelDirection == null) {
			tunnelDirection = ctx.player.getDirection();
		}
		if (!ctx.player.onGround() && !ctx.player.isInWater()) {
			return null;
		}
		if (openSteps > MAX_OPEN_STEPS) {
			return SkillResult.failure("the tunnel came out into the open: nothing to dig here; go underground "
					+ "or face a hillside first" + (broken > 0 ? " (mined " + broken + " " + match + ")" : ""), data());
		}
		BlockState needsTool = null;
		while (turns < MAX_TURNS) {
			BlockPos front = ctx.feet().relative(tunnelDirection);
			boolean blocked = false;
			for (BlockPos pos : List.of(front.above(), front)) {
				BlockState there = ctx.level.getBlockState(pos);
				if (!there.getFluidState().isEmpty()) {
					blocked = true;
					break;
				}
				if (there.isAir() || there.canBeReplaced()) {
					continue;
				}
				if (Mining.isNaturalTerrain(there) && !Mining.canHarvest(ctx.player.getInventory(), there)) {
					needsTool = there;
				}
				if (!Mining.isNaturalTerrain(there) || there.hasBlockEntity()
						|| !Mining.canHarvest(ctx.player.getInventory(), there) || Mining.touchesLava(ctx.level, pos)
						|| !com.xzier9.minegpt.client.MineGPTClient.safety().mayBreak(ctx.mc, pos)) {
					blocked = true;
					break;
				}
				target = pos.immutable();
				lastState = there;
				state = State.TUNNEL_BREAK;
				startChild(new BreakBlockSkill(target));
				return null;
			}
			BlockPos floor = front.below();
			BlockState under = ctx.level.getBlockState(floor);
			if (!blocked && under.getFluidState().isEmpty() && under.isCollisionShapeFullBlock(ctx.level, floor)) {
				state = State.TUNNEL_STEP;
				startChild(GotoSkill.to(new Goal.Block(front.getX(), front.getY(), front.getZ()),
						"tunnel " + tunnelDirection.getName(), GotoSkill.Dig.NEVER, 500, 5 * 20));
				return null;
			}
			turn();
		}
		if (needsTool != null && broken == 0) {
			return SkillResult.failure("need a " + Mining.toolKind(needsTool) + " that can mine "
					+ BreakBlockSkill.blockName(needsTool), data());
		}
		return SkillResult.failure("the tunnel is blocked every way (lava, water, a drop or something built)"
				+ (broken > 0 ? " after mining " + broken + " " + match : ""), data());
	}

	private void unreachable(SkillContext ctx, BlockPos pos) {
		skip.add(pos);
		UNREACHABLE.add(pos.immutable(), ctx.level.getGameTime());
	}

	/** Leaves out blocks that failed to be reached a short while ago. */
	private void skipUnreachable(SkillContext ctx) {
		skip.addAll(UNREACHABLE.active(ctx.level.getGameTime()));
	}

	private void addPicked(SkillResult collected) {
		if (collected == null || collected.data() == null || !collected.data().has("items")) {
			return;
		}
		for (var entry : collected.data().getAsJsonObject("items").entrySet()) {
			String id = entry.getKey();
			int count = entry.getValue().getAsInt();
			picked.addProperty(id, picked.has(id) ? picked.get(id).getAsInt() + count : count);
		}
	}

	private String pickedSummary() {
		if (picked.isEmpty()) {
			return ", picked up nothing";
		}
		StringBuilder text = new StringBuilder(", picked up ");
		for (var entry : picked.entrySet()) {
			if (text.length() > 12) {
				text.append(", ");
			}
			text.append(entry.getValue().getAsInt()).append(' ').append(entry.getKey());
		}
		return text.toString();
	}

	private SkillResult search(SkillContext ctx) {
		skipUnreachable(ctx);
		BlockPos feet = ctx.feet();
		List<BlockPos> found = BlockSearch.find(ctx.level, feet, radius, match, !hidden, skip, CANDIDATES);
		BlockState needsTool = null;
		List<TargetPicker.Option<BlockPos>> options = new ArrayList<>();
		for (BlockPos pos : found) {
			BlockState blockState = ctx.level.getBlockState(pos);
			if (!Mining.canHarvest(ctx.player.getInventory(), blockState)) {
				needsTool = blockState;
				continue;
			}
			if (!worthReaching(ctx, pos)) {
				skip.add(pos);
				continue;
			}
			Goal reach = new Goal.Reach(pos.getX(), pos.getY(), pos.getZ(), REACH);
			if ((ctx.player.onGround() || ctx.player.isInWater()) && reach.reached(feet.getX(), feet.getY(), feet.getZ())) {
				// Within arm's reach already.
				target = pos;
				lastState = blockState;
				state = State.BREAK;
				startChild(new BreakBlockSkill(pos));
				return null;
			}
			if (options.size() < PICK_OPTIONS) {
				options.add(new TargetPicker.Option<>(pos, reach));
			}
		}
		if (options.size() == 1) {
			// Nothing to choose between: the walk there finds out whether there is a way.
			return approach(ctx, options.get(0).target());
		}
		if (!options.isEmpty()) {
			if (!ctx.player.onGround() && !ctx.player.isInWater() && !ctx.player.onClimbable()) {
				// Still falling: wait to land, so the routes start from solid ground.
				return null;
			}
			picker = new TargetPicker<>(ctx, options, true, Tuning.MINE_PICK_NODES.i());
			state = State.PICK;
			return pick(ctx);
		}

		if (needsTool != null) {
			return SkillResult.failure("need a " + Mining.toolKind(needsTool) + " that can mine "
					+ BreakBlockSkill.blockName(needsTool), data());
		}
		if (explores >= maxExplores) {
			return SkillResult.failure("found no " + (hidden ? "" : "visible ") + match + " within " + radius
					+ " blocks" + (broken > 0 ? " after mining " + broken : ""), data());
		}
		explores++;
		if (exploreDirection == null) {
			exploreDirection = ctx.player.getDirection();
		}
		BlockPos goal = feet.relative(exploreDirection, EXPLORE_DISTANCE);
		state = State.EXPLORE;
		startChild(GotoSkill.to(new Goal.XZ(goal.getX(), goal.getZ()), "explore " + exploreDirection.getName(),
				GotoSkill.Dig.ALWAYS, 6_000, 60 * 20));
		return null;
	}

	/** Works out which candidate has the cheapest route, a slice per tick, then sets off for it. */
	private SkillResult pick(SkillContext ctx) {
		if (!picker.advance()) {
			return null;
		}
		TargetPicker<BlockPos> picked = picker;
		picker = null;
		state = State.SEARCH;
		BlockPos chosen = picked.chosen();
		if (chosen == null && picked.noneReachable()) {
			// Even digging, none can be got to. Rule them out and look again.
			for (TargetPicker.Option<BlockPos> option : picked.options()) {
				unreachable(ctx, option.target());
			}
			return search(ctx);
		}
		// With no answer in the budget, fall back on the nearest; the walk there searches further.
		return approach(ctx, chosen != null ? chosen : picked.options().get(0).target());
	}

	private SkillResult approach(SkillContext ctx, BlockPos pos) {
		BlockState blockState = ctx.level.getBlockState(pos);
		target = pos;
		lastState = blockState;
		state = State.APPROACH;
		startChild(GotoSkill.to(new Goal.Reach(pos.getX(), pos.getY(), pos.getZ(), REACH),
				BreakBlockSkill.blockName(blockState) + " at " + pos.toShortString(), GotoSkill.Dig.ALWAYS, 10_000, 60 * 20));
		return null;
	}

	@Override
	protected boolean interruptChild(SkillContext ctx) {
		// While exploring, look around now and then and stop as soon as something turns up.
		if (state != State.EXPLORE || ticks() % 20 != 0) {
			return false;
		}
		for (BlockPos pos : BlockSearch.find(ctx.level, ctx.feet(), 12, match, !hidden, skip, 4)) {
			if (!Mining.canHarvest(ctx.player.getInventory(), ctx.level.getBlockState(pos))) {
				continue;
			}
			if (worthReaching(ctx, pos)) {
				return true;
			}
			skip.add(pos);
		}
		return false;
	}

	/**
	 * False for blocks that touch lava, which breaking would let out, and for visible blocks with
	 * nowhere dry to stand within reach: too high up, boxed in, or under water, where pathing
	 * there would fail slowly or leave the player mining while it runs out of air.
	 */
	private boolean worthReaching(SkillContext ctx, BlockPos pos) {
		return !Mining.touchesLava(ctx.level, pos) && (hidden || hasStandingSpot(ctx, pos));
	}

	@Override
	protected String describe() {
		return switch (state) {
			case EXPLORE -> "exploring for " + match;
			case TUNNEL_BREAK, TUNNEL_STEP -> "tunnelling for " + match + " (" + broken + "/" + wanted + ")";
			default -> "mining " + match + " (" + broken + "/" + wanted + ")";
		};
	}

	private JsonObject data() {
		JsonObject data = new JsonObject();
		data.addProperty("broken", broken);
		data.add("blocks", blocks);
		data.addProperty("explored", explores);
		data.add("picked_up", picked);
		return data;
	}

	/**
	 * True if there is somewhere dry to stand within reach of the block without digging, and not
	 * beside lava. Leaves do not count as ground, so logs at the top of a tree are not chased.
	 */
	private static boolean hasStandingSpot(SkillContext ctx, BlockPos target) {
		Goal reach = new Goal.Reach(target.getX(), target.getY(), target.getZ(), REACH);
		BlockView view = new ClientBlockView(ctx.level, ctx.player.getInventory());
		int r = (int) Math.ceil(REACH);
		for (int dy = -r - 2; dy <= r; dy++) {
			for (int dx = -r; dx <= r; dx++) {
				for (int dz = -r; dz <= r; dz++) {
					int x = target.getX() + dx;
					int y = target.getY() + dy;
					int z = target.getZ() + dz;
					if (reach.reached(x, y, z) && view.cell(x, y, z) == BlockView.PASSABLE
							&& view.cell(x, y + 1, z) == BlockView.PASSABLE && view.cell(x, y - 1, z) == BlockView.SOLID
							&& Hazards.beside(view, x, y, z) != Hazards.DANGER) {
						return true;
					}
				}
			}
		}
		return false;
	}
}
