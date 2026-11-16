package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;
import com.xzier9.minegpt.MineGPT;
import com.xzier9.minegpt.client.act.Action.MoveKey;
import com.xzier9.minegpt.client.path.ClientBlockView;
import com.xzier9.minegpt.path.BlockView;
import com.xzier9.minegpt.path.Goal;
import com.xzier9.minegpt.path.Hazards;
import com.xzier9.minegpt.path.Path;
import com.xzier9.minegpt.path.Pathfinder;
import com.xzier9.minegpt.path.Steering;
import com.xzier9.minegpt.path.StuckWatch;
import it.unimi.dsi.fastutil.longs.Long2BooleanOpenHashMap;
import it.unimi.dsi.fastutil.longs.Long2DoubleOpenHashMap;
import java.util.EnumSet;
import java.util.List;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.util.Mth;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

/**
 * Walks to a goal along a path from {@link Pathfinder}, and plans again whenever the player
 * is pushed off course, gets stuck, or reaches the end of a partial path.
 *
 * By default it plans a walking route first and digs through natural terrain only when there
 * is no other way. With spare blocks ({@link Scaffold}) it pillars up out of holes and bridges
 * gaps and water. Along water, lava or a long drop it sneaks, which stops the player at the
 * edge instead of letting it overshoot and fall in. Cells where the player got stuck or went
 * under water are remembered, and later plans keep away from them. On open, level ground it
 * heads straight for a step further along instead of zigzagging along the grid, and it sprints
 * on long straight runs.
 *
 * It climbs ladders, vines and scaffolding, opens wooden doors and gates and shuts them behind
 * it, jumps narrow gaps, and drops into water from any height. On a long trip the next stretch
 * is planned while the player is still walking the current one, so it does not stop to think at
 * the end of each stretch.
 */
public final class GotoSkill extends Skill {
	/** When the path may break blocks. */
	public enum Dig {
		/** Never: walk, swim, climb and place only. */
		NEVER,
		/** Only once no way without digging is found. */
		FALLBACK,
		/** From the start, wherever digging is quicker than going round. */
		ALWAYS
	}

	/** Plans that fail for a reason (blocked, stuck, off course) before giving up. */
	private static final int MAX_FAILURES = 10;
	/** Partial paths in a row that get no closer before giving up. */
	private static final int MAX_STALLS = 3;
	private static final double LOITER_RADIUS = 2.5;
	private static final int PLACE_TRIES = 3;
	/** Time spent searching per tick while standing still: a long search is spread over several ticks instead of freezing a frame. */
	private static final long SEARCH_NANOS = 8_000_000;
	/** The same while walking, when the search is for the stretch after this one. */
	private static final long PREPLAN_NANOS = 3_000_000;
	/** Steps left on a partial path when planning of the next stretch starts. */
	private static final int PREPLAN_STEPS = 14;
	/** Ticks under water that are not held against a spot right after dropping into it from high up. */
	private static final int PLUNGE_GRACE = 60;
	/** Ticks to wait after clicking a door before clicking again. */
	private static final int DOOR_COOLDOWN = 8;
	/** Times the head goes under before giving up on a flooded area. */
	private static final int MAX_SUBMERSIONS = 3;
	/** Most steps ahead to head for directly across open, level ground. */
	private static final int SHORTCUT_STEPS = 8;
	/** Straight steps ahead before sprinting is worth it; less and the player overshoots the turn. */
	private static final int SPRINT_RUN = 3;
	/** Half the player's width, a hair less so a body against a wall does not count the wall. */
	private static final double HALF_WIDTH = 0.29;
	/** How far ahead, at least, the eyes rest on the ground while walking: closer and the camera would dip at every step. */
	private static final double LOOK_AHEAD = 3.5;
	/** Turned further than this from the way to go, the player sidesteps while the camera comes round. */
	private static final float SIDESTEP_DEGREES = 35;
	/** Ticks pressed against something before hopping over it; a brush past a corner is not a reason to jump. */
	private static final int BUMP_TICKS = 3;

	/**
	 * Told where each pillar or bridge block goes, while set. The build skill uses it to take
	 * its scaffolding down again afterwards. Only touched on the client thread.
	 */
	static java.util.function.Consumer<BlockPos> placedListener;

	private final Goal goal;
	private final String target;
	private final Dig dig;
	private final boolean allowPlace;
	private final int maxNodes;
	private final boolean sprint;
	private final boolean parkour;
	/** Extra costs for cells this goto has learned to avoid, by {@link Pathfinder#pack}. */
	private final Long2DoubleOpenHashMap penalties = new Long2DoubleOpenHashMap();
	/** Safe-ground answers for the current tick's shortcut checks. */
	private final Long2BooleanOpenHashMap safe = new Long2BooleanOpenHashMap();

	/** Catches what the per-step limit misses: shuffling between steps, or circling, without getting anywhere. */
	private final StuckWatch watch = new StuckWatch(Tuning.GOTO_STILL_TICKS.i(), Tuning.GOTO_LOITER_TICKS.i(), LOITER_RADIUS);
	private final Steering.Sustained bumped = new Steering.Sustained(BUMP_TICKS);
	private boolean digging;
	private Path path;
	private Pathfinder.Search search;
	private int searchBudget;
	private long searchStartedAt;
	private BlockPos start;
	private int index;
	private int stepTicks;
	private boolean planNeeded = true;
	private int failures;
	private int stalls;
	private double bestDistance = Double.MAX_VALUE;
	private String lastProblem = "no problem";
	private int broken;
	private int placed;
	private int submergedTicks;
	private int submersions;
	private int unstickTicks;
	private BlockPos unstickFrom;
	/** For the step being placed: a click has been sent and the block has not shown up yet. */
	private boolean clicked;
	private int clickTicks;
	private int placeTries;
	/** The search for the stretch after the current partial path, run while walking, and where it starts. */
	private Pathfinder.Search nextSearch;
	private Path nextPath;
	private BlockPos nextStart;
	private int nextBudget;
	private long nextStartedAt;
	private int plungeGrace;
	private int doorCooldown;
	/** A door this goto opened and has yet to shut behind it. */
	private BlockPos openedDoor;
	private int closeTicks;
	/** For a two-block jump: the run-up has been taken. */
	private boolean ranUp;
	/** Pillaring: off the ground since the last jump, and jumps that came down with no click. */
	private boolean airborne;
	private int idleJumps;
	/** Ticks spent on a scaffold step without a click, and knocking a soft block off it. */
	private int waitTicks;
	private int knockTicks;

