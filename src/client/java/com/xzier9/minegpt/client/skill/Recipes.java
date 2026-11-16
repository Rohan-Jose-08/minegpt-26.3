package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Predicate;
import net.minecraft.client.gui.screens.recipebook.RecipeCollection;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.util.context.ContextMap;
import net.minecraft.world.entity.player.StackedItemContents;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.crafting.Ingredient;
import net.minecraft.world.item.crafting.display.RecipeDisplay;
import net.minecraft.world.item.crafting.display.RecipeDisplayEntry;
import net.minecraft.world.item.crafting.display.ShapedCraftingRecipeDisplay;
import net.minecraft.world.item.crafting.display.ShapelessCraftingRecipeDisplay;
import net.minecraft.world.item.crafting.display.SlotDisplayContext;

/**
 * Crafting recipes as the client knows them: the player's recipe book. Since 1.21.2 the server
 * only sends recipes the player has unlocked, which happens as they pick up ingredients.
 */
public final class Recipes {
	private Recipes() {
	}

	/** Crafting-table and inventory recipes, as opposed to furnace or stonecutter ones. */
	public static boolean isCrafting(RecipeDisplay display) {
		return display instanceof ShapedCraftingRecipeDisplay || display instanceof ShapelessCraftingRecipeDisplay;
	}

	/** True if the recipe does not fit the inventory's 2x2 grid. */
	public static boolean needsTable(RecipeDisplay display) {
		if (display instanceof ShapedCraftingRecipeDisplay shaped) {
			return shaped.width() > 2 || shaped.height() > 2;
		}
		if (display instanceof ShapelessCraftingRecipeDisplay shapeless) {
			return shapeless.ingredients().size() > 4;
		}
		return true;
	}

	/** Every known crafting recipe whose result matches. */
	public static List<RecipeDisplayEntry> find(LocalPlayer player, ContextMap context, Predicate<ItemStack> result) {
		List<RecipeDisplayEntry> out = new ArrayList<>();
		for (RecipeCollection collection : player.getRecipeBook().getCollections()) {
			for (RecipeDisplayEntry entry : collection.getRecipes()) {
				if (isCrafting(entry.display()) && result.test(result(entry, context))) {
					out.add(entry);
				}
			}
		}
		return out;
	}

	public static ItemStack result(RecipeDisplayEntry entry, ContextMap context) {
		List<ItemStack> results = entry.resultItems(context);
		return results.isEmpty() ? ItemStack.EMPTY : results.get(0);
	}

	public static StackedItemContents contents(LocalPlayer player) {
		StackedItemContents contents = new StackedItemContents();
		player.getInventory().fillStackedContents(contents);
		return contents;
	}

	/** Ingredients grouped by what they accept, such as "3x minecraft:oak_planks". */
	public static String ingredients(RecipeDisplayEntry entry) {
		Map<String, Integer> counts = new LinkedHashMap<>();
		for (Ingredient ingredient : entry.craftingRequirements().orElse(List.of())) {
			List<String> ids = ids(ingredient);
			String name = ids.isEmpty() ? "?" : ids.size() == 1 ? ids.get(0) : ids.get(0) + " (or " + (ids.size() - 1) + " others)";
			counts.merge(name, 1, Integer::sum);
		}
		List<String> parts = new ArrayList<>();
		counts.forEach((name, count) -> parts.add(count + "x " + name));
		return String.join(", ", parts);
	}

	public static JsonObject toJson(RecipeDisplayEntry entry, ContextMap context, StackedItemContents contents) {
		ItemStack result = result(entry, context);
		JsonObject json = new JsonObject();
		json.addProperty("result", BuiltInRegistries.ITEM.getKey(result.getItem()).toString());
		json.addProperty("count", result.getCount());
		json.addProperty("needsTable", needsTable(entry.display()));
		json.addProperty("craftable", entry.canCraft(contents));
		JsonArray ingredients = new JsonArray();
		for (Ingredient ingredient : entry.craftingRequirements().orElse(List.of())) {
			JsonArray options = new JsonArray();
			ids(ingredient).forEach(options::add);
			ingredients.add(options);
		}
		json.add("ingredients", ingredients);
		return json;
	}

	/** Recipes for the bridge, optionally only those that make a given item. */
	public static JsonArray list(LocalPlayer player, Predicate<ItemStack> filter, boolean craftableOnly) {
		ContextMap context = SlotDisplayContext.fromLevel(player.level());
		StackedItemContents contents = contents(player);
		JsonArray out = new JsonArray();
		for (RecipeDisplayEntry entry : find(player, context, filter == null ? stack -> !stack.isEmpty() : filter)) {
			if (!craftableOnly || entry.canCraft(contents)) {
				out.add(toJson(entry, context, contents));
			}
		}
		return out;
	}

	// items() is marked deprecated as a hint to prefer display(), but it is the plain list of accepted items.
	@SuppressWarnings("deprecation")
	private static List<String> ids(Ingredient ingredient) {
		List<String> ids = new ArrayList<>();
		ingredient.items().map(Holder::value).forEach((Item item) -> ids.add(BuiltInRegistries.ITEM.getKey(item).toString()));
		return ids;
	}
}
