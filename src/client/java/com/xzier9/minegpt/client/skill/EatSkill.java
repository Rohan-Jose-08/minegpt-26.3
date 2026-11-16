package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;
import com.xzier9.minegpt.client.act.Action.MoveKey;
import java.util.Set;
import net.minecraft.core.component.DataComponents;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.food.FoodProperties;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;

/** Eats the most filling safe food carried, or a given one. */
public final class EatSkill extends Skill {
	/** Foods with nasty side effects, only eaten when asked for by name. */
	private static final Set<Item> RISKY = Set.of(Items.ROTTEN_FLESH, Items.SPIDER_EYE, Items.POISONOUS_POTATO,
			Items.PUFFERFISH, Items.CHORUS_FRUIT, Items.SUSPICIOUS_STEW);

	private final ItemMatcher item;
	private ItemMatcher eating;
	private int countBefore;
	private int foodBefore;

	public EatSkill(ItemMatcher item) {
		super("eat", 8 * 20);
		this.item = item;
	}

	static EatSkill fromArgs(Args args) {
		return new EatSkill(args.has("item") ? ItemMatcher.parse(args.string("item")) : null);
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		Inventory inventory = ctx.player.getInventory();
		int food = ctx.player.getFoodData().getFoodLevel();
		if (eating == null) {
			int slot = item != null ? Inventories.find(inventory, s -> item.test(s) && s.has(DataComponents.FOOD))
					: bestFood(inventory);
			if (slot < 0) {
				return SkillResult.failure(item != null ? "no " + item + " to eat" : "nothing safe to eat");
			}
			ItemStack stack = inventory.getItem(slot);
			FoodProperties properties = stack.get(DataComponents.FOOD);
			if (food >= 20 && !properties.canAlwaysEat()) {
				return SkillResult.success("not hungry");
			}
			eating = ItemMatcher.of(stack.getItem());
			countBefore = Inventories.count(inventory, eating);
			foodBefore = food;
		}
		if (ctx.player.containerMenu != ctx.player.inventoryMenu || ctx.mc.gui.screen() != null) {
			ctx.closeScreen();
			return null;
		}
		if (Inventories.count(inventory, eating) < countBefore || food > foodBefore) {
			JsonObject data = new JsonObject();
			data.addProperty("food", food);
			return SkillResult.success("ate " + eating, data);
		}
		if (!eating.test(ctx.player.getMainHandItem())) {
			if (!Inventories.select(ctx, eating)) {
				return SkillResult.failure("could not get " + eating + " into the hand");
			}
			return null;
		}
		// Sneaking first, so right-clicking with the crosshair on a chest or door eats instead of opening it.
		ctx.hold(MoveKey.SNEAK);
		if (ctx.player.isShiftKeyDown()) {
			ctx.holdUse();
		}
		return null;
	}

	@Override
	protected String describe() {
		return eating == null ? "looking for food" : "eating " + eating;
	}

	private static int bestFood(Inventory inventory) {
		int best = -1;
		float bestScore = 0;
		for (int slot = 0; slot < Inventories.MAIN_SIZE; slot++) {
			ItemStack stack = inventory.getItem(slot);
			FoodProperties food = stack.get(DataComponents.FOOD);
			if (food == null || RISKY.contains(stack.getItem())) {
				continue;
			}
			float score = food.nutrition() + food.saturation();
			if (score > bestScore) {
				best = slot;
				bestScore = score;
			}
		}
		return best;
	}
}