	public GotoSkill(Goal goal, String target, Dig dig, boolean allowPlace, int maxNodes, boolean sprint, int maxTicks) {
		this(goal, target, dig, allowPlace, maxNodes, sprint, true, maxTicks);
	}

	public GotoSkill(Goal goal, String target, Dig dig, boolean allowPlace, int maxNodes, boolean sprint, boolean parkour,
			int maxTicks) {
		super("goto", maxTicks);
		this.goal = goal;
		this.target = target;
		this.dig = dig;
		this.allowPlace = allowPlace;
		this.maxNodes = maxNodes;
		this.sprint = sprint;
		this.parkour = parkour;
		this.digging = dig == Dig.ALWAYS;
	}

	/** Goto with default settings, for other skills. */
	static GotoSkill to(Goal goal, String target, Dig dig, int maxNodes, int maxTicks) {
		return new GotoSkill(goal, target, dig, true, maxNodes, true, maxTicks);
	}

	static GotoSkill fromArgs(Args args) {
		Goal goal;
		String target;
		if (args.has("x") && args.has("y") && args.has("z")) {
			int x = args.integer("x");
			int y = args.integer("y");
			int z = args.integer("z");
			double radius = args.decimal("radius", 0);
			goal = radius > 0 ? new Goal.Near(x, y, z, radius) : new Goal.Block(x, y, z);
			target = x + " " + y + " " + z;
		} else if (args.has("x") && args.has("z")) {
			goal = new Goal.XZ(args.integer("x"), args.integer("z"));
			target = args.integer("x") + " ~ " + args.integer("z");
		} else if (args.has("y")) {
			// Down to that height from above it, up to it from below.
			LocalPlayer player = Minecraft.getInstance().player;
			boolean up = player != null && player.getBlockY() < args.integer("y");
			goal = up ? new Goal.AboveY(args.integer("y")) : new Goal.BelowY(args.integer("y"));
			target = "y=" + args.integer("y") + (up ? " or above" : " or below");
		} else {
			throw args.bad("give x, y and z; or x and z; or just y to dig down");
		}
		// Left out, it digs only when there is no other way; true digs wherever quicker; false never.
		Dig dig = !args.has("allowBreak") ? Dig.FALLBACK : args.bool("allowBreak", false) ? Dig.ALWAYS : Dig.NEVER;
		int maxNodes = Math.max(1000, Math.min(200_000, args.integer("maxNodes", Pathfinder.Options.DEFAULT.maxNodes())));
		return new GotoSkill(goal, target, dig, args.bool("allowPlace", true), maxNodes, args.bool("sprint", true),
				args.bool("parkour", true), args.ticks("timeout", 120));
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		SkillResult dug = childResult();
		if (dug != null) {
			if (dug.success()) {
				broken++;
			} else {
				return replan(dug.message());
			}
		}
		if (ctx.mc.gui.screen() != null) {
			// An open screen swallows the movement keys.
			ctx.closeScreen();
			return null;
		}

		LocalPlayer player = ctx.player;
		BlockPos feet = ctx.feet();
		boolean settled = player.onGround() || player.isInWater() || player.onClimbable();
		if (doorCooldown > 0) {
			doorCooldown--;
		}
		if (plungeGrace > 0) {
			plungeGrace--;
		}
		if (settled && goal.reached(feet.getX(), feet.getY(), feet.getZ())) {
			return SkillResult.success("arrived at " + target, result(feet));
		}
		if (unstickTicks > 0) {
			unstickTicks--;
			EnumSet<MoveKey> keys = toward(player, unstickFrom.getX() + 0.5 - player.getX(), unstickFrom.getZ() + 0.5 - player.getZ());
			keys.add(MoveKey.JUMP);
			ctx.hold(keys);
			return null;
		}
		if (planNeeded) {
			if (search == null) {
				if (!settled) {
					return null;
				}
				BlockView view = new ClientBlockView(ctx.level, player.getInventory());
				int scaffold = allowPlace ? Scaffold.count(player.getInventory()) : 0;
				searchBudget = maxNodes;
				search = new Pathfinder(view, new Pathfinder.Options(digging, maxNodes, scaffold, penalties, parkour))
						.start(feet.getX(), feet.getY(), feet.getZ(), goal);
				searchStartedAt = System.nanoTime();
				start = feet;
			}
			if (!search.advanceFor(SEARCH_NANOS)) {
				return null;
			}
			Path found = search.result();
			search = null;
			SkillResult failed = accept(found);
			if (failed != null || planNeeded) {
				return failed;
			}
		}
		List<Path.Step> steps = path.steps();
		if (index >= steps.size()) {
			if (!path.complete() && nextStart != null && nextStart.equals(feet)) {
				// The next stretch was planned on the way here: finish that search if need be and carry on.
				if (nextPath == null && !nextSearch.advanceFor(SEARCH_NANOS)) {
					return null;
				}
				Path found = nextPath != null ? nextPath : nextSearch.result();
				if (found.isEmpty() || (!found.complete() && found.nodesExplored() < nextBudget)) {
					// It found no way on, perhaps because the chunks ahead had not loaded yet: look again from here.
					return replan(null);
				}
				start = nextStart;
				searchBudget = nextBudget;
				searchStartedAt = nextStartedAt;
				dropPreplan();
				SkillResult failed = accept(found);
				if (failed != null || planNeeded) {
					return failed;
				}
				steps = path.steps();
			} else {
				// The end of a partial path is normal: plan the next stretch. The end of a full path
				// without being at the goal means the world did not match the plan.
				return replan(path.complete() ? "the path ended short of the goal" : null);
			}
		}
		planAhead(ctx);
		if (closeDoorBehind(ctx, feet)) {
			return null;
		}

		if (startDigging(ctx, steps.get(index))) {
			return null;
		}
		skipAhead(ctx, feet);
		if (arrived(ctx, steps.get(index))) {
			advance();
			if (index >= steps.size()) {
				return null;
			}
			if (startDigging(ctx, steps.get(index))) {
				return null;
			}
		}
		Path.Step step = steps.get(index);
		if (!isOpen(ctx, step.x(), step.y(), step.z()) || !isOpen(ctx, step.x(), step.y() + 1, step.z())) {
			return replan("something is in the way at " + step.x() + " " + step.y() + " " + step.z());
		}
		if (underWaterTooLong(ctx, step, feet)) {
			if (++submersions >= MAX_SUBMERSIONS) {
				return SkillResult.failure("kept going under water near " + feet.toShortString()
						+ ": the way to " + target + " looks flooded", result(feet));
			}
			return replan("went under water at " + feet.toShortString());
		}

		StuckWatch.State headway = watch.tick(feet.getX(), feet.getY(), feet.getZ());
		if (headway == StuckWatch.State.LOITERING) {
			return SkillResult.failure("made no headway near " + feet.toShortString() + " for " + Tuning.GOTO_LOITER_TICKS.i() / 20
					+ "s on the way to " + target + " (" + lastProblem + ")", result(feet));
		}
		if (headway == StuckWatch.State.STILL && !step.move().places()) {
			watch.moved();
			penalties.addTo(Pathfinder.pack(step.x(), step.y(), step.z()), Tuning.GOTO_STUCK_PENALTY.d());
			unstickFrom = index == 0 ? start : new BlockPos(path.steps().get(index - 1).x(), feet.getY(),
					path.steps().get(index - 1).z());
			unstickTicks = Tuning.GOTO_UNSTICK_TICKS.i();
			return replan("not moving at " + feet.toShortString());
		}

		int aim = shortcut(ctx, feet);
		int limit = (step.move().places() ? Tuning.GOTO_PLACE_STEP_TICKS.i() : Tuning.GOTO_WALK_STEP_TICKS.i()) + 5 * (aim - index)
				+ 3 * Math.max(0, feet.getY() - step.y());
		if (++stepTicks > limit) {
			if (step.move().places()) {
				return placeFailed(step, "could not place a block at " + step.x() + " " + (step.y() - 1) + " " + step.z());
			}
			penalties.addTo(Pathfinder.pack(step.x(), step.y(), step.z()), Tuning.GOTO_STUCK_PENALTY.d());
			// Back off towards where the player came from, jumping, to come loose from a corner.
			unstickFrom = index == 0 ? start : new BlockPos(path.steps().get(index - 1).x(), feet.getY(),
					path.steps().get(index - 1).z());
			unstickTicks = Tuning.GOTO_UNSTICK_TICKS.i();
			return replan("stuck near " + feet.toShortString());
		}
		if (settled && offPath(feet, aim)) {
			if (rejoin(feet)) {
				return null;
			}
			return replan("pushed off the path");
		}
		if (step.move() == Path.Move.DESCEND && feet.getY() - step.y() > Pathfinder.MAX_FALL) {
			// A long drop into water: sinking for a moment after it is expected.
			plungeGrace = PLUNGE_GRACE;
		}
		if (openDoor(ctx, step)) {
			return null;
		}
		if (step.move() == Path.Move.PARKOUR && !landed(ctx, step, feet)) {
			return jumpGap(ctx, step, feet);
		}
		BlockPos below = new BlockPos(step.x(), step.y() - 1, step.z());
		if (step.move().places() && isOpen(ctx, below.getX(), below.getY(), below.getZ())) {
			return step.move() == Path.Move.PILLAR ? pillar(ctx, step, below) : bridge(ctx, step, below);
		}
		if (clicked) {
			// The block just placed is there.
			placed++;
			clicked = false;
			OwnBlocks.record(ctx, below, com.xzier9.minegpt.build.Ledger.Purpose.SCAFFOLD);
			if (placedListener != null) {
				placedListener.accept(below);
			}
		}
		drive(ctx, step, steps.get(aim), Math.max(aim - index, straightRun()));
		return null;
	}

