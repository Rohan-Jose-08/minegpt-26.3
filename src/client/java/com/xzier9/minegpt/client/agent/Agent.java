package com.xzier9.minegpt.client.agent;

import com.xzier9.minegpt.MineGPT;
import com.xzier9.minegpt.client.MineGPTClient;
import com.xzier9.minegpt.client.act.Action;
import com.xzier9.minegpt.client.act.ActionExecutor;
import com.xzier9.minegpt.client.safety.Safety;
import com.xzier9.minegpt.client.skill.Skill;
import com.xzier9.minegpt.client.skill.SkillContext;
import com.xzier9.minegpt.client.skill.SkillResult;
import java.util.function.Consumer;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.monster.Enemy;

/**
 * The agent's top-level state: what it has been told to do and whether it is allowed
 * to touch the controls. Lives on the client thread.
 */
public final class Agent {
	public enum Mode {
		/** Not controlling the player. */
		IDLE,
		/** Pressing keys on the player's behalf. */
		RUNNING,
		/** Has a goal but has let go of the controls until resumed. */
		PAUSED
	}

	/** Air, out of 300, below which a running skill is put on hold while the player swims up to breathe. */
	private static final int LOW_AIR = 150;

	private final ActionExecutor executor = new ActionExecutor();
	private Mode mode = Mode.IDLE;
	private String goal;
	private String status = "Waiting for a goal";
	private boolean remoteControlled;

	private Skill skill;
	/** Who started the skill (the bridge or a command), so only they cancel it on leaving. */
	private Object skillOwner;
	private Consumer<SkillResult> skillDone;
	private boolean surfacing;
	private final Surfacing swimmer = new Surfacing();
	/** Why the agent last paused itself (death, low health), or null if the player paused it. */
	private String pauseReason;
	private boolean throttled;
	/** False after a low-health stop until health climbs back over the line, so resuming hurt does not stop again at once. */
	private static final double LOW_HEALTH_DANGER = 8.0;
	private boolean healthStopArmed = true;
	/** Whether the skill was already stopped for low health while a mob was close. */
	private boolean lowHealthInFight;

	public Mode mode() {
		return mode;
	}

	public String goal() {
		return goal;
	}

	public String status() {
		if (throttled && mode == Mode.RUNNING) {
			return "Waiting: too many actions this minute";
		}
		if (skill == null) {
			return status;
		}
		return skill.name() + ": " + (surfacing ? "coming up for air" : skill.status());
	}

	public Skill skill() {
		return skill;
	}

	/** Why the agent paused itself, such as low health; null when the player paused it or it is not paused. */
	public String pauseReason() {
		return mode == Mode.PAUSED ? pauseReason : null;
	}

	public void setStatus(String status) {
		this.status = status;
	}

	public void setGoal(String goal) {
		this.goal = goal;
		MineGPT.LOGGER.info("New goal: {}", goal);
	}

	/** True while the Python brain is connected, so the agent stays in charge between its steps. */
	public void setRemoteControlled(boolean remoteControlled) {
		this.remoteControlled = remoteControlled;
		if (remoteControlled && goal == null) {
			status = "Brain connected";
		} else if (!remoteControlled) {
			status = goal == null ? "Waiting for a goal" : "Brain disconnected";
		}
	}

	/** Releases every key without changing the goal, for when the brain goes away. */
	public void releaseControls(Minecraft mc) {
		executor.releaseAll(mc);
		if (mode == Mode.RUNNING && goal == null) {
			mode = Mode.IDLE;
		}
	}

	/** Called by the brain when it has achieved (or given up on) the goal. */
	public void finishGoal(Minecraft mc, String message) {
		MineGPT.LOGGER.info("Goal finished: {} ({})", goal, message);
		cancelSkill(mc, "the goal was finished");
		goal = null;
		status = message;
		executor.releaseAll(mc);
		mode = Mode.IDLE;
	}

	public void start() {
		mode = Mode.RUNNING;
	}

	public void pause(Minecraft mc) {
		if (mode == Mode.RUNNING) {
			mode = Mode.PAUSED;
			pauseReason = null;
			executor.releaseAll(mc);
		}
	}

	/** Pauses on the agent's own account, keeping the goal, so the player decides what happens next. */
	private void pauseFor(Minecraft mc, String reason) {
		pause(mc);
		pauseReason = reason;
		status = "Paused: " + reason + ". Press F8 to carry on";
		MineGPT.LOGGER.warn("MineGPT paused itself: {}", reason);
	}

	public void resume() {
		if (mode == Mode.PAUSED) {
			mode = Mode.RUNNING;
		}
	}

	public void togglePause(Minecraft mc) {
		if (mode == Mode.RUNNING) {
			pause(mc);
		} else {
			resume();
		}
	}

	/** Drops the goal and gives the controls back. */
	public void stop(Minecraft mc) {
		cancelSkill(mc, "stopped by the user");
		mode = Mode.IDLE;
		goal = null;
		status = "Stopped";
		executor.releaseAll(mc);
	}

	/**
	 * Runs an action now. Starts the agent if it was idle, and is refused while paused.
	 * A raw action takes over from any running skill.
	 */
	public boolean act(Minecraft mc, Action action) {
		if (mode == Mode.PAUSED) {
			return false;
		}
		cancelSkill(mc, "interrupted by an action");
		mode = Mode.RUNNING;
		executor.submit(mc, action);
		return true;
	}

