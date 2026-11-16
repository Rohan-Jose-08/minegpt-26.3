package com.xzier9.minegpt.client.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.xzier9.minegpt.MineGPT;
import com.xzier9.minegpt.client.MineGPTClient;
import com.xzier9.minegpt.client.act.Action;
import com.xzier9.minegpt.client.agent.Agent;
import com.xzier9.minegpt.client.agent.Scene;
import com.xzier9.minegpt.client.agent.StallWatch;
import com.xzier9.minegpt.client.observe.Observer;
import com.xzier9.minegpt.client.observe.Snapshot;
import com.xzier9.minegpt.client.skill.BlockMatcher;
import com.xzier9.minegpt.client.skill.BlockSearch;
import com.xzier9.minegpt.client.skill.ItemMatcher;
import com.xzier9.minegpt.client.skill.Recipes;
import com.xzier9.minegpt.client.skill.SiteSurvey;
import com.xzier9.minegpt.client.skill.Skill;
import com.xzier9.minegpt.client.skill.SkillResult;
import com.xzier9.minegpt.client.skill.Skills;
import com.xzier9.minegpt.client.skill.Tuning;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.Set;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.chat.Component;

/**
 * Runs the bridge protocol on the client thread: applies the brain's actions, answers its
 * requests, and tells it when the user changes the goal or pauses. See docs/protocol.md.
 */
public final class BridgeHandler {
	public static final int PROTOCOL_VERSION = 1;

	/** A step request waiting for its ticks to pass before the observation is sent. */
	private static final class PendingStep {
		final JsonElement id;
		final boolean grid;
		final boolean applied;
		int ticksLeft;

		PendingStep(JsonElement id, int ticks, boolean grid, boolean applied) {
			this.id = id;
			this.ticksLeft = ticks;
			this.grid = grid;
			this.applied = applied;
		}
	}

	/** A screenshot request waiting for a frame or two to be drawn without the HUD. */
	private static final class PendingShot {
		final JsonElement id;
		final int maxWidth;
		int ticksLeft = SHOT_DELAY;

		PendingShot(JsonElement id, int maxWidth) {
			this.id = id;
			this.maxWidth = maxWidth;
		}
	}

	/** Ticks between hiding the HUD and reading the frame, so at least one frame without it has been drawn. */
	private static final int SHOT_DELAY = 4;

	private final BridgeServer server;
	private final Agent agent;
	private final List<PendingStep> pending = new ArrayList<>();
	private final List<PendingShot> shots = new ArrayList<>();
	private final StallWatch stallWatch = new StallWatch();
	/** The HUD was hidden for a screenshot and has yet to be put back. */
	private boolean hudHiddenForShot;
	/** Frames being read back from the graphics card. */
	private int shotsInFlight;

	private long tick;
	private int streamEvery;
	private int streamGridEvery;
	private Agent.Mode lastMode;
	private String lastGoal;
	/** Send the current goal and mode once after connecting, even if they have not changed. */
	private boolean announce;

	public BridgeHandler(BridgeServer server, Agent agent) {
		this.server = server;
		this.agent = agent;
	}

	/**
	 * Passes an in-game command meant for the brain, such as {@code /minegpt job}, on to it as a
	 * {@code command} event. False if no brain is connected.
	 */
	public boolean sendCommand(String command, String text) {
		if (!server.isConnected()) {
			return false;
		}
		JsonObject message = event("command");
		message.addProperty("command", command);
		message.addProperty("text", text);
		server.send(message);
		return true;
	}

	/** Call at the start of each client tick, before the agent applies its keys. */
	public void startTick(Minecraft mc) {
		BridgeServer.Event event;
		while ((event = server.poll()) != null) {
			switch (event) {
				case BridgeServer.Event.Connected connected -> {
					pending.clear();
					shots.clear();
					streamEvery = 0;
					announce = true;
					agent.setRemoteControlled(true);
				}
				case BridgeServer.Event.Disconnected disconnected -> {
					pending.clear();
					shots.clear();
					restoreHud(mc);
					streamEvery = 0;
					agent.cancelSkill(mc, this, "the brain disconnected");
					agent.setRemoteControlled(false);
					agent.releaseControls(mc);
				}
				case BridgeServer.Event.Message message -> handle(mc, message.json());
			}
		}
	}

