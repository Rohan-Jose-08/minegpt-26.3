package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;
import com.xzier9.minegpt.path.Goal;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.function.Predicate;
import net.minecraft.core.BlockPos;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ChestMenu;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.inventory.ShulkerBoxMenu;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;

/**
 * Puts items into a chest or barrel ({@code store}) or takes them out ({@code take}). Uses the
 * container at x y z, or the nearest one in sight. Every result lists what the container holds
 * afterwards, so the brain can remember what is stored where.
 *
 * Storing with no item list stores everything except what the agent needs to keep working:
 * tools, weapons, armour, food, torches and the blocks it climbs and bridges with.
 */
public final class ChestSkill extends Skill {
	public enum Mode {
		STORE, TAKE
	}

	private enum State {
		FIND, OPEN, MOVE
	}

	private static final double REACH = 4.0;
	private static final int SEARCH_RADIUS = 24;
	/** Scaffold blocks kept back when storing everything, so goto can still pillar and bridge. */
	private static final int KEEP_SCAFFOLD = 32;

	private final Mode mode;
	private final List<ItemMatcher> items;
	private final List<ItemMatcher> keep;
	private final int count;
	private final BlockPos requested;
	private State state = State.FIND;
	private BlockPos container;
	private final Set<BlockPos> bad = new HashSet<>();
	private int tries;
	private boolean opened;

	private ChestSkill(Mode mode, List<ItemMatcher> items, List<ItemMatcher> keep, int count, BlockPos requested,
			int maxTicks) {
		super(mode == Mode.STORE ? "store" : "take", maxTicks);
		this.mode = mode;
		this.items = items;
		this.keep = keep;
		this.count = count;
		this.requested = requested;
	}

	static ChestSkill fromArgs(Args args, Mode mode) {
		BlockPos pos = null;
		if (args.has("x") || args.has("y") || args.has("z")) {
			pos = new BlockPos(args.integer("x"), args.integer("y"), args.integer("z"));
		}
		List<ItemMatcher> items = new ArrayList<>();
		if (mode == Mode.TAKE) {
			items.add(ItemMatcher.parse(args.string("item")));
		} else {
			args.optionalStrings("items").forEach(spec -> items.add(ItemMatcher.parse(spec)));
		}
		List<ItemMatcher> keep = new ArrayList<>();
		args.optionalStrings("keep").forEach(spec -> keep.add(ItemMatcher.parse(spec)));
		int count = Math.max(1, Math.min(36 * 64, args.integer("count", mode == Mode.TAKE ? 64 : 36 * 64)));
		return new ChestSkill(mode, items, keep, count, pos, args.ticks("timeout", 60));
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		SkillResult child = childResult();
		switch (state) {
			case FIND -> {
				if (child != null && !child.success()) {
					return retry(child.message());
				}
				return find(ctx);
			}
			case OPEN -> {
				if (child == null || !child.success()) {
					return retry(child == null ? "could not open it" : child.message());
				}
				opened = true;
				state = State.MOVE;
				return null;
			}
			case MOVE -> {
				return move(ctx);
			}
		}
		return null;
	}

	@Override
	protected String describe() {
		return (mode == Mode.STORE ? "storing items" : "taking " + items.get(0)) + (container == null ? "" : " at "
				+ container.toShortString());
	}

	@Override
	protected void onStop(SkillContext ctx) {
		close(ctx);
	}

	private SkillResult find(SkillContext ctx) {
		if (requested != null) {
			if (!isContainer(ctx.level.getBlockState(requested))) {
				return SkillResult.failure("there is no chest or barrel at " + requested.toShortString());
			}
			container = requested;
		} else {
			List<BlockPos> found = BlockSearch.find(ctx.level, ctx.feet(), SEARCH_RADIUS, ChestSkill::isContainer, true, bad, 1);
			if (found.isEmpty()) {
				return SkillResult.failure("no chest or barrel within " + SEARCH_RADIUS + " blocks");
			}
			container = found.get(0);
		}
		Goal reach = new Goal.Reach(container.getX(), container.getY(), container.getZ(), REACH);
		BlockPos feet = ctx.feet();
		if (reach.reached(feet.getX(), feet.getY(), feet.getZ())) {
			// An empty hand, so the click opens the container instead of placing a block against it.
			Inventories.select(ctx, -1);
			state = State.OPEN;
			startChild(new OpenContainerSkill(container, ChestSkill::isContainerMenu));
		} else {
			startChild(GotoSkill.to(reach, "chest", GotoSkill.Dig.FALLBACK, 8_000, 60 * 20));
		}
		return null;
	}

