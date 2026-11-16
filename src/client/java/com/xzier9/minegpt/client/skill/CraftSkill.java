package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;
import com.xzier9.minegpt.path.Goal;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import net.minecraft.client.gui.screens.inventory.InventoryScreen;
import net.minecraft.core.BlockPos;
import net.minecraft.util.context.ContextMap;
import net.minecraft.world.entity.player.StackedItemContents;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.inventory.CraftingMenu;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.crafting.display.RecipeDisplayEntry;
import net.minecraft.world.item.crafting.display.SlotDisplayContext;
import net.minecraft.world.level.block.Blocks;

/**
 * Crafts an item through the recipe book, the same request the game sends when a player
 * clicks a recipe there. Small recipes use the inventory grid; bigger ones walk to a nearby
 * crafting table, placing one (and crafting it first) if there is none.
 */
public final class CraftSkill extends Skill {
	private enum State {
		CHOOSE, FIND_TABLE, OPEN_TABLE, PLACE_RECIPE, TAKE_RESULT
	}

	private static final double REACH = 4.0;
	/** How close to get for a second try at opening a table that would not open from further off. */
	private static final double CLOSE_REACH = 2.5;
	private static final int TABLE_SEARCH = 24;
	private static final int RESULT_WAIT = 20;

	private final ItemMatcher item;
	private final int wanted;
	private State state = State.CHOOSE;
	private int target = -1;
	private int startCount;
	private RecipeDisplayEntry recipe;
	private BlockPos table;
	/** Tables that could not be reached or opened, left for another or a new one. */
	private final Set<BlockPos> badTables = new HashSet<>();
	private boolean closer;
	private int waitTicks;
	private int tableTries;
	private boolean openedScreen;

	public CraftSkill(ItemMatcher item, int wanted, int maxTicks) {
		super("craft", maxTicks);
		this.item = item;
		this.wanted = wanted;
	}

	static CraftSkill fromArgs(Args args) {
		return new CraftSkill(ItemMatcher.parse(args.string("item")), Math.max(1, Math.min(576, args.integer("count", 1))),
				args.ticks("timeout", 120));
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		SkillResult child = childResult();
		int have = Inventories.count(ctx.player.getInventory(), item);
		if (target < 0) {
			startCount = have;
			target = have + wanted;
		}
		if (have >= target) {
			return finish(ctx, true, "crafted " + (have - startCount) + " " + item);
		}

		switch (state) {
			case CHOOSE -> {
				recipe = chooseRecipe(ctx);
				if (recipe == null) {
					return finish(ctx, false, whyNot(ctx));
				}
				if (ctx.player.containerMenu instanceof CraftingMenu) {
					// A crafting table is already open; it takes small recipes too.
					state = State.PLACE_RECIPE;
				} else if (!Recipes.needsTable(recipe.display())) {
					if (ctx.player.containerMenu != ctx.player.inventoryMenu) {
						ctx.closeScreen();
						return null;
					}
					if (ctx.mc.gui.screen() == null) {
						// Purely for show: the inventory screen opens on the client only.
						ctx.mc.gui.setScreen(new InventoryScreen(ctx.player));
						openedScreen = true;
					}
					state = State.PLACE_RECIPE;
				} else {
					state = State.FIND_TABLE;
				}
				return null;
			}
			case FIND_TABLE -> {
				return findTable(ctx, child);
			}
			case OPEN_TABLE -> {
				if (child == null || !child.success()) {
					// Walk right up to it and try once more, then use another table or place one.
					if (closer) {
						badTables.add(table);
					}
					closer = !closer;
					state = State.FIND_TABLE;
					return null;
				}
				openedScreen = true;
				state = State.PLACE_RECIPE;
				return null;
			}
			case PLACE_RECIPE -> {
				AbstractContainerMenu menu = craftingMenu(ctx);
				if (menu == null) {
					state = State.CHOOSE;
					return null;
				}
				if (!recipe.canCraft(Recipes.contents(ctx.player))) {
					// Used up one variant (say oak planks); choose again, possibly another one.
					state = State.CHOOSE;
					return null;
				}
				ctx.gameMode.handlePlaceRecipe(menu.containerId, recipe.id(), false);
				state = State.TAKE_RESULT;
				waitTicks = 0;
				return null;
			}
			case TAKE_RESULT -> {
				AbstractContainerMenu menu = craftingMenu(ctx);
				if (menu == null) {
					state = State.CHOOSE;
					return null;
				}
				Slot result = menu.getSlot(0);
				if (result.hasItem() && item.test(result.getItem())) {
					// Shift-click takes one craft's worth, since only one set of ingredients was placed.
					ctx.gameMode.handleContainerInput(menu.containerId, 0, 0, ContainerInput.QUICK_MOVE, ctx.player);
					state = State.PLACE_RECIPE;
					return null;
				}
				if (++waitTicks > RESULT_WAIT) {
					return finish(ctx, false, "the game did not accept the recipe for " + item);
				}
				return null;
			}
		}
		return null;
	}

