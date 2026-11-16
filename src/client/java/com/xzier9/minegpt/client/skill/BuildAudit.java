package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.xzier9.minegpt.build.Ledger;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;

/**
 * Compares what stands in the world with what a blueprint says should stand there, and says
 * where they differ. It is how a build is known to be finished: not by counting what was
 * placed, but by looking.
 *
 * Each blueprint entry is {@code [x, y, z, item]} or {@code [x, y, z, item, facing]}, as for the
 * build skill, with the item {@code air} for a spot that must be empty. A spot is
 * <ul>
 * <li>{@code missing} when it is empty or holds something that would be replaced,</li>
 * <li>{@code wrong} when another block stands there,</li>
 * <li>{@code turned} when the right block stands there the wrong way round,</li>
 * <li>{@code blocked} when it should be empty and is not.</li>
 * </ul>
 * With a {@code box}, every other spot in it at or above its floor is looked at too, and
 * reported as {@code extra} when it holds the agent's own scaffolding, filling or a table it
 * put down to work at, or something that is neither natural ground nor known to be its own.
 * With {@code ground} (the item a structure's footing course is made of), a spot that wants that
 * item and holds solid natural ground counts as right, as does any spot holding natural ground
 * closed in on all six sides: the build skill leaves both as they are.
 * Each problem says whether the block there is the agent's own ({@code own}), since those are
 * the ones it may repair.
 */
public final class BuildAudit {
	public static final int MAX_BLOCKS = 8192;
	public static final int MAX_BOX = 80_000;
	private static final int DEFAULT_LIMIT = 40;

	private BuildAudit() {
	}

	public static JsonObject run(Minecraft mc, JsonObject request) {
		JsonArray list = request.getAsJsonArray("blocks");
		if (list == null) {
			throw new IllegalArgumentException("audit: give blocks, a list of [x, y, z, item] or [x, y, z, item, facing]");
		}
		if (list.size() > MAX_BLOCKS) {
			throw new IllegalArgumentException("audit: at most " + MAX_BLOCKS + " blocks at a time");
		}
		int limit = request.has("limit") ? Math.max(1, Math.min(400, request.get("limit").getAsInt())) : DEFAULT_LIMIT;
		ItemMatcher ground = request.has("ground") && !request.get("ground").isJsonNull()
				? ItemMatcher.parse(request.get("ground").getAsString()) : null;
		int kept = 0;
		Map<String, ItemMatcher> items = new HashMap<>();
		Map<String, Integer> counts = new HashMap<>();
		JsonArray problems = new JsonArray();
		Set<BlockPos> listed = new HashSet<>();
		int right = 0;
		int unloaded = 0;
		for (JsonElement element : list) {
			JsonArray a = element.getAsJsonArray();
			BlockPos pos = new BlockPos(a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt());
			String spec = a.get(3).getAsString();
			String facing = a.size() > 4 && !a.get(4).isJsonNull() ? a.get(4).getAsString() : null;
			listed.add(pos);
			if (!mc.level.hasChunkAt(pos)) {
				unloaded++;
				continue;
			}
			BlockState there = mc.level.getBlockState(pos);
			if (spec.equals("air") || spec.equals("minecraft:air")) {
				if (there.canBeReplaced()) {
					right++;
				} else {
					add(problems, counts, limit, "blocked", mc, pos, there, "empty");
				}
				continue;
			}
			ItemMatcher item = items.computeIfAbsent(spec, ItemMatcher::parse);
			if (!(item.item() instanceof BlockItem blockItem)) {
				throw new IllegalArgumentException("audit: " + spec + " is not a block item");
			}
			Block block = blockItem.getBlock();
			PlaceSkill.Want want = PlaceSkill.Want.parse(facing);
			Map<String, String> expected = BlockStates.expected(block, want);
			String wanted = spec + (expected.isEmpty() ? "" : "[" + BlockStates.describe(expected) + "]");
			if (there.canBeReplaced()) {
				add(problems, counts, limit, "missing", mc, pos, there, wanted);
			} else if (!there.is(block)) {
				if (asGround(mc, pos, there, ground != null && item.item() == ground.item())) {
					// What the build skill leaves on purpose: solid ground under a wall, or a spot nobody can see.
					right++;
					kept++;
				} else {
					add(problems, counts, limit, "wrong", mc, pos, there, wanted);
				}
			} else if (BlockStates.mismatch(there, block, want) != null) {
				add(problems, counts, limit, "turned", mc, pos, there, wanted);
			} else {
				right++;
			}
		}
		if (request.has("box")) {
			JsonArray b = request.getAsJsonArray("box");
			if (b.size() != 6) {
				throw new IllegalArgumentException("audit: box is [x1, y1, z1, x2, y2, z2]");
			}
			int[] box = new int[6];
			for (int i = 0; i < 3; i++) {
				box[i] = Math.min(b.get(i).getAsInt(), b.get(i + 3).getAsInt());
				box[i + 3] = Math.max(b.get(i).getAsInt(), b.get(i + 3).getAsInt());
			}
			long cells = (long) (box[3] - box[0] + 1) * (box[4] - box[1] + 1) * (box[5] - box[2] + 1);
			if (cells > MAX_BOX) {
				throw new IllegalArgumentException("audit: box holds " + cells + " cells; at most " + MAX_BOX);
			}
			BlockPos.MutableBlockPos at = new BlockPos.MutableBlockPos();
			for (int y = box[1]; y <= box[4]; y++) {
				for (int x = box[0]; x <= box[3]; x++) {
					for (int z = box[2]; z <= box[5]; z++) {
						at.set(x, y, z);
						if (listed.contains(at) || !mc.level.hasChunkAt(at)) {
							continue;
						}
						BlockState there = mc.level.getBlockState(at);
						if (there.canBeReplaced()) {
							continue;
						}
						Ledger.Entry entry = OwnBlocks.owns(mc, at, there);
						boolean stray = entry != null ? entry.purpose() != Ledger.Purpose.BUILD
								: !Mining.isClearable(mc.level, at, there) && !there.hasBlockEntity()
										&& Scaffold.isScaffold(new net.minecraft.world.item.ItemStack(there.getBlock()));
						if (stray) {
							add(problems, counts, limit, "extra", mc, at.immutable(), there, "empty");
						}
					}
				}
			}
		}
		JsonObject reply = new JsonObject();
		reply.addProperty("checked", list.size() - unloaded);
		reply.addProperty("right", right);
		reply.addProperty("unloaded", unloaded);
		reply.addProperty("ground", kept);
		for (String kind : new String[] {"missing", "wrong", "turned", "blocked", "extra"}) {
			reply.addProperty(kind, counts.getOrDefault(kind, 0));
		}
		reply.add("problems", problems);
		return reply;
	}

