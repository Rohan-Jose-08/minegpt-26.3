package com.xzier9.minegpt.client.skill;

import java.util.function.Predicate;
import net.minecraft.core.BlockPos;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.phys.Vec3;

/**
 * Looks at a block within reach and right-clicks it until the expected screen opens. It aims
 * at whatever part of the block is in sight, so a table half hidden behind another block still
 * opens, and gives up quickly when none of it is.
 */
final class OpenContainerSkill extends Skill {
	private static final int MAX_CLICKS = 3;
	/** Ticks without any of the block in sight before giving up. */
	private static final int OUT_OF_SIGHT_LIMIT = 20;

	private final BlockPos pos;
	private final Predicate<AbstractContainerMenu> expected;
	private int clicks;
	private int outOfSight;

	OpenContainerSkill(BlockPos pos, Predicate<AbstractContainerMenu> expected) {
		super("open", 6 * 20);
		this.pos = pos;
		this.expected = expected;
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		if (expected.test(ctx.player.containerMenu)) {
			return SkillResult.success("opened " + BreakBlockSkill.blockName(ctx.level.getBlockState(pos)));
		}
		if (ctx.mc.gui.screen() != null) {
			ctx.closeScreen();
			return null;
		}
		Vec3 aim = BreakBlockSkill.aimPoint(ctx, pos);
		if (aim == null) {
			if (++outOfSight > OUT_OF_SIGHT_LIMIT) {
				return SkillResult.failure("cannot see the " + BreakBlockSkill.blockName(ctx.level.getBlockState(pos))
						+ " at " + pos.toShortString() + " from here");
			}
			return null;
		}
		outOfSight = 0;
		ctx.lookAt(aim);
		// Click, then give the server a moment to answer before clicking again.
		if (ctx.isLookingAt(pos) && ticks() % 10 == 1) {
			if (++clicks > MAX_CLICKS) {
				return SkillResult.failure("could not open the block at " + pos.toShortString());
			}
			ctx.clickUse();
		}
		return null;
	}

	@Override
	protected String describe() {
		return "opening " + pos.toShortString();
	}
}