	/** Call at the end of each client tick, after the game has moved. */
	public void endTick(Minecraft mc) {
		tick++;
		if (!server.isConnected()) {
			return;
		}

		Iterator<PendingStep> it = pending.iterator();
		while (it.hasNext()) {
			PendingStep step = it.next();
			if (--step.ticksLeft <= 0) {
				it.remove();
				JsonObject reply = observation(mc, step.id, step.grid);
				reply.addProperty("applied", step.applied);
				server.send(reply);
			}
		}

		takeShots(mc);

		// A skill that has stopped getting anywhere: tell the brain as it happens, with the scene.
		JsonObject stall = stallWatch.tick(mc, agent.mode() == Agent.Mode.RUNNING ? agent.skill() : null);
		if (stall != null) {
			server.send(stall);
		}

		if (streamEvery > 0 && tick % streamEvery == 0 && mc.player != null) {
			boolean grid = streamGridEvery > 0 && tick % streamGridEvery == 0;
			JsonObject message = observation(mc, null, grid);
			message.addProperty("stream", true);
			server.send(message);
		}

		// Tell the brain about changes the user made in game.
		if (announce || !Objects.equals(lastGoal, agent.goal())) {
			lastGoal = agent.goal();
			JsonObject message = event("goal");
			message.addProperty("goal", lastGoal);
			server.send(message);
		}
		if (announce || lastMode != agent.mode()) {
			lastMode = agent.mode();
			JsonObject message = event("mode");
			message.addProperty("mode", lastMode.name().toLowerCase(Locale.ROOT));
			message.addProperty("reason", agent.pauseReason());
			server.send(message);
		}
		announce = false;
	}

	private void handle(Minecraft mc, JsonObject request) {
		JsonElement id = request.get("id");
		String op = request.has("op") ? request.get("op").getAsString() : "";
		try {
			switch (op) {
				case "hello" -> server.send(hello(mc, id));
				case "observe" -> server.send(observation(mc, id, flag(request, "grid", true)));
				case "act" -> {
					boolean applied = applyActions(mc, request);
					JsonObject reply = reply("ack", id);
					reply.addProperty("applied", applied);
					server.send(reply);
				}
				case "step" -> {
					boolean applied = applyActions(mc, request);
					int ticks = Math.max(1, Math.min(20 * 60, request.has("ticks") ? request.get("ticks").getAsInt() : 1));
					pending.add(new PendingStep(id, ticks, flag(request, "grid", true), applied));
				}
				case "subscribe" -> {
					streamEvery = Math.max(0, request.has("every") ? request.get("every").getAsInt() : 0);
					streamGridEvery = Math.max(0, request.has("gridEvery") ? request.get("gridEvery").getAsInt() : 0);
					server.send(reply("ack", id));
				}
				case "status" -> server.send(status(id));
				case "set_status" -> {
					agent.setStatus(request.get("text").getAsString());
					server.send(reply("ack", id));
				}
				case "set_goal" -> {
					JsonElement goal = request.get("goal");
					agent.setGoal(goal == null || goal.isJsonNull() ? null : goal.getAsString());
					server.send(reply("ack", id));
				}
				case "find" -> {
					if (mc.player == null) {
						server.send(error(id, "no world loaded"));
						return;
					}
					server.send(find(mc, id, request));
				}
				case "skill" -> startSkill(mc, id, request);
				case "tuning" -> {
					JsonObject reply = reply("tuning", id);
					reply.add("knobs", Tuning.describe());
					server.send(reply);
				}
				case "cancel_skill" -> {
					agent.cancelSkill(mc, "cancelled by the brain");
					server.send(reply("ack", id));
				}
				case "recipes" -> {
					if (mc.player == null) {
						server.send(error(id, "no world loaded"));
						return;
					}
					ItemMatcher filter = request.has("item") ? ItemMatcher.parse(request.get("item").getAsString()) : null;
					JsonObject reply = reply("recipes", id);
					reply.add("recipes", Recipes.list(mc.player, filter, flag(request, "craftable", false)));
					server.send(reply);
				}
				case "finish_goal" -> {
					String message = request.has("message") ? request.get("message").getAsString() : "Goal finished";
					agent.finishGoal(mc, message);
					server.send(reply("ack", id));
				}
				case "safety" -> {
					JsonObject reply = reply("safety", id);
					reply.add("safety", MineGPTClient.safety().toJson(mc));
					server.send(reply);
				}
				case "notify" -> {
					if (mc.player != null) {
						mc.player.sendSystemMessage(Component.literal("[MineGPT] " + request.get("text").getAsString()));
					}
					server.send(reply("ack", id));
				}
				case "run_command" -> runCommand(mc, id, request.get("command").getAsString());
				case "blocks" -> {
					if (mc.player == null) {
						server.send(error(id, "no world loaded"));
						return;
					}
					server.send(blocks(mc, id, request.getAsJsonArray("positions"),
							request.has("states") && request.get("states").getAsBoolean()));
				}
				case "audit" -> {
					if (mc.player == null || mc.level == null) {
						server.send(error(id, "no world loaded"));
						return;
					}
					JsonObject reply = com.xzier9.minegpt.client.skill.BuildAudit.run(mc, request);
					reply.addProperty("op", "audit");
					reply.add("id", id);
					server.send(reply);
				}
				case "screenshot" -> {
					if (mc.player == null || mc.level == null) {
						server.send(error(id, "no world loaded"));
						return;
					}
					int width = request.has("width") ? request.get("width").getAsInt() : Snapshot.DEFAULT_WIDTH;
					if (flag(request, "hideHud", true) && !mc.gui.hud.isHidden()) {
						mc.gui.hud.toggle();
						hudHiddenForShot = true;
					}
					shots.add(new PendingShot(id, width));
				}
				case "survey" -> {
					if (mc.player == null) {
						server.send(error(id, "no world loaded"));
						return;
					}
					int width = Math.max(1, Math.min(48, request.get("width").getAsInt()));
					int depth = Math.max(1, Math.min(48, request.get("depth").getAsInt()));
					int radius = request.has("radius") ? request.get("radius").getAsInt() : 32;
					int limit = Math.max(1, Math.min(8, request.has("limit") ? request.get("limit").getAsInt() : 3));
					JsonObject reply = reply("survey", id);
					reply.add("sites", SiteSurvey.find(mc, width, depth, radius, limit));
					server.send(reply);
				}
				default -> server.send(error(id, "unknown op \"" + op + "\""));
			}
		} catch (RuntimeException e) {
			// Bad field types and invalid actions end up here; the game keeps running.
			MineGPT.LOGGER.debug("Bridge request failed: {}", request, e);
			server.send(error(id, e.getMessage() == null ? e.toString() : e.getMessage()));
		}
	}