	@Override
	protected String describe() {
		String remaining = path == null ? "" : " (" + Math.max(0, path.steps().size() - index) + " steps)";
		return "going to " + target + remaining;
	}

	@Override
	protected void onStop(SkillContext ctx) {
		if (ctx.player.isSprinting()) {
			ctx.player.setSprinting(false);
		}
	}

	/** Takes a finished search's path, or gives up if it leads nowhere or no closer than before. */
	private SkillResult accept(Path found) {
		MineGPT.LOGGER.debug("Path to {}: {} steps, complete={}, {} nodes in {} ms, digging={}", target,
				found.steps().size(), found.complete(), found.nodesExplored(),
				(System.nanoTime() - searchStartedAt) / 1_000_000, digging);
		BlockPos feet = start;
		// A search that ran out of places to try, rather than out of budget, found every way there is.
		boolean noWay = found.isEmpty() || (!found.complete() && found.nodesExplored() < searchBudget);
		if (noWay && startDiggingInstead()) {
			return null;
		}
		if (found.isEmpty()) {
			return SkillResult.failure("no path to " + target
					+ (dig == Dig.NEVER ? " without digging (leave allowBreak out to dig when needed)" : ""), result(feet));
		}
		Path.Step end = found.steps().get(found.steps().size() - 1);
		double distance = goal.distance(end.x(), end.y(), end.z());
		if (!found.complete()) {
			if (distance >= bestDistance - 1) {
				if (startDiggingInstead()) {
					return null;
				}
				if (++stalls >= MAX_STALLS) {
					return SkillResult.failure("cannot get closer than " + Math.round(bestDistance) + " blocks to "
							+ target, result(feet));
				}
			} else {
				stalls = 0;
			}
		}
		bestDistance = Math.min(bestDistance, distance);
		path = found;
		index = 0;
		stepTicks = 0;
		resetPlacing();
		planNeeded = false;
		return null;
	}

	/** Switches a fallback goto over to digging and plans again. False if it digs already or may not. */
	private boolean startDiggingInstead() {
		if (dig != Dig.FALLBACK || digging) {
			return false;
		}
		digging = true;
		stalls = 0;
		bestDistance = Double.MAX_VALUE;
		planNeeded = true;
		return true;
	}

	private SkillResult replan(String problem) {
		search = null;
		dropPreplan();
		resetPlacing();
		if (problem != null) {
			lastProblem = problem;
			if (++failures > MAX_FAILURES) {
				return SkillResult.failure("gave up going to " + target + ": " + lastProblem);
			}
			if (failures >= Tuning.GOTO_DIG_AFTER_FAILURES.i() && dig == Dig.FALLBACK) {
				digging = true;
			}
		}
		planNeeded = true;
		return null;
	}

	private void advance() {
		index++;
		stepTicks = 0;
		resetPlacing();
	}

	private void resetPlacing() {
		clicked = false;
		clickTicks = 0;
		placeTries = 0;
		ranUp = false;
		airborne = false;
		idleJumps = 0;
		waitTicks = 0;
		knockTicks = 0;
	}

