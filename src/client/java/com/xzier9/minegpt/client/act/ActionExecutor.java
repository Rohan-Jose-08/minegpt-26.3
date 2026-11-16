package com.xzier9.minegpt.client.act;

import com.xzier9.minegpt.client.act.Action.MoveKey;
import com.xzier9.minegpt.path.Steering;
import java.util.EnumSet;
import java.util.Set;
import net.fabricmc.fabric.api.client.keymapping.v1.KeyMappingHelper;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Options;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.util.Mth;
import net.minecraft.world.phys.Vec3;

/**
 * Carries out {@link Action}s by pressing the player's own key bindings, so the game
 * and any server see the same input a human would produce.
 *
 * Movement, camera, attack and use are separate channels: a new action replaces only
 * its own channel, so the agent can walk, turn and mine in the same tick.
 * Call {@link #tick} at the start of every client tick, before vanilla reads the keys.
 *
 * Skills turn the camera with {@link #lookToward}, which moves it the way a hand on a mouse
 * does: it speeds up over a couple of ticks, never turns faster than a quick flick, and eases
 * off as it comes onto the target, instead of snapping half the angle at once.
 */
public final class ActionExecutor {
	private final Set<MoveKey> heldKeys = EnumSet.noneOf(MoveKey.class);
	private int moveTicks;
	private boolean moveActive;

	private float yawPerTick;
	private float pitchPerTick;
	private int lookTicks;

	/** Fastest the camera turns while tracking a point, in degrees a tick. */
	private static final float MAX_YAW_SPEED = 32;
	private static final float MAX_PITCH_SPEED = 18;
	/** Most the turning speed may grow in one tick, so a turn starts gently. */
	private static final float YAW_ACCELERATION = 11;
	private static final float PITCH_ACCELERATION = 6;
	/** Share of the angle left that is covered each tick once the speed limits no longer bind. */
	private static final float EASE = 0.45F;
	/** Closer than this the camera settles onto the target, so aiming stays exact. */
	private static final float SETTLE_DEGREES = 0.6F;
	/** Set false for the old behaviour: half the angle left each tick. */
	public static boolean smoothLook = true;
	private Vec3 trackTarget;
	private int trackTicks;
	private float yawSpeed;
	private float pitchSpeed;

	private int attackTicks;
	private int useTicks;
	private boolean attackActive;
	private boolean useActive;

	public void submit(Minecraft mc, Action action) {
		LocalPlayer player = mc.player;
		if (player == null) {
			return;
		}
		switch (action) {
			case Action.Move move -> {
				heldKeys.clear();
				heldKeys.addAll(move.keys());
				moveTicks = move.ticks();
			}
			case Action.Look look -> {
				stopTracking();
				startLook(look.yaw(), look.pitch(), look.ticks());
			}
			case Action.LookAt at -> {
				stopTracking();
				Vec3 eye = player.getEyePosition();
				double dx = at.x() - eye.x;
				double dy = at.y() - eye.y;
				double dz = at.z() - eye.z;
				float yaw = (float) Math.toDegrees(Math.atan2(-dx, dz));
				float pitch = (float) -Math.toDegrees(Math.atan2(dy, Math.sqrt(dx * dx + dz * dz)));
				startLook(Mth.wrapDegrees(yaw - player.getYRot()), pitch - player.getXRot(), at.ticks());
			}
			case Action.Attack attack -> {
				if (attack.ticks() == 0) {
					click(mc.options.keyAttack);
				}
				attackTicks = attack.ticks();
			}
			case Action.Use use -> {
				if (use.ticks() == 0) {
					click(mc.options.keyUse);
				}
				useTicks = use.ticks();
			}
			case Action.Hotbar hotbar -> player.getInventory().setSelectedSlot(hotbar.slot());
			case Action.Click c -> mc.gameMode.handleContainerInput(
					player.containerMenu.containerId, c.slot(), c.button(), c.mode(), player);
			case Action.Drop drop -> mc.gameMode.dropItem(player, drop.wholeStack());
			case Action.OpenInventory open -> click(mc.options.keyInventory);
			case Action.CloseScreen close -> {
				if (mc.gui.screen() != null) {
					player.closeContainer();
					mc.gui.setScreen(null);
				}
			}
			case Action.Stop stop -> releaseAll(mc);
		}
	}

	/**
	 * Turns the camera towards a point, smoothly, for this tick and the next. Call it every tick
	 * while the point should stay in view; the point may move.
	 */
	public void lookToward(Minecraft mc, double x, double y, double z) {
		if (!smoothLook) {
			submit(mc, new Action.LookAt(x, y, z, 2));
			return;
		}
		trackTarget = new Vec3(x, y, z);
		trackTicks = 2;
		lookTicks = 0;
	}

