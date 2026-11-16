package com.xzier9.minegpt.client.safety;

import com.xzier9.minegpt.MineGPT;
import com.xzier9.minegpt.client.MineGPTClient;
import com.xzier9.minegpt.client.agent.Agent;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.BlockHitResult;

/**
 * Called from {@code MultiPlayerGameModeMixin} before the game breaks or uses a block. While
 * the agent is in control it refuses what {@link Safety} forbids and counts what it allows; when
 * the player is playing by hand everything goes through untouched.
 */
public final class Guard {
	/** Milliseconds between log lines about refused actions, so a skill retrying does not flood the log. */
	private static final long LOG_EVERY_MS = 5_000;
	private static long lastLog;

	private Guard() {
	}

	private static boolean agentInControl() {
		return MineGPTClient.agent().mode() == Agent.Mode.RUNNING;
	}

	public static boolean mayBreak(BlockPos pos) {
		if (!agentInControl()) {
			return true;
		}
		boolean ok = MineGPTClient.safety().mayBreak(Minecraft.getInstance(), pos);
		if (!ok) {
			refused("break", pos);
		}
		return ok;
	}

	public static boolean mayUseOn(LocalPlayer player, InteractionHand hand, BlockHitResult hit) {
		if (!agentInControl()) {
			return true;
		}
		ItemStack stack = player.getItemInHand(hand);
		boolean ok = MineGPTClient.safety().mayUseOn(Minecraft.getInstance(), player, hit, stack);
		if (!ok) {
			refused("use " + stack.getItem() + " on", hit.getBlockPos());
		} else if (!stack.isEmpty()) {
			count();
		}
		return ok;
	}

	/** Counts an action towards the per-minute cap. */
	public static void count() {
		if (agentInControl()) {
			MineGPTClient.safety().recordAction();
		}
	}

	private static void refused(String what, BlockPos pos) {
		long now = System.currentTimeMillis();
		if (now - lastLog > LOG_EVERY_MS) {
			lastLog = now;
			MineGPT.LOGGER.info("Safety: refused to {} {} in a protected region", what, pos.toShortString());
		}
	}
}