	private void dropPreplan() {
		nextSearch = null;
		nextPath = null;
		nextStart = null;
	}

	/**
	 * Near the end of a partial path, searches on from its last step while the player is still
	 * walking, a little each tick. Not when the rest of the path digs or places, since the world
	 * at its end will have changed by the time the player gets there.
	 */
	private void planAhead(SkillContext ctx) {
		if (path.complete() || nextPath != null) {
			return;
		}
		List<Path.Step> steps = path.steps();
		if (nextSearch == null) {
			if (steps.size() - index > PREPLAN_STEPS || index >= steps.size()) {
				return;
			}
			for (int i = index; i < steps.size(); i++) {
				if (steps.get(i).move().places() || steps.get(i).breaks().length > 0) {
					return;
				}
			}
			Path.Step last = steps.get(steps.size() - 1);
			BlockView view = new ClientBlockView(ctx.level, ctx.player.getInventory());
			int scaffold = allowPlace ? Scaffold.count(ctx.player.getInventory()) : 0;
			nextStart = new BlockPos(last.x(), last.y(), last.z());
			nextBudget = maxNodes;
			nextStartedAt = System.nanoTime();
			nextSearch = new Pathfinder(view, new Pathfinder.Options(digging, maxNodes, scaffold, penalties, parkour))
					.start(last.x(), last.y(), last.z(), goal);
		}
		if (nextSearch.advanceFor(PREPLAN_NANOS)) {
			nextPath = nextSearch.result();
		}
	}

	/** After being pushed off the path, picks it up again if the player stands on a step close to where it was. */
	private boolean rejoin(BlockPos feet) {
		List<Path.Step> steps = path.steps();
		int end = Math.min(steps.size(), index + 16);
		for (int i = Math.max(0, index - 4); i < end; i++) {
			Path.Step s = steps.get(i);
			if (s.x() == feet.getX() && s.y() == feet.getY() && s.z() == feet.getZ() && i + 1 < steps.size()) {
				index = i + 1;
				stepTicks = 0;
				resetPlacing();
				return true;
			}
		}
		return false;
	}

	/** The shut door or gate in the way of this step, or null. */
	private static BlockPos shutDoor(SkillContext ctx, Path.Step step) {
		for (int dy = 0; dy <= 1; dy++) {
			BlockPos pos = new BlockPos(step.x(), step.y() + dy, step.z());
			BlockState state = ctx.level.getBlockState(pos);
			if (ClientBlockView.isDoor(state) && state.hasProperty(BlockStateProperties.OPEN)
					&& !state.getValue(BlockStateProperties.OPEN)) {
				return pos;
			}
		}
		return null;
	}

	/** Opens a shut door or gate in the next step's way. True while busy with it. */
	private boolean openDoor(SkillContext ctx, Path.Step step) {
		BlockPos door = shutDoor(ctx, step);
		if (door == null) {
			return false;
		}
		if (clickDoor(ctx, door)) {
			openedDoor = door;
			closeTicks = 0;
		}
		return true;
	}

	/** Looks at a door and clicks it once it is under the crosshair. True once clicked. */
	private boolean clickDoor(SkillContext ctx, BlockPos door) {
		BlockState state = ctx.level.getBlockState(door);
		Vec3 aim = state.getShape(ctx.level, door).isEmpty() ? Vec3.atCenterOf(door)
				: state.getShape(ctx.level, door).bounds().getCenter().add(Vec3.atLowerCornerOf(door));
		ctx.lookAt(aim);
		BlockHitResult hit = ctx.blockTarget();
		if (doorCooldown > 0 || hit == null || !ctx.level.getBlockState(hit.getBlockPos()).is(state.getBlock())
				|| hit.getBlockPos().getX() != door.getX() || hit.getBlockPos().getZ() != door.getZ()
				|| ctx.player.isShiftKeyDown()) {
			return false;
		}
		ctx.clickUse();
		doorCooldown = DOOR_COOLDOWN;
		return true;
	}

	/**
	 * Shuts the door this goto opened once the player is through it, so mobs do not follow it
	 * into a house. Gives up after a moment or when the door is out of reach. True while busy.
	 */
	private boolean closeDoorBehind(SkillContext ctx, BlockPos feet) {
		if (openedDoor == null) {
			return false;
		}
		BlockState state = ctx.level.getBlockState(openedDoor);
		double dx = ctx.player.getX() - (openedDoor.getX() + 0.5);
		double dz = ctx.player.getZ() - (openedDoor.getZ() + 0.5);
		double distance = Math.sqrt(dx * dx + dz * dz);
		if (!ClientBlockView.isDoor(state) || !state.hasProperty(BlockStateProperties.OPEN)
				|| !state.getValue(BlockStateProperties.OPEN) || distance > 4 || closeTicks > 30) {
			openedDoor = null;
			return false;
		}
		if (distance < 1.3 || !ctx.player.onGround()) {
			// Still in the doorway.
			return false;
		}
		closeTicks++;
		if (clickDoor(ctx, openedDoor)) {
			openedDoor = null;
		}
		return true;
	}

	/** True once the player stands on the far side of the gap, where plain walking finishes the step. */
	private boolean landed(SkillContext ctx, Path.Step step, BlockPos feet) {
		int fromX = index == 0 ? start.getX() : path.steps().get(index - 1).x();
		int fromZ = index == 0 ? start.getZ() : path.steps().get(index - 1).z();
		double along = Math.abs(ctx.player.getX() - (fromX + 0.5)) + Math.abs(ctx.player.getZ() - (fromZ + 0.5));
		int length = Math.abs(step.x() - fromX) + Math.abs(step.z() - fromZ);
		return ctx.player.onGround() && feet.getY() == step.y() && along > length - 0.75;
	}

