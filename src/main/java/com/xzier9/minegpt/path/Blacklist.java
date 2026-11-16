package com.xzier9.minegpt.path;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Targets that could not be reached, left alone for a while so they are not picked again
 * straight away. Each further failure of the same target doubles its time out, up to eight
 * times the base. Times are game ticks; a clock that jumps backwards (another world) clears
 * the list.
 */
public final class Blacklist<K> {
	private static final int MAX_DOUBLINGS = 3;

	private record Entry(long until, int strikes, long forgetAt) {
	}

	private final long baseTicks;
	private final Map<K, Entry> entries = new HashMap<>();
	private long lastNow = Long.MIN_VALUE;

	public Blacklist(long baseTicks) {
		this.baseTicks = baseTicks;
	}

	/** Rules a target out from now. Returns for how many ticks. */
	public long add(K key, long now) {
		prune(now);
		Entry old = entries.get(key);
		int strikes = old == null ? 1 : Math.min(old.strikes() + 1, MAX_DOUBLINGS + 1);
		long ticks = baseTicks << (strikes - 1);
		// Strikes are remembered for a while after the time out ends, so a target that fails
		// again soon after being let back in is ruled out for longer.
		entries.put(key, new Entry(now + ticks, strikes, now + ticks * 3));
		return ticks;
	}

	public boolean contains(K key, long now) {
		prune(now);
		Entry entry = entries.get(key);
		return entry != null && entry.until() > now;
	}

	/** Everything ruled out right now. */
	public List<K> active(long now) {
		prune(now);
		List<K> keys = new ArrayList<>();
		for (Map.Entry<K, Entry> e : entries.entrySet()) {
			if (e.getValue().until() > now) {
				keys.add(e.getKey());
			}
		}
		return keys;
	}

	/** Lets a target back in, as after reaching it. */
	public void remove(K key) {
		entries.remove(key);
	}

	public void clear() {
		entries.clear();
	}

	public int size(long now) {
		return active(now).size();
	}

	private void prune(long now) {
		if (now < lastNow) {
			entries.clear();
		}
		lastNow = now;
		entries.values().removeIf(e -> e.forgetAt() <= now);
	}
}
