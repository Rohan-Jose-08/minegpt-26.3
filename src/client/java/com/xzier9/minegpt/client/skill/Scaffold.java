package com.xzier9.minegpt.client.skill;

import java.util.List;
import java.util.Map;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;

/**
 * Blocks the player can spend on pillaring and bridging: common stone and dirt that nothing
 * else needs. Cobblestone and its kin make furnaces and stone tools, so a few are always kept
 * back. Logs, planks, ores and anything rarer are never used.
 *
 * A build in progress can hold back what it needs for itself ({@link #reserve}), so that a
 * wall is not left short because its cobblestone went into a pillar.
 */
final class Scaffold {
	/** Spent first: no recipe the agent needs uses them. */
	private static final List<Item> SPARE = List.of(Items.DIRT, Items.COARSE_DIRT, Items.NETHERRACK, Items.ANDESITE,
			Items.DIORITE, Items.GRANITE, Items.TUFF);
	/** Spent only above {@link #KEEP} of each. */
	private static final List<Item> USEFUL = List.of(Items.COBBLESTONE, Items.COBBLED_DEEPSLATE, Items.BLACKSTONE);
	/** Enough of each useful block for a furnace and a stone tool. */
	private static final int KEEP = 12;
	/** How many of each item a build in progress still needs. */
	private static Map<Item, Integer> reserved = Map.of();

	private Scaffold() {
	}

	/** Sets how many of each item to hold back, until the next call. An empty map frees them all. */
	static void reserve(Map<Item, Integer> needed) {
		reserved = Map.copyOf(needed);
	}

	/** How many of this item can be spent: what is carried, less what is held back. */
	private static int spare(Inventory inventory, Item item, int keep) {
		return Inventories.count(inventory, stack -> stack.is(item)) - keep - reserved.getOrDefault(item, 0);
	}

	/** Blocks that can be spent. */
	static int count(Inventory inventory) {
		int total = 0;
		for (Item item : SPARE) {
			total += Math.max(0, spare(inventory, item, 0));
		}
		for (Item item : USEFUL) {
			total += Math.max(0, spare(inventory, item, KEEP));
		}
		return total;
	}

	/** The inventory slot of the block to spend next, or -1 if there is none to spare. */
	static int slot(Inventory inventory) {
		for (Item item : SPARE) {
			if (spare(inventory, item, 0) > 0) {
				return Inventories.find(inventory, stack -> stack.is(item));
			}
		}
		for (Item item : USEFUL) {
			if (spare(inventory, item, KEEP) > 0) {
				return Inventories.find(inventory, stack -> stack.is(item));
			}
		}
		return -1;
	}

	/** True if this is a kind of block goto spends, whether or not any can be spared right now. */
	static boolean isScaffold(ItemStack stack) {
		return SPARE.stream().anyMatch(stack::is) || USEFUL.stream().anyMatch(stack::is);
	}

	/** True if this stack, such as the one in hand, can be spent. */
	static boolean usable(Inventory inventory, ItemStack stack) {
		for (Item item : SPARE) {
			if (stack.is(item)) {
				return spare(inventory, item, 0) > 0;
			}
		}
		for (Item item : USEFUL) {
			if (stack.is(item)) {
				return spare(inventory, item, KEEP) > 0;
			}
		}
		return false;
	}
}