	/**
	 * Jumps a gap: lines up on the far side, walks to the edge and jumps. A two-block gap takes
	 * a sprint, so the player first steps back a block for a run-up. A jump that falls short
	 * lands somewhere the pathfinder checked was safe; the spot is then avoided.
	 */
	private SkillResult jumpGap(SkillContext ctx, Path.Step step, BlockPos feet) {
		LocalPlayer player = ctx.player;
		int fromX = index == 0 ? start.getX() : path.steps().get(index - 1).x();
		int fromZ = index == 0 ? start.getZ() : path.steps().get(index - 1).z();
		int dirX = Integer.signum(step.x() - fromX);
		int dirZ = Integer.signum(step.z() - fromZ);
		boolean far = Math.abs(step.x() - fromX) + Math.abs(step.z() - fromZ) >= 3;
		if (player.onGround() && feet.getY() < step.y()) {
			penalties.put(Pathfinder.pack(step.x(), step.y(), step.z()), Double.POSITIVE_INFINITY);
			return replan("missed the jump to " + step.x() + " " + step.y() + " " + step.z());
		}
		if (far && player.getFoodData().getFoodLevel() <= 6) {
			penalties.put(Pathfinder.pack(step.x(), step.y(), step.z()), Double.POSITIVE_INFINITY);
			return replan("too hungry to sprint over a gap");
		}
		// How far along the jump the player is, from the middle of the block it jumps from.
		double along = (player.getX() - (fromX + 0.5)) * dirX + (player.getZ() - (fromZ + 0.5)) * dirZ;
		double across = (player.getX() - (fromX + 0.5)) * dirZ + (player.getZ() - (fromZ + 0.5)) * dirX;
		double tx = step.x() + 0.5;
		double tz = step.z() + 0.5;
		ctx.lookAt(new Vec3(tx, player.getEyeY(), tz));
		float yaw = (float) Math.toDegrees(Math.atan2(-(tx - player.getX()), tz - player.getZ()));
		boolean facing = Math.abs(Mth.wrapDegrees(yaw - player.getYRot())) < 8;
		EnumSet<MoveKey> keys = EnumSet.noneOf(MoveKey.class);
		if (!player.onGround()) {
			// In the air: keep going until over the far side.
			if (along < (far ? 2.1 : 1.6)) {
				keys.add(MoveKey.FORWARD);
				if (far) {
					keys.add(MoveKey.SPRINT);
				}
			}
		} else if (far && !ranUp) {
			// Back to the far end of the block behind for a run-up.
			if (along > -0.9 || Math.abs(across) > 0.2) {
				keys.addAll(toward(player, fromX + 0.5 - dirX * 1.1 - player.getX(), fromZ + 0.5 - dirZ * 1.1 - player.getZ()));
			} else {
				ranUp = true;
			}
		} else if (Math.abs(across) > 0.2 && along < 0.1) {
			// Line up with the middle of the block first.
			keys.addAll(toward(player, fromX + 0.5 - player.getX(), fromZ + 0.5 - player.getZ()));
		} else if (facing) {
			keys.add(MoveKey.FORWARD);
			if (far) {
				keys.add(MoveKey.SPRINT);
			}
			if (along >= 0.25 && (!far || player.isSprinting())) {
				keys.add(MoveKey.JUMP);
			} else if (along >= 0.25) {
				// At the edge without the speed for it: take the run-up again.
				ranUp = false;
			}
		}
		ctx.hold(keys);
		return null;
	}

	/** Starts breaking the next block this step needs out of the way, top block first. */
	private boolean startDigging(SkillContext ctx, Path.Step step) {
		if (step.breaks().length == 0) {
			return false;
		}
		BlockPos best = null;
		for (long packed : step.breaks()) {
			BlockPos pos = new BlockPos(Pathfinder.unpackX(packed), Pathfinder.unpackY(packed), Pathfinder.unpackZ(packed));
			if (!isOpen(ctx, pos.getX(), pos.getY(), pos.getZ()) && (best == null || pos.getY() > best.getY())) {
				best = pos;
			}
		}
		if (best == null) {
			return false;
		}
		startChild(new BreakBlockSkill(best));
		return true;
	}

	private static boolean isOpen(SkillContext ctx, int x, int y, int z) {
		BlockPos pos = new BlockPos(x, y, z);
		int cell = ClientBlockView.classify(ctx.level, pos, ctx.level.getBlockState(pos));
		return cell == BlockView.PASSABLE || cell == BlockView.WATER || cell == BlockView.CLIMBABLE
				|| cell == BlockView.SCAFFOLDING || cell == BlockView.DOOR;
	}

	/**
	 * True once the head has been under water too long on a step that should keep it out, as
	 * when water flows the player under or the swim sinks. The spot is ruled out for later plans.
	 */
	private boolean underWaterTooLong(SkillContext ctx, Path.Step step, BlockPos feet) {
		boolean rising = step.move() == Path.Move.SWIM && step.y() > feet.getY();
		if (!ctx.player.isUnderWater() || rising || plungeGrace > 0) {
			submergedTicks = 0;
			return false;
		}
		if (++submergedTicks <= Tuning.GOTO_SUBMERGED_TICKS.i()) {
			return false;
		}
		submergedTicks = 0;
		penalties.put(Pathfinder.pack(feet.getX(), feet.getY(), feet.getZ()), Double.POSITIVE_INFINITY);
		penalties.put(Pathfinder.pack(step.x(), step.y(), step.z()), Double.POSITIVE_INFINITY);
		return true;
	}

	/** Puts down the block to stand on by jumping and placing it underneath. */
	private SkillResult pillar(SkillContext ctx, Path.Step step, BlockPos pos) {
		if (!holdScaffold(ctx)) {
			return replan("ran out of blocks to pillar with");
		}
		LocalPlayer player = ctx.player;
		BlockPos support = pos.below();
		SkillResult soft = knockOffSoft(ctx, step, pos);
		if (soft != null || knockTicks > 0 && !softDone(ctx, pos)) {
			return soft;
		}
		// Short grass in the spot is clicked itself: the block then takes its place.
		BlockState inSpot = ctx.level.getBlockState(pos);
		boolean grassy = !inSpot.isAir() && inSpot.canBeReplaced() && inSpot.getFluidState().isEmpty()
				&& !inSpot.getShape(ctx.level, pos).isEmpty();
		ctx.lookAt(Vec3.atCenterOf(support));
		EnumSet<MoveKey> keys = EnumSet.noneOf(MoveKey.class);
		if (player.onGround()) {
			if (airborne && !clicked && ++idleJumps >= Tuning.GOTO_IDLE_JUMPS.i()) {
				// Up and down with no chance to click: something catches the crosshair. Try elsewhere.
				return placeFailed(step, "could not aim at the spot to pillar up at " + pos.toShortString());
			}
			airborne = false;
			if (clicked) {
				// Landed again without the block appearing.
				clicked = false;
				if (++placeTries >= PLACE_TRIES) {
					return placeFailed(step, "could not pillar up at " + pos.toShortString());
				}
			}
			double dx = pos.getX() + 0.5 - player.getX();
			double dz = pos.getZ() + 0.5 - player.getZ();
			if (Math.abs(dx) > 0.3 || Math.abs(dz) > 0.3) {
				keys.addAll(toward(player, dx, dz));
			} else if (player.getXRot() > 75) {
				keys.add(MoveKey.JUMP);
			}
		} else if (!clicked && player.getY() > pos.getY() + 1.02
				&& (aimingAt(ctx, support, Direction.UP) || grassy && ctx.isLookingAt(pos))
				&& Scaffold.usable(player.getInventory(), player.getMainHandItem())) {
			// High enough that the new block does not overlap the feet.
			ctx.clickUse();
			clicked = true;
		}
		if (!player.onGround()) {
			airborne = true;
		}
		ctx.hold(keys);
		return null;
	}

