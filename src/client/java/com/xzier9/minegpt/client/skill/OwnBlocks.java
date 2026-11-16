package com.xzier9.minegpt.client.skill;

import com.xzier9.minegpt.MineGPT;
import com.xzier9.minegpt.build.Ledger;
import com.xzier9.minegpt.client.observe.Observer;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.block.state.BlockState;

/**
 * The {@link Ledger} of blocks the agent placed, one per world and dimension, kept on disk
 * under {@code config/minegpt/ledger/} so that it outlives the game session. Skills note what
 * they place here and ask it before digging something that is not natural terrain.
 *
 * Only touched on the client thread.
 */
public final class OwnBlocks {
	private static final Map<String, Ledger> LEDGERS = new HashMap<>();

	private OwnBlocks() {
	}

	private static String key(Minecraft mc) {
		String dimension = mc.level == null ? "unknown" : mc.level.dimension().identifier().getPath();
		return (Observer.worldId(mc) + "-" + dimension).replaceAll("[^A-Za-z0-9._-]", "_");
	}

	private static Path file(String key) {
		return FabricLoader.getInstance().getConfigDir().resolve("minegpt").resolve("ledger").resolve(key + ".txt");
	}

	/** The ledger for the world the player is in, read from disk the first time it is asked for. */
	public static Ledger ledger(Minecraft mc) {
		return LEDGERS.computeIfAbsent(key(mc), key -> {
			Path file = file(key);
			if (Files.isRegularFile(file)) {
				try {
					List<String> lines = Files.readAllLines(file, StandardCharsets.UTF_8);
					return Ledger.parse(lines, Ledger.DEFAULT_LIMIT);
				} catch (IOException | RuntimeException e) {
					MineGPT.LOGGER.warn("Could not read the ledger of placed blocks {}", file, e);
				}
			}
			return new Ledger();
		});
	}

	/** Notes a block the agent has just placed, as it now stands in the world. */
	public static void record(SkillContext ctx, BlockPos pos, Ledger.Purpose purpose) {
		BlockState state = ctx.level.getBlockState(pos);
		if (state.isAir()) {
			return;
		}
		ledger(ctx.mc).record(pos.getX(), pos.getY(), pos.getZ(), purpose, id(state));
	}

	/** The ledger entry for a spot if the agent's own block still stands there, else null. */
	public static Ledger.Entry owns(Minecraft mc, BlockPos pos, BlockState there) {
		return ledger(mc).owns(pos.getX(), pos.getY(), pos.getZ(), id(there));
	}

	/** True if what stands here is the agent's own scaffolding or filling: always its to take down. */
	public static boolean temporary(Minecraft mc, BlockPos pos, BlockState there) {
		Ledger.Entry entry = owns(mc, pos, there);
		return entry != null && entry.purpose().temporary();
	}

	private static long lastSave;

	/** Saves if anything changed and the last save was a while ago. Called whenever a skill ends. */
	public static void saveSoon() {
		long now = System.nanoTime();
		if (now - lastSave > 15_000_000_000L) {
			save();
		}
	}

	/** Writes every ledger that changed; cheap when nothing changed. */
	public static void save() {
		lastSave = System.nanoTime();
		LEDGERS.forEach((key, ledger) -> {
			if (!ledger.dirty()) {
				return;
			}
			Path file = file(key);
			try {
				Files.createDirectories(file.getParent());
				Path temp = file.resolveSibling(file.getFileName() + ".tmp");
				Files.write(temp, ledger.lines(), StandardCharsets.UTF_8);
				Files.move(temp, file, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
			} catch (IOException e) {
				MineGPT.LOGGER.warn("Could not save the ledger of placed blocks {}", file, e);
			}
		});
	}

	static String id(BlockState state) {
		return BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString();
	}
}
