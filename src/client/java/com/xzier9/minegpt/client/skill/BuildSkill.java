package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.xzier9.minegpt.MineGPT;
import com.xzier9.minegpt.build.Ledger;
import com.xzier9.minegpt.client.MineGPTClient;
import com.xzier9.minegpt.client.path.ClientBlockView;
import com.xzier9.minegpt.path.Enclosure;
import com.xzier9.minegpt.path.Goal;
import com.xzier9.minegpt.path.Pathfinder;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

/**
 * Builds a structure from a list of blocks: walks to each spot and places the block there,
 * bottom layer first and nearest first within a layer, so each block has something under or
 * beside it to be placed against. Every block that can be placed (or dug) from where the player
 * already stands goes first, so it walks only when nothing is left in reach. Blocks already in place are skipped. Natural terrain in the
 * way (grass, dirt, stone) is dug out first when {@code clear} is set; anything else in the way
 * is left alone and reported. A block that fails once is tried again at the end, since a
 * neighbour placed later may give it something to stand on. When nothing left has anything to be
 * placed against, as over a hole or down a slope, spare dirt or stone is put under it down to
 * the ground (unless {@code fill} is off).
 *
 * With a {@code foundation} item the lowest layer is carried down to the ground in that block
 * wherever the ground falls away under it, so a house on a slope stands on a stone footing
 * instead of hanging over the hillside or resting on odd bits of dirt.
 *
 * A block can carry a facing (see {@link PlaceSkill.Want}), and the item {@code air} marks a
 * spot to dig out and leave empty, such as the inside of a house on a slope. Dirt and stone that
 * the player pillared or bridged with to reach high spots is taken down again at the end
 * (unless {@code tidy} is off). With {@code tidyOnly} and a {@code site} and no blocks, it only
 * takes down the agent's own scaffold left in the site: a big build does that once, at the end.
 *
 * {@code clearBox} names a box to clear before anything is placed: the site of a new build. In
 * it the skill fells trees (the whole trunk, also above the box), picks flowers and mushrooms,
 * and digs away ground that stands higher than the box's floor, top down. Only natural things
 * are touched; anything built is left standing.
 *
 * Three things keep a build from getting stuck. A block that has to be dug but cannot be seen,
 * because ground or leaves cover it, has what covers it dug first; if built blocks hide it, the
 * player walks to a spot on the ground from which a face of it is in sight. A footing block whose spot
 * still holds solid ground that cannot be got at is counted as done: the ground does the job.
 * And before a block is placed that would shut the player inside the build with no door yet,
 * the player walks out and places it from outside.
 *
 * A block counts as done only when it is the right block turned the right way (see
 * {@link BlockStates}). One the agent placed itself that came out turned wrong is broken and
 * placed again, once; if it is still wrong, or was not the agent's to break, it is reported as
 * turned wrong, never as done.
 *
 * The skill knows its own blocks from the ledger ({@link OwnBlocks}), in this call and from
 * earlier ones. Those it may always dig when they are in the way of what the blueprint wants,
 * which is how a pillar left inside a room by an earlier call comes down. With a {@code site}
 * (the box of the whole structure this call is part of) it also takes down, at the end, every
 * pillar, bridge and filler block of its own that stands in that box at walking level or above
 * and is not part of the build. {@code reserve} names items, with counts, that later calls for
 * the same structure still need, so they are not spent on scaffolding.
 */
public final class BuildSkill extends Skill {
	public static final int MAX_BLOCKS = 2048;
	private static final int TRIES = 2;
	private static final double REACH = 4.0;
	private static final int REPORT_LIMIT = 12;
	/** How far down a hole is filled to give a block something to stand on. */
	private static final int FILL_DEPTH = 4;
	private static final int MAX_FILL = 64;
	/** How far below the lowest layer a foundation is carried down, unless told otherwise. */
	private static final int FOUNDATION_DEPTH = 5;
	/** Most cells a clear box may hold. */
	private static final int MAX_CLEAR_BOX = 24_000;
	/** Most logs of one tree followed up out of the clear box. */
	private static final int MAX_TRUNK = 64;
	/** How far from the eye a block may be to count as within reach from where the player stands. */
	private static final double HERE = 4.4;
	/** Times the player walks out of the build to avoid shutting itself in. */
	private static final int MAX_WALK_OUTS = 8;
	/** Cells the way-out check looks at. */
	private static final int LEAVE_BUDGET = 1500;

	private enum Phase {
		NEXT, APPROACH, CLEAR, PLACE, LEAVE
	}

	private static final class Target {
		final BlockPos pos;
		final ItemMatcher item;
		final Block block;
		/** Put in to hold up part of the build, not part of it. */
		final boolean filler;
		/** Which way it should face. */
		final PlaceSkill.Want want;
		/** Scaffolding to take down again; failing to is not reported. */
		boolean tidy;
		/** Dug only to get at a block behind or under it; failing to is not reported. */
		boolean uncover;
		int tries;
		/** Times this block was hidden and had its cover dug. */
		int hidden;
		/** Walks to a better view tried for it. */
		int moves;
		/** Places to stand already tried for a view of it. */
		final Set<BlockPos> views = new HashSet<>();
		/** Times it stood there turned wrong and was broken to be placed again. */
		int turns;

		/** A spot to dig out and leave empty. */
		static Target empty(BlockPos pos) {
			return new Target(pos, null, null, PlaceSkill.Want.ANY, false);
		}

		boolean clearOnly() {
			return item == null;
		}

		Target(BlockPos pos, ItemMatcher item, Block block, PlaceSkill.Want want) {
			this(pos, item, block, want, false);
		}

		Target(BlockPos pos, ItemMatcher item, Block block, PlaceSkill.Want want, boolean filler) {
			this.pos = pos;
			this.item = item;
			this.block = block;
			this.want = want;
			this.filler = filler;
		}
	}

