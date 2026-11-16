package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.List;

/** Reads skill arguments from JSON with defaults and readable errors. */
final class Args {
	private final String skill;
	private final JsonObject json;

	Args(String skill, JsonObject json) {
		this.skill = skill;
		this.json = json == null ? new JsonObject() : json;
	}

	boolean has(String key) {
		return json.has(key) && !json.get(key).isJsonNull();
	}

	int integer(String key, int fallback) {
		return has(key) ? number(key).intValue() : fallback;
	}

	int integer(String key) {
		return number(require(key)).intValue();
	}

	double decimal(String key, double fallback) {
		return has(key) ? number(key).doubleValue() : fallback;
	}

	boolean bool(String key, boolean fallback) {
		if (!has(key)) {
			return fallback;
		}
		JsonElement value = json.get(key);
		if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isBoolean()) {
			throw bad(key + " must be true or false");
		}
		return value.getAsBoolean();
	}

	String string(String key) {
		JsonElement value = json.get(require(key));
		if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()) {
			throw bad(key + " must be a string");
		}
		return value.getAsString();
	}

	String string(String key, String fallback) {
		return has(key) ? string(key) : fallback;
	}

	/** A string or a list of strings. */
	List<String> strings(String key) {
		require(key);
		JsonElement value = json.get(key);
		List<String> out = new ArrayList<>();
		if (value.isJsonArray()) {
			for (JsonElement e : value.getAsJsonArray()) {
				out.add(e.getAsString());
			}
		} else {
			out.add(string(key));
		}
		if (out.isEmpty()) {
			throw bad(key + " must not be empty");
		}
		return out;
	}

	/** A JSON array, for arguments too structured for the helpers above. */
	JsonArray array(String key) {
		JsonElement value = json.get(require(key));
		if (!value.isJsonArray()) {
			throw bad(key + " must be a list");
		}
		return value.getAsJsonArray();
	}

	/** A JSON object, such as a map from item ids to counts. */
	com.google.gson.JsonObject object(String key) {
		JsonElement value = json.get(require(key));
		if (!value.isJsonObject()) {
			throw bad(key + " must be an object");
		}
		return value.getAsJsonObject();
	}

	/** A list of strings if given, else an empty list. */
	List<String> optionalStrings(String key) {
		return has(key) ? strings(key) : List.of();
	}

	/** Seconds in the arguments, converted to ticks. */
	int ticks(String key, int fallbackSeconds) {
		return (int) Math.round(decimal(key, fallbackSeconds) * 20);
	}

	IllegalArgumentException bad(String message) {
		return new IllegalArgumentException(skill + ": " + message);
	}

	private String require(String key) {
		if (!has(key)) {
			throw bad("missing argument \"" + key + "\"");
		}
		return key;
	}

	private Number number(String key) {
		JsonElement value = json.get(key);
		if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isNumber()) {
			throw bad(key + " must be a number");
		}
		return value.getAsNumber();
	}
}