	private SkillResult retry(String problem) {
		if (requested != null || ++tries > 3) {
			return SkillResult.failure("could not use the " + (container == null ? "chest" : "chest at "
					+ container.toShortString()) + ": " + problem);
		}
		if (container != null) {
			bad.add(container);
		}
		state = State.FIND;
		return null;
	}

	private SkillResult move(SkillContext ctx) {
		AbstractContainerMenu menu = ctx.player.containerMenu;
		if (!isContainerMenu(menu)) {
			state = State.FIND;
			return null;
		}
		Inventory inventory = ctx.player.getInventory();
		Map<String, Integer> before = carried(inventory);
		boolean full = mode == Mode.STORE ? store(ctx, menu, inventory) : take(ctx, menu, inventory);
		Map<String, Integer> after = carried(inventory);
		JsonObject moved = new JsonObject();
		int total = 0;
		Set<String> keys = new HashSet<>(before.keySet());
		keys.addAll(after.keySet());
		for (String id : keys) {
			int n = mode == Mode.STORE ? before.getOrDefault(id, 0) - after.getOrDefault(id, 0)
					: after.getOrDefault(id, 0) - before.getOrDefault(id, 0);
			if (n > 0) {
				moved.addProperty(id, n);
				total += n;
			}
		}
		JsonObject data = new JsonObject();
		data.addProperty("x", container.getX());
		data.addProperty("y", container.getY());
		data.addProperty("z", container.getZ());
		data.add(mode == Mode.STORE ? "stored" : "took", moved);
		data.add("contents", contents(menu, inventory));
		close(ctx);

		String where = " the chest at " + container.toShortString();
		if (mode == Mode.STORE) {
			if (total == 0) {
				return full ? SkillResult.failure(where.trim() + " is full", data)
						: SkillResult.success("nothing to store", data);
			}
			return SkillResult.success("stored " + total + " items in" + where + (full ? "; it is full now" : ""), data);
		}
		if (total == 0) {
			return SkillResult.failure(full ? "no room in the inventory" : "no " + items.get(0) + " in" + where, data);
		}
		return SkillResult.success("took " + total + " " + items.get(0) + " from" + where
				+ (total < count ? " (all there was" + (full ? ", or all that fit" : "") + ")" : ""), data);
	}

	/** Shift-clicks matching stacks into the container. Returns true if it filled up. */
	private boolean store(SkillContext ctx, AbstractContainerMenu menu, Inventory inventory) {
		Predicate<ItemStack> wanted = items.isEmpty() ? s -> !keeper(s) : s -> items.stream().anyMatch(m -> m.test(s));
		int scaffoldLeft = items.isEmpty() ? KEEP_SCAFFOLD : 0;
		int moved = 0;
		for (int i = 0; i < menu.slots.size() && moved < count; i++) {
			Slot slot = menu.slots.get(i);
			ItemStack stack = slot.getItem();
			if (!Inventories.isPlayerSlot(slot, inventory) || stack.isEmpty() || !wanted.test(stack)
					|| keep.stream().anyMatch(m -> m.test(stack))) {
				continue;
			}
			if (scaffoldLeft > 0 && Scaffold.isScaffold(stack)) {
				// Keep the first stacks of scaffold, store the rest.
				scaffoldLeft -= stack.getCount();
				continue;
			}
			int size = stack.getCount();
			ctx.gameMode.handleContainerInput(menu.containerId, i, 0, ContainerInput.QUICK_MOVE, ctx.player);
			int left = slot.getItem().getCount();
			moved += size - left;
			if (left > 0) {
				return true;
			}
		}
		return false;
	}