	/**
	 * True where solid natural ground stands in for a blueprint block, as the build skill allows:
	 * under a footing course, or anywhere with solid blocks on all six sides.
	 */
	private static boolean asGround(Minecraft mc, BlockPos pos, BlockState there, boolean footing) {
		if (!Mining.isNaturalTerrain(there) || !there.isCollisionShapeFullBlock(mc.level, pos)) {
			return false;
		}
		if (footing) {
			return true;
		}
		for (net.minecraft.core.Direction direction : net.minecraft.core.Direction.values()) {
			BlockPos side = pos.relative(direction);
			if (!mc.level.getBlockState(side).isCollisionShapeFullBlock(mc.level, side)) {
				return false;
			}
		}
		return true;
	}

	private static void add(JsonArray problems, Map<String, Integer> counts, int limit, String kind, Minecraft mc,
			BlockPos pos, BlockState there, String want) {
		counts.merge(kind, 1, Integer::sum);
		if (problems.size() >= limit) {
			return;
		}
		JsonObject p = new JsonObject();
		p.addProperty("kind", kind);
		p.addProperty("x", pos.getX());
		p.addProperty("y", pos.getY());
		p.addProperty("z", pos.getZ());
		p.addProperty("want", want);
		p.addProperty("is", there.isAir() ? "minecraft:air" : BlockStates.describe(there));
		Ledger.Entry entry = there.isAir() ? null : OwnBlocks.owns(mc, pos, there);
		p.addProperty("own", entry != null);
		if (entry != null) {
			p.addProperty("purpose", entry.purpose().name().toLowerCase(java.util.Locale.ROOT));
		}
		problems.add(p);
	}
}
