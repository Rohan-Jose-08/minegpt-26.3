package com.xzier9.minegpt.client.skill;

import com.xzier9.minegpt.client.path.ClientBlockView;
import com.xzier9.minegpt.path.BlockView;
import com.xzier9.minegpt.path.Goal;
import com.xzier9.minegpt.path.Path;
import com.xzier9.minegpt.path.Pathfinder;
import java.util.ArrayList;
import java.util.List;
import net.minecraft.core.BlockPos;

/**
 * Chooses among several candidate targets by the route to each, not by straight-line
 * distance: one bounded search finds whichever has the cheapest real path from where the
 * player stands, counting jumps, head room, fences, water and walls as the pathfinder does.
 * A skill runs it a slice per tick before committing to a target, so it never sets off for
 * something that looks near and cannot be reached.
 */
final class TargetPicker<T> {
	/** Time spent per tick; a pick usually takes one or two. */
	static final long SLICE_NANOS = 4_000_000;

	record Option<T>(T target, Goal goal) {
	}

	private final List<Option<T>> options;
	private final Pathfinder.Search search;
	private final int maxNodes;
	private final BlockPos start;
	private T chosen;
	private boolean finished;

	/**
	 * @param dig      whether the route may break blocks, as the goto that follows would
	 * @param maxNodes search budget; when it runs out the answer is "do not know"
	 */
	TargetPicker(SkillContext ctx, List<Option<T>> options, boolean dig, int maxNodes) {
		this.options = List.copyOf(options);
		this.maxNodes = maxNodes;
		BlockView view = new ClientBlockView(ctx.level, ctx.player.getInventory());
		BlockPos feet = ctx.feet();
		List<Goal> goals = new ArrayList<>();
		for (Option<T> option : options) {
			goals.add(option.goal());
		}
		search = new Pathfinder(view, new Pathfinder.Options(dig, maxNodes, Scaffold.count(ctx.player.getInventory()),
				Pathfinder.Options.DEFAULT.penalties())).start(feet.getX(), feet.getY(), feet.getZ(), new Goal.AnyOf(goals));
		start = feet;
	}

	/** Runs a slice of the search. True once it is over. */
	boolean advance() {
		if (finished) {
			return true;
		}
		if (!search.advanceFor(SLICE_NANOS)) {
			return false;
		}
		finished = true;
		if (search.found()) {
			Path path = search.result();
			int x = start.getX();
			int y = start.getY();
			int z = start.getZ();
			if (!path.isEmpty()) {
				Path.Step end = path.steps().get(path.steps().size() - 1);
				x = end.x();
				y = end.y();
				z = end.z();
			}
			for (Option<T> option : options) {
				if (option.goal().reached(x, y, z)) {
					chosen = option.target();
					break;
				}
			}
		}
		return true;
	}

	/** The candidate with the cheapest route, or null if none was found. */
	T chosen() {
		return chosen;
	}

	/** Cost of the route to the chosen candidate, in ticks. */
	double cost() {
		return search.cost();
	}

	/**
	 * True if the search looked everywhere it could get to and found none of the candidates:
	 * they are out of reach for certain. False with no choice means the budget ran out first.
	 */
	boolean noneReachable() {
		return finished && chosen == null && search.exhausted();
	}

	int explored() {
		return search.explored();
	}

	int budget() {
		return maxNodes;
	}

	List<Option<T>> options() {
		return options;
	}
}
