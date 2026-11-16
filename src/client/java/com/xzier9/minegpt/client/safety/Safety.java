package com.xzier9.minegpt.client.safety;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;
import com.google.gson.reflect.TypeToken;
import com.xzier9.minegpt.MineGPT;
import com.xzier9.minegpt.client.observe.Observer;
import java.io.IOException;
import java.io.Reader;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.tags.BlockTags;
import net.minecraft.tags.ItemTags;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.BucketItem;
import net.minecraft.world.item.FireChargeItem;
import net.minecraft.world.item.FlintAndSteelItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.ButtonBlock;
import net.minecraft.world.level.block.DoorBlock;
import net.minecraft.world.level.block.FenceGateBlock;
import net.minecraft.world.level.block.LeverBlock;
import net.minecraft.world.level.block.TrapDoorBlock;
import net.minecraft.world.level.block.CropBlock;
import net.minecraft.world.level.block.state.BlockState;

/**
 * The agent's safety rails, enforced in the mod so they hold whatever the brain asks for:
 * protected regions it may not break or build in, a cap on block and attack actions per
 * minute, and the health at which it lets go of the controls.
 *
 * Regions are columns (every height) set by the player with {@code /minegpt protect}, stored
 * per world in {@code config/minegpt/protected.json}. They only bind the agent: the player
 * playing by hand is never blocked.
 */
public final class Safety {
	/** A protected column of blocks. {@code farm} lets the agent harvest and replant crops inside. */
	public record Region(String name, String dimension, int minX, int minZ, int maxX, int maxZ, boolean farm) {
		public boolean contains(String dim, int x, int z) {
			return dimension.equals(dim) && x >= minX && x <= maxX && z >= minZ && z <= maxZ;
		}

		public JsonObject toJson() {
			JsonObject json = new JsonObject();
			json.addProperty("name", name);
			json.addProperty("dimension", dimension);
			json.addProperty("minX", minX);
			json.addProperty("minZ", minZ);
			json.addProperty("maxX", maxX);
			json.addProperty("maxZ", maxZ);
			json.addProperty("farm", farm);
			return json;
		}

		public String describe() {
			return name + ": x " + minX + " to " + maxX + ", z " + minZ + " to " + maxZ
					+ (farm ? " (farming allowed)" : "");
		}
	}

	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
	private static final long MINUTE_MS = 60_000;

	private final Path file = FabricLoader.getInstance().getConfigDir().resolve("minegpt").resolve("protected.json");
	/** Regions by world id, as {@link Observer#worldId} names worlds. */
	private Map<String, List<Region>> regions = new LinkedHashMap<>();
	private final Deque<Long> actions = new ArrayDeque<>();

	private final int maxActionsPerMinute;
	private final float stopHealth;

	public Safety(int maxActionsPerMinute, float stopHealth) {
		this.maxActionsPerMinute = maxActionsPerMinute;
		this.stopHealth = stopHealth;
		load();
	}

	public int maxActionsPerMinute() {
		return maxActionsPerMinute;
	}

	public float stopHealth() {
		return stopHealth;
	}

	// Regions -----------------------------------------------------------------------------

	public List<Region> regions(Minecraft mc) {
		return regions.getOrDefault(Observer.worldId(mc), List.of());
	}

	/** The region covering this block in the player's dimension, or null. */
	public Region regionAt(Minecraft mc, BlockPos pos) {
		if (mc.level == null) {
			return null;
		}
		String dimension = mc.level.dimension().identifier().toString();
		for (Region region : regions(mc)) {
			if (region.contains(dimension, pos.getX(), pos.getZ())) {
				return region;
			}
		}
		return null;
	}

	/** True if the agent must not break or place at this block. */
	public boolean isProtected(Minecraft mc, BlockPos pos) {
		return regionAt(mc, pos) != null;
	}

	/** True if the agent may break this block: outside every region, or a ripe crop in a farm region. */
	public boolean mayBreak(Minecraft mc, BlockPos pos) {
		Region region = regionAt(mc, pos);
		if (region == null) {
			return true;
		}
		BlockState state = mc.level.getBlockState(pos);
		return region.farm() && state.getBlock() instanceof CropBlock crop && crop.isMaxAge(state);
	}

	/** True if the agent may place {@code item} at {@code pos}: outside every region, or a crop on farmland in a farm region. */
	public boolean mayPlace(Minecraft mc, BlockPos pos, Item item) {
		Region region = regionAt(mc, pos);
		if (region == null) {
			return true;
		}
		return region.farm() && item instanceof BlockItem block && block.getBlock() instanceof CropBlock
				&& mc.level.getBlockState(pos.below()).is(Blocks.FARMLAND);
	}