	private final List<Target> todo;
	private final boolean clear;
	private final boolean partial;
	private final boolean fill;
	private final boolean tidy;
	/** What to carry the lowest layer down to the ground with, or null. */
	private final ItemMatcher foundation;
	private final int foundationDepth;
	private int footings;
	/** Where goto put pillar and bridge blocks during this build. */
	private final List<BlockPos> scaffold = new ArrayList<>();
	/** Every spot the structure itself covers. */
	private final Set<BlockPos> ownSpots = new HashSet<>();
	/** The block each spot of the structure should end up holding. */
	private final Map<BlockPos, Block> wanted = new HashMap<>();
	/** The site to clear first, as min x y z, max x y z, or null. */
	private final int[] clearBox;
	/** The box of the whole structure this call belongs to, as min x y z, max x y z, or null. */
	private final int[] site;
	/** Items later calls for the same structure still need, on top of this call's own. */
	private final Map<net.minecraft.world.item.Item, Integer> reserve;
	/** Sites of recent builds, newest last: nothing is put down to work at inside one. */
	private static final ArrayDeque<int[]> SITES = new ArrayDeque<>();
	/** Blocks found turned wrong and put right. */
	private int turned;
	/** Footing spots left as the solid ground they already were. */
	private int kept;
	private int walkOuts;
	/** The block being dug out of the way is a log of a standing tree. */
	private boolean treeLog;
	/** The world, kept from the last tick for checks made while reporting a failure. */
	private net.minecraft.client.multiplayer.ClientLevel level;
	private int tidyRounds;
	private int tidied;
	private Phase phase = Phase.NEXT;
	private Target current;
	private boolean checked;
	private int placed;
	private int present;
	private int cleared;
	private int filled;
	/** Why each block that could not be placed failed, by position (a block retried and failed again counts once). */
	private final Map<BlockPos, String> failures = new LinkedHashMap<>();
	/** Cells where a filler block could not be placed: never tried again, or the build loops on them. */
	private final Set<BlockPos> badFillers = new HashSet<>();
	/** The build's extent as min x, min z, max x, max z; worked out when first needed. */
	private int[] bounds;
	private final Map<String, Integer> missing = new LinkedHashMap<>();

	private BuildSkill(List<Target> blocks, boolean clear, boolean partial, boolean fill, boolean tidy,
			ItemMatcher foundation, int foundationDepth, int[] clearBox, int[] site,
			Map<net.minecraft.world.item.Item, Integer> reserve, int maxTicks) {
		super("build", maxTicks);
		this.clearBox = clearBox;
		this.site = site;
		this.reserve = reserve;
		this.foundation = foundation;
		this.foundationDepth = foundationDepth;
		this.todo = new ArrayList<>(blocks);
		this.clear = clear;
		this.partial = partial;
		this.fill = fill;
		this.tidy = tidy;
		for (Target t : blocks) {
			if (!t.clearOnly()) {
				ownSpots.add(t.pos);
				wanted.put(t.pos, t.block);
			}
		}
	}

	/** True if the spot lies in the box of a structure built lately: no place for a crafting table. */
	static boolean inSite(BlockPos pos) {
		for (int[] box : SITES) {
			if (pos.getX() >= box[0] && pos.getX() <= box[3] && pos.getY() >= box[1] - 1 && pos.getY() <= box[4]
					&& pos.getZ() >= box[2] && pos.getZ() <= box[5]) {
				return true;
			}
		}
		return false;
	}

	static BuildSkill fromArgs(Args args) {
		int[] clearBox = box(args, "clearBox", MAX_CLEAR_BOX);
		int[] site = box(args, "site", 400_000);
		Map<net.minecraft.world.item.Item, Integer> reserve = new HashMap<>();
		if (args.has("reserve")) {
			for (Map.Entry<String, JsonElement> e : args.object("reserve").entrySet()) {
				reserve.merge(ItemMatcher.parse(e.getKey()).item(), e.getValue().getAsInt(), Integer::sum);
			}
		}
		JsonArray list = (clearBox != null || args.bool("tidyOnly", false)) && !args.has("blocks") ? new JsonArray()
				: args.array("blocks");
		boolean tidyOnly = args.bool("tidyOnly", false);
		if (tidyOnly && site == null) {
			throw args.bad("tidyOnly needs the site whose scaffold to take down");
		}
		if (list.isEmpty() && clearBox == null && !tidyOnly) {
			throw args.bad("blocks must not be empty");
		}
		if (list.size() > MAX_BLOCKS) {
			throw args.bad("at most " + MAX_BLOCKS + " blocks at a time; split the build up");
		}
		Map<String, ItemMatcher> items = new HashMap<>();
		Map<BlockPos, Target> targets = new LinkedHashMap<>();
		for (JsonElement element : list) {
			BlockPos pos;
			String spec;
			String facing = null;
			if (element.isJsonArray() && (element.getAsJsonArray().size() == 4 || element.getAsJsonArray().size() == 5)) {
				JsonArray a = element.getAsJsonArray();
				pos = new BlockPos(a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt());
				spec = a.get(3).getAsString();
				facing = a.size() == 5 && !a.get(4).isJsonNull() ? a.get(4).getAsString() : null;
			} else if (element.isJsonObject()) {
				JsonObject o = element.getAsJsonObject();
				pos = new BlockPos(o.get("x").getAsInt(), o.get("y").getAsInt(), o.get("z").getAsInt());
				spec = o.get("item").getAsString();
				facing = o.has("facing") && !o.get("facing").isJsonNull() ? o.get("facing").getAsString() : null;
			} else {
				throw args.bad("each block is [x, y, z, \"item\"], [x, y, z, \"item\", \"facing\"] or "
						+ "{\"x\", \"y\", \"z\", \"item\", \"facing\"}");
			}
			PlaceSkill.Want want;
			try {
				want = PlaceSkill.Want.parse(facing);
			} catch (IllegalArgumentException e) {
				throw args.bad(e.getMessage());
			}
			if (spec.equals("air") || spec.equals("minecraft:air")) {
				targets.put(pos, Target.empty(pos));
				continue;
			}
			ItemMatcher item = items.computeIfAbsent(spec, ItemMatcher::parse);
			if (!(item.item() instanceof BlockItem blockItem)) {
				throw args.bad(spec + " is not a block item (tags are not allowed either)");
			}
			// A later entry for the same spot wins, as in a schematic.
			targets.put(pos, new Target(pos, item, blockItem.getBlock(), want));
		}
		ItemMatcher foundation = null;
		if (args.has("foundation")) {
			foundation = ItemMatcher.parse(args.string("foundation"));
			if (!(foundation.item() instanceof BlockItem)) {
				throw args.bad("foundation " + args.string("foundation") + " is not a block item");
			}
		}
		int depth = Math.max(1, Math.min(8, args.integer("foundationDepth", FOUNDATION_DEPTH)));
		// Footings are extra blocks: allow for a few under every column.
		int timeout = args.ticks("timeout", 60 + 12 * targets.size() * (foundation == null ? 1 : 2)
				+ (clearBox == null ? 0 : 300) + (tidyOnly ? 600 : 0));
		return new BuildSkill(new ArrayList<>(targets.values()), args.bool("clear", false), args.bool("partial", false),
				args.bool("fill", true), tidyOnly || args.bool("tidy", true), foundation, depth, clearBox, site, reserve, timeout);
	}