	/**
	 * Puts down the block to walk onto: sneaks out over the edge, looks back down at the side
	 * of the block it stands on, and places against it.
	 */
	private SkillResult bridge(SkillContext ctx, Path.Step step, BlockPos pos) {
		if (!holdScaffold(ctx)) {
			return replan("ran out of blocks to bridge with");
		}
		LocalPlayer player = ctx.player;
		int fromX = index == 0 ? start.getX() : path.steps().get(index - 1).x();
		int fromZ = index == 0 ? start.getZ() : path.steps().get(index - 1).z();
		BlockPos support = new BlockPos(fromX, pos.getY(), fromZ);
		Direction face = Direction.getNearest(pos.getX() - fromX, 0, pos.getZ() - fromZ, Direction.NORTH);
		SkillResult soft = knockOffSoft(ctx, step, pos);
		if (soft != null || knockTicks > 0 && !softDone(ctx, pos)) {
			return soft;
		}
		BlockState inSpot = ctx.level.getBlockState(pos);
		boolean grassy = !inSpot.isAir() && inSpot.canBeReplaced() && inSpot.getFluidState().isEmpty()
				&& !inSpot.getShape(ctx.level, pos).isEmpty();
		if (!clicked && ++waitTicks > Tuning.GOTO_BRIDGE_WAIT_TICKS.i() + PLACE_TRIES * 10) {
			return placeFailed(step, "could not aim at the spot to bridge to " + pos.toShortString());
		}
		// Low on the face, so the line of sight from just past the edge comes in steeply.
		ctx.lookAt(Vec3.atCenterOf(support).add(face.getStepX() * 0.5, -0.25, face.getStepZ() * 0.5));
		EnumSet<MoveKey> keys = EnumSet.of(MoveKey.SNEAK);
		if (clicked) {
			if (++clickTicks > 10) {
				clicked = false;
				if (++placeTries >= PLACE_TRIES) {
					return placeFailed(step, "could not bridge to " + step.x() + " " + step.y() + " " + step.z());
				}
			}
		} else if (player.isShiftKeyDown() && player.onGround()
				&& (aimingAt(ctx, support, face) || grassy && ctx.isLookingAt(pos))
				&& Scaffold.usable(player.getInventory(), player.getMainHandItem())) {
			ctx.clickUse();
			clicked = true;
			clickTicks = 0;
		} else {
			// Sneaking stops the player at the edge, with the eyes just past it.
			keys.addAll(toward(player, face.getStepX(), face.getStepZ()));
		}
		ctx.hold(keys);
		return null;
	}

	/** Puts a spare block in hand. False if there is none left. */
	private static boolean holdScaffold(SkillContext ctx) {
		if (Scaffold.usable(ctx.player.getInventory(), ctx.player.getMainHandItem())) {
			return true;
		}
		int slot = Scaffold.slot(ctx.player.getInventory());
		if (slot < 0) {
			return false;
		}
		Inventories.select(ctx, slot);
		return true;
	}

	/** Rules out the step that could not be placed, so the next plan goes another way. */
	/**
	 * Knocks a torch, flower or other soft block out of the spot a scaffold block is to go in:
	 * its outline would catch the crosshair, and clicking on it does not replace it. Returns null
	 * when there is none (or it is gone), a result when the spot has to be given up, and keeps
	 * {@code knockTicks} counting while it works on it.
	 */
	private SkillResult knockOffSoft(SkillContext ctx, Path.Step step, BlockPos pos) {
		BlockState there = ctx.level.getBlockState(pos);
		if (there.isAir() || there.canBeReplaced() || !Mining.isSoft(ctx.level, pos, there)) {
			knockTicks = 0;
			return null;
		}
		if (!Mining.mayKnockOff(ctx.mc, ctx.level, pos, there)) {
			return placeFailed(step, BreakBlockSkill.blockName(there) + " is in the way at " + pos.toShortString()
					+ " (not placed by MineGPT, so left alone)");
		}
		if (++knockTicks > 30) {
			return placeFailed(step, "could not knock " + BreakBlockSkill.blockName(there) + " off " + pos.toShortString());
		}
		ctx.hold(EnumSet.noneOf(MoveKey.class));
		ctx.lookAt(BreakBlockSkill.softAim(ctx, pos));
		if (ctx.isLookingAt(pos)) {
			ctx.clickAttack();
		}
		return null;
	}

	/** True once the soft block being knocked off a scaffold spot is gone. */
	private boolean softDone(SkillContext ctx, BlockPos pos) {
		BlockState there = ctx.level.getBlockState(pos);
		boolean gone = there.isAir() || there.canBeReplaced() || !Mining.isSoft(ctx.level, pos, there);
		if (gone) {
			knockTicks = 0;
		}
		return gone;
	}

	private SkillResult placeFailed(Path.Step step, String problem) {
		penalties.put(Pathfinder.pack(step.x(), step.y(), step.z()), Double.POSITIVE_INFINITY);
		return replan(problem);
	}

	private static boolean aimingAt(SkillContext ctx, BlockPos pos, Direction face) {
		BlockHitResult hit = ctx.blockTarget();
		return hit != null && hit.getBlockPos().equals(pos) && hit.getDirection() == face;
	}

	/** True for a step on level, dry ground with nothing to break or place on the way. */
	private static boolean plain(Path.Step step, int y) {
		return (step.move() == Path.Move.WALK || step.move() == Path.Move.DIAGONAL) && step.breaks().length == 0
				&& step.y() == y;
	}

