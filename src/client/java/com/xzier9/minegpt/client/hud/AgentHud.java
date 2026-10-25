package com.xzier9.minegpt.client.hud;

import com.xzier9.minegpt.client.MineGPTClient;
import com.xzier9.minegpt.client.agent.Agent;
import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElement;
import net.minecraft.client.DeltaTracker;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;

/** A small panel in the top-left corner showing what MineGPT is doing. Hidden when idle. */
public final class AgentHud implements HudElement {
	private static final int PADDING = 4;
	private static final int BACKGROUND = 0x90000000;
	private static final int TITLE_RUNNING = 0xFF7CC464;
	private static final int TITLE_PAUSED = 0xFFF0A060;
	private static final int TEXT = 0xFFE0E0E0;
	private static final int RECORDING = 0xFFFF5555;

	@Override
	public void extractRenderState(GuiGraphicsExtractor graphics, DeltaTracker deltaTracker) {
		Minecraft mc = Minecraft.getInstance();
		Agent agent = MineGPTClient.agent();
		boolean recording = MineGPTClient.recorder().isRecording();
		if (agent.mode() == Agent.Mode.IDLE && agent.goal() == null && !recording) {
			return;
		}

		Font font = mc.font;
		String title = "MineGPT: " + switch (agent.mode()) {
			case RUNNING -> "running";
			case PAUSED -> "paused";
			case IDLE -> "idle";
		};
		String goal = "Goal: " + (agent.goal() == null ? "none" : agent.goal());
		String status = agent.status();
		String rec = recording ? "REC " + MineGPTClient.recorder().ticks() / 20 + "s" : null;

		int width = Math.max(font.width(title), Math.max(font.width(goal), font.width(status)));
		if (rec != null) {
			width = Math.max(width, font.width(rec));
		}
		int lines = rec == null ? 3 : 4;
		int x = PADDING;
		int y = PADDING;
		graphics.fill(x - 2, y - 2, x + width + 2, y + lines * font.lineHeight + 1, BACKGROUND);

		graphics.text(font, title, x, y, agent.mode() == Agent.Mode.PAUSED ? TITLE_PAUSED : TITLE_RUNNING);
		graphics.text(font, goal, x, y + font.lineHeight, TEXT);
		graphics.text(font, status, x, y + 2 * font.lineHeight, TEXT);
		if (rec != null) {
			graphics.text(font, rec, x, y + 3 * font.lineHeight, RECORDING);
		}
	}
}
