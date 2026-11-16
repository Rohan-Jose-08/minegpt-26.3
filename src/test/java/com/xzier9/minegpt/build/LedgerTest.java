package com.xzier9.minegpt.build;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.xzier9.minegpt.build.Ledger.Purpose;
import java.util.List;
import org.junit.jupiter.api.Test;

class LedgerTest {
	private static final String COBBLE = "minecraft:cobblestone";

	@Test
	void tellsItsOwnScaffoldingFromAWallOfTheSameBlock() {
		Ledger ledger = new Ledger();
		ledger.record(10, 64, -3, Purpose.SCAFFOLD, COBBLE);
		assertNotNull(ledger.owns(10, 64, -3, COBBLE));
		assertTrue(ledger.owns(10, 64, -3, COBBLE).purpose().temporary());
		assertNull(ledger.owns(11, 64, -3, COBBLE));
	}

	@Test
	void forgetsASpotThatHasBecomeSomethingElse() {
		Ledger ledger = new Ledger();
		ledger.record(1, 70, 1, Purpose.FILL, "minecraft:dirt");
		assertNull(ledger.owns(1, 70, 1, "minecraft:oak_planks"));
		// Even if dirt comes back, it is no longer known to be the agent's.
		assertNull(ledger.owns(1, 70, 1, "minecraft:dirt"));
		assertEquals(0, ledger.size());
	}

	@Test
	void aLaterNoteForASpotReplacesTheEarlierOne() {
		Ledger ledger = new Ledger();
		ledger.record(0, 64, 0, Purpose.SCAFFOLD, "minecraft:dirt");
		ledger.record(0, 64, 0, Purpose.BUILD, "minecraft:oak_stairs");
		assertEquals(1, ledger.size());
		assertEquals(Purpose.BUILD, ledger.at(0, 64, 0).purpose());
		assertFalse(ledger.at(0, 64, 0).purpose().temporary());
	}

	@Test
	void findsEntriesInABoxAcrossNegativeCoordinates() {
		Ledger ledger = new Ledger();
		ledger.record(-5, -60, -5, Purpose.SCAFFOLD, COBBLE);
		ledger.record(5, 100, 5, Purpose.BUILD, COBBLE);
		ledger.record(40000, 67, 39995, Purpose.FILL, COBBLE);
		assertEquals(1, ledger.within(-6, -64, -6, 0, 0, 0).size());
		assertEquals(2, ledger.within(-6, -64, -6, 6, 320, 6).size());
		assertEquals(Purpose.FILL, ledger.within(39990, 60, 39990, 40010, 70, 40000).get(0).purpose());
		assertNotNull(ledger.at(-5, -60, -5));
	}

	@Test
	void survivesBeingWrittenAndReadBack() {
		Ledger ledger = new Ledger();
		ledger.record(-12, 64, 7, Purpose.UTILITY, "minecraft:crafting_table");
		ledger.record(3, 71, 3, Purpose.BUILD, "minecraft:birch_stairs");
		assertTrue(ledger.dirty());
		List<String> lines = ledger.lines();
		assertFalse(ledger.dirty());
		assertEquals("-12 64 7 utility minecraft:crafting_table", lines.get(0));
		Ledger back = Ledger.parse(List.of(lines.get(0), "not a line", lines.get(1), "1 2 3 nonsense x"), 100);
		assertEquals(2, back.size());
		assertEquals(Purpose.UTILITY, back.owns(-12, 64, 7, "minecraft:crafting_table").purpose());
		assertFalse(back.dirty());
	}

	@Test
	void dropsTheOldestWhenFull() {
		Ledger ledger = new Ledger(3);
		for (int i = 0; i < 5; i++) {
			ledger.record(i, 64, 0, Purpose.BUILD, COBBLE);
		}
		assertEquals(3, ledger.size());
		assertNull(ledger.at(0, 64, 0));
		assertNotNull(ledger.at(4, 64, 0));
		ledger.forget(4, 64, 0);
		assertEquals(2, ledger.size());
	}
}