	/** Moves on to a later step when the player already stands in it, as after a shortcut. */
	private void skipAhead(SkillContext ctx, BlockPos feet) {
		if (!ctx.player.onGround()) {
			return;
		}
		List<Path.Step> steps = path.steps();
		int end = Math.min(steps.size(), index + SHORTCUT_STEPS + 1);
		for (int i = index; i < end; i++) {
			Path.Step s = steps.get(i);
			if (!plain(s, feet.getY())) {
				return;
			}
			if (i > index && s.x() == feet.getX() && s.z() == feet.getZ()) {
				index = i;
				stepTicks = 0;
				return;
			}
		}
	}

	/**
	 * The furthest step, up to {@link #SHORTCUT_STEPS} ahead, that the player can walk to in a
	 * straight line: every step up to it is plain, and the player's whole body stays over open,
	 * level, dry ground away from any edge on the way. The current step if there is none.
	 */
	private int shortcut(SkillContext ctx, BlockPos feet) {
		LocalPlayer player = ctx.player;
		if (!player.onGround() || player.isInWater()) {
			return index;
		}
		safe.clear();
		BlockView view = new ClientBlockView(ctx.level, player.getInventory());
		List<Path.Step> steps = path.steps();
		int best = index;
		int end = Math.min(steps.size(), index + SHORTCUT_STEPS + 1);
		for (int i = index; i < end; i++) {
			Path.Step s = steps.get(i);
			if (!plain(s, feet.getY())) {
				break;
			}
			if (i > index) {
				if (!clearLine(view, player.getX(), player.getZ(), s.x() + 0.5, s.z() + 0.5, feet.getY())) {
					break;
				}
				best = i;
			}
		}
		return best;
	}

	private boolean clearLine(BlockView view, double x1, double z1, double x2, double z2, int y) {
		double dx = x2 - x1;
		double dz = z2 - z1;
		int samples = Math.max(1, (int) Math.ceil(Math.sqrt(dx * dx + dz * dz) / 0.25));
		for (int i = 0; i <= samples; i++) {
			double x = x1 + dx * i / samples;
			double z = z1 + dz * i / samples;
			for (double ox = -HALF_WIDTH; ox <= HALF_WIDTH; ox += 2 * HALF_WIDTH) {
				for (double oz = -HALF_WIDTH; oz <= HALF_WIDTH; oz += 2 * HALF_WIDTH) {
					if (!safeGround(view, Mth.floor(x + ox), y, Mth.floor(z + oz))) {
						return false;
					}
				}
			}
		}
		return true;
	}

	private boolean safeGround(BlockView view, int x, int y, int z) {
		long key = Pathfinder.pack(x, y, z);
		if (safe.containsKey(key)) {
			return safe.get(key);
		}
		boolean ok = view.cell(x, y, z) == BlockView.PASSABLE && view.cell(x, y + 1, z) == BlockView.PASSABLE
				&& view.cell(x, y - 1, z) == BlockView.SOLID && Hazards.beside(view, x, y, z) == Hazards.NONE;
		safe.put(key, ok);
		return ok;
	}

	/** True if the player is more than a couple of blocks from every step it is heading along. */
	private boolean offPath(BlockPos feet, int aim) {
		for (int i = index; i <= aim; i++) {
			Path.Step s = path.steps().get(i);
			if (Math.max(Math.abs(feet.getX() - s.x()), Math.abs(feet.getZ() - s.z())) <= 2
					&& Math.abs(feet.getY() - s.y()) <= 3) {
				return false;
			}
		}
		return true;
	}

	private boolean arrived(SkillContext ctx, Path.Step step) {
		LocalPlayer player = ctx.player;
		double dx = player.getX() - (step.x() + 0.5);
		double dz = player.getZ() - (step.z() + 0.5);
		// On a straight run the player only needs to be in the block; before a turn it must be
		// near the middle, or its 0.6-wide body clips the corner of the next block.
		double tolerance = goesStraightOn(step) || step.move() == Path.Move.PILLAR || step.move() == Path.Move.CLIMB
				? 0.45 : 0.25;
		if (Math.abs(dx) > tolerance || Math.abs(dz) > tolerance) {
			return false;
		}
		int feetY = ctx.feet().getY();
		if (player.isInWater() && step.move() != Path.Move.SWIM) {
			// Floating players bob up and down.
			return Math.abs(feetY - step.y()) <= 1;
		}
		return (player.onGround() || player.isInWater() || player.onClimbable()) && feetY == step.y();
	}

	private boolean goesStraightOn(Path.Step step) {
		if (index + 1 >= path.steps().size()) {
			return false;
		}
		Path.Step next = path.steps().get(index + 1);
		int px = index == 0 ? start.getX() : path.steps().get(index - 1).x();
		int pz = index == 0 ? start.getZ() : path.steps().get(index - 1).z();
		return step.move() == Path.Move.WALK && next.move() == Path.Move.WALK && next.breaks().length == 0
				&& next.y() == step.y() && step.x() - px == next.x() - step.x() && step.z() - pz == next.z() - step.z();
	}

	/** Steps in a straight line ahead of the player, walking on the same level. */
	private int straightRun() {
		List<Path.Step> steps = path.steps();
		Path.Step first = steps.get(index);
		int px = index == 0 ? start.getX() : steps.get(index - 1).x();
		int pz = index == 0 ? start.getZ() : steps.get(index - 1).z();
		int dx = first.x() - px;
		int dz = first.z() - pz;
		int run = 0;
		for (int i = index; i < steps.size() && run < SPRINT_RUN; i++) {
			Path.Step s = steps.get(i);
			Path.Step before = i == index ? null : steps.get(i - 1);
			if (!plain(s, first.y()) || (before != null && (s.x() - before.x() != dx || s.z() - before.z() != dz))) {
				break;
			}
			run++;
		}
		return run;
	}