	/** Reads the frame for each screenshot request whose wait is over, and answers when the pixels arrive. */
	private void takeShots(Minecraft mc) {
		Iterator<PendingShot> it = shots.iterator();
		while (it.hasNext()) {
			PendingShot shot = it.next();
			if (--shot.ticksLeft > 0) {
				continue;
			}
			it.remove();
			if (mc.player == null || mc.level == null) {
				server.send(error(shot.id, "no world loaded"));
				continue;
			}
			shotsInFlight++;
			try {
				Screenshot.takeScreenshot(mc.gameRenderer.mainRenderTarget(), image -> {
					JsonObject reply = reply("screenshot", shot.id);
					try (image) {
						Snapshot.encode(image, shot.maxWidth, reply);
						server.send(reply);
					} catch (Exception e) {
						MineGPT.LOGGER.warn("Screenshot for the brain failed", e);
						server.send(error(shot.id, "could not encode the screenshot: " + e));
					} finally {
						// The callback may run off the client thread; the count and the HUD belong to it.
						mc.execute(() -> {
							shotsInFlight--;
							if (shots.isEmpty() && shotsInFlight == 0) {
								restoreHud(mc);
							}
						});
					}
				});
			} catch (RuntimeException e) {
				shotsInFlight--;
				server.send(error(shot.id, "could not take a screenshot: " + e));
			}
		}
		if (shots.isEmpty() && shotsInFlight == 0) {
			restoreHud(mc);
		}
	}

	private void restoreHud(Minecraft mc) {
		if (hudHiddenForShot) {
			hudHiddenForShot = false;
			if (mc.gui.hud.isHidden()) {
				mc.gui.hud.toggle();
			}
		}
	}

