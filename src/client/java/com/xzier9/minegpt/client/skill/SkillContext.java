package com.xzier9.minegpt.client.skill;

import com.xzier9.minegpt.client.act.Action;
import com.xzier9.minegpt.client.act.Action.MoveKey;
import com.xzier9.minegpt.client.act.ActionExecutor;
import java.util.EnumSet;
import java.util.Set;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.multiplayer.MultiPlayerGameMode;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

/**
 * What a skill sees and touches during one tick.
 *
 * Skills press keys through the agent's {@link ActionExecutor}, one tick at a time:
 * a key is held for as long as the skill keeps asking for it and is let go the tick after.
 * That keeps skill input identical to what the brain or a recording would produce.
 */
public final class SkillContext {
	public final Minecraft mc;
	public final LocalPlayer player;
	public final ClientLevel level;
	public final MultiPlayerGameMode gameMode;
	private final ActionExecutor executor;

	public SkillContext(Minecraft mc, ActionExecutor executor) {
		this.mc = mc;
		this.player = mc.player;
		this.level = mc.level;
		this.gameMode = mc.gameMode;
		this.executor = executor;
	}

	public void act(Action action) {
		executor.submit(mc, action);
	}

	/** Holds these movement keys for this tick. */
	public void hold(Set<MoveKey> keys) {
		if (!keys.isEmpty()) {
			act(new Action.Move(keys, 1));
		}
	}

	public void hold(MoveKey key) {
		hold(EnumSet.of(key));
	}

	/** Turns towards a point smoothly: call it each tick while the point should stay in view. */
	public void lookAt(Vec3 target) {
		executor.lookToward(mc, target.x, target.y, target.z);
	}

	public void holdAttack() {
		act(new Action.Attack(1));
	}

	public void clickAttack() {
		act(new Action.Attack(0));
	}

	public void clickUse() {
		act(new Action.Use(0));
	}

	/**
	 * Uses the item in hand on the block face the crosshair was just found on, as a right click
	 * does, but with that very face: a click a tick later can land on the face next to it while
	 * the camera is still settling.
	 */
	public void useOn(BlockHitResult hit) {
		var hand = net.minecraft.world.InteractionHand.MAIN_HAND;
		var stack = player.getItemInHand(hand);
		var animation = stack.getInteractAnimation();
		// As a click does: the arm swings when the client predicts the use worked.
		if (gameMode.useItemOn(player, hand, hit) instanceof net.minecraft.world.InteractionResult.Success success
				&& success.swingSource() == net.minecraft.world.InteractionResult.SwingSource.PREDICTED) {
			player.swing(hand, animation, false);
		}
	}

	public void holdUse() {
		act(new Action.Use(1));
	}

	public void closeScreen() {
		act(new Action.CloseScreen());
	}

	/** The block under the crosshair, or null. */
	public BlockHitResult blockTarget() {
		HitResult hit = mc.hitResult;
		return hit instanceof BlockHitResult block && hit.getType() == HitResult.Type.BLOCK ? block : null;
	}

	public boolean isLookingAt(BlockPos pos) {
		BlockHitResult hit = blockTarget();
		return hit != null && hit.getBlockPos().equals(pos);
	}

	/** The block the player's feet are in, allowing for floors slightly lower than a full block. */
	public BlockPos feet() {
		return BlockPos.containing(player.getX(), player.getY() + 0.2, player.getZ());
	}
}