	private void stopTracking() {
		trackTarget = null;
		trackTicks = 0;
		yawSpeed = 0;
		pitchSpeed = 0;
	}

	public void tick(Minecraft mc) {
		LocalPlayer player = mc.player;
		if (player == null) {
			return;
		}
		Options options = mc.options;

		// Each channel only touches its keys while it is active, plus one release when it
		// ends, so the user keeps normal control of everything the agent is not using.
		if (moveTicks > 0) {
			options.keyUp.setDown(heldKeys.contains(MoveKey.FORWARD));
			options.keyDown.setDown(heldKeys.contains(MoveKey.BACK));
			options.keyLeft.setDown(heldKeys.contains(MoveKey.LEFT));
			options.keyRight.setDown(heldKeys.contains(MoveKey.RIGHT));
			options.keyJump.setDown(heldKeys.contains(MoveKey.JUMP));
			options.keyShift.setDown(heldKeys.contains(MoveKey.SNEAK));
			options.keySprint.setDown(heldKeys.contains(MoveKey.SPRINT));
			moveTicks--;
			moveActive = true;
		} else if (moveActive) {
			releaseMovement(options);
			moveActive = false;
		}

		if (trackTicks > 0 && trackTarget != null) {
			Vec3 eye = player.getEyePosition();
			double dx = trackTarget.x - eye.x;
			double dy = trackTarget.y - eye.y;
			double dz = trackTarget.z - eye.z;
			double flat = Math.sqrt(dx * dx + dz * dz);
			// Straight above or below there is no direction to turn to: keep the heading.
			float yawLeft = flat < 1e-4 ? 0 : Mth.wrapDegrees((float) Math.toDegrees(Math.atan2(-dx, dz)) - player.getYRot());
			float pitchLeft = (float) -Math.toDegrees(Math.atan2(dy, flat)) - player.getXRot();
			yawSpeed = Steering.turnStep(yawLeft, yawSpeed, MAX_YAW_SPEED, YAW_ACCELERATION, EASE, SETTLE_DEGREES);
			pitchSpeed = Steering.turnStep(pitchLeft, pitchSpeed, MAX_PITCH_SPEED, PITCH_ACCELERATION, EASE, SETTLE_DEGREES);
			player.setYRot(player.getYRot() + yawSpeed);
			player.setXRot(Mth.clamp(player.getXRot() + pitchSpeed, -90.0F, 90.0F));
			if (--trackTicks == 0) {
				stopTracking();
			}
		} else if (lookTicks > 0) {
			player.setYRot(player.getYRot() + yawPerTick);
			player.setXRot(Mth.clamp(player.getXRot() + pitchPerTick, -90.0F, 90.0F));
			lookTicks--;
		}

		if (attackTicks > 0) {
			options.keyAttack.setDown(true);
			attackTicks--;
			attackActive = true;
		} else if (attackActive) {
			options.keyAttack.setDown(false);
			attackActive = false;
		}

		if (useTicks > 0) {
			options.keyUse.setDown(true);
			useTicks--;
			useActive = true;
		} else if (useActive) {
			options.keyUse.setDown(false);
			useActive = false;
		}
	}

	/** Lets go of every key this executor may be holding. Call when the agent stops or pauses. */
	public void releaseAll(Minecraft mc) {
		heldKeys.clear();
		moveTicks = 0;
		lookTicks = 0;
		stopTracking();
		attackTicks = 0;
		useTicks = 0;
		moveActive = false;
		attackActive = false;
		useActive = false;
		Options options = mc.options;
		releaseMovement(options);
		options.keyAttack.setDown(false);
		options.keyUse.setDown(false);
	}

	private static void releaseMovement(Options options) {
		for (KeyMapping key : new KeyMapping[] {
				options.keyUp, options.keyDown, options.keyLeft, options.keyRight,
				options.keyJump, options.keyShift, options.keySprint}) {
			key.setDown(false);
		}
	}

	public boolean isBusy() {
		// The active flags count too: a channel that just ran out still has a release to send.
		return moveTicks > 0 || lookTicks > 0 || trackTicks > 0 || attackTicks > 0 || useTicks > 0
				|| moveActive || attackActive || useActive;
	}

	private void startLook(float yaw, float pitch, int ticks) {
		int steps = Math.max(1, ticks);
		yawPerTick = yaw / steps;
		pitchPerTick = pitch / steps;
		lookTicks = steps;
	}

	/** Queues one press of a binding, as if the user tapped its key. */
	private static void click(KeyMapping mapping) {
		KeyMapping.click(KeyMappingHelper.getBoundKeyOf(mapping));
	}
}
