package com.xzier9.minegpt.path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;

/**
 * Places where MineGPT got stuck in game, kept as tests.
 *
 * The brain's failure miner saves the blocks round the player when a skill fails or stalls, and
 * {@code python -m minegpt_brain.practice keep <id>} writes one as a {@code .scene} file in
 * {@code src/test/resources/scenes}. Each scene here must let the pathfinder do what it says on
 * its {@code expect} line: {@code escape} (get at least 4 blocks away from where the player was
 * stuck) or {@code reach x y z} (get to that cell of the box). A failing scene is a stuck spot
 * that a change to the pathfinder has not fixed yet, or has broken again.
 */
class SceneTest {
	static final java.nio.file.Path DIR = Paths.get(System.getProperty("minegpt.scenes", "src/test/resources/scenes"));

	/** One saved scene: the pathfinder's cells in a box, where the player stood and what must work. */
	record Scene(String name, int sx, int sy, int sz, int[][][] cells, String expect, int[] target, boolean breaking,
			int scaffold) implements BlockView {
		static Scene parse(String name, List<String> lines) {
			int[] size = null;
			int[] start = null;
			String expect = "escape";
			int[] target = null;
			boolean breaking = true;
			int scaffold = 0;
			List<String> rows = new ArrayList<>();
			boolean inCells = false;
			for (String raw : lines) {
				String line = raw.strip();
				if (line.isEmpty() || line.startsWith("#")) {
					continue;
				}
				if (inCells) {
					rows.add(line);
					continue;
				}
				String[] parts = line.split("\\s+");
				switch (parts[0]) {
					case "size" -> size = ints(parts, 1, 3);
					case "start" -> start = ints(parts, 1, 3);
					case "expect" -> {
						expect = parts[1];
						if (expect.equals("reach")) {
							target = ints(parts, 2, 3);
						}
					}
					case "breaking" -> breaking = Boolean.parseBoolean(parts[1]);
					case "scaffold" -> scaffold = Integer.parseInt(parts[1]);
					case "cells" -> inCells = true;
					default -> throw new IllegalArgumentException(name + ": unknown line " + line);
				}
			}
			if (size == null || start == null) {
				throw new IllegalArgumentException(name + ": needs size and start lines");
			}
			assertEquals(size[1] * size[2], rows.size(), name + ": cell rows");
			int[][][] cells = new int[size[1]][size[2]][size[0]];
			for (int y = 0; y < size[1]; y++) {
				for (int z = 0; z < size[2]; z++) {
					String row = rows.get(y * size[2] + z);
					assertEquals(size[0], row.length(), name + ": row length");
					for (int x = 0; x < size[0]; x++) {
						cells[y][z][x] = row.charAt(x) - '0';
					}
				}
			}
			return new Scene(name, start[0], start[1], start[2], cells, expect, target, breaking, scaffold);
		}

		private static int[] ints(String[] parts, int from, int n) {
			int[] out = new int[n];
			for (int i = 0; i < n; i++) {
				out[i] = Integer.parseInt(parts[from + i]);
			}
			return out;
		}

		/**
		 * Inside the box, the saved cell. Below it solid ground, above it air, and to the sides the
		 * box's edge column carried on, so the land round the scene looks like its border.
		 */
		@Override
		public int cell(int x, int y, int z) {
			int h = cells.length;
			int d = cells[0].length;
			int w = cells[0][0].length;
			if (x < -30 || z < -30 || x > w + 30 || z > d + 30) {
				return UNLOADED;
			}
			if (y < 0) {
				return y < -20 ? UNLOADED : SOLID;
			}
			if (y >= h) {
				return PASSABLE;
			}
			return cells[y][Math.max(0, Math.min(d - 1, z))][Math.max(0, Math.min(w - 1, x))];
		}

		@Override
		public float breakTicks(int x, int y, int z) {
			return 10f;
		}

		Goal goal() {
			if (target != null) {
				return new Goal.Block(target[0], target[1], target[2]);
			}
			// Out: 4 or more blocks from the stuck spot on the ground plan.
			return new Goal() {
				@Override
				public boolean reached(int x, int y, int z) {
					return Math.max(Math.abs(x - sx), Math.abs(z - sz)) >= 4;
				}

				@Override
				public double distance(int x, int y, int z) {
					return Math.max(0, 4 - Math.max(Math.abs(x - sx), Math.abs(z - sz)));
				}
			};
		}
	}

	static List<Scene> load() throws IOException {
		List<Scene> out = new ArrayList<>();
		if (!Files.isDirectory(DIR)) {
			return out;
		}
		try (Stream<java.nio.file.Path> files = Files.list(DIR)) {
			for (java.nio.file.Path file : files.filter(f -> f.toString().endsWith(".scene")).sorted().toList()) {
				out.add(Scene.parse(file.getFileName().toString(), Files.readAllLines(file)));
			}
		}
		return out;
	}

	@TestFactory
	Stream<DynamicTest> everySavedSceneCanBeLeft() throws IOException {
		return load().stream().filter(s -> !s.expect().equals("none")).map(s -> DynamicTest.dynamicTest(s.name(), () -> {
			Pathfinder.Options options = Pathfinder.Options.DEFAULT.withBreaking(s.breaking()).withScaffold(s.scaffold());
			Path path = new Pathfinder(s, options).find(s.sx(), s.sy(), s.sz(), s.goal());
			assertTrue(path.complete(), s.name() + ": the pathfinder found no way to " + s.expect());
		}));
	}

	@Test
	void parsesTheFormat() {
		List<String> lines = new ArrayList<>(List.of("# a tiny scene", "size 3 2 3", "start 1 1 1", "expect escape",
				"breaking false", "cells"));
		lines.addAll(List.of("222", "222", "222", "111", "111", "111"));
		Scene scene = Scene.parse("tiny", lines);
		assertEquals(BlockView.SOLID, scene.cell(1, 0, 1));
		assertEquals(BlockView.PASSABLE, scene.cell(1, 1, 1));
		assertEquals(BlockView.SOLID, scene.cell(9, 0, 9), "edge columns carry on past the box");
		Path path = new Pathfinder(scene, Pathfinder.Options.DEFAULT).find(1, 1, 1, scene.goal());
		assertTrue(path.complete(), "open flat ground can be left");
	}

	@Test
	void aClosedPitNeedsBreaking() {
		// A 1x1 pit two deep with solid walls: no way out without digging or pillaring.
		List<String> lines = new ArrayList<>(List.of("size 3 4 3", "start 1 1 1", "breaking false", "cells"));
		lines.addAll(List.of("222", "222", "222", "222", "212", "222", "222", "212", "222", "111", "111", "111"));
		Scene scene = Scene.parse("pit", lines);
		Path stuck = new Pathfinder(scene, Pathfinder.Options.DEFAULT).find(1, 1, 1, scene.goal());
		assertTrue(!stuck.complete(), "walls two high cannot be walked out of");
		Path dug = new Pathfinder(scene, Pathfinder.Options.DEFAULT.withBreaking(true)).find(1, 1, 1, scene.goal());
		assertTrue(dug.complete(), "digging gets out");
	}
}
