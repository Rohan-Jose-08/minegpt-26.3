package com.xzier9.minegpt.client.skill;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Predicate;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.tags.TagKey;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;

/** Block ids and tags, such as {@code ["minecraft:iron_ore", "#minecraft:logs"]}. */
public final class BlockMatcher implements Predicate<BlockState> {
	private final List<String> specs;
	private final Set<Block> blocks;
	private final List<TagKey<Block>> tags;

	private BlockMatcher(List<String> specs, Set<Block> blocks, List<TagKey<Block>> tags) {
		this.specs = specs;
		this.blocks = blocks;
		this.tags = tags;
	}

	public static BlockMatcher parse(List<String> specs) {
		Set<Block> blocks = new HashSet<>();
		List<TagKey<Block>> tags = new ArrayList<>();
		List<String> names = new ArrayList<>();
		for (String spec : specs) {
			if (spec.startsWith("#")) {
				Identifier id = Identifier.tryParse(spec.substring(1));
				if (id == null) {
					throw new IllegalArgumentException("bad block tag \"" + spec + "\"");
				}
				tags.add(TagKey.create(Registries.BLOCK, id));
				names.add("#" + id);
			} else {
				Identifier id = Identifier.tryParse(spec);
				if (id == null || !BuiltInRegistries.BLOCK.containsKey(id)) {
					throw new IllegalArgumentException("unknown block \"" + spec + "\"");
				}
				blocks.add(BuiltInRegistries.BLOCK.getValue(id));
				names.add(id.toString());
			}
		}
		return new BlockMatcher(names, blocks, tags);
	}

	public static BlockMatcher of(Block block) {
		return new BlockMatcher(List.of(BuiltInRegistries.BLOCK.getKey(block).toString()), Set.of(block), List.of());
	}

	@Override
	public boolean test(BlockState state) {
		if (blocks.contains(state.getBlock())) {
			return true;
		}
		for (TagKey<Block> tag : tags) {
			if (state.is(tag)) {
				return true;
			}
		}
		return false;
	}

	@Override
	public String toString() {
		return specs.size() == 1 ? specs.get(0) : String.join(", ", specs);
	}
}
