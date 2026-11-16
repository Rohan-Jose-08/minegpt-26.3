package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;
import com.xzier9.minegpt.path.Goal;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.inventory.AbstractFurnaceMenu;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Blocks;

/**
 * Smelts items in a nearby furnace (placing or crafting one if needed): puts in the input and
 * enough fuel, waits, and takes the results out as they come. It tops the fuel up if it runs
 * low, and picks up where an earlier smelt left off when the input is already in the furnace.
 */
public final class SmeltSkill extends Skill {
	private enum State {
		FIND, OPEN, LOAD, WAIT
	}

	/** A fuel and how many items one of it smelts. */
	private record Fuel(ItemMatcher item, double smelts) {
	}

	/** Preferred fuels, best first. The client cannot see the server's fuel table, so these are fixed. */
	private static final List<Fuel> FUELS = List.of(
			new Fuel(ItemMatcher.of(Items.COAL), 8),
			new Fuel(ItemMatcher.of(Items.CHARCOAL), 8),
			new Fuel(ItemMatcher.of(Items.COAL_BLOCK), 80),
			new Fuel(ItemMatcher.of(Items.BLAZE_ROD), 12),
			new Fuel(ItemMatcher.of(Items.DRIED_KELP_BLOCK), 20),
			new Fuel(ItemMatcher.parse("#minecraft:planks"), 1.5),
			new Fuel(ItemMatcher.parse("#minecraft:logs_that_burn"), 1.5),
			new Fuel(ItemMatcher.of(Items.STICK), 0.5));

	private static final double REACH = 4.0;
	/** How close to get for a second try at opening a furnace that would not open from further off. */
	private static final double CLOSE_REACH = 2.5;
	private static final int SEARCH_RADIUS = 24;
	/** A furnace takes 200 ticks per item; this much longer with nothing happening means it is stuck. */
	private static final int STALL_TICKS = 260;

	private final ItemMatcher input;
	private final int wanted;
	private final ItemMatcher fuelChoice;
	private State state = State.FIND;
	private BlockPos furnace;
	/** Furnaces that could not be reached or opened. */
	private final Set<BlockPos> badFurnaces = new HashSet<>();
	private boolean closer;
	private int tries;
	private int loaded;
	private int collected;
	/** Results of an earlier batch found waiting in the furnace. */
	private int leftovers;
	private String result;
	private int idleTicks;
	private boolean opened;

	public SmeltSkill(ItemMatcher input, int wanted, ItemMatcher fuel, int maxTicks) {
		super("smelt", maxTicks);
		this.input = input;
		this.wanted = wanted;
		this.fuelChoice = fuel;
	}

	static SmeltSkill fromArgs(Args args) {
		int count = Math.max(1, Math.min(64, args.integer("count", 1)));
		ItemMatcher fuel = args.has("fuel") ? ItemMatcher.parse(args.string("fuel")) : null;
		return new SmeltSkill(ItemMatcher.parse(args.string("item")), count, fuel, args.ticks("timeout", count * 11 + 60));
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		SkillResult child = childResult();
		switch (state) {
			case FIND -> {
				return find(ctx, child);
			}
			case OPEN -> {
				if (child == null || !child.success()) {
					// Walk right up to it and try once more, then use another furnace or place one.
					if (closer) {
						badFurnaces.add(furnace);
					}
					closer = !closer;
					state = State.FIND;
					return null;
				}
				opened = true;
				state = State.LOAD;
				return null;
			}
			case LOAD -> {
				return load(ctx);
			}
			case WAIT -> {
				return await(ctx);
			}
		}
		return null;
	}

	@Override
	protected String describe() {
		return switch (state) {
			case FIND, OPEN -> "finding a furnace";
			default -> "smelting " + input + " (" + collected + "/" + loaded + ")";
		};
	}

	@Override
	protected void onStop(SkillContext ctx) {
		close(ctx);
	}

