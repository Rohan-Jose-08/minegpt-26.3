package com.xzier9.minegpt.client.agent;

import com.xzier9.minegpt.MineGPT;
import com.xzier9.minegpt.client.act.Action;
import com.xzier9.minegpt.client.act.ActionExecutor;
import net.minecraft.client.Minecraft;

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

	private final ActionExecutor executor = new ActionExecutor();
	private Mode mode = Mode.IDLE;
	private String goal;
	private String status = "Waiting for a goal";

	public Mode mode() {
		return mode;
	}

	public String goal() {
		return goal;
	}

	public String status() {
		return status;
	}

	public void setStatus(String status) {
		this.status = status;
	}

	public void setGoal(String goal) {
		this.goal = goal;
		MineGPT.LOGGER.info("New goal: {}", goal);
	}

	public void start() {
		mode = Mode.RUNNING;
	}

	public void pause(Minecraft mc) {
		if (mode == Mode.RUNNING) {
			mode = Mode.PAUSED;
			executor.releaseAll(mc);
		}
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
		mode = Mode.IDLE;
		goal = null;
		status = "Stopped";
		executor.releaseAll(mc);
	}

	/** Runs an action now. Starts the agent if it was idle, and is refused while paused. */
	public boolean act(Minecraft mc, Action action) {
		if (mode == Mode.PAUSED) {
			return false;
		}
		mode = Mode.RUNNING;
		executor.submit(mc, action);
		return true;
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
				pause(mc);
				status = "Paused: player died";
				return;
			}
			executor.tick(mc);
			if (goal == null && !executor.isBusy()) {
				// A one-off action (from /minegpt act) has finished; hand control back.
				mode = Mode.IDLE;
			}
		}
	}
}
