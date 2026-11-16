package com.xzier9.minegpt.client.agent;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.xzier9.minegpt.client.path.ClientBlockView;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;

/**
 * A snapshot of the blocks round the player, taken when a skill fails or stalls, so the brain's
 * failure miner can tell what the player was stuck in and the scene can be rebuilt later to test
 * a fix.
 *
 * The box is 9 by 9 blocks round the player's feet and 6 high, from 2 below the feet to 3 above.
 * Each cell is given twice: as a full block state ({@code blocks}, indexes into {@code palette}),
 * which is enough to put the scene back with {@code /setblock}, and as the pathfinder's view of
 * it ({@code cells}, one digit per cell, the {@code BlockView} codes), which unit tests can load
 * without Minecraft. Both run x fastest, then z, then y.
 */
public final class Scene {
	public static final int HALF = 4;
	public static final int BELOW = 2;
	public static final int HEIGHT = 6;

	private Scene() {
	}

	/** The scene round the player now, or null without a world. */
	public static JsonObject capture(Minecraft mc) {
		LocalPlayer player = mc.player;
		ClientLevel level = mc.level;
		if (player == null || level == null) {
			return null;
		}
		BlockPos feet = BlockPos.containing(player.getX(), player.getY() + 0.2, player.getZ());
		JsonObject out = new JsonObject();
		out.add("feet", ints(feet.getX(), feet.getY(), feet.getZ()));
		JsonArray exact = new JsonArray();
		exact.add(round(player.getX()));
		exact.add(round(player.getY()));
		exact.add(round(player.getZ()));
		out.add("exact", exact);
		out.addProperty("yaw", round(player.getYRot()));
		out.addProperty("pitch", round(player.getXRot()));
		out.addProperty("dimension", level.dimension().identifier().toString());
		out.addProperty("onGround", player.onGround());
		out.addProperty("inWater", player.isInWater());
		out.addProperty("underWater", player.isUnderWater());
		out.addProperty("health", round(player.getHealth()));
		out.addProperty("held", BuiltInRegistries.ITEM.getKey(player.getMainHandItem().getItem()).toString());

		int x0 = feet.getX() - HALF;
		int y0 = feet.getY() - BELOW;
		int z0 = feet.getZ() - HALF;
		int side = 2 * HALF + 1;
		out.add("origin", ints(x0, y0, z0));
		out.add("size", ints(side, HEIGHT, side));
		Map<String, Integer> palette = new LinkedHashMap<>();
		JsonArray blocks = new JsonArray();
		StringBuilder cells = new StringBuilder();
		BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
		for (int y = 0; y < HEIGHT; y++) {
			for (int z = 0; z < side; z++) {
				for (int x = 0; x < side; x++) {
					pos.set(x0 + x, y0 + y, z0 + z);
					BlockState state = level.getBlockState(pos);
					blocks.add(palette.computeIfAbsent(full(state), k -> palette.size()));
					cells.append(ClientBlockView.classify(level, pos, state));
				}
			}
		}
		JsonArray names = new JsonArray();
		palette.keySet().forEach(names::add);
		out.add("palette", names);
		out.add("blocks", blocks);
		out.addProperty("cells", cells.toString());
		return out;
	}

	/** The block with every property, like {@code minecraft:water[level=3]}, as {@code /setblock} takes it. */
	static String full(BlockState state) {
		String id = BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString();
		StringBuilder props = new StringBuilder();
		for (Property<?> property : state.getProperties()) {
			props.append(props.length() > 0 ? "," : "").append(property.getName()).append('=').append(value(state, property));
		}
		return props.length() == 0 ? id : id + "[" + props + "]";
	}

	private static <T extends Comparable<T>> String value(BlockState state, Property<T> property) {
		return property.getName(state.getValue(property)).toLowerCase(Locale.ROOT);
	}

	private static JsonArray ints(int a, int b, int c) {
		JsonArray out = new JsonArray();
		out.add(a);
		out.add(b);
		out.add(c);
		return out;
	}

	private static double round(double v) {
		return Math.round(v * 100) / 100.0;
	}
}
