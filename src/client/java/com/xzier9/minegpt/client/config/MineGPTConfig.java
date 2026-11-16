package com.xzier9.minegpt.client.config;

import com.xzier9.minegpt.MineGPT;
import java.io.IOException;
import java.io.Reader;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;
import net.fabricmc.loader.api.FabricLoader;

/** Settings from {@code config/minegpt.properties}, written with defaults on first launch. */
public final class MineGPTConfig {
	private static final String FILE_NAME = "minegpt.properties";

	/** Start the Python bridge when the game starts, instead of waiting for /minegpt bridge start. */
	public final boolean bridgeAutostart;
	public final int bridgePort;
	/** Let the brain run server commands (for benchmark resets) on a multiplayer server; always allowed in singleplayer. */
	public final boolean bridgeAllowCommands;
	/** Block breaks, placements and attacks the agent may make per minute; 0 for no cap. */
	public final int maxActionsPerMinute;
	/** Health at or below which the agent lets go of the controls and pauses; 0 to never stop. */
	public final float stopHealth;

	private MineGPTConfig(boolean bridgeAutostart, int bridgePort, boolean bridgeAllowCommands, int maxActionsPerMinute,
			float stopHealth) {
		this.bridgeAutostart = bridgeAutostart;
		this.bridgePort = bridgePort;
		this.bridgeAllowCommands = bridgeAllowCommands;
		this.maxActionsPerMinute = maxActionsPerMinute;
		this.stopHealth = stopHealth;
	}

	public static MineGPTConfig load() {
		Path file = FabricLoader.getInstance().getConfigDir().resolve(FILE_NAME);
		Properties props = new Properties();
		props.setProperty("bridge.autostart", "false");
		props.setProperty("bridge.port", "5577");
		props.setProperty("bridge.allowCommands", "false");
		props.setProperty("safety.maxActionsPerMinute", "150");
		props.setProperty("safety.stopHealth", "4");

		boolean write = !Files.exists(file);
		if (!write) {
			Properties saved = new Properties();
			try (Reader reader = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
				saved.load(reader);
			} catch (IOException e) {
				MineGPT.LOGGER.error("Could not read {}, using defaults", file, e);
			}
			// Settings added in a later version are written back, so they show up in the file.
			write = !saved.keySet().containsAll(props.keySet());
			props.putAll(saved);
		}
		if (write) {
			try (Writer writer = Files.newBufferedWriter(file, StandardCharsets.UTF_8)) {
				props.store(writer, "MineGPT settings. bridge.port is the local port the Python brain connects to. "
						+ "safety.maxActionsPerMinute caps block breaks, placements and attacks (0 = no cap); "
						+ "safety.stopHealth pauses the agent at this health or below (0 = never).");
			} catch (IOException e) {
				MineGPT.LOGGER.warn("Could not write {}", file, e);
			}
		}

		int port = (int) number(props, file, "bridge.port", 5577);
		int cap = (int) number(props, file, "safety.maxActionsPerMinute", 150);
		float stopHealth = (float) number(props, file, "safety.stopHealth", 4);
		return new MineGPTConfig(Boolean.parseBoolean(props.getProperty("bridge.autostart").trim()), port,
				Boolean.parseBoolean(props.getProperty("bridge.allowCommands").trim()), Math.max(0, cap),
				Math.max(0, stopHealth));
	}

	private static double number(Properties props, Path file, String key, double fallback) {
		try {
			return Double.parseDouble(props.getProperty(key).trim());
		} catch (NumberFormatException e) {
			MineGPT.LOGGER.error("{} in {} is not a number, using {}", key, file, fallback);
			return fallback;
		}
	}
}