	/**
	 * Presses the keys for this tick: towards {@code aim}, which is the current step or one
	 * further along that can be reached in a straight line, with jumps and sneaks for the
	 * current step. {@code run} is how many steps ahead lie in a straight line.
	 */
	private void drive(SkillContext ctx, Path.Step step, Path.Step aim, int run) {
		LocalPlayer player = ctx.player;
		double tx = aim.x() + 0.5;
		double tz = aim.z() + 0.5;
		double dx = tx - player.getX();
		double dz = tz - player.getZ();
		double horizontal = Math.sqrt(dx * dx + dz * dz);
		int feetY = ctx.feet().getY();
		EnumSet<MoveKey> keys = EnumSet.noneOf(MoveKey.class);

		if (step.move() == Path.Move.CLIMB) {
			// Straight up or down a ladder: stay in the middle of the block, jump to go up. Going
			// down, letting go slides down a ladder or vine; scaffolding takes a sneak.
			if (horizontal > 0.15) {
				keys.addAll(toward(player, dx, dz));
			}
			if (step.y() > feetY || (step.y() == feetY && player.getY() < step.y())) {
				keys.add(MoveKey.JUMP);
			} else if (step.y() < feetY) {
				BlockPos at = ctx.feet();
				if (ctx.level.getBlockState(at).is(Blocks.SCAFFOLDING) || ctx.level.getBlockState(at.below()).is(Blocks.SCAFFOLDING)) {
					keys.add(MoveKey.SNEAK);
				}
			}
			if (player.isSprinting()) {
				player.setSprinting(false);
			}
			ctx.hold(keys);
			return;
		}
		if (horizontal > 0.1) {
			// Eyes on the ground a few blocks along the way, as a player's are, rather than level
			// at the next block: the camera then barely moves from one step to the next, and
			// looks up a slope or down a drop before reaching it.
			double ahead = Math.max(horizontal, LOOK_AHEAD);
			ctx.lookAt(new Vec3(player.getX() + dx / horizontal * ahead, aim.y() + 0.9, player.getZ() + dz / horizontal * ahead));
			// The keys follow where the target is, not where the camera points, so the feet keep
			// to the path while the camera comes round: forward once roughly facing it, with a
			// sidestep while still turning. Straight behind, turn first.
			EnumSet<MoveKey> heading = toward(player, dx, dz);
			float off = Math.abs(Mth.wrapDegrees((float) Math.toDegrees(Math.atan2(-dx, dz)) - player.getYRot()));
			// Exact steps (up, down, a jump) are taken facing them; sidestepping is for level ground.
			boolean level = (step.move() == Path.Move.WALK || step.move() == Path.Move.DIAGONAL) && player.onGround();
			if (level && off > SIDESTEP_DEGREES && !heading.contains(MoveKey.BACK)) {
				keys.addAll(heading);
			} else if (off < 50) {
				keys.add(MoveKey.FORWARD);
			}
		}
		if (step.move() == Path.Move.ASCEND && feetY < step.y() && (horizontal < 1.2 || player.horizontalCollision)) {
			keys.add(MoveKey.JUMP);
		}
		if (player.onClimbable() && !player.isInWater() && player.getY() <= step.y() + 0.1) {
			// Stepping off a ladder: hold on, or climb the last bit, instead of sliding down it.
			keys.add(MoveKey.JUMP);
		}
		if (player.isInWater()) {
			if (step.move() == Path.Move.SWIM && step.y() < feetY) {
				keys.add(MoveKey.SNEAK);
			} else if (feetY <= step.y()) {
				keys.add(MoveKey.JUMP);
			}
		} else if (bumped.update(player.horizontalCollision && player.onGround() && keys.contains(MoveKey.FORWARD))) {
			// Held up by something the path did not expect, such as a slab: hop over it.
			keys.add(MoveKey.JUMP);
		}
		boolean sprinting = false;
		if (keys.contains(MoveKey.FORWARD) && nearEdge(ctx, step, feetY)) {
			keys.add(MoveKey.SNEAK);
		} else if (sprint && run >= SPRINT_RUN && keys.contains(MoveKey.FORWARD) && !keys.contains(MoveKey.LEFT)
				&& !keys.contains(MoveKey.RIGHT) && !player.isInWater()
				&& step.move() != Path.Move.ASCEND && player.getFoodData().getFoodLevel() > 6) {
			keys.add(MoveKey.SPRINT);
			sprinting = true;
		}
		if (!sprinting && player.isSprinting()) {
			// Sprinting carries on after the key is let go; stop it before a turn or an edge.
			player.setSprinting(false);
		}
		ctx.hold(keys);
	}

	/** The movement keys that move the player in this direction in the world, whichever way it faces. */
	static EnumSet<MoveKey> toward(LocalPlayer player, double dx, double dz) {
		EnumSet<MoveKey> keys = EnumSet.noneOf(MoveKey.class);
		double length = Math.sqrt(dx * dx + dz * dz);
		if (length < 1e-6) {
			return keys;
		}
		float yaw = player.getYRot() * Mth.DEG_TO_RAD;
		// The player faces (-sin yaw, cos yaw); its right hand points to (-cos yaw, -sin yaw).
		double forward = (-Mth.sin(yaw) * dx + Mth.cos(yaw) * dz) / length;
		double right = (-Mth.cos(yaw) * dx - Mth.sin(yaw) * dz) / length;
		if (forward > 0.38) {
			keys.add(MoveKey.FORWARD);
		} else if (forward < -0.38) {
			keys.add(MoveKey.BACK);
		}
		if (right > 0.38) {
			keys.add(MoveKey.RIGHT);
		} else if (right < -0.38) {
			keys.add(MoveKey.LEFT);
		}
		return keys;
	}

	/**
	 * True when walking on level, dry ground beside water, lava or a long drop. Sneaking there
	 * keeps the player from stepping off the edge. Steps that mean to go down or into water are
	 * left alone, since sneaking would stop them.
	 */
	private static boolean nearEdge(SkillContext ctx, Path.Step step, int feetY) {
		if (!ctx.player.onGround() || ctx.player.isInWater() || step.y() != feetY
				|| (step.move() != Path.Move.WALK && step.move() != Path.Move.DIAGONAL && !step.move().places())) {
			return false;
		}
		BlockView view = new ClientBlockView(ctx.level, ctx.player.getInventory());
		if (view.cell(step.x(), step.y(), step.z()) != BlockView.PASSABLE
				|| view.cell(step.x(), step.y() - 1, step.z()) != BlockView.SOLID) {
			return false;
		}
		BlockPos feet = ctx.feet();
		return Hazards.beside(view, feet.getX(), feet.getY(), feet.getZ()) != Hazards.NONE
				|| Hazards.beside(view, step.x(), step.y(), step.z()) != Hazards.NONE;
	}

	private JsonObject result(BlockPos feet) {
		JsonObject data = new JsonObject();
		data.addProperty("x", feet.getX());
		data.addProperty("y", feet.getY());
		data.addProperty("z", feet.getZ());
		data.addProperty("broken", broken);
		data.addProperty("placed", placed);
		return data;
	}
}