	@Override
	protected String describe() {
		return state == State.FIND_TABLE || state == State.OPEN_TABLE
				? "finding a crafting table for " + item
				: "crafting " + item;
	}

	@Override
	protected void onStop(SkillContext ctx) {
		closeOurScreen(ctx);
	}

	private SkillResult findTable(SkillContext ctx, SkillResult child) {
		if (child != null && !child.success()) {
			if (table == null || ++tableTries > 3) {
				return finish(ctx, false, "could not get a crafting table: " + child.message());
			}
			// Could not walk to that table; try another, or place one.
			badTables.add(table);
			closer = false;
		}
		BlockPos feet = ctx.feet();
		List<BlockPos> tables = BlockSearch.find(ctx.level, feet, TABLE_SEARCH, s -> s.is(Blocks.CRAFTING_TABLE), true,
				badTables, 1);
		table = null;
		if (!tables.isEmpty()) {
			table = tables.get(0);
			Goal reach = new Goal.Reach(table.getX(), table.getY(), table.getZ(), closer ? CLOSE_REACH : REACH);
			if (reach.reached(feet.getX(), feet.getY(), feet.getZ())) {
				state = State.OPEN_TABLE;
				startChild(new OpenContainerSkill(table, menu -> menu instanceof CraftingMenu));
			} else {
				startChild(GotoSkill.to(reach, "crafting table", GotoSkill.Dig.FALLBACK, 8_000, 60 * 20));
			}
			return null;
		}
		if (++tableTries > 3) {
			return finish(ctx, false, "no crafting table nearby and could not make one");
		}
		if (Inventories.find(ctx.player.getInventory(), ItemMatcher.of(Items.CRAFTING_TABLE)) >= 0) {
			startChild(new PlaceSkill(ItemMatcher.of(Items.CRAFTING_TABLE), null, 20 * 20).as(com.xzier9.minegpt.build.Ledger.Purpose.UTILITY));
		} else {
			startChild(new CraftSkill(ItemMatcher.of(Items.CRAFTING_TABLE), 1, 30 * 20));
		}
		return null;
	}

	/** A craftable recipe for the item, or null. */
	private RecipeDisplayEntry chooseRecipe(SkillContext ctx) {
		ContextMap context = SlotDisplayContext.fromLevel(ctx.level);
		StackedItemContents contents = Recipes.contents(ctx.player);
		for (RecipeDisplayEntry entry : Recipes.find(ctx.player, context, item)) {
			if (entry.canCraft(contents)) {
				return entry;
			}
		}
		return null;
	}

	private String whyNot(SkillContext ctx) {
		List<RecipeDisplayEntry> known = Recipes.find(ctx.player, SlotDisplayContext.fromLevel(ctx.level), item);
		if (known.isEmpty()) {
			return "no unlocked recipe makes " + item + " (recipes unlock as you pick up their ingredients)";
		}
		return "missing ingredients for " + item + ": needs " + Recipes.ingredients(known.get(0));
	}

	/** The open crafting grid: a crafting table, or the inventory's own 2x2 grid. */
	private static AbstractContainerMenu craftingMenu(SkillContext ctx) {
		AbstractContainerMenu menu = ctx.player.containerMenu;
		return menu instanceof CraftingMenu || menu == ctx.player.inventoryMenu ? menu : null;
	}

	private SkillResult finish(SkillContext ctx, boolean success, String message) {
		closeOurScreen(ctx);
		JsonObject data = new JsonObject();
		data.addProperty("crafted", Inventories.count(ctx.player.getInventory(), item) - Math.max(0, startCount));
		return new SkillResult(success, message, data);
	}

	private void closeOurScreen(SkillContext ctx) {
		if (openedScreen && ctx.mc.gui.screen() != null) {
			ctx.player.closeContainer();
		}
		openedScreen = false;
	}
}