	/** A box argument as min x y z, max x y z, or null when there is none. */
	private static int[] box(Args args, String name, int maxCells) {
		if (!args.has(name)) {
			return null;
		}
		JsonArray a = args.array(name);
		if (a.size() != 6) {
			throw args.bad(name + " is [x1, y1, z1, x2, y2, z2]");
		}
		int[] box = new int[6];
		for (int i = 0; i < 3; i++) {
			int one = a.get(i).getAsInt();
			int two = a.get(i + 3).getAsInt();
			box[i] = Math.min(one, two);
			box[i + 3] = Math.max(one, two);
		}
		long cells = (long) (box[3] - box[0] + 1) * (box[4] - box[1] + 1) * (box[5] - box[2] + 1);
		if (cells > maxCells) {
			throw args.bad(name + " holds " + cells + " cells; at most " + maxCells);
		}
		return box;
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		level = ctx.level;
		if (!checked) {
			checked = true;
			if (tidy) {
				GotoSkill.placedListener = scaffold::add;
			}
			if (site != null) {
				SITES.removeIf(box -> java.util.Arrays.equals(box, site));
				SITES.add(site);
				while (SITES.size() > 8) {
					SITES.poll();
				}
			}
			holdBack(ctx);
			addSiteClearing(ctx);
			addFootings(ctx);
			SkillResult shortage = checkMaterials(ctx);
			if (shortage != null) {
				return shortage;
			}
		}
		SkillResult child = childResult();
		if (current != null && child != null) {
			switch (phase) {
				case APPROACH -> {
					if (!child.success()) {
						fail(current, "could not get to it: " + child.message());
					} else {
						phase = Phase.CLEAR;
						startChild(new BreakBlockSkill(current.pos));
						return null;
					}
				}
				case CLEAR -> {
					if (child.success()) {
						OwnBlocks.ledger(ctx.mc).forget(current.pos.getX(), current.pos.getY(), current.pos.getZ());
						if (treeLog) {
							fellRest(ctx, current.pos);
						}
					}
					treeLog = false;
					if (!child.success()) {
						if (isHidden(child.message()) && uncover(ctx, current)) {
							if (hasChild()) {
								// Walking round to where it can be seen.
								return null;
							}
							// What covers it is queued to be dug; this one waits its turn again.
						} else {
							fail(current, "could not clear the way: " + child.message());
						}
					} else if (current.clearOnly()) {
						if (current.tidy) {
							tidied++;
						} else {
							cleared++;
						}
					} else {
						cleared++;
						startPlacing(ctx, current);
						return null;
					}
				}
				case LEAVE -> {
					// Outside now, or as far out as there was a way: place it either way.
					phase = Phase.PLACE;
					startChild(placer(current));
					return null;
				}
				case PLACE -> {
					if (child.success() && current.filler) {
						filled++;
					} else if (child.success()) {
						placed++;
						if (current.turns > 0) {
							turned++;
						}
					} else if (++current.tries < TRIES && Inventories.find(ctx.player.getInventory(), current.item) >= 0) {
						todo.add(current);
					} else if (current.filler) {
						badFillers.add(current.pos);
						MineGPT.LOGGER.debug("Build: filler at {} failed: {}", current.pos.toShortString(), child.message());
					} else {
						fail(current, child.message());
					}
				}
				default -> {
				}
			}
			current = null;
			phase = Phase.NEXT;
		}
		if (current != null) {
			return null;
		}

		Target next = choose(ctx);
		if (next == null) {
			return finish();
		}
		current = next;
		BlockState there = ctx.level.getBlockState(next.pos);
		if (!next.clearOnly() && !MineGPTClient.safety().mayPlace(ctx.mc, next.pos, next.item.item())) {
			fail(next, "it is in a protected region");
			current = null;
			return null;
		}
		if (!next.clearOnly() && Inventories.find(ctx.player.getInventory(), next.item) < 0) {
			missing.merge(next.item.toString(), 1, Integer::sum);
			fail(next, "no " + next.item + " left");
			current = null;
			return null;
		}
		if (!there.canBeReplaced() || (next.clearOnly() && needsClearing(ctx, next.pos, there))) {
			// Spots marked empty are dug out whatever clear says; scaffolding is the player's own to take down.
			// So is anything else the agent put there itself that the blueprint does not want there: a
			// pillar from an earlier call, a crafting table, one of its own blocks turned the wrong way.
			boolean own = OwnBlocks.owns(ctx.mc, next.pos, there) != null;
			// A torch or flower right where the plan wants a block is knocked off too, whoever put it there.
			boolean mayDig = next.tidy || own
					|| (Mining.isClearable(ctx.level, next.pos, there) && (clear || next.clearOnly()))
					|| (clear && !next.clearOnly() && Mining.isSoft(ctx.level, next.pos, there));
			if (!mayDig || there.hasBlockEntity() || Mining.touchesLava(ctx.level, next.pos)
					|| !MineGPTClient.safety().mayBreak(ctx.mc, next.pos)) {
				fail(next, BreakBlockSkill.blockName(there) + " is in the way"
						+ (own ? "" : there.hasBlockEntity() ? "" : " (not placed by MineGPT, so left alone)"));
				current = null;
				return null;
			}
			if (!next.clearOnly() && there.is(next.block)) {
				next.turns++;
			}
			treeLog = clear && there.is(BlockTags.OVERWORLD_NATURAL_LOGS) && !own;
			Goal reach = new Goal.Reach(next.pos.getX(), next.pos.getY(), next.pos.getZ(), REACH);
			BlockPos feet = ctx.feet();
			if (reach.reached(feet.getX(), feet.getY(), feet.getZ())) {
				phase = Phase.CLEAR;
				startChild(new BreakBlockSkill(next.pos));
			} else {
				phase = Phase.APPROACH;
				startChild(GotoSkill.to(reach, "block in the way", GotoSkill.Dig.FALLBACK, 8_000, 40 * 20));
			}
			return null;
		}
		if (next.clearOnly()) {
			// Already empty.
			current = null;
			return null;
		}
		startPlacing(ctx, next);
		return null;
	}

