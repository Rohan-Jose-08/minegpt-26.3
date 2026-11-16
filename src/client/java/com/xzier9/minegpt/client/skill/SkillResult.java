package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;

/**
 * How a skill ended.
 *
 * @param data skill-specific details for the brain, such as how many blocks were mined
 */
public record SkillResult(boolean success, String message, JsonObject data) {
	public static SkillResult success(String message) {
		return new SkillResult(true, message, new JsonObject());
	}

	public static SkillResult success(String message, JsonObject data) {
		return new SkillResult(true, message, data);
	}

	public static SkillResult failure(String message) {
		return new SkillResult(false, message, new JsonObject());
	}

	public static SkillResult failure(String message, JsonObject data) {
		return new SkillResult(false, message, data);
	}
}
