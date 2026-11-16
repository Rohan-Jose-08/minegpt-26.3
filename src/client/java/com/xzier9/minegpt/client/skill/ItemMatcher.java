package com.xzier9.minegpt.client.skill;

import java.util.function.Predicate;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.tags.TagKey;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;

/** An item id such as {@code minecraft:oak_planks}, or a tag such as {@code #minecraft:planks}. */
public final class ItemMatcher implements Predicate<ItemStack> {
	private final String spec;
	private final Item item;
	private final TagKey<Item> tag;

	private ItemMatcher(String spec, Item item, TagKey<Item> tag) {
		this.spec = spec;
		this.item = item;
		this.tag = tag;
	}

	public static ItemMatcher parse(String spec) {
		if (spec.startsWith("#")) {
			Identifier id = Identifier.tryParse(spec.substring(1));
			if (id == null) {
				throw new IllegalArgumentException("bad item tag \"" + spec + "\"");
			}
			return new ItemMatcher(spec, null, TagKey.create(Registries.ITEM, id));
		}
		Identifier id = Identifier.tryParse(spec);
		if (id == null || !BuiltInRegistries.ITEM.containsKey(id)) {
			throw new IllegalArgumentException("unknown item \"" + spec + "\"");
		}
		return new ItemMatcher(id.toString(), BuiltInRegistries.ITEM.getValue(id), null);
	}

	public static ItemMatcher of(Item item) {
		return new ItemMatcher(BuiltInRegistries.ITEM.getKey(item).toString(), item, null);
	}

	@Override
	public boolean test(ItemStack stack) {
		if (stack.isEmpty()) {
			return false;
		}
		return item != null ? stack.is(item) : stack.is(tag);
	}

	/** The single item this matches, or null for a tag. */
	public Item item() {
		return item;
	}

	@Override
	public String toString() {
		return spec;
	}
}