	/**
	 * Places the block, unless that would shut the player inside the build: then the player
	 * walks out of it first and the block goes in from outside.
	 */
	private void startPlacing(SkillContext ctx, Target t) {
		int[] out = walkOuts < MAX_WALK_OUTS && !t.filler ? wayOutIfShutIn(ctx, t.pos) : null;
		if (out != null) {
			walkOuts++;
			MineGPT.LOGGER.debug("Build: stepping out before placing at {}", t.pos.toShortString());
			phase = Phase.LEAVE;
			startChild(GotoSkill.to(new Goal.XZ(out[0], out[1]), "out of the build", GotoSkill.Dig.NEVER, 6_000, 20 * 20));
			return;
		}
		phase = Phase.PLACE;
		startChild(placer(t));
	}

	private PlaceSkill placer(Target t) {
		return new PlaceSkill(t.item, t.pos, t.want, 30 * 20, wanted::containsKey)
				.as(t.filler ? Ledger.Purpose.FILL : Ledger.Purpose.BUILD);
	}

	/** Holds back from scaffolding what this call, and later calls for the same structure, still have to place. */
	private void holdBack(SkillContext ctx) {
		Map<net.minecraft.world.item.Item, Integer> needed = new HashMap<>(reserve);
		for (Target t : todo) {
			if (!t.clearOnly() && !ctx.level.getBlockState(t.pos).is(t.block)) {
				needed.merge(t.item.item(), 1, Integer::sum);
			}
		}
		Scaffold.reserve(needed);
	}

	/**
	 * If the player stands inside the build and a block at {@code pos} would close the last way
	 * out of it, the x and z of a spot just outside to walk to; otherwise null.
	 */
	private int[] wayOutIfShutIn(SkillContext ctx, BlockPos pos) {
		if (ownSpots.isEmpty()) {
			return null;
		}
		if (bounds == null) {
			int minX = Integer.MAX_VALUE;
			int minZ = Integer.MAX_VALUE;
			int maxX = Integer.MIN_VALUE;
			int maxZ = Integer.MIN_VALUE;
			for (BlockPos spot : ownSpots) {
				minX = Math.min(minX, spot.getX());
				minZ = Math.min(minZ, spot.getZ());
				maxX = Math.max(maxX, spot.getX());
				maxZ = Math.max(maxZ, spot.getZ());
			}
			bounds = new int[] {minX, minZ, maxX, maxZ};
		}
		BlockPos feet = ctx.feet();
		int x = feet.getX();
		int z = feet.getZ();
		if (x < bounds[0] || x > bounds[2] || z < bounds[1] || z > bounds[3] || !ctx.player.onGround()) {
			return null;
		}
		ClientBlockView view = new ClientBlockView(ctx.level, ctx.player.getInventory());
		long blocked = Pathfinder.pack(pos.getX(), pos.getY(), pos.getZ());
		if (Enclosure.canLeave(view, x, feet.getY(), z, blocked, bounds[0], bounds[1], bounds[2], bounds[3], LEAVE_BUDGET)
				|| !Enclosure.canLeave(view, x, feet.getY(), z, Long.MIN_VALUE, bounds[0], bounds[1], bounds[2], bounds[3],
						LEAVE_BUDGET)) {
			// Either it shuts nothing, or there is no way out as it is and waiting will not help.
			return null;
		}
		// The nearest side of the build, two blocks beyond it.
		int west = x - bounds[0];
		int east = bounds[2] - x;
		int north = z - bounds[1];
		int south = bounds[3] - z;
		int least = Math.min(Math.min(west, east), Math.min(north, south));
		if (least == west) {
			return new int[] {bounds[0] - 2, z};
		}
		if (least == east) {
			return new int[] {bounds[2] + 2, z};
		}
		return least == north ? new int[] {x, bounds[1] - 2} : new int[] {x, bounds[3] + 2};
	}

	/**
	 * True if a spot to be left empty still holds something: a solid block, or a plant or torch
	 * whose outline would hide what is around it.
	 */
	private static boolean needsClearing(SkillContext ctx, BlockPos pos, BlockState state) {
		return !state.canBeReplaced() || Mining.isSoft(ctx.level, pos, state);
	}