	/**
	 * Starts a skill, replacing any running one. {@code onDone} hears exactly once how it
	 * ended, including when it is cancelled. Refused while paused.
	 */
	public boolean runSkill(Minecraft mc, Skill newSkill, Object owner, Consumer<SkillResult> onDone) {
		if (mode == Mode.PAUSED) {
			return false;
		}
		cancelSkill(mc, "replaced by " + newSkill.name());
		skill = newSkill;
		skillOwner = owner;
		skillDone = onDone;
		mode = Mode.RUNNING;
		MineGPT.LOGGER.info("Skill started: {}", newSkill.name());
		return true;
	}

	/** Cancels the running skill, whoever started it. */
	public void cancelSkill(Minecraft mc, String reason) {
		if (skill != null) {
			endSkill(mc, SkillResult.failure("cancelled: " + reason));
		}
	}

	/** Cancels the running skill only if {@code owner} started it. */
	public void cancelSkill(Minecraft mc, Object owner, String reason) {
		if (skill != null && skillOwner == owner) {
			cancelSkill(mc, reason);
		}
	}

	private void endSkill(Minecraft mc, SkillResult result) {
		Skill ended = skill;
		Consumer<SkillResult> done = skillDone;
		skill = null;
		skillOwner = null;
		skillDone = null;
		if (mc.player != null) {
			ended.stop(new SkillContext(mc, executor));
		}
		executor.releaseAll(mc);
		MineGPT.LOGGER.info("Skill {} {}: {}", ended.name(), result.success() ? "succeeded" : "failed", result.message());
		status = ended.name() + (result.success() ? " done: " : " failed: ") + result.message();
		done.accept(result);
	}

	public void tick(Minecraft mc) {
		if (mc.player == null) {
			if (mode != Mode.IDLE) {
				stop(mc);
			}
			return;
		}
		if (mode == Mode.RUNNING) {
			if (mc.player.isDeadOrDying()) {
				// Never respawn and carry on unattended; the user decides what happens next.
				cancelSkill(mc, "the player died");
				pauseFor(mc, "the player died");
				return;
			}
			Safety safety = MineGPTClient.safety();
			if (mc.player.getHealth() > safety.stopHealth()) {
				healthStopArmed = true;
				lowHealthInFight = false;
			} else if (safety.stopHealth() > 0 && healthStopArmed) {
				int health = Math.round(mc.player.getHealth());
				if (hostileNear(mc, LOW_HEALTH_DANGER)) {
					// Standing still next to a mob gets the player killed, so stop the work and leave the
					// brain to fight back; pause once the mobs are gone.
					if (!lowHealthInFight) {
						lowHealthInFight = true;
						if (skill != null && !"fight".equals(skill.name())) {
							cancelSkill(mc, "health is down to " + health + " with a mob close");
						}
					}
				} else {
					healthStopArmed = false;
					lowHealthInFight = false;
					cancelSkill(mc, "health is down to " + health);
					pauseFor(mc, "health is low (" + health + "/20)");
					return;
				}
			}
			if (safety.throttled()) {
				// Over the action cap: stand still until the oldest actions drop out of the last minute.
				if (!throttled) {
					throttled = true;
					executor.releaseAll(mc);
					MineGPT.LOGGER.info("Action cap of {} a minute reached; waiting", safety.maxActionsPerMinute());
				}
				return;
			}
			throttled = false;
			if (skill != null) {
				tickSkill(mc);
			}
			executor.tick(mc);
			if (goal == null && !remoteControlled && skill == null && !executor.isBusy()) {
				// A one-off action (from /minegpt act) has finished; hand control back.
				mode = Mode.IDLE;
			}
		}
	}

	private void tickSkill(Minecraft mc) {
		if (needsAir(mc.player)) {
			// Breathing comes first: swim to air, then the skill carries on from wherever that leaves it.
			swimmer.tick(mc, executor);
			return;
		}
		SkillResult result;
		try {
			result = skill.tick(new SkillContext(mc, executor));
		} catch (RuntimeException e) {
			// A bug in a skill must not take the game down with it.
			MineGPT.LOGGER.error("Skill {} crashed", skill.name(), e);
			result = SkillResult.failure("crashed: " + e);
		}
		if (result != null) {
			endSkill(mc, result);
		}
	}

	/** Starts surfacing when air runs low under water, and keeps at it until the player has breathed back to full. */
	private boolean needsAir(LocalPlayer player) {
		if (!surfacing && player.isUnderWater() && player.getAirSupply() < LOW_AIR) {
			MineGPT.LOGGER.info("Low on air under water: swimming to air before carrying on");
			surfacing = true;
			swimmer.reset();
		} else if (surfacing && !player.isUnderWater() && player.getAirSupply() >= player.getMaxAirSupply()) {
			surfacing = false;
		}
		return surfacing;
	}

	private static boolean hostileNear(Minecraft mc, double radius) {
		return !mc.level.getEntitiesOfClass(LivingEntity.class, mc.player.getBoundingBox().inflate(radius),
				e -> e instanceof Enemy && e.isAlive()).isEmpty();
	}
}
