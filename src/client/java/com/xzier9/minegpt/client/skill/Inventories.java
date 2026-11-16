package com.xzier9.minegpt.client.skill;

import com.xzier9.minegpt.client.act.Action;
import java.util.function.Predicate;
import net.minecraft.core.component.DataComponents;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;

/**
 * Finding, counting and selecting items. Inventory slots 0-8 are the hotbar and 9-35 the
 * rest; menus number their slots differently, so clicks go through {@link #menuSlot}.
 */
public final class Inventories {
	public static final int HOTBAR_SIZE = 9;
	public static final int MAIN_SIZE = 36;

	private Inventories() {
	}

	/** The first matching slot, hotbar first, or -1. */
	public static int find(Inventory inventory, Predicate<ItemStack> match) {
		for (int slot = 0; slot < MAIN_SIZE; slot++) {
			if (match.test(inventory.getItem(slot))) {
				return slot;
			}
		}
		return -1;
	}

	public static int count(Inventory inventory, Predicate<ItemStack> match) {
		int total = 0;
		for (int slot = 0; slot < MAIN_SIZE; slot++) {
			ItemStack stack = inventory.getItem(slot);
			if (match.test(stack)) {
				total += stack.getCount();
			}
		}
		return total;
	}

	/**
	 * Puts the item in this inventory slot in the player's hand: selects it if it is on the
	 * hotbar, otherwise swaps it onto the hotbar first. A slot of -1 means an empty hand.
	 * Returns false if it cannot be done right now because another container is open.
	 */
	public static boolean select(SkillContext ctx, int slot) {
		Inventory inventory = ctx.player.getInventory();
		if (slot < 0) {
			for (int i = 0; i < HOTBAR_SIZE; i++) {
				if (inventory.getItem(i).isEmpty()) {
					select(ctx, i);
					break;
				}
			}
			return true;
		}
		if (slot < HOTBAR_SIZE) {
			if (inventory.getSelectedSlot() != slot) {
				ctx.act(new Action.Hotbar(slot));
			}
			return true;
		}
		Player player = ctx.player;
		if (player.containerMenu != player.inventoryMenu) {
			return false;
		}
		int target = hotbarTarget(inventory);
		ctx.gameMode.handleContainerInput(player.inventoryMenu.containerId,
				menuSlot(player.inventoryMenu, inventory, slot), target, ContainerInput.SWAP, player);
		ctx.act(new Action.Hotbar(target));
		return true;
	}

	/** Finds a matching item and puts it in the player's hand. Returns false if there is none. */
	public static boolean select(SkillContext ctx, Predicate<ItemStack> match) {
		if (match.test(ctx.player.getMainHandItem())) {
			return true;
		}
		int slot = find(ctx.player.getInventory(), match);
		return slot >= 0 && select(ctx, slot);
	}

	/** The menu slot that shows this inventory slot, or -1 if the menu does not show it. */
	public static int menuSlot(AbstractContainerMenu menu, Inventory inventory, int inventorySlot) {
		for (int i = 0; i < menu.slots.size(); i++) {
			Slot slot = menu.slots.get(i);
			if (slot.container == inventory && slot.getContainerSlot() == inventorySlot) {
				return i;
			}
		}
		return -1;
	}

	/** True if the menu slot belongs to the player's own inventory rather than the container. */
	public static boolean isPlayerSlot(Slot slot, Inventory inventory) {
		return slot.container == inventory && slot.getContainerSlot() < MAIN_SIZE;
	}

	/** Where to put an item fetched from the main inventory: an empty hotbar slot, else the least useful one. */
	private static int hotbarTarget(Inventory inventory) {
		for (int i = 0; i < HOTBAR_SIZE; i++) {
			if (inventory.getItem(i).isEmpty()) {
				return i;
			}
		}
		for (int i = 0; i < HOTBAR_SIZE; i++) {
			if (!isKeeper(inventory.getItem(i))) {
				return i;
			}
		}
		return inventory.getSelectedSlot();
	}

	/** Tools, weapons and food stay on the hotbar when something else needs room. */
	private static boolean isKeeper(ItemStack stack) {
		return stack.has(DataComponents.TOOL) || stack.has(DataComponents.WEAPON) || stack.has(DataComponents.FOOD);
	}

	/** Attack damage the item adds in the main hand. */
	public static double attackDamage(ItemStack stack) {
		double[] total = {0};
		stack.forEachModifier(EquipmentSlot.MAINHAND, (attribute, modifier) -> {
			if (attribute.value() == Attributes.ATTACK_DAMAGE.value()) {
				total[0] += modifier.amount();
			}
		});
		return total[0];
	}

	/** The slot of the strongest weapon, or -1 if nothing beats bare hands. */
	public static int bestWeaponSlot(Inventory inventory) {
		int best = -1;
		double bestDamage = 0;
		for (int slot = 0; slot < MAIN_SIZE; slot++) {
			double damage = attackDamage(inventory.getItem(slot));
			if (damage > bestDamage) {
				best = slot;
				bestDamage = damage;
			}
		}
		return best;
	}
}