	/**
	 * Runs a server command as the player, for benchmark resets such as {@code /clear}. Allowed in
	 * singleplayer, and on a server only with bridge.allowCommands in the config, since the
	 * commands run with the player's own permissions there.
	 */
	private void runCommand(Minecraft mc, JsonElement id, String command) {
		if (mc.player == null) {
			server.send(error(id, "no world loaded"));
			return;
		}
		if (!mc.hasSingleplayerServer() && !MineGPTClient.config().bridgeAllowCommands) {
			server.send(error(id, "commands from the brain are off on servers; set bridge.allowCommands=true in "
					+ "config/minegpt.properties to allow them"));
			return;
		}
		String text = command.startsWith("/") ? command.substring(1) : command;
		// The server only sends the player the commands they may use, so a missing one means no permission.
		String name = text.split(" ", 2)[0];
		if (mc.player.connection.getCommands().getRoot().getChild(name) == null) {
			server.send(error(id, "the game does not let this player use /" + name + "; turn cheats on (Open to LAN, "
					+ "Allow Cheats) or make the player an operator"));
			return;
		}
		MineGPT.LOGGER.info("Running command from the brain: /{}", text);
		mc.player.connection.sendCommand(text);
		server.send(reply("ack", id));
	}

	/**
	 * The block at each position, or null where the chunk is not loaded. With {@code states}, a
	 * block that can be turned comes with how it is, like {@code minecraft:oak_stairs[facing=east,half=bottom]}.
	 */
	private static JsonObject blocks(Minecraft mc, JsonElement id, JsonArray positions, boolean states) {
		if (positions.size() > 4096) {
			throw new IllegalArgumentException("blocks: at most 4096 positions at a time");
		}
		JsonArray out = new JsonArray();
		for (JsonElement element : positions) {
			JsonArray xyz = element.getAsJsonArray();
			BlockPos pos = new BlockPos(xyz.get(0).getAsInt(), xyz.get(1).getAsInt(), xyz.get(2).getAsInt());
			if (!mc.level.hasChunkAt(pos)) {
				out.add((String) null);
			} else {
				out.add(states ? com.xzier9.minegpt.client.skill.BlockStates.describe(mc.level.getBlockState(pos))
						: BuiltInRegistries.BLOCK.getKey(mc.level.getBlockState(pos).getBlock()).toString());
			}
		}
		JsonObject reply = reply("blocks", id);
		reply.add("blocks", out);
		return reply;
	}

	/** Starts a skill; its skill_result reply, with the request's id, comes when it ends. */
	private void startSkill(Minecraft mc, JsonElement id, JsonObject request) {
		if (mc.player == null) {
			server.send(error(id, "no world loaded"));
			return;
		}
		String name = request.get("name").getAsString();
		JsonObject args = request.has("args") ? request.getAsJsonObject("args") : new JsonObject();
		// Settings go in before the skill is made, since some are read when it is built.
		List<String> unknown = Tuning.apply(request.has("tuning") ? request.getAsJsonObject("tuning") : null);
		if (!unknown.isEmpty()) {
			MineGPT.LOGGER.debug("Ignoring unknown skill settings: {}", unknown);
		}
		Skill skill = Skills.create(name, args);
		boolean started = agent.runSkill(mc, skill, this, result -> server.send(skillResult(id, name, result)));
		if (!started) {
			server.send(skillResult(id, name, SkillResult.failure("MineGPT is paused")));
		}
	}

	/**
	 * Blocks of a kind near the player, nearest first. Only blocks with a face a player could
	 * see are listed, so this is no x-ray, the same rule the mine skill follows.
	 */
	private static JsonObject find(Minecraft mc, JsonElement id, JsonObject request) {
		JsonElement spec = request.get("block");
		List<String> specs = new ArrayList<>();
		if (spec != null && spec.isJsonArray()) {
			spec.getAsJsonArray().forEach(element -> specs.add(element.getAsString()));
		} else if (spec != null) {
			specs.add(spec.getAsString());
		}
		if (specs.isEmpty()) {
			throw new IllegalArgumentException("find: give a block id or #tag");
		}
		int radius = Math.max(1, Math.min(64, request.has("radius") ? request.get("radius").getAsInt() : 32));
		int limit = Math.max(1, Math.min(64, request.has("limit") ? request.get("limit").getAsInt() : 10));
		BlockPos feet = mc.player.blockPosition();
		JsonArray blocks = new JsonArray();
		for (BlockPos pos : BlockSearch.find(mc.level, feet, radius, BlockMatcher.parse(specs), true, Set.of(), limit)) {
			JsonObject block = new JsonObject();
			JsonArray xyz = new JsonArray();
			xyz.add(pos.getX());
			xyz.add(pos.getY());
			xyz.add(pos.getZ());
			block.add("pos", xyz);
			block.addProperty("block", BuiltInRegistries.BLOCK.getKey(mc.level.getBlockState(pos).getBlock()).toString());
			block.addProperty("distance", Math.round(Math.sqrt(feet.distSqr(pos)) * 10) / 10.0);
			blocks.add(block);
		}
		JsonObject reply = reply("found", id);
		reply.add("blocks", blocks);
		return reply;
	}

