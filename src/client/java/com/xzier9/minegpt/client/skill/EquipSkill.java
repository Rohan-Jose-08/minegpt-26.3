package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.tags.ItemTags;
import net.minecraft.tags.TagKey;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.equipment.Equippable;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;

/**
 * Puts something in the player's hand (an item, the best tool of a kind, or the best tool
 * for a block), or puts on the best armour carried.
 */
public final class EquipSkill extends Skill {
	private enum Mode {
		ITEM, TOOL, BLOCK, ARMOR
	}

	private static final EquipmentSlot[] ARMOR_SLOTS = {
			EquipmentSlot.HEAD, EquipmentSlot.CHEST, EquipmentSlot.LEGS, EquipmentSlot.FEET};

	private final Mode mode;
	private final ItemMatcher item;
	private final String tool;
	private final Block block;

	private EquipSkill(Mode mode, ItemMatcher item, String tool, Block block) {
		super("equip", 5 * 20);
		this.mode = mode;
		this.item = item;
		this.tool = tool;
		this.block = block;
	}

	static EquipSkill fromArgs(Args args) {
		if (args.bool("armor", false)) {
			return new EquipSkill(Mode.ARMOR, null, null, null);
		}
		if (args.has("item")) {
			return new EquipSkill(Mode.ITEM, ItemMatcher.parse(args.string("item")), null, null);
		}
		if (args.has("tool")) {
			String tool = args.string("tool");
			if (toolTag(tool) == null) {
				throw args.bad("tool must be pickaxe, axe, shovel, hoe or sword");
			}
			return new EquipSkill(Mode.TOOL, null, tool, null);
		}
		if (args.has("block")) {
			Identifier id = Identifier.tryParse(args.string("block"));
			if (id == null || !BuiltInRegistries.BLOCK.containsKey(id)) {
				throw args.bad("unknown block \"" + args.string("block") + "\"");
			}
			return new EquipSkill(Mode.BLOCK, null, null, BuiltInRegistries.BLOCK.getValue(id));
		}
		throw args.bad("give item, tool, block, or armor: true");
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		if (ctx.player.containerMenu != ctx.player.inventoryMenu) {
			ctx.closeScreen();
			return null;
		}
		Inventory inventory = ctx.player.getInventory();
		return switch (mode) {
			case ITEM -> Inventories.select(ctx, item)
					? holding(ctx, "holding " + item)
					: SkillResult.failure("no " + item + " in the inventory");
			case TOOL -> {
				int slot = bestTool(inventory, tool);
				if (slot < 0) {
					yield SkillResult.failure("no " + tool + " in the inventory");
				}
				Inventories.select(ctx, slot);
				yield holding(ctx, "holding the best " + tool);
			}
			case BLOCK -> {
				BlockState state = block.defaultBlockState();
				float hardness = Math.max(0, state.getDestroySpeed(ctx.level, ctx.player.blockPosition()));
				Inventories.select(ctx, Mining.bestToolSlot(inventory, state, hardness));
				yield holding(ctx, "holding the best tool for " + BuiltInRegistries.BLOCK.getKey(block));
			}
			case ARMOR -> wearArmor(ctx);
		};
	}

	@Override
	protected String describe() {
		return "equipping";
	}

	private static SkillResult holding(SkillContext ctx, String message) {
		JsonObject data = new JsonObject();
		data.addProperty("slot", ctx.player.getInventory().getSelectedSlot());
		return SkillResult.success(message, data);
	}

	private static SkillResult wearArmor(SkillContext ctx) {
		Inventory inventory = ctx.player.getInventory();
		int worn = 0;
		for (EquipmentSlot armorSlot : ARMOR_SLOTS) {
			if (!ctx.player.getItemBySlot(armorSlot).isEmpty()) {
				continue;
			}
			int best = -1;
			double bestArmor = 0;
			for (int slot = 0; slot < Inventories.MAIN_SIZE; slot++) {
				ItemStack stack = inventory.getItem(slot);
				Equippable equippable = stack.get(DataComponents.EQUIPPABLE);
				if (equippable == null || equippable.slot() != armorSlot) {
					continue;
				}
				double armor = armorValue(stack, armorSlot);
				if (armor > bestArmor) {
					best = slot;
					bestArmor = armor;
				}
			}
			if (best >= 0) {
				// Shift-clicking armour in the inventory puts it on.
				int menuSlot = Inventories.menuSlot(ctx.player.inventoryMenu, inventory, best);
				ctx.gameMode.handleContainerInput(ctx.player.inventoryMenu.containerId, menuSlot, 0,
						ContainerInput.QUICK_MOVE, ctx.player);
				worn++;
			}
		}
		JsonObject data = new JsonObject();
		data.addProperty("worn", worn);
		return SkillResult.success(worn == 0 ? "no better armour to put on" : "put on " + worn + " pieces of armour", data);
	}

	private static double armorValue(ItemStack stack, EquipmentSlot slot) {
		double[] total = {0};
		stack.forEachModifier(slot, (attribute, modifier) -> {
			if (attribute.value() == Attributes.ARMOR.value()) {
				total[0] += modifier.amount();
			}
		});
		return total[0];
	}

	private static TagKey<Item> toolTag(String tool) {
		return switch (tool) {
			case "pickaxe" -> ItemTags.PICKAXES;
			case "axe" -> ItemTags.AXES;
			case "shovel" -> ItemTags.SHOVELS;
			case "hoe" -> ItemTags.HOES;
			case "sword" -> ItemTags.SWORDS;
			default -> null;
		};
	}

	/** The best tool of a kind: the one that mines the hardest blocks, then the fastest. */
	private static int bestTool(Inventory inventory, String tool) {
		TagKey<Item> tag = toolTag(tool);
		BlockState[] tiers = switch (tool) {
			case "pickaxe" -> new BlockState[] {Blocks.OBSIDIAN.defaultBlockState(), Blocks.DIAMOND_ORE.defaultBlockState(),
					Blocks.IRON_ORE.defaultBlockState(), Blocks.STONE.defaultBlockState()};
			case "axe" -> new BlockState[] {Blocks.OAK_LOG.defaultBlockState()};
			case "shovel" -> new BlockState[] {Blocks.DIRT.defaultBlockState()};
			case "hoe" -> new BlockState[] {Blocks.HAY_BLOCK.defaultBlockState()};
			default -> new BlockState[0];
		};
		int best = -1;
		double bestScore = -1;
		for (int slot = 0; slot < Inventories.MAIN_SIZE; slot++) {
			ItemStack stack = inventory.getItem(slot);
			if (!stack.is(tag)) {
				continue;
			}
			double score;
			if (tiers.length == 0) {
				score = Inventories.attackDamage(stack);
			} else {
				score = 0;
				for (BlockState state : tiers) {
					if (stack.isCorrectToolForDrops(state)) {
						score += 100;
					}
				}
				score += stack.getDestroySpeed(tiers[tiers.length - 1]);
			}
			if (score > bestScore) {
				best = slot;
				bestScore = score;
			}
		}
		return best;
	}
}
