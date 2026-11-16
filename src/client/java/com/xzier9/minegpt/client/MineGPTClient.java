package com.xzier9.minegpt.client;

import com.mojang.blaze3d.platform.InputConstants;
import com.xzier9.minegpt.MineGPT;
import com.xzier9.minegpt.client.agent.Agent;
import com.xzier9.minegpt.client.bridge.BridgeHandler;
import com.xzier9.minegpt.client.bridge.BridgeServer;
import com.xzier9.minegpt.client.command.MineGPTCommands;
import com.xzier9.minegpt.client.config.MineGPTConfig;
import com.xzier9.minegpt.client.hud.AgentHud;
import com.xzier9.minegpt.client.record.Recorder;
import com.xzier9.minegpt.client.safety.Safety;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.keymapping.v1.KeyMappingHelper;
import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.minecraft.client.KeyMapping;
import java.io.IOException;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.Component;

public class MineGPTClient implements ClientModInitializer {
	private static final KeyMapping.Category CATEGORY = KeyMapping.Category.register(MineGPT.id("main"));

	private static final Agent AGENT = new Agent();
	private static final Recorder RECORDER = new Recorder();
	private static final BridgeServer BRIDGE = new BridgeServer();
	private static final BridgeHandler BRIDGE_HANDLER = new BridgeHandler(BRIDGE, AGENT);

	private static MineGPTConfig config;
	private static Safety safety;

	private static KeyMapping pauseKey;

	public static Agent agent() {
		return AGENT;
	}

	public static Recorder recorder() {
		return RECORDER;
	}

	public static BridgeServer bridge() {
		return BRIDGE;
	}

	public static BridgeHandler bridgeHandler() {
		return BRIDGE_HANDLER;
	}

	public static MineGPTConfig config() {
		return config;
	}

	public static Safety safety() {
		return safety;
	}

	@Override
	public void onInitializeClient() {
		config = MineGPTConfig.load();
		safety = new Safety(config.maxActionsPerMinute, config.stopHealth);
		if (config.bridgeAutostart) {
			try {
				BRIDGE.start(config.bridgePort);
			} catch (IOException e) {
				MineGPT.LOGGER.error("Could not start the bridge on port {}", config.bridgePort, e);
			}
		}

		pauseKey = KeyMappingHelper.registerKeyMapping(
				new KeyMapping("key.minegpt.pause", InputConstants.KEY_F8, CATEGORY));

		// Start of tick: take the brain's actions, then press the keys before vanilla reads them.
		ClientTickEvents.START_CLIENT_TICK.register(mc -> {
			BRIDGE_HANDLER.startTick(mc);
			AGENT.tick(mc);
		});
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
		BRIDGE_HANDLER.endTick(mc);
	}
}
