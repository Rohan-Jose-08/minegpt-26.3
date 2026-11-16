package com.xzier9.minegpt.client.skill;

import com.xzier9.minegpt.build.Turning;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.block.AbstractFurnaceBlock;
import net.minecraft.world.level.block.BedBlock;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.ChestBlock;
import net.minecraft.world.level.block.DoorBlock;
import net.minecraft.world.level.block.EnderChestBlock;
import net.minecraft.world.level.block.FenceGateBlock;
import net.minecraft.world.level.block.LadderBlock;
import net.minecraft.world.level.block.RotatedPillarBlock;
import net.minecraft.world.level.block.SlabBlock;
import net.minecraft.world.level.block.StairBlock;
import net.minecraft.world.level.block.TrapDoorBlock;
import net.minecraft.world.level.block.WallTorchBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;

/**
 * Reads which way a block in the world is turned, and checks it against how it was asked for.
 * The rules themselves are in {@link Turning}; this class only translates the game's blocks
 * and block states into its terms.
 */
public final class BlockStates {
	/** The properties that say how a block is turned, in the order they are written out. */
	private static final String[] SHOWN = {"facing", "half", "type", "axis", "hinge", "shape", "part", "open"};

	private BlockStates() {
	}

	/** How this kind of block takes its direction when placed. */
	public static Turning.Family family(Block block) {
		if (block instanceof StairBlock || block instanceof DoorBlock || block instanceof BedBlock
				|| block instanceof FenceGateBlock) {
			return Turning.Family.WITH_LOOK;
		}
		if (block instanceof ChestBlock || block instanceof EnderChestBlock || block instanceof AbstractFurnaceBlock) {
			return Turning.Family.AGAINST_LOOK;
		}
		if (block instanceof LadderBlock || block instanceof WallTorchBlock) {
			return Turning.Family.HANGING;
		}
		if (block instanceof RotatedPillarBlock) {
			return Turning.Family.PILLAR;
		}
		if (block instanceof SlabBlock) {
			return Turning.Family.SLAB;
		}
		return Turning.Family.PLAIN;
	}

	/** The state a block of this kind has when it is placed as {@code want} asks. */
	public static Map<String, String> expected(Block block, PlaceSkill.Want want) {
		return Turning.expected(family(block), name(want.look()), want.top(), name(want.against()),
				want.axis() == null ? null : want.axis().getName(),
				block instanceof StairBlock || block instanceof TrapDoorBlock);
	}

	/** What is wrong with how the block is turned, or null when it is as asked (or is another block altogether). */
	public static String mismatch(BlockState state, Block block, PlaceSkill.Want want) {
		if (!state.is(block)) {
			return null;
		}
		Map<String, String> expected = expected(block, want);
		return expected.isEmpty() ? null : Turning.mismatch(expected, properties(state));
	}

	/** True if the block there is the one wanted and turned as asked. */
	public static boolean matches(BlockState state, Block block, PlaceSkill.Want want) {
		return state.is(block) && mismatch(state, block, want) == null;
	}

	/** The block's properties by name. A slab's {@code type} is given as {@code half}, as {@link Turning} wants it. */
	public static Map<String, String> properties(BlockState state) {
		Map<String, String> out = new LinkedHashMap<>();
		for (Property<?> property : state.getProperties()) {
			String name = property.getName();
			if (name.equals("type") && state.getBlock() instanceof SlabBlock) {
				name = "half";
			}
			out.put(name, value(state, property));
		}
		return out;
	}

	/**
	 * The block's id with how it is turned, like {@code minecraft:oak_stairs[facing=east,half=bottom]}.
	 * Blocks with nothing to turn come out as the bare id.
	 */
	public static String describe(BlockState state) {
		String id = BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString();
		String turned = turned(state);
		return turned.isEmpty() ? id : id + "[" + turned + "]";
	}

	/** Just the turning properties, like {@code facing=east,half=bottom}; empty when there are none. */
	public static String turned(BlockState state) {
		StringBuilder out = new StringBuilder();
		for (String shown : SHOWN) {
			for (Property<?> property : state.getProperties()) {
				if (property.getName().equals(shown)) {
					if (out.length() > 0) {
						out.append(',');
					}
					out.append(shown).append('=').append(value(state, property));
				}
			}
		}
		return out.toString();
	}

	/** The wanted turning written the same way, for messages. */
	public static String describe(Map<String, String> expected) {
		StringBuilder out = new StringBuilder();
		expected.forEach((k, v) -> out.append(out.length() > 0 ? "," : "").append(k).append('=').append(v));
		return out.toString();
	}

	private static <T extends Comparable<T>> String value(BlockState state, Property<T> property) {
		return property.getName(state.getValue(property)).toLowerCase(Locale.ROOT);
	}

	private static String name(Direction direction) {
		return direction == null ? null : direction.getName();
	}
}
