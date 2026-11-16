package com.xzier9.minegpt.path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;

class TargetingTest {
	@Test
	void anItemBesideThePlayerIsInRange() {
		assertTrue(Pickup.inRange(0.5, 64, 0.5, 1.5, 64, 0.5));
		// Diagonally next door: both gaps are a block, and the box is square.
		assertTrue(Pickup.inRange(0.5, 64, 0.5, 1.5, 64, 1.5));
		assertEquals(Pickup.Approach.WAIT, Pickup.approach(0.5, 64, 0.5, 1.5, 64, 1.5, 2.5));
	}

	@Test
	void sidewaysAndHeightAreCheckedApart() {
		// Straight overhead on a ledge three up: a short way off, and out of reach.
		assertFalse(Pickup.inRange(0.5, 64, 0.5, 0.6, 67, 0.5));
		assertEquals(Pickup.Approach.PATH, Pickup.approach(0.5, 64, 0.5, 0.6, 67, 0.5, 2.5));
		// Down a hole next to the player.
		assertFalse(Pickup.inRange(0.5, 64, 0.5, 1.5, 62, 0.5));
		assertEquals(Pickup.Approach.PATH, Pickup.approach(0.5, 64, 0.5, 1.5, 62, 0.5, 2.5));
		// On the block the player's head is level with: the box reaches half a block above the head.
		assertTrue(Pickup.inRange(0.5, 64, 0.5, 1.5, 66, 0.5));
	}

	@Test
	void anItemTwoBlocksOffIsWalkedAt() {
		assertFalse(Pickup.inRange(0.5, 64, 0.5, 2.5, 64, 0.5));
		assertEquals(Pickup.Approach.WALK, Pickup.approach(0.5, 64, 0.5, 2.5, 64, 0.5, 2.5));
		assertEquals(Pickup.Approach.WALK, Pickup.approach(0.5, 64, 0.5, 2.5, 65, 0.5, 2.5));
		assertEquals(Pickup.Approach.PATH, Pickup.approach(0.5, 64, 0.5, 6.5, 64, 0.5, 2.5));
	}

	@Test
	void touchingAnItemsBlockPutsItLevel() {
		// Every place the Touch goal accepts has the item within the pickup box's height.
		Goal touch = new Goal.Touch(10, 64, 10);
		for (int py = 60; py <= 68; py++) {
			if (touch.reached(10, py, 10)) {
				assertTrue(Pickup.levelWith(py, 64.0), "feet at " + py);
				assertTrue(Pickup.levelWith(py, 64.9), "feet at " + py);
			}
		}
	}

	@Test
	void standingStillCountsAsStuck() {
		StuckWatch watch = new StuckWatch(20, 100, 2.5);
		for (int i = 0; i < 19; i++) {
			assertEquals(StuckWatch.State.MOVING, watch.tick(0, 64, 0));
		}
		assertEquals(StuckWatch.State.STILL, watch.tick(0, 64, 0));
		assertEquals(StuckWatch.State.MOVING, watch.tick(1, 64, 0));
	}

	@Test
	void circlingCountsAsLoitering() {
		StuckWatch watch = new StuckWatch(20, 100, 2.5);
		int[][] ring = {{0, 0}, {1, 0}, {1, 1}, {0, 1}};
		StuckWatch.State state = StuckWatch.State.MOVING;
		int ticks = 0;
		while (state == StuckWatch.State.MOVING && ticks < 500) {
			int[] at = ring[(ticks / 5) % 4];
			state = watch.tick(at[0], 64, at[1]);
			ticks++;
		}
		assertEquals(StuckWatch.State.LOITERING, state);
		assertTrue(ticks >= 100 && ticks <= 102, "after " + ticks);
	}

	@Test
	void walkingAwayIsNeitherStuckNorLoitering() {
		StuckWatch watch = new StuckWatch(20, 100, 2.5);
		for (int i = 0; i < 400; i++) {
			assertEquals(StuckWatch.State.MOVING, watch.tick(i / 5, 64, 0));
		}
		// Climbing a ladder or pillaring up is headway too.
		watch.reset();
		for (int i = 0; i < 400; i++) {
			assertEquals(StuckWatch.State.MOVING, watch.tick(0, 64 + i / 12, 0));
		}
	}