	private SkillResult find(SkillContext ctx, SkillResult child) {
		if (child != null && !child.success()) {
			if (furnace == null || ++tries > 3) {
				return finish(ctx, false, "could not get a furnace: " + child.message());
			}
			// Could not walk to that furnace; try another, or place one.
			badFurnaces.add(furnace);
			closer = false;
		}
		boolean haveInput = Inventories.count(ctx.player.getInventory(), input) > 0;
		BlockPos feet = ctx.feet();
		List<BlockPos> found = BlockSearch.find(ctx.level, feet, SEARCH_RADIUS, s -> s.is(Blocks.FURNACE), true,
				badFurnaces, 1);
		furnace = null;
		if (!found.isEmpty()) {
			// Without any input in hand, the furnace may still hold some from an earlier smelt.
			furnace = found.get(0);
			Goal reach = new Goal.Reach(furnace.getX(), furnace.getY(), furnace.getZ(), closer ? CLOSE_REACH : REACH);
			if (reach.reached(feet.getX(), feet.getY(), feet.getZ())) {
				state = State.OPEN;
				startChild(new OpenContainerSkill(furnace, menu -> menu instanceof AbstractFurnaceMenu));
			} else {
				startChild(GotoSkill.to(reach, "furnace", GotoSkill.Dig.FALLBACK, 8_000, 60 * 20));
			}
			return null;
		}
		if (!haveInput) {
			return finish(ctx, false, "no " + input + " to smelt");
		}
		if (++tries > 3) {
			return finish(ctx, false, "no furnace nearby and could not make one");
		}
		if (Inventories.find(ctx.player.getInventory(), ItemMatcher.of(Items.FURNACE)) >= 0) {
			startChild(new PlaceSkill(ItemMatcher.of(Items.FURNACE), null, 20 * 20).as(com.xzier9.minegpt.build.Ledger.Purpose.UTILITY));
		} else {
			startChild(new CraftSkill(ItemMatcher.of(Items.FURNACE), 1, 90 * 20));
		}
		return null;
	}

	private SkillResult load(SkillContext ctx) {
		if (!(ctx.player.containerMenu instanceof AbstractFurnaceMenu menu)) {
			state = State.FIND;
			return null;
		}
		Inventory inventory = ctx.player.getInventory();
		Slot in = menu.getSlot(AbstractFurnaceMenu.INGREDIENT_SLOT);
		Slot fuelSlot = menu.getSlot(AbstractFurnaceMenu.FUEL_SLOT);
		Slot out = menu.getSlot(AbstractFurnaceMenu.RESULT_SLOT);
		if (in.hasItem() && !input.test(in.getItem())) {
			return finish(ctx, false, "the furnace is busy smelting " + id(in.getItem()));
		}
		if (out.hasItem()) {
			// Results of an earlier batch: take them, and they would block the output anyway.
			leftovers += out.getItem().getCount();
			result = id(out.getItem());
			ctx.gameMode.handleContainerInput(menu.containerId, AbstractFurnaceMenu.RESULT_SLOT, 0, ContainerInput.QUICK_MOVE,
					ctx.player);
		}

		int already = in.hasItem() ? in.getItem().getCount() : 0;
		int toLoad = Math.max(0, Math.min(wanted - already, Inventories.count(inventory, input)));
		loaded = already + moveInto(ctx, menu, input, AbstractFurnaceMenu.INGREDIENT_SLOT, toLoad);
		if (loaded == 0) {
			if (leftovers > 0) {
				return finish(ctx, true, "took " + leftovers + " " + result + " left in the furnace; no more " + input
						+ " to smelt");
			}
			return finish(ctx, false, "no " + input + " to smelt");
		}

		if (fuelSlot.hasItem()) {
			Fuel current = fuelFor(fuelSlot.getItem());
			int needed = (int) Math.ceil(loaded / current.smelts()) - fuelSlot.getItem().getCount();
			if (needed > 0) {
				moveInto(ctx, menu, current.item(), AbstractFurnaceMenu.FUEL_SLOT, needed);
			}
		} else if (!addFuel(ctx, menu, loaded) && !menu.isLit()) {
			return finish(ctx, false, "no fuel: bring coal, charcoal, planks, logs or sticks");
		}
		state = State.WAIT;
		idleTicks = 0;
		return null;
	}

	/**
	 * Puts fuel for {@code items} more items into the empty fuel slot: the first fuel the player
	 * has enough of, or else the one that covers the most. False if there is no fuel at all.
	 */
	private boolean addFuel(SkillContext ctx, AbstractFurnaceMenu menu, int items) {
		Fuel fuel = chooseFuel(ctx.player.getInventory(), items);
		if (fuel == null) {
			return false;
		}
		moveInto(ctx, menu, fuel.item(), AbstractFurnaceMenu.FUEL_SLOT, (int) Math.ceil(items / fuel.smelts()));
		return true;
	}

