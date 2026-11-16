package com.xzier9.minegpt.path;

/**
 * Decides when to drop the target being headed for in favour of another. Without it a
 * chooser that picks "the nearest" afresh each time flips between two targets about equally
 * far away and reaches neither. A target is kept unless something more urgent turns up, or
 * something much closer does after the target has been given a fair go.
 */
public final class Commitment {
	/** A rival must cost less than this share of what is left to the current target. */
	public static final double MUCH_CLOSER = 0.5;

	private final int holdTicks;
	private final double minSaving;
	private long committedAt = Long.MIN_VALUE;

	/**
	 * @param holdTicks ticks after committing during which only something more urgent may take over
	 * @param minSaving the least a switch must save, in the same unit as the costs compared
	 */
	public Commitment(int holdTicks, double minSaving) {
		this.holdTicks = holdTicks;
		this.minSaving = minSaving;
	}

	/** Call when a target is chosen. */
	public void commit(long now) {
		committedAt = now;
	}

	/**
	 * True if the rival should replace the current target.
	 *
	 * @param currentCost what is left to the current target; distance or path cost
	 * @param rivalCost   the same for the rival, in the same unit
	 * @param currentPriority,rivalPriority higher is more urgent, such as a mob in striking range
	 */
	public boolean shouldSwitch(long now, double currentCost, int currentPriority, double rivalCost, int rivalPriority) {
		if (rivalPriority != currentPriority) {
			return rivalPriority > currentPriority;
		}
		if (committedAt != Long.MIN_VALUE && now >= committedAt && now - committedAt < holdTicks) {
			return false;
		}
		return rivalCost < currentCost * MUCH_CLOSER && currentCost - rivalCost >= minSaving;
	}
}