	/**
	 * True if the agent may right-click a block with {@code stack}. Placing a block is checked
	 * where the block would go; tilling, buckets and fire where the click lands. Anything else,
	 * such as opening a chest, a door or a bed, is allowed everywhere.
	 */
	public boolean mayUseOn(Minecraft mc, Player player, BlockHitResult hit, ItemStack stack) {
		BlockPos clicked = hit.getBlockPos();
		BlockState state = mc.level.getBlockState(clicked);
		if (!player.isSecondaryUseActive() && opensOnClick(state)) {
			// The block reacts to the click itself (a chest opens, a door swings), so nothing is placed.
			return true;
		}
		if (stack.getItem() instanceof BlockItem) {
			BlockPos target = state.canBeReplaced() ? clicked : clicked.relative(hit.getDirection());
			return mayPlace(mc, target, stack.getItem());
		}
		Region region = regionAt(mc, clicked);
		if (region == null) {
			return true;
		}
		if (stack.is(ItemTags.HOES)) {
			return region.farm() && (state.is(BlockTags.SUBSTRATE_OVERWORLD) || state.is(BlockTags.TURNS_INTO_DIRT_PATH));
		}
		return !(stack.getItem() instanceof BucketItem || stack.getItem() instanceof FlintAndSteelItem
				|| stack.getItem() instanceof FireChargeItem);
	}

	private static boolean opensOnClick(BlockState state) {
		return state.hasBlockEntity() || state.getBlock() instanceof DoorBlock || state.getBlock() instanceof TrapDoorBlock
				|| state.getBlock() instanceof FenceGateBlock || state.getBlock() instanceof ButtonBlock
				|| state.getBlock() instanceof LeverBlock || state.is(Blocks.CRAFTING_TABLE);
	}

	/** Adds a region, replacing one of the same name. */
	public Region protect(Minecraft mc, String name, int x1, int z1, int x2, int z2, boolean farm) {
		Region region = new Region(name, mc.level.dimension().identifier().toString(), Math.min(x1, x2), Math.min(z1, z2),
				Math.max(x1, x2), Math.max(z1, z2), farm);
		List<Region> list = regions.computeIfAbsent(Observer.worldId(mc), k -> new ArrayList<>());
		list.removeIf(r -> r.name().equals(name));
		list.add(region);
		save();
		return region;
	}

	public boolean unprotect(Minecraft mc, String name) {
		List<Region> list = regions.get(Observer.worldId(mc));
		boolean removed = list != null && list.removeIf(r -> r.name().equals(name));
		if (removed) {
			save();
		}
		return removed;
	}

	/** Sets whether the agent may harvest and replant crops inside a region. False if there is no such region. */
	public boolean setFarm(Minecraft mc, String name, boolean farm) {
		List<Region> list = regions.get(Observer.worldId(mc));
		if (list == null) {
			return false;
		}
		for (int i = 0; i < list.size(); i++) {
			Region r = list.get(i);
			if (r.name().equals(name)) {
				list.set(i, new Region(r.name(), r.dimension(), r.minX(), r.minZ(), r.maxX(), r.maxZ(), farm));
				save();
				return true;
			}
		}
		return false;
	}

	public JsonObject toJson(Minecraft mc) {
		JsonObject json = new JsonObject();
		JsonArray list = new JsonArray();
		if (mc.level != null) {
			regions(mc).forEach(r -> list.add(r.toJson()));
		}
		json.add("regions", list);
		json.addProperty("maxActionsPerMinute", maxActionsPerMinute);
		json.addProperty("stopHealth", stopHealth);
		return json;
	}

	// Action cap --------------------------------------------------------------------------

	/** Counts a block break, placement or attack the agent made. */
	public void recordAction() {
		actions.addLast(System.currentTimeMillis());
	}

	/** True while the agent has used up its actions for the last minute and must wait. */
	public boolean throttled() {
		if (maxActionsPerMinute <= 0) {
			return false;
		}
		long cutoff = System.currentTimeMillis() - MINUTE_MS;
		while (!actions.isEmpty() && actions.peekFirst() < cutoff) {
			actions.removeFirst();
		}
		return actions.size() >= maxActionsPerMinute;
	}

	public int actionsLastMinute() {
		throttled();
		return actions.size();
	}

	// Storage -----------------------------------------------------------------------------

	private void load() {
		if (!Files.exists(file)) {
			return;
		}
		try (Reader reader = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
			Map<String, List<Region>> loaded = GSON.fromJson(reader, new TypeToken<LinkedHashMap<String, List<Region>>>() {
			}.getType());
			if (loaded != null) {
				loaded.replaceAll((world, list) -> new ArrayList<>(list));
				regions = loaded;
			}
		} catch (IOException | JsonParseException e) {
			MineGPT.LOGGER.error("Could not read protected regions from {}", file, e);
		}
	}

	private void save() {
		try {
			Files.createDirectories(file.getParent());
			try (Writer writer = Files.newBufferedWriter(file, StandardCharsets.UTF_8)) {
				GSON.toJson(regions, writer);
			}
		} catch (IOException e) {
			MineGPT.LOGGER.error("Could not save protected regions to {}", file, e);
		}
	}
}