	private SkillResult await(SkillContext ctx) {
		if (!(ctx.player.containerMenu instanceof AbstractFurnaceMenu menu)) {
			return finish(ctx, collected > 0, "the furnace was closed after smelting " + collected);
		}
		Slot out = menu.getSlot(AbstractFurnaceMenu.RESULT_SLOT);
		if (out.hasItem()) {
			collected += out.getItem().getCount();
			result = id(out.getItem());
			ctx.gameMode.handleContainerInput(menu.containerId, AbstractFurnaceMenu.RESULT_SLOT, 0, ContainerInput.QUICK_MOVE,
					ctx.player);
			idleTicks = 0;
		}
		if (collected >= loaded) {
			return finish(ctx, true, "smelted " + collected + " " + input + (result == null ? "" : " into " + result)
					+ (leftovers > 0 ? ", and took " + leftovers + " more left in the furnace" : ""));
		}
		Slot in = menu.getSlot(AbstractFurnaceMenu.INGREDIENT_SLOT);
		boolean inputLeft = in.hasItem();
		if (inputLeft && !menu.getSlot(AbstractFurnaceMenu.FUEL_SLOT).hasItem()) {
			// The fuel put in will not last: add more for what is left, of whatever kind there is.
			addFuel(ctx, menu, in.getItem().getCount());
		}
		if (!inputLeft && !out.hasItem() && ++idleTicks > 40) {
			return finish(ctx, collected > 0, "smelted " + collected + " of " + loaded);
		}
		if (inputLeft && !menu.isLit() && ++idleTicks > STALL_TICKS) {
			return finish(ctx, false, "the furnace went out after " + collected + "; it needs more fuel");
		}
		return null;
	}

	/** Moves up to {@code amount} matching items from the player's slots into a furnace slot. */
	private static int moveInto(SkillContext ctx, AbstractFurnaceMenu menu, ItemMatcher what, int dest, int amount) {
		Inventory inventory = ctx.player.getInventory();
		int moved = 0;
		for (int i = 0; i < menu.slots.size() && moved < amount; i++) {
			Slot slot = menu.slots.get(i);
			if (!Inventories.isPlayerSlot(slot, inventory) || !what.test(slot.getItem())) {
				continue;
			}
			// Pick up the stack, drop what is needed into the furnace, and put the rest back.
			// The client applies each click at once, so the cursor can be checked in between.
			ctx.gameMode.handleContainerInput(menu.containerId, i, 0, ContainerInput.PICKUP, ctx.player);
			int carried = menu.getCarried().getCount();
			int want = amount - moved;
			if (carried <= want) {
				ctx.gameMode.handleContainerInput(menu.containerId, dest, 0, ContainerInput.PICKUP, ctx.player);
				moved += carried - menu.getCarried().getCount();
			} else {
				for (int k = 0; k < want; k++) {
					ctx.gameMode.handleContainerInput(menu.containerId, dest, 1, ContainerInput.PICKUP, ctx.player);
				}
				moved += carried - menu.getCarried().getCount();
			}
			if (!menu.getCarried().isEmpty()) {
				ctx.gameMode.handleContainerInput(menu.containerId, i, 0, ContainerInput.PICKUP, ctx.player);
			}
		}
		return moved;
	}

	private Fuel chooseFuel(Inventory inventory, int items) {
		if (fuelChoice != null) {
			return Inventories.find(inventory, fuelChoice) >= 0 ? new Fuel(fuelChoice, fuelFor(firstOf(inventory, fuelChoice)).smelts())
					: null;
		}
		Fuel most = null;
		double mostSmelts = 0;
		for (Fuel fuel : FUELS) {
			// Never burn what is being smelted, such as logs being turned into charcoal.
			int slot = Inventories.find(inventory, s -> fuel.item().test(s) && !input.test(s));
			if (slot < 0) {
				continue;
			}
			Fuel exact = new Fuel(ItemMatcher.of(inventory.getItem(slot).getItem()), fuel.smelts());
			double smelts = Inventories.count(inventory, exact.item()) * exact.smelts();
			if (smelts >= items) {
				return exact;
			}
			if (smelts > mostSmelts) {
				most = exact;
				mostSmelts = smelts;
			}
		}
		return most;
	}

	private static ItemStack firstOf(Inventory inventory, ItemMatcher match) {
		int slot = Inventories.find(inventory, match);
		return slot < 0 ? ItemStack.EMPTY : inventory.getItem(slot);
	}

	private static Fuel fuelFor(ItemStack stack) {
		for (Fuel fuel : FUELS) {
			if (fuel.item().test(stack)) {
				return new Fuel(ItemMatcher.of(stack.getItem()), fuel.smelts());
			}
		}
		return new Fuel(ItemMatcher.of(stack.getItem()), 1);
	}

	private SkillResult finish(SkillContext ctx, boolean success, String message) {
		close(ctx);
		JsonObject data = new JsonObject();
		data.addProperty("smelted", collected);
		data.addProperty("leftovers", leftovers);
		if (result != null) {
			data.addProperty("result", result);
		}
		return new SkillResult(success, message, data);
	}

	private void close(SkillContext ctx) {
		if (opened && ctx.player.containerMenu instanceof AbstractFurnaceMenu) {
			ctx.player.closeContainer();
		}
		opened = false;
	}

	private static String id(ItemStack stack) {
		return BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
	}
}
