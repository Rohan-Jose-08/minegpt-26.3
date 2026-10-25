package com.xzier9.minegpt.client;

import com.mojang.blaze3d.platform.InputConstants;
import com.xzier9.minegpt.MineGPT;
import com.xzier9.minegpt.client.agent.Agent;
import com.xzier9.minegpt.client.command.MineGPTCommands;
import com.xzier9.minegpt.client.hud.AgentHud;
import com.xzier9.minegpt.client.record.Recorder;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.keymapping.v1.KeyMappingHelper;
import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.Component;

public class MineGPTClient implements ClientModInitializer {
	private static final KeyMapping.Category CATEGORY = KeyMapping.Category.register(MineGPT.id("main"));

	private static final Agent AGENT = new Agent();
	private static final Recorder RECORDER = new Recorder();

	private static KeyMapping pauseKey;

	public static Agent agent() {
		return AGENT;
	}

	public static Recorder recorder() {
		return RECORDER;
	}

	@Override
	public void onInitializeClient() {
		pauseKey = KeyMappingHelper.registerKeyMapping(
				new KeyMapping("key.minegpt.pause", InputConstants.KEY_F8, CATEGORY));

		// Start of tick: apply the agent's key presses before vanilla reads the bindings.
		ClientTickEvents.START_CLIENT_TICK.register(AGENT::tick);
		ClientTickEvents.END_CLIENT_TICK.register(MineGPTClient::endTick);

		ClientCommandRegistrationCallback.EVENT.register(
				(dispatcher, buildContext) -> MineGPTCommands.register(dispatcher));
		HudElementRegistry.addLast(MineGPT.id("agent_status"), new AgentHud());
	}

	private static void endTick(Minecraft mc) {
		while (pauseKey.consumeClick()) {
			if (AGENT.mode() != Agent.Mode.IDLE) {
				AGENT.togglePause(mc);
				if (mc.player != null) {
					mc.player.sendOverlayMessage(Component.literal(
							AGENT.mode() == Agent.Mode.PAUSED ? "MineGPT paused" : "MineGPT resumed"));
				}
			}
		}
		RECORDER.tick(mc, AGENT.mode() == Agent.Mode.RUNNING);
	}
}