	private static JsonObject skillResult(JsonElement id, String name, SkillResult result) {
		JsonObject reply = reply("skill_result", id);
		reply.addProperty("skill", name);
		reply.addProperty("success", result.success());
		reply.addProperty("message", result.message());
		JsonObject data = result.data();
		if (!result.success() && sceneWorthy(result.message())) {
			// What the player was in when it failed, for the brain's failure miner.
			JsonObject scene = Scene.capture(Minecraft.getInstance());
			if (scene != null) {
				data.add("scene", scene);
			}
		}
		reply.add("data", data);
		return reply;
	}

	/** Failures that say something about the world: not ones the brain or the player caused by stopping the skill. */
	private static boolean sceneWorthy(String message) {
		return message != null && !message.equals("MineGPT is paused") && !message.startsWith("cancelled: cancelled by")
				&& !message.startsWith("cancelled: replaced by") && !message.startsWith("cancelled: the brain disconnected")
				&& !message.startsWith("cancelled: the goal");
	}

	/** Returns false when nothing was applied because the agent is paused. */
	private boolean applyActions(Minecraft mc, JsonObject request) {
		if (!request.has("actions")) {
			return true;
		}
		JsonArray actions = request.getAsJsonArray("actions");
		// Parse everything first so one bad action rejects the whole request.
		List<Action> parsed = new ArrayList<>();
		for (JsonElement element : actions) {
			parsed.add(Action.fromJson(element.getAsJsonObject()));
		}
		for (Action action : parsed) {
			if (!agent.act(mc, action)) {
				return false;
			}
		}
		return true;
	}

	private JsonObject hello(Minecraft mc, JsonElement id) {
		JsonObject reply = status(id);
		reply.addProperty("op", "hello");
		reply.addProperty("protocol", PROTOCOL_VERSION);
		reply.addProperty("schema", Observer.SCHEMA_VERSION);
		reply.addProperty("version", FabricLoader.getInstance().getModContainer(MineGPT.MOD_ID)
				.map(mod -> mod.getMetadata().getVersion().getFriendlyString())
				.orElse("unknown"));
		reply.addProperty("inWorld", mc.player != null);
		JsonArray skills = new JsonArray();
		Skills.NAMES.forEach(skills::add);
		reply.add("skills", skills);
		reply.add("safety", MineGPTClient.safety().toJson(mc));
		return reply;
	}

	private JsonObject status(JsonElement id) {
		JsonObject reply = reply("status", id);
		reply.addProperty("mode", agent.mode().name().toLowerCase(Locale.ROOT));
		reply.addProperty("goal", agent.goal());
		reply.addProperty("status", agent.status());
		reply.addProperty("skill", agent.skill() == null ? null : agent.skill().name());
		reply.addProperty("pauseReason", agent.pauseReason());
		reply.addProperty("tick", tick);
		return reply;
	}

	private JsonObject observation(Minecraft mc, JsonElement id, boolean grid) {
		JsonObject obs = Observer.observe(mc, grid);
		if (obs == null) {
			return error(id, "no world loaded");
		}
		JsonObject reply = reply("obs", id);
		reply.addProperty("tick", tick);
		reply.addProperty("mode", agent.mode().name().toLowerCase(Locale.ROOT));
		reply.add("obs", obs);
		return reply;
	}

	private static JsonObject event(String name) {
		JsonObject message = new JsonObject();
		message.addProperty("op", "event");
		message.addProperty("event", name);
		return message;
	}

	private static JsonObject reply(String op, JsonElement id) {
		JsonObject reply = new JsonObject();
		reply.addProperty("op", op);
		if (id != null) {
			reply.add("id", id);
		}
		return reply;
	}

	private static JsonObject error(JsonElement id, String message) {
		JsonObject reply = reply("error", id);
		reply.addProperty("message", message);
		return reply;
	}

	private static boolean flag(JsonObject json, String key, boolean fallback) {
		return json.has(key) ? json.get(key).getAsBoolean() : fallback;
	}
}
