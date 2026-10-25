package com.xzier9.minegpt.client.record;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.xzier9.minegpt.MineGPT;
import com.xzier9.minegpt.client.observe.Observer;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.zip.GZIPOutputStream;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Options;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.inventory.ContainerInput;

/**
 * Writes one JSON line per tick with the observation and the inputs that were pressed,
 * so gameplay (yours or the agent's) can be used later for imitation learning.
 *
 * Files go to {@code <game dir>/minegpt/recordings/<timestamp>.jsonl.gz}.
 */
public final class Recorder {
	/** The block grid is large, so it is only written every this many ticks. */
	public static final int GRID_EVERY_TICKS = 20;

	private static final Gson GSON = new Gson();
	private static final DateTimeFormatter FILE_NAME = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss");

	private BufferedWriter out;
	private Path file;
	private long ticks;
	private float lastYaw;
	private float lastPitch;

	public boolean isRecording() {
		return out != null;
	}

	public Path file() {
		return file;
	}

	public long ticks() {
		return ticks;
	}

	public Path start(Minecraft mc) throws IOException {
		if (out != null) {
			return file;
		}
		Path dir = mc.gameDirectory.toPath().resolve("minegpt").resolve("recordings");
		Files.createDirectories(dir);
		file = dir.resolve(LocalDateTime.now().format(FILE_NAME) + ".jsonl.gz");
		out = new BufferedWriter(new OutputStreamWriter(
				new GZIPOutputStream(Files.newOutputStream(file)), StandardCharsets.UTF_8));
		ticks = 0;
		if (mc.player != null) {
			lastYaw = mc.player.getYRot();
			lastPitch = mc.player.getXRot();
		}
		MineGPT.LOGGER.info("Recording to {}", file);
		return file;
	}

	public void stop() {
		if (out == null) {
			return;
		}
		try {
			out.close();
		} catch (IOException e) {
			MineGPT.LOGGER.error("Could not finish recording {}", file, e);
		}
		MineGPT.LOGGER.info("Recorded {} ticks to {}", ticks, file);
		out = null;
	}

	/** Call at the end of every client tick. */
	public void tick(Minecraft mc, boolean agentInControl) {
		LocalPlayer player = mc.player;
		if (out == null) {
			return;
		}
		if (player == null) {
			stop();
			return;
		}

		JsonObject line = new JsonObject();
		line.addProperty("t", ticks);
		line.addProperty("agent", agentInControl);
		line.add("input", input(mc.options, player));
		line.add("obs", Observer.observe(mc, ticks % GRID_EVERY_TICKS == 0));
		write(line);
		ticks++;
	}

	/** Records an inventory click, which does not show up in key states. */
	public void onContainerInput(int containerId, int slot, int button, ContainerInput mode) {
		if (out == null) {
			return;
		}
		JsonObject line = new JsonObject();
		line.addProperty("t", ticks);
		line.addProperty("event", "click");
		line.addProperty("containerId", containerId);
		line.addProperty("slot", slot);
		line.addProperty("button", button);
		line.addProperty("mode", mode.name());
		write(line);
	}

	private JsonObject input(Options options, LocalPlayer player) {
		JsonObject in = new JsonObject();
		in.addProperty("forward", options.keyUp.isDown());
		in.addProperty("back", options.keyDown.isDown());
		in.addProperty("left", options.keyLeft.isDown());
		in.addProperty("right", options.keyRight.isDown());
		in.addProperty("jump", options.keyJump.isDown());
		in.addProperty("sneak", options.keyShift.isDown());
		in.addProperty("sprint", options.keySprint.isDown());
		in.addProperty("attack", options.keyAttack.isDown());
		in.addProperty("use", options.keyUse.isDown());
		in.addProperty("hotbar", player.getInventory().getSelectedSlot());

		float yaw = player.getYRot();
		float pitch = player.getXRot();
		in.addProperty("dYaw", yaw - lastYaw);
		in.addProperty("dPitch", pitch - lastPitch);
		lastYaw = yaw;
		lastPitch = pitch;
		return in;
	}

	private void write(JsonObject line) {
		try {
			out.write(GSON.toJson(line));
			out.newLine();
		} catch (IOException e) {
			MineGPT.LOGGER.error("Recording failed, stopping", e);
			stop();
		}
	}
}
