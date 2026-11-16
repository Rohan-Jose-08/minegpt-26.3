package com.xzier9.minegpt.path;

/**
 * What the pathfinder needs to know about the world, one block cell at a time.
 *
 * Kept free of Minecraft classes so the search can be unit tested with a fake world.
 * The game-side implementation is {@code ClientBlockView}.
 */
public interface BlockView {
	/** Chunk not loaded: treated as a wall, since nothing is known about it. */
	int UNLOADED = 0;
	/** No collision: air, grass, flowers, torches. The player can stand in it. */
	int PASSABLE = 1;
	/** A full block with a solid top: can be stood on, blocks movement. */
	int SOLID = 2;
	/** Has collision but cannot be stood on safely: fences, slabs, glass panes, doors, leaves. */
	int OBSTACLE = 3;
	/** Water: can be swum through. */
	int WATER = 4;
	/** Lava, fire, magma, cactus, powder snow and similar: never entered or stood on. */
	int DANGER = 5;
	/** Ladders and vines: the player can be in the cell and climbs up or down in it. */
	int CLIMBABLE = 6;
	/** Scaffolding: climbed like a ladder, and its top can be stood on. */
	int SCAFFOLDING = 7;
	/** A wooden door or fence gate, open or shut: passed through by opening it. */
	int DOOR = 8;

	/** Still water, or not water at all. */
	int CURRENT_NONE = 0;
	/** Water flowing sideways, which pushes the player along. */
	int CURRENT_SIDEWAYS = 1;
	/** Water falling down, which drags the player under. */
	int CURRENT_DOWN = 2;

	/** One of the constants above. */
	int cell(int x, int y, int z);

	/** For water cells, how the water moves: one of the {@code CURRENT_} constants. */
	default int current(int x, int y, int z) {
		return CURRENT_NONE;
	}

	/**
	 * Game ticks needed to break the block with the best tool the player carries,
	 * or a negative number when it must not be broken (unbreakable, a container,
	 * next to lava, under water, or protected).
	 */
	float breakTicks(int x, int y, int z);

	/** False where the player must not put a block, such as inside a protected region. */
	default boolean placeable(int x, int y, int z) {
		return true;
	}
}