	private static boolean isHidden(String message) {
		return message != null && (message.startsWith("cannot see") || message.startsWith("cannot aim"));
	}

	/**
	 * Deals with a block that has to be dug but cannot be seen. What covers it, the block on top
	 * or failing that the first block in the line of sight, is queued to be dug first if it is
	 * natural, and the block itself goes back in the queue. If the cover is something built, the
	 * player walks to a spot a little away for another angle, once. False if neither can be done.
	 */
	private boolean uncover(SkillContext ctx, Target t) {
		if (t.hidden < Tuning.BUILD_UNCOVER_TRIES.i()) {
			BlockPos cover = cover(ctx, t.pos);
			if (cover != null) {
				t.hidden++;
				Target dig = Target.empty(cover);
				dig.uncover = true;
				todo.add(dig);
				todo.add(t);
				return true;
			}
		}
		if (t.moves < Tuning.BUILD_VIEW_MOVES.i()) {
			t.moves++;
			t.views.add(ctx.feet());
			// Somewhere on the ground with a face of it in plain sight; failing that, just a little away.
			BlockPos stand = BreakBlockSkill.vantage(ctx, t.pos, t.views);
			Goal goal;
			if (stand != null) {
				t.views.add(stand);
				goal = new Goal.Block(stand.getX(), stand.getY(), stand.getZ());
			} else if (t.moves == 1) {
				goal = new Goal.Apart(t.pos.getX(), t.pos.getY(), t.pos.getZ(), REACH);
			} else {
				return false;
			}
			phase = Phase.APPROACH;
			startChild(GotoSkill.to(goal, "a clear view", GotoSkill.Dig.FALLBACK, 8_000, 30 * 20));
			return true;
		}
		return false;
	}

	/** The block hiding {@code pos} that may be dug away, or null. */
	private BlockPos cover(SkillContext ctx, BlockPos pos) {
		BlockPos above = pos.above();
		if (mayUncover(ctx, above)) {
			return above;
		}
		Vec3 eye = ctx.player.getEyePosition();
		BlockHitResult hit = ctx.level.clip(new ClipContext(eye, Vec3.atCenterOf(pos), ClipContext.Block.OUTLINE,
				ClipContext.Fluid.NONE, ctx.player));
		if (hit.getType() == HitResult.Type.BLOCK && !hit.getBlockPos().equals(pos) && mayUncover(ctx, hit.getBlockPos())) {
			return hit.getBlockPos().immutable();
		}
		return null;
	}

	/** True for natural ground or growth that is not already a finished part of this build. */
	private boolean mayUncover(SkillContext ctx, BlockPos pos) {
		BlockState state = ctx.level.getBlockState(pos);
		boolean scaffolding = !state.canBeReplaced() && OwnBlocks.temporary(ctx.mc, pos, state);
		if (!scaffolding && (state.canBeReplaced() || !Mining.isClearable(ctx.level, pos, state) || state.hasBlockEntity())) {
			return false;
		}
		Block meant = wanted.get(pos);
		if (meant != null && state.is(meant)) {
			return false;
		}
		return !Mining.touchesLava(ctx.level, pos) && MineGPTClient.safety().mayBreak(ctx.mc, pos);
	}

	/**
	 * Queues the clear box: every natural thing in it that is not where a block of the build
	 * goes, and the rest of any tree whose trunk starts in it. Built blocks are left alone.
	 */
	private void addSiteClearing(SkillContext ctx) {
		if (clearBox == null) {
			return;
		}
		Set<BlockPos> queued = new HashSet<>();
		for (Target t : todo) {
			queued.add(t.pos);
		}
		ArrayDeque<BlockPos> trunks = new ArrayDeque<>();
		BlockPos.MutableBlockPos at = new BlockPos.MutableBlockPos();
		for (int y = clearBox[1]; y <= clearBox[4]; y++) {
			for (int x = clearBox[0]; x <= clearBox[3]; x++) {
				for (int z = clearBox[2]; z <= clearBox[5]; z++) {
					BlockState state = ctx.level.getBlockState(at.set(x, y, z));
					// Plants count too, short grass and all: their outlines hide the ground they stand on.
					if (!needsClearing(ctx, at, state) || !Mining.isClearable(ctx.level, at, state)
							|| !MineGPTClient.safety().mayBreak(ctx.mc, at)) {
						continue;
					}
					BlockPos pos = at.immutable();
					if (state.is(BlockTags.LOGS)) {
						trunks.add(pos);
					}
					if (queued.add(pos)) {
						todo.add(Target.empty(pos));
					}
				}
			}
		}
		followTrunks(ctx, trunks, queued);
	}

	/**
	 * Queues the rest of each tree whose logs are in {@code trunks}: logs joined to them, at their
	 * level or above, so no trunk is left hanging over the build.
	 */
	private void followTrunks(SkillContext ctx, ArrayDeque<BlockPos> trunks, Set<BlockPos> queued) {
		int followed = 0;
		Set<BlockPos> seen = new HashSet<>(trunks);
		while (!trunks.isEmpty() && followed < MAX_TRUNK * 4) {
			BlockPos log = trunks.poll();
			for (int dy = 0; dy <= 1; dy++) {
				for (int dx = -1; dx <= 1; dx++) {
					for (int dz = -1; dz <= 1; dz++) {
						BlockPos next = log.offset(dx, dy, dz);
						if (!seen.add(next) || !ctx.level.getBlockState(next).is(BlockTags.OVERWORLD_NATURAL_LOGS)
								|| !MineGPTClient.safety().mayBreak(ctx.mc, next)) {
							continue;
						}
						followed++;
						trunks.add(next);
						if (queued.add(next)) {
							todo.add(Target.empty(next));
						}
					}
				}
			}
		}
	}