	@Test
	void blacklistedTargetsComeBackAfterTheirTime() {
		Blacklist<String> list = new Blacklist<>(100);
		assertEquals(100, list.add("a", 1000));
		assertTrue(list.contains("a", 1099));
		assertFalse(list.contains("a", 1100));
		assertFalse(list.contains("b", 1000));
		assertEquals(List.of(), list.active(1100));
	}

	@Test
	void failingAgainSoonDoublesTheTimeOut() {
		Blacklist<String> list = new Blacklist<>(100);
		list.add("a", 0);
		assertEquals(200, list.add("a", 150));
		assertEquals(400, list.add("a", 400));
		assertEquals(800, list.add("a", 900));
		assertEquals(800, list.add("a", 1800));
		// Left alone long enough, it starts from the base again.
		assertEquals(100, list.add("a", 1800 + 800 * 3));
	}

	@Test
	void aClockThatJumpsBackClearsTheBlacklist() {
		Blacklist<String> list = new Blacklist<>(100);
		list.add("a", 5000);
		assertFalse(list.contains("a", 10));
	}

	@Test
	void aTargetIsKeptUnlessARivalIsMuchCloser() {
		Commitment commitment = new Commitment(40, 4);
		commitment.commit(0);
		// About as far: stay.
		assertFalse(commitment.shouldSwitch(100, 10, 0, 8, 0));
		// Under half as far and a real saving: go.
		assertTrue(commitment.shouldSwitch(100, 10, 0, 4, 0));
		// Half of very little is not worth turning round for.
		assertFalse(commitment.shouldSwitch(100, 5, 0, 2, 0));
	}

	@Test
	void aFreshTargetIsNotDroppedExceptForAThreat() {
		Commitment commitment = new Commitment(40, 4);
		commitment.commit(100);
		assertFalse(commitment.shouldSwitch(110, 20, 0, 2, 0));
		assertTrue(commitment.shouldSwitch(110, 20, 0, 9, 1));
		assertTrue(commitment.shouldSwitch(140, 20, 0, 2, 0));
		// And nothing less urgent takes over from a threat, however near.
		assertFalse(commitment.shouldSwitch(200, 20, 1, 1, 0));
	}

	@Test
	void theCameraSpeedsUpThenEasesOntoTheTargetWithoutOvershooting() {
		float left = 120;
		float speed = 0;
		float fastest = 0;
		int ticks = 0;
		float firstStep = 0;
		while (left != 0 && ticks < 100) {
			float step = Steering.turnStep(left, speed, 32, 11, 0.45f, 0.6f);
			if (ticks == 0) {
				firstStep = step;
			}
			assertTrue(step > 0 && step <= left, "never past the target");
			assertTrue(step <= speed + 11 + 1e-4, "never a jerk");
			fastest = Math.max(fastest, step);
			speed = step;
			left -= step;
			ticks++;
		}
		assertEquals(0, left);
		assertEquals(11, firstStep, 1e-4);
		assertEquals(32, fastest, 1e-4);
		assertTrue(ticks <= 16, "took " + ticks + " ticks");
	}

	@Test
	void aSmallCorrectionIsMadeInOneGo() {
		assertEquals(-0.4f, Steering.turnStep(-0.4f, 0, 32, 11, 0.45f, 0.6f));
		// Turning the other way starts gently again.
		assertEquals(-11, Steering.turnStep(-90, 30, 32, 11, 0.45f, 0.6f), 1e-4);
	}

	@Test
	void oneTickAgainstAWallIsNotAReasonToJump() {
		Steering.Sustained bumped = new Steering.Sustained(3);
		assertFalse(bumped.update(true));
		assertFalse(bumped.update(false));
		assertFalse(bumped.update(true));
		assertFalse(bumped.update(true));
		assertTrue(bumped.update(true));
	}
}