	/** Moves up to {@link #count} of the item out of the container. Returns true if the inventory filled up. */
	private boolean take(SkillContext ctx, AbstractContainerMenu menu, Inventory inventory) {
		ItemMatcher item = items.get(0);
		int moved = 0;
		for (int i = 0; i < menu.slots.size() && moved < count; i++) {
			Slot slot = menu.slots.get(i);
			if (Inventories.isPlayerSlot(slot, inventory) || slot.container == inventory || !item.test(slot.getItem())) {
				continue;
			}
			int size = slot.getItem().getCount();
			int want = count - moved;
			if (size <= want) {
				ctx.gameMode.handleContainerInput(menu.containerId, i, 0, ContainerInput.QUICK_MOVE, ctx.player);
				int left = slot.getItem().getCount();
				moved += size - left;
				if (left > 0) {
					return true;
				}
				continue;
			}
			// Part of a stack: pick it all up, drop the wanted number into a free slot one at a time, put the rest back.
			int free = freeSlot(menu, inventory, slot.getItem());
			if (free < 0) {
				return true;
			}
			ctx.gameMode.handleContainerInput(menu.containerId, i, 0, ContainerInput.PICKUP, ctx.player);
			for (int k = 0; k < want; k++) {
				ctx.gameMode.handleContainerInput(menu.containerId, free, 1, ContainerInput.PICKUP, ctx.player);
			}
			moved += want;
			if (!menu.getCarried().isEmpty()) {
				ctx.gameMode.handleContainerInput(menu.containerId, i, 0, ContainerInput.PICKUP, ctx.player);
			}
		}
		return false;
	}

	/** A player slot in the menu that is empty or holds the same item with room, or -1. */
	private static int freeSlot(AbstractContainerMenu menu, Inventory inventory, ItemStack like) {
		int empty = -1;
		for (int i = 0; i < menu.slots.size(); i++) {
			Slot slot = menu.slots.get(i);
			if (!Inventories.isPlayerSlot(slot, inventory)) {
				continue;
			}
			ItemStack stack = slot.getItem();
			if (!stack.isEmpty() && ItemStack.isSameItemSameComponents(stack, like) && stack.getCount() < stack.getMaxStackSize()) {
				return i;
			}
			if (stack.isEmpty() && empty < 0) {
				empty = i;
			}
		}
		return empty;
	}

	/** Things the agent needs to keep working, never stored unless named. */
	private static boolean keeper(ItemStack stack) {
		return stack.has(DataComponents.TOOL) || stack.has(DataComponents.WEAPON) || stack.has(DataComponents.FOOD)
				|| stack.has(DataComponents.EQUIPPABLE) || stack.is(Items.TORCH) || stack.is(Items.CRAFTING_TABLE)
				|| stack.is(Items.FURNACE) || stack.is(Items.SHIELD);
	}

	private static Map<String, Integer> carried(Inventory inventory) {
		Map<String, Integer> totals = new TreeMap<>();
		for (int slot = 0; slot < Inventories.MAIN_SIZE; slot++) {
			ItemStack stack = inventory.getItem(slot);
			if (!stack.isEmpty()) {
				totals.merge(BuiltInRegistries.ITEM.getKey(stack.getItem()).toString(), stack.getCount(), Integer::sum);
			}
		}
		return totals;
	}

	private static JsonObject contents(AbstractContainerMenu menu, Inventory inventory) {
		Map<String, Integer> totals = new TreeMap<>();
		for (Slot slot : menu.slots) {
			if (slot.container != inventory && !slot.getItem().isEmpty()) {
				totals.merge(BuiltInRegistries.ITEM.getKey(slot.getItem().getItem()).toString(), slot.getItem().getCount(),
						Integer::sum);
			}
		}
		JsonObject json = new JsonObject();
		totals.forEach(json::addProperty);
		return json;
	}

	static boolean isContainer(BlockState state) {
		return state.is(Blocks.CHEST) || state.is(Blocks.TRAPPED_CHEST) || state.is(Blocks.BARREL);
	}

	private static boolean isContainerMenu(AbstractContainerMenu menu) {
		return menu instanceof ChestMenu || menu instanceof ShulkerBoxMenu;
	}

	private void close(SkillContext ctx) {
		if (opened && isContainerMenu(ctx.player.containerMenu)) {
			ctx.player.closeContainer();
		}
		opened = false;
	}
}