	/**
	 * After a tree's log was dug out of the way mid-build, the rest of that tree goes too: a
	 * trunk left standing over the build is what the next block would run into.
	 */
	private void fellRest(SkillContext ctx, BlockPos log) {
		Set<BlockPos> queued = new HashSet<>();
		for (Target t : todo) {
			queued.add(t.pos);
		}
		ArrayDeque<BlockPos> trunks = new ArrayDeque<>();
		trunks.add(log);
		followTrunks(ctx, trunks, queued);
	}

	@Override
	protected void onStop(SkillContext ctx) {
		GotoSkill.placedListener = null;
		Scaffold.reserve(Map.of());
		OwnBlocks.save();
	}

	/**
	 * Queues the pillar and bridge blocks goto left around the build for removal. Reaching one
	 * may take new scaffolding, so this runs a few rounds. False when there is nothing to take down.
	 */
	private boolean queueTidying(SkillContext ctx) {
		if (!tidy || tidyRounds >= 3) {
			return false;
		}
		Set<BlockPos> down = new HashSet<>();
		for (BlockPos pos : scaffold) {
			BlockState state = ctx.level.getBlockState(pos);
			if (!ownSpots.contains(pos) && !state.canBeReplaced()
					&& Scaffold.isScaffold(new net.minecraft.world.item.ItemStack(state.getBlock()))) {
				down.add(pos);
			}
		}
		scaffold.clear();
		down.addAll(leftovers(ctx));
		if (down.isEmpty()) {
			return false;
		}
		tidyRounds++;
		for (BlockPos pos : down) {
			Target target = Target.empty(pos);
			target.tidy = true;
			todo.add(target);
		}
		return true;
	}

	/**
	 * The agent's own pillar, bridge and filler blocks that stand in the site, from this call or
	 * any earlier one, at walking level or above and not where the build wants that very block.
	 * Filling below walking level is left: it is the ground the build stands on.
	 */
	private List<BlockPos> leftovers(SkillContext ctx) {
		List<BlockPos> out = new ArrayList<>();
		if (site == null) {
			return out;
		}
		for (Ledger.Entry e : OwnBlocks.ledger(ctx.mc).within(site[0] - 3, site[1], site[2] - 3, site[3] + 3, site[4] + 8,
				site[5] + 3)) {
			if (!e.purpose().temporary()) {
				continue;
			}
			BlockPos pos = new BlockPos(e.x(), e.y(), e.z());
			BlockState state = ctx.level.getBlockState(pos);
			Block meant = wanted.get(pos);
			if (OwnBlocks.owns(ctx.mc, pos, state) == null || (meant != null && state.is(meant))
					|| !MineGPTClient.safety().mayBreak(ctx.mc, pos)) {
				continue;
			}
			out.add(pos);
		}
		return out;
	}

	@Override
	protected String describe() {
		int total = placed + present + failures.size() + todo.size() + (current == null ? 0 : 1);
		return "building (" + (placed + present) + "/" + total + ")";
	}

	/** Fails at once when the inventory cannot cover the blocks still to place, unless partial builds are allowed. */
	private SkillResult checkMaterials(SkillContext ctx) {
		Map<ItemMatcher, Integer> needed = new LinkedHashMap<>();
		for (Target t : todo) {
			if (!t.clearOnly() && !ctx.level.getBlockState(t.pos).is(t.block)) {
				needed.merge(t.item, 1, Integer::sum);
			}
		}
		JsonObject shortBy = new JsonObject();
		List<String> parts = new ArrayList<>();
		for (Map.Entry<ItemMatcher, Integer> e : needed.entrySet()) {
			int have = Inventories.count(ctx.player.getInventory(), e.getKey());
			if (have < e.getValue()) {
				shortBy.addProperty(e.getKey().toString(), e.getValue() - have);
				parts.add((e.getValue() - have) + " more " + e.getKey());
			}
		}
		if (parts.isEmpty() || partial) {
			return null;
		}
		JsonObject data = new JsonObject();
		data.add("missing", shortBy);
		return SkillResult.failure("not enough blocks: need " + String.join(", ", parts), data);
	}

	/**
	 * The next block to place: the lowest layer first, and within it the nearest block that
	 * has something to be placed against. Blocks already in place are counted and dropped.
	 */
	private Target choose(SkillContext ctx) {
		todo.removeIf(t -> {
			if (t.clearOnly()) {
				return !needsClearing(ctx, t.pos, ctx.level.getBlockState(t.pos));
			}
			BlockState there = ctx.level.getBlockState(t.pos);
			if (!there.is(t.block)) {
				return false;
			}
			String wrong = t.filler ? null : BlockStates.mismatch(there, t.block, t.want);
			if (wrong == null) {
				present += t.filler ? 0 : 1;
				return true;
			}
			// The right block turned the wrong way. The agent's own is broken and placed again, once.
			// Breaking it drops the item where the player may not pick it up, so only with one to spare.
			boolean spare = Inventories.find(ctx.player.getInventory(), t.item) >= 0;
			boolean own = OwnBlocks.owns(ctx.mc, t.pos, there) != null;
			if (t.turns < 1 && !there.hasBlockEntity() && own && spare) {
				return false;
			}
			fail(t, "turned wrong (" + wrong + ")"
					+ (t.turns > 0 ? ", also after placing it again" : there.hasBlockEntity() ? "; it holds things, so it was left"
							: !own ? "; not placed by MineGPT, so left alone" : "; no spare one carried to place it again"));
			return true;
		});
		if (todo.isEmpty() && !queueTidying(ctx)) {
			return null;
		}
		Target best = best(ctx);
		if (best == null && fill && addFillers(ctx)) {
			best = best(ctx);
		}
		if (best == null) {
			// Nothing left has anything to be placed against.
			for (Target t : todo) {
				if (!t.filler) {
					fail(t, "nothing to place it against");
				}
			}
			todo.clear();
			best = queueTidying(ctx) ? best(ctx) : null;
			if (best == null) {
				todo.clear();
				return null;
			}
		}
		todo.remove(best);
		return best;
	}

