package com.xzier9.minegpt.build;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A record of the blocks the agent itself put into one world, and why. With it the agent can
 * tell its own scaffolding from somebody's wall when both are cobblestone, take down what it
 * left behind in an earlier call or an earlier session, and repair its own work without ever
 * touching a block it did not place.
 *
 * Entries are kept in the order they were made and the oldest are dropped once there are too
 * many. An entry is only trusted while the block it names is still there: {@link #owns} is
 * given what stands at the spot now, and forgets the entry when that is something else.
 *
 * This class knows nothing of the game; positions are three ints and blocks are id strings.
 */
public final class Ledger {
	/** Why a block was placed. */
	public enum Purpose {
		/** Part of a structure, or placed on purpose with the place skill. */
		BUILD,
		/** Stood on or walked over to get somewhere: pillars and bridges. Always the agent's to remove. */
		SCAFFOLD,
		/** Put under a block so that it had something to be placed against. */
		FILL,
		/** A crafting table or furnace put down to work at. */
		UTILITY;

		/** True for blocks that were only ever a means to an end. */
		public boolean temporary() {
			return this == SCAFFOLD || this == FILL;
		}
	}

	/** One placed block. */
	public record Entry(int x, int y, int z, Purpose purpose, String block) {
	}

	public static final int DEFAULT_LIMIT = 60_000;

	private final LinkedHashMap<Long, Entry> entries = new LinkedHashMap<>();
	private final int limit;
	private boolean dirty;

	public Ledger() {
		this(DEFAULT_LIMIT);
	}

	public Ledger(int limit) {
		this.limit = limit;
	}

	private static long key(int x, int y, int z) {
		return ((long) (x & 0x3FFFFFF) << 38) | ((long) (z & 0x3FFFFFF) << 12) | (y & 0xFFF);
	}

	/** Notes a block the agent just placed. A later note for the same spot replaces the earlier one. */
	public void record(int x, int y, int z, Purpose purpose, String block) {
		long key = key(x, y, z);
		entries.remove(key);
		entries.put(key, new Entry(x, y, z, purpose, block));
		if (entries.size() > limit) {
			var oldest = entries.keySet().iterator();
			oldest.next();
			oldest.remove();
		}
		dirty = true;
	}

	/** Forgets a spot, for instance once its block has been taken down. */
	public void forget(int x, int y, int z) {
		if (entries.remove(key(x, y, z)) != null) {
			dirty = true;
		}
	}

	/** The entry for a spot as recorded, whatever stands there now, or null. */
	public Entry at(int x, int y, int z) {
		return entries.get(key(x, y, z));
	}

	/**
	 * The entry for a spot if the block standing there now is the one the agent placed, else
	 * null. A spot that has since become something else is forgotten: someone changed it.
	 *
	 * @param there the id of the block at the spot now
	 */
	public Entry owns(int x, int y, int z, String there) {
		Entry entry = entries.get(key(x, y, z));
		if (entry == null) {
			return null;
		}
		if (!entry.block().equals(there)) {
			entries.remove(key(x, y, z));
			dirty = true;
			return null;
		}
		return entry;
	}

	/** Every entry inside a box, edges included, oldest first. */
	public List<Entry> within(int minX, int minY, int minZ, int maxX, int maxY, int maxZ) {
		List<Entry> out = new ArrayList<>();
		for (Entry e : entries.values()) {
			if (e.x() >= minX && e.x() <= maxX && e.y() >= minY && e.y() <= maxY && e.z() >= minZ && e.z() <= maxZ) {
				out.add(e);
			}
		}
		return out;
	}

	public int size() {
		return entries.size();
	}

	/** True if something changed since the last {@link #lines}. */
	public boolean dirty() {
		return dirty;
	}

	/** The ledger as text, one entry a line: {@code x y z purpose block}. Clears {@link #dirty}. */
	public List<String> lines() {
		List<String> out = new ArrayList<>(entries.size());
		for (Entry e : entries.values()) {
			out.add(e.x() + " " + e.y() + " " + e.z() + " " + e.purpose().name().toLowerCase(java.util.Locale.ROOT) + " "
					+ e.block());
		}
		dirty = false;
		return out;
	}

	/** Reads what {@link #lines} wrote. Lines that do not parse are skipped. */
	public static Ledger parse(Iterable<String> lines, int limit) {
		Ledger ledger = new Ledger(limit);
		for (String line : lines) {
			String[] parts = line.trim().split(" ");
			if (parts.length != 5) {
				continue;
			}
			try {
				ledger.record(Integer.parseInt(parts[0]), Integer.parseInt(parts[1]), Integer.parseInt(parts[2]),
						Purpose.valueOf(parts[3].toUpperCase(java.util.Locale.ROOT)), parts[4]);
			} catch (IllegalArgumentException e) {
				// A damaged line: the rest of the ledger is still good.
			}
		}
		ledger.dirty = false;
		return ledger;
	}

	public Map<Purpose, Integer> counts() {
		Map<Purpose, Integer> out = new java.util.EnumMap<>(Purpose.class);
		for (Entry e : entries.values()) {
			out.merge(e.purpose(), 1, Integer::sum);
		}
		return out;
	}
}
