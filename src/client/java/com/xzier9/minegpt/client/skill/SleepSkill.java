package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;
import com.xzier9.minegpt.path.Goal;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import net.minecraft.core.BlockPos;
import net.minecraft.network.protocol.game.ServerboundPlayerCommandPacket;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.level.Level;
import net.minecraft.world.phys.Vec3;

/**
 * Sleeps in a bed nearby (or at x y z) until morning, which also sets the respawn point there.
 * On a server where the night does not pass because others stay up, it gets up again when
 * the time runs out. Beds only work in the overworld; elsewhere they explode, so it refuses.
 */
public final class SleepSkill extends Skill {
	private enum State {
		FIND, CLICK, SLEEP
	}

	private static final double REACH = 3.5;
	private static final int SEARCH_RADIUS = 32;
	private static final int MAX_CLICKS = 3;

	private final BlockPos requested;
	private State state = State.FIND;
	private BlockPos bed;
	private final Set<BlockPos> bad = new HashSet<>();
	private int clicks;
	private int clickTicks;
	private int tries;

	private SleepSkill(BlockPos requested, int maxTicks) {
		super("sleep", maxTicks);
		this.requested = requested;
	}

	static SleepSkill fromArgs(Args args) {
		BlockPos pos = null;
		if (args.has("x") || args.has("y") || args.has("z")) {
			pos = new BlockPos(args.integer("x"), args.integer("y"), args.integer("z"));
		}
		// A full night is a little under ten minutes.
		return new SleepSkill(pos, args.ticks("timeout", 11 * 60));
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		if (ctx.level.dimension() != Level.OVERWORLD) {
			return SkillResult.failure("beds only work in the overworld; anywhere else they explode");
		}
		SkillResult child = childResult();
		switch (state) {
			case FIND -> {
				if (child != null && !child.success()) {
					return retry("could not get to the bed: " + child.message());
				}
				return find(ctx);
			}
			case CLICK -> {
				return click(ctx);
			}
			case SLEEP -> {
				if (ctx.player.isSleeping()) {
					return null;
				}
				JsonObject data = new JsonObject();
				data.addProperty("x", bed.getX());
				data.addProperty("y", bed.getY());
				data.addProperty("z", bed.getZ());
				return SkillResult.success("slept in the bed at " + bed.toShortString() + " and woke up; respawn point set there",
						data);
			}
		}
		return null;
	}

	@Override
	protected String describe() {
		return state == State.SLEEP ? "sleeping" : "going to bed";
	}

	@Override
	protected void onStop(SkillContext ctx) {
		if (ctx.player.isSleeping()) {
			// Out of time (others on the server are still up) or cancelled: get out of bed, as the Leave Bed button does.
			ctx.player.connection.send(new ServerboundPlayerCommandPacket(ctx.player, ServerboundPlayerCommandPacket.Action.STOP_SLEEPING));
		}
	}

	private SkillResult find(SkillContext ctx) {
		if (requested != null) {
			if (!ctx.level.getBlockState(requested).is(BlockTags.BEDS)) {
				return SkillResult.failure("there is no bed at " + requested.toShortString());
			}
			bed = requested;
		} else {
			List<BlockPos> found = BlockSearch.find(ctx.level, ctx.feet(), SEARCH_RADIUS, s -> s.is(BlockTags.BEDS), true, bad, 1);
			if (found.isEmpty()) {
				return SkillResult.failure("no bed within " + SEARCH_RADIUS + " blocks; craft one from 3 wool and 3 planks "
						+ "and place it");
			}
			bed = found.get(0);
		}
		Goal reach = new Goal.Reach(bed.getX(), bed.getY(), bed.getZ(), REACH);
		BlockPos feet = ctx.feet();
		if (reach.reached(feet.getX(), feet.getY(), feet.getZ())) {
			Inventories.select(ctx, -1);
			state = State.CLICK;
			clicks = 0;
			clickTicks = 0;
		} else {
			startChild(GotoSkill.to(reach, "bed", GotoSkill.Dig.FALLBACK, 8_000, 60 * 20));
		}
		return null;
	}

	private SkillResult click(SkillContext ctx) {
		if (ctx.player.isSleeping()) {
			state = State.SLEEP;
			return null;
		}
		if (ctx.mc.gui.screen() != null) {
			ctx.closeScreen();
			return null;
		}
		Vec3 aim = BreakBlockSkill.aimPoint(ctx, bed);
		if (aim == null) {
			return retry("cannot see the bed from here");
		}
		ctx.lookAt(aim);
		// Click, then give the server a second to answer before clicking again.
		if (ctx.isLookingAt(bed) && clickTicks++ % 20 == 0) {
			if (++clicks > MAX_CLICKS) {
				return SkillResult.failure("could not sleep in the bed at " + bed.toShortString() + ": you can only sleep "
						+ "at night or in a thunderstorm, with no monsters nearby, in a bed that is not taken or blocked");
			}
			ctx.clickUse();
		}
		return null;
	}

	private SkillResult retry(String problem) {
		if (requested != null || ++tries > 2) {
			return SkillResult.failure(problem);
		}
		bad.add(bed);
		state = State.FIND;
		return null;
	}
}