	private Target best(SkillContext ctx) {
		BlockPos feet = ctx.feet();
		Vec3 eye = ctx.player.getEyePosition();
		Target best = null;
		double bestScore = Double.MAX_VALUE;
		for (Target t : todo) {
			if (!t.clearOnly() && !hasSupport(ctx, t.pos)) {
				continue;
			}
			// One layer up counts as 64 blocks of walking, so layers finish before the next starts.
			// Digging out comes before any placing, from the top down. Before that, trees come down
			// from the bottom up, standing where the trunk was; and before anything, whatever covers
			// a block that is waiting to be dug.
			//
			// Within each of those, what can be done from where the player stands now comes first:
			// every block in reach and in sight is placed or dug before walking anywhere, lowest
			// first when placing and highest first when digging. That is most of the time saved on
			// walls, and on a roof it means one scaffold pillar does many blocks instead of one.
			double walk = Math.sqrt(feet.distSqr(t.pos));
			boolean here = Vec3.atCenterOf(t.pos).distanceTo(eye) <= HERE;
			double score;
			if (t.uncover) {
				score = -3e7 + walk;
			} else if (t.clearOnly() && !t.tidy && ctx.level.getBlockState(t.pos).is(BlockTags.LOGS)) {
				score = -2e7 + t.pos.getY() * 64.0 + walk;
			} else if (t.clearOnly()) {
				here = here && BreakBlockSkill.aimPoint(ctx, t.pos) != null;
				score = -1e7 - t.pos.getY() * (here ? 4.0 : 64.0) + walk - (here ? 5e5 : 0);
			} else {
				here = here && ctx.level.getBlockState(t.pos).canBeReplaced()
						&& PlaceSkill.placeableFromHere(ctx, t.pos, t.want, t.block);
				score = t.pos.getY() * (here ? 4.0 : 64.0) + walk - (here ? 5e5 : 0);
			}
			if (score < bestScore) {
				best = t;
				bestScore = score;
			}
		}
		return best;
	}

	/**
	 * Adds spare blocks under the lowest blocks left, down to solid ground no more than
	 * {@link #FILL_DEPTH} below. Returns false if there was nothing to fill or nothing to fill with.
	 */
	private boolean addFillers(SkillContext ctx) {
		if (filled >= MAX_FILL) {
			return false;
		}
		Map<BlockPos, Target> pending = new HashMap<>();
		todo.forEach(t -> pending.put(t.pos, t));
		List<Target> lowest = new ArrayList<>(todo);
		lowest.sort((a, b) -> Integer.compare(a.pos.getY(), b.pos.getY()));
		boolean added = false;
		int budget = MAX_FILL - filled;
		for (Target t : lowest) {
			BlockPos below = t.pos.below();
			if (t.clearOnly() || pending.containsKey(below) || badFillers.contains(below)) {
				continue; // that block gets its own filling
			}
			List<BlockPos> column = new ArrayList<>();
			BlockPos pos = below;
			boolean grounded = false;
			for (int depth = 0; depth < FILL_DEPTH; depth++, pos = pos.below()) {
				BlockState state = ctx.level.getBlockState(pos);
				if (!state.canBeReplaced()) {
					grounded = state.isFaceSturdy(ctx.level, pos, Direction.UP);
					break;
				}
				column.add(pos);
			}
			if (!grounded || column.isEmpty() || column.size() > budget || column.stream().anyMatch(badFillers::contains)) {
				continue;
			}
			for (BlockPos cell : column) {
				ItemMatcher filler = fillerItem(ctx, pending);
				if (filler == null || !MineGPTClient.safety().mayPlace(ctx.mc, cell, filler.item())) {
					return added;
				}
				Target support = new Target(cell, filler, ((BlockItem) filler.item()).getBlock(), PlaceSkill.Want.ANY, true);
				todo.add(support);
				pending.put(cell, support);
				budget--;
				added = true;
			}
		}
		return added;
	}

	/**
	 * Carries the lowest layer down to the ground in the foundation block: under each of its
	 * blocks, every open spot down to the first solid one, if that is within reach. A column
	 * with no ground under it in range is left for the spare-block filling.
	 */
	private void addFootings(SkillContext ctx) {
		if (foundation == null) {
			return;
		}
		int lowest = Integer.MAX_VALUE;
		Set<BlockPos> taken = new HashSet<>();
		for (Target t : todo) {
			taken.add(t.pos);
			if (!t.clearOnly()) {
				lowest = Math.min(lowest, t.pos.getY());
			}
		}
		Block block = ((BlockItem) foundation.item()).getBlock();
		List<Target> extra = new ArrayList<>();
		for (Target t : todo) {
			if (t.clearOnly() || t.pos.getY() != lowest) {
				continue;
			}
			List<BlockPos> column = new ArrayList<>();
			BlockPos pos = t.pos.below();
			boolean grounded = false;
			for (int depth = 0; depth < foundationDepth; depth++, pos = pos.below()) {
				BlockState state = ctx.level.getBlockState(pos);
				if (!state.canBeReplaced()) {
					grounded = true;
					break;
				}
				if (taken.contains(pos) || !MineGPTClient.safety().mayPlace(ctx.mc, pos, foundation.item())) {
					break;
				}
				column.add(pos);
			}
			if (grounded) {
				for (BlockPos cell : column) {
					extra.add(new Target(cell, foundation, block, PlaceSkill.Want.ANY));
					ownSpots.add(cell);
					wanted.put(cell, block);
				}
			}
		}
		footings = extra.size();
		todo.addAll(extra);
	}

