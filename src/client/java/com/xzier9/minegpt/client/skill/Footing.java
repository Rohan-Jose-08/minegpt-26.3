package com.xzier9.minegpt.client.skill;

import com.xzier9.minegpt.client.act.Action.MoveKey;
import com.xzier9.minegpt.client.path.ClientBlockView;
import com.xzier9.minegpt.path.BlockView;
import com.xzier9.minegpt.path.Hazards;
import java.util.Set;
import net.minecraft.core.BlockPos;
import net.minecraft.util.Mth;

/**
 * Keeps skills that walk straight at something, such as a dropped item or a mob, from walking
 * into trouble on the way. The pathfinder does this for paths; this covers the last few blocks.
 */
final class Footing {
	/** How far ahead of the player's middle to look: a little past the edge of its body. */
	private static final double LOOK_AHEAD = 0.8;

	private Footing() {
	}

	/**
	 * Adjusts the keys about to be held. Next to water, lava or a long drop it adds sneak, which
	 * stops the player at the edge instead of over it. Facing lava or fire at the same level,
	 * where sneaking would not help, it lets go of the walking keys.
	 */
	static void guard(SkillContext ctx, Set<MoveKey> keys) {
		boolean forward = keys.contains(MoveKey.FORWARD);
		boolean back = keys.contains(MoveKey.BACK);
		if (forward == back || !ctx.player.onGround() || ctx.player.isInWater()) {
			return;
		}
		BlockView view = new ClientBlockView(ctx.level, ctx.player.getInventory());
		BlockPos feet = ctx.feet();
		if (Hazards.beside(view, feet.getX(), feet.getY(), feet.getZ()) == Hazards.NONE) {
			return;
		}
		float yaw = ctx.player.getYRot() * Mth.DEG_TO_RAD;
		double ahead = forward ? LOOK_AHEAD : -LOOK_AHEAD;
		int x = Mth.floor(ctx.player.getX() - Mth.sin(yaw) * ahead);
		int z = Mth.floor(ctx.player.getZ() + Mth.cos(yaw) * ahead);
		if (view.cell(x, feet.getY(), z) == BlockView.DANGER || view.cell(x, feet.getY() + 1, z) == BlockView.DANGER) {
			keys.remove(MoveKey.FORWARD);
			keys.remove(MoveKey.BACK);
			keys.remove(MoveKey.SPRINT);
			return;
		}
		keys.remove(MoveKey.SPRINT);
		keys.add(MoveKey.SNEAK);
	}
}
