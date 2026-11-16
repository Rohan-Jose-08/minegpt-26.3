package com.xzier9.minegpt.build;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.xzier9.minegpt.build.Turning.Family;
import java.util.Map;
import org.junit.jupiter.api.Test;

class TurningTest {
	@Test
	void stairsRiseTheWayThePlayerLooked() {
		Map<String, String> want = Turning.expected(Family.WITH_LOOK, "south", null, null, null, true);
		assertEquals(Map.of("facing", "south"), want);
		assertNull(Turning.mismatch(want, Map.of("facing", "south", "half", "bottom", "shape", "straight")));
		assertEquals("facing north not south", Turning.mismatch(want, Map.of("facing", "north", "half", "bottom")));
	}

	@Test
	void upsideDownStairsAreCheckedForTheirHalfToo() {
		Map<String, String> want = Turning.expected(Family.WITH_LOOK, "east", true, null, null, true);
		assertEquals("facing west not east, half bottom not top",
				Turning.mismatch(want, Map.of("facing", "west", "half", "bottom")));
	}

	@Test
	void aDoorHasNoTopHalfToGetWrong() {
		// "top" on a door would be its upper block, which is not a way of placing it.
		assertEquals(Map.of("facing", "north"), Turning.expected(Family.WITH_LOOK, "north", true, null, null, false));
	}

	@Test
	void chestsFaceBackAtThePlayer() {
		Map<String, String> want = Turning.expected(Family.AGAINST_LOOK, "north", null, null, null, false);
		assertEquals(Map.of("facing", "south"), want);
	}

	@Test
	void aLadderFacesAwayFromItsWall() {
		assertEquals(Map.of("facing", "south"), Turning.expected(Family.HANGING, null, null, "north", null, false));
		// A torch on the floor has no facing to check.
		assertTrue(Turning.expected(Family.HANGING, null, null, "down", null, false).isEmpty());
	}

	@Test
	void logsStandUprightUnlessAskedOtherwise() {
		Map<String, String> post = Turning.expected(Family.PILLAR, null, null, null, null, false);
		assertEquals(Map.of("axis", "y"), post);
		assertEquals("axis x not y", Turning.mismatch(post, Map.of("axis", "x")));
		assertEquals(Map.of("axis", "x"), Turning.expected(Family.PILLAR, null, null, null, "x", false));
		// Placed against the wall to its north, a log lies north to south.
		assertEquals(Map.of("axis", "z"), Turning.expected(Family.PILLAR, null, null, "north", null, false));
		assertEquals(Map.of("axis", "y"), Turning.expected(Family.PILLAR, null, null, "down", null, false));
	}

	@Test
	void aDoubleSlabPassesForEitherHalf() {
		Map<String, String> want = Turning.expected(Family.SLAB, null, false, null, null, true);
		assertNull(Turning.mismatch(want, Map.of("half", "double")));
		assertNotNull(Turning.mismatch(want, Map.of("half", "top")));
	}

	@Test
	void plainBlocksAndMissingPropertiesAreNeverWrong() {
		assertTrue(Turning.expected(Family.PLAIN, "east", true, "down", "x", true).isEmpty());
		assertNull(Turning.mismatch(Map.of("facing", "east"), Map.of()));
	}
}