	/** A spare scaffold block the build itself does not need, or null. */
	private static ItemMatcher fillerItem(SkillContext ctx, Map<BlockPos, Target> pending) {
		var inventory = ctx.player.getInventory();
		for (int slot = 0; slot < 36; slot++) {
			var stack = inventory.getItem(slot);
			if (stack.isEmpty() || !(stack.getItem() instanceof BlockItem) || !Scaffold.isScaffold(stack)) {
				continue;
			}
			ItemMatcher item = ItemMatcher.of(stack.getItem());
			long needed = pending.values().stream().filter(t -> !t.clearOnly() && t.item.item() == stack.getItem()).count();
			if (Inventories.count(inventory, s -> s.is(stack.getItem())) > needed) {
				return item;
			}
		}
		return null;
	}

	/** True if a neighbour has a solid face to place against, or the spot holds something to dig out first. */
	private boolean hasSupport(SkillContext ctx, BlockPos pos) {
		if (!ctx.level.getBlockState(pos).canBeReplaced()) {
			return true;
		}
		for (Direction direction : Direction.values()) {
			BlockPos support = pos.relative(direction);
			if (ctx.level.getBlockState(support).isFaceSturdy(ctx.level, support, direction.getOpposite())) {
				return true;
			}
		}
		return false;
	}

	private void fail(Target t, String reason) {
		if (t.filler) {
			badFillers.add(t.pos);
			return;
		}
		if (t.tidy || t.uncover) {
			return;
		}
		if (keptAsGround(t)) {
			kept++;
			MineGPT.LOGGER.debug("Build: footing at {} left as the ground it is: {}", t.pos.toShortString(), reason);
			return;
		}
		String text = (t.clearOnly() ? "digging out" : BuiltInRegistries.ITEM.getKey(t.item.item()).getPath()) + " at " + t.pos.toShortString() + ": " + reason;
		MineGPT.LOGGER.debug("Build: {}", text);
		failures.put(t.pos, text);
	}

	/**
	 * True for a footing block whose spot holds solid natural ground: that carries the wall as
	 * well. And for any block whose spot holds solid ground with solid blocks on all six sides:
	 * nobody can ever see it, so what it is made of does not matter.
	 */
	private boolean keptAsGround(Target t) {
		if (t.clearOnly() || level == null) {
			return false;
		}
		BlockState there = level.getBlockState(t.pos);
		if (!Mining.isNaturalTerrain(there) || !there.isCollisionShapeFullBlock(level, t.pos)) {
			return false;
		}
		if (foundation != null && t.item.item() == foundation.item()) {
			return true;
		}
		for (Direction direction : Direction.values()) {
			BlockPos side = t.pos.relative(direction);
			if (!level.getBlockState(side).isCollisionShapeFullBlock(level, side)) {
				return false;
			}
		}
		return true;
	}

	private int leftoverCount() {
		if (site == null) {
			return 0;
		}
		int count = 0;
		for (Ledger.Entry e : OwnBlocks.ledger(net.minecraft.client.Minecraft.getInstance()).within(site[0] - 3, site[1],
				site[2] - 3, site[3] + 3, site[4] + 8, site[5] + 3)) {
			BlockPos pos = new BlockPos(e.x(), e.y(), e.z());
			Block meant = wanted.get(pos);
			BlockState state = level.getBlockState(pos);
			if (e.purpose().temporary() && OwnBlocks.id(state).equals(e.block()) && !(meant != null && state.is(meant))) {
				count++;
			}
		}
		return count;
	}

	private SkillResult finish() {
		JsonObject data = new JsonObject();
		data.addProperty("placed", placed);
		data.addProperty("present", present);
		data.addProperty("cleared", cleared);
		data.addProperty("filled", filled);
		data.addProperty("footings", footings);
		data.addProperty("tidied", tidied);
		data.addProperty("kept", kept);
		data.addProperty("turned", turned);
		int left = level == null ? 0 : leftoverCount();
		data.addProperty("leftover", left);
		data.addProperty("failed", failures.size());
		JsonArray failed = new JsonArray();
		failures.values().stream().limit(REPORT_LIMIT).forEach(failed::add);
		data.add("failures", failed);
		// Every spot that failed, so the brain can go over just those and their neighbours again.
		JsonArray failedAt = new JsonArray();
		for (Map.Entry<BlockPos, String> e : failures.entrySet()) {
			JsonArray at = new JsonArray();
			at.add(e.getKey().getX());
			at.add(e.getKey().getY());
			at.add(e.getKey().getZ());
			String text = e.getValue();
			int colon = text.indexOf(": ");
			at.add(colon < 0 ? text : text.substring(colon + 2));
			failedAt.add(at);
		}
		data.add("failedAt", failedAt);
		if (!missing.isEmpty()) {
			JsonObject m = new JsonObject();
			missing.forEach(m::addProperty);
			data.add("missing", m);
		}
		String summary = "placed " + placed + (present > 0 ? ", " + present + " already there" : "")
				+ (cleared > 0 ? ", cleared " + cleared : "") + (filled > 0 ? ", filled " + filled + " under it" : "")
				+ (footings > 0 ? ", " + footings + " of them footings down to the ground" : "")
				+ (kept > 0 ? ", " + kept + " footing spots left as solid ground" : "")
				+ (turned > 0 ? ", " + turned + " turned the right way round" : "")
				+ (tidied > 0 ? ", took down " + tidied + " of its own scaffold blocks" : "")
				+ (left > 0 ? ", " + left + " scaffold blocks could not be taken down" : "");
		if (failures.isEmpty()) {
			return SkillResult.success("built it: " + summary, data);
		}
		String message = summary + "; " + failures.size() + " could not be placed, e.g. " + failures.values().iterator().next();
		// Most of it standing counts as a partial success the brain can finish off.
		return placed + present > 0 && partial ? SkillResult.success(message, data) : SkillResult.failure(message, data);
	}
}
