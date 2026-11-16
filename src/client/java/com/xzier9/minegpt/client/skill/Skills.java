package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;
import java.util.List;

/** Builds skills by name from JSON arguments. The arguments are documented in docs/protocol.md. */
public final class Skills {
	public static final List<String> NAMES = List.of("goto", "mine", "collect", "craft", "smelt", "place", "equip", "eat",
			"fight", "build", "farm", "store", "take", "sleep");

	private Skills() {
	}

	/** Throws IllegalArgumentException for an unknown skill or bad arguments. */
	public static Skill create(String name, JsonObject json) {
		Args args = new Args(name, json);
		return switch (name) {
			case "goto" -> GotoSkill.fromArgs(args);
			case "mine" -> MineSkill.fromArgs(args);
			case "collect" -> CollectSkill.fromArgs(args);
			case "craft" -> CraftSkill.fromArgs(args);
			case "smelt" -> SmeltSkill.fromArgs(args);
			case "place" -> PlaceSkill.fromArgs(args);
			case "equip" -> EquipSkill.fromArgs(args);
			case "eat" -> EatSkill.fromArgs(args);
			case "fight" -> FightSkill.fromArgs(args);
			case "build" -> BuildSkill.fromArgs(args);
			case "farm" -> FarmSkill.fromArgs(args);
			case "store" -> ChestSkill.fromArgs(args, ChestSkill.Mode.STORE);
			case "take" -> ChestSkill.fromArgs(args, ChestSkill.Mode.TAKE);
			case "sleep" -> SleepSkill.fromArgs(args);
			default -> throw new IllegalArgumentException("unknown skill \"" + name + "\"; known skills: "
					+ String.join(", ", NAMES));
		};
	}
}
