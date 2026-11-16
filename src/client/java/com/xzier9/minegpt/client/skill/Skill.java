package com.xzier9.minegpt.client.skill;

/**
 * A multi-tick behaviour such as walking somewhere or mining a block, built from the same
 * key presses a player would make. The agent runs one skill at a time.
 *
 * Subclasses implement {@link #update}, which runs once per tick until it returns a result.
 * A skill can hand work to a child skill with {@link #startChild}; the child then runs in
 * its parent's place, and the parent reads its outcome with {@link #childResult}.
 */
public abstract class Skill {
	private final String name;
	private final int maxTicks;
	private int ticks;
	private Skill child;
	private SkillResult childResult;

	/** @param maxTicks give up after this many ticks; 0 for no limit */
	protected Skill(String name, int maxTicks) {
		this.name = name;
		this.maxTicks = maxTicks;
	}

	public final String name() {
		return name;
	}

	/** Runs one tick. Returns null while the skill is still working. */
	public final SkillResult tick(SkillContext ctx) {
		ticks++;
		if (maxTicks > 0 && ticks > maxTicks) {
			stopChild(ctx);
			return SkillResult.failure(name + " timed out after " + maxTicks / 20 + "s");
		}
		if (child != null && interruptChild(ctx)) {
			stopChild(ctx);
		}
		if (child != null) {
			SkillResult result = child.tick(ctx);
			if (result == null) {
				return null;
			}
			child.stop(ctx);
			child = null;
			childResult = result;
		}
		return update(ctx);
	}

	/** Called once when the skill ends for any reason, including cancellation. */
	public final void stop(SkillContext ctx) {
		stopChild(ctx);
		onStop(ctx);
		OwnBlocks.saveSoon();
	}

	/** What the skill is doing right now, for the HUD. */
	public final String status() {
		return child != null ? child.status() : describe();
	}

	protected abstract SkillResult update(SkillContext ctx);

	protected abstract String describe();

	/**
	 * Called each tick while a child runs. Return true to stop the child early, for example
	 * when something worth doing instead has come into view. {@link #update} then runs with
	 * no child result.
	 */
	protected boolean interruptChild(SkillContext ctx) {
		return false;
	}

	/** Releases whatever this skill holds besides keys, such as an open screen. */
	protected void onStop(SkillContext ctx) {
	}

	protected final int ticks() {
		return ticks;
	}

	protected final void startChild(Skill skill) {
		child = skill;
		childResult = null;
	}

	protected final boolean hasChild() {
		return child != null;
	}

	/** The outcome of the child that just finished, or null. Reading it clears it. */
	protected final SkillResult childResult() {
		SkillResult result = childResult;
		childResult = null;
		return result;
	}

	protected final void stopChild(SkillContext ctx) {
		if (child != null) {
			child.stop(ctx);
			child = null;
		}
	}
}
