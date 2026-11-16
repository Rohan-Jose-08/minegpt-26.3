package com.xzier9.minegpt.client.command;

import static net.fabricmc.fabric.api.client.command.v2.ClientCommands.argument;
import static net.fabricmc.fabric.api.client.command.v2.ClientCommands.literal;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;
import com.google.gson.JsonParser;
import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.IntegerArgumentType;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import com.xzier9.minegpt.MineGPT;
import com.xzier9.minegpt.client.MineGPTClient;
import com.xzier9.minegpt.client.act.Action;
import com.xzier9.minegpt.client.agent.Agent;
import com.xzier9.minegpt.client.bridge.BridgeServer;
import com.xzier9.minegpt.client.observe.Observer;
import com.xzier9.minegpt.client.record.Recorder;
import com.xzier9.minegpt.client.safety.Safety;
import com.xzier9.minegpt.client.skill.Skill;
import com.xzier9.minegpt.client.skill.Skills;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import net.fabricmc.fabric.api.client.command.v2.FabricClientCommandSource;
import net.minecraft.client.Minecraft;
import net.minecraft.commands.SharedSuggestionProvider;
import net.minecraft.network.chat.Component;

/**
 * Client-side {@code /minegpt} commands. Client commands never reach the server, so they
 * work on any multiplayer server without needing permissions there.
 */
public final class MineGPTCommands {
	private MineGPTCommands() {
	}

	public static void register(CommandDispatcher<FabricClientCommandSource> dispatcher) {
		dispatcher.register(literal("minegpt")
				.then(literal("goal")
						.then(argument("text", StringArgumentType.greedyString())
								.executes(MineGPTCommands::goal)))
				.then(literal("pause").executes(ctx -> {
					agent().pause(ctx.getSource().getClient());
					return feedback(ctx, "Paused. Use /minegpt resume or press the pause key.");
				}))
				.then(literal("resume").executes(ctx -> {
					agent().resume();
					return feedback(ctx, "Resumed.");
				}))
				.then(literal("stop").executes(ctx -> {
					agent().stop(ctx.getSource().getClient());
					return feedback(ctx, "Stopped and released all keys.");
				}))
				.then(literal("status").executes(MineGPTCommands::status))
				.then(literal("act")
						.then(argument("json", StringArgumentType.greedyString())
								.executes(MineGPTCommands::act)))
				.then(literal("skill")
						.then(argument("name", StringArgumentType.word())
								.suggests((ctx, builder) -> SharedSuggestionProvider.suggest(Skills.NAMES, builder))
								.executes(ctx -> skill(ctx, "{}"))
								.then(argument("args", StringArgumentType.greedyString())
										.executes(ctx -> skill(ctx, StringArgumentType.getString(ctx, "args"))))))
				.then(literal("obs").executes(MineGPTCommands::dumpObservation))
				.then(literal("bridge")
						.then(literal("start").executes(MineGPTCommands::bridgeStart))
						.then(literal("stop").executes(ctx -> {
							MineGPTClient.bridge().stop();
							return feedback(ctx, "Bridge stopped.");
						}))
						.then(literal("status").executes(ctx -> {
							BridgeServer bridge = MineGPTClient.bridge();
							if (!bridge.isRunning()) {
								return feedback(ctx, "Bridge is off. Start it with /minegpt bridge start.");
							}
							return feedback(ctx, "Bridge on 127.0.0.1:" + bridge.port()
									+ (bridge.isConnected() ? ", brain connected." : ", waiting for the brain."));
						})))
				.then(literal("record")
						.then(literal("start").executes(MineGPTCommands::recordStart))
						.then(literal("stop").executes(ctx -> {
							Recorder recorder = MineGPTClient.recorder();
							if (!recorder.isRecording()) {
								return error(ctx, "Not recording.");
							}
							recorder.stop();
							return feedback(ctx, "Saved " + recorder.ticks() + " ticks to " + recorder.file().getFileName());
						})))
				.then(literal("protect")
						.then(literal("here")
								.then(argument("name", StringArgumentType.word())
										.executes(ctx -> protectHere(ctx, 16))
										.then(argument("radius", IntegerArgumentType.integer(1, 512))
												.executes(ctx -> protectHere(ctx, IntegerArgumentType.getInteger(ctx, "radius"))))))
						.then(literal("area")
								.then(argument("name", StringArgumentType.word())
										.then(argument("x1", IntegerArgumentType.integer())
												.then(argument("z1", IntegerArgumentType.integer())
														.then(argument("x2", IntegerArgumentType.integer())
																.then(argument("z2", IntegerArgumentType.integer())
																		.executes(MineGPTCommands::protectArea)))))))
						.then(literal("farm")
								.then(argument("name", StringArgumentType.word())
										.then(literal("on").executes(ctx -> protectFarm(ctx, true)))
										.then(literal("off").executes(ctx -> protectFarm(ctx, false)))))
						.then(literal("remove")
								.then(argument("name", StringArgumentType.word())
										.executes(MineGPTCommands::protectRemove)))
						.then(literal("list").executes(MineGPTCommands::protectList)))
				.then(literal("job")
						.executes(ctx -> job(ctx, ""))
						.then(argument("text", StringArgumentType.greedyString())
								.executes(ctx -> job(ctx, StringArgumentType.getString(ctx, "text"))))));
	}

	private static int goal(CommandContext<FabricClientCommandSource> ctx) {
		String text = StringArgumentType.getString(ctx, "text");
		agent().setGoal(text);
		agent().setStatus("Goal received, waiting for the brain to connect");
		return feedback(ctx, "Goal set: " + text);
	}

	private static int protectHere(CommandContext<FabricClientCommandSource> ctx, int radius) {
		Minecraft mc = ctx.getSource().getClient();
		int x = mc.player.blockPosition().getX();
		int z = mc.player.blockPosition().getZ();
		Safety.Region region = MineGPTClient.safety().protect(mc, StringArgumentType.getString(ctx, "name"), x - radius,
				z - radius, x + radius, z + radius, false);
		return feedback(ctx, "Protected " + region.describe() + ", at every height. MineGPT will not break or place "
				+ "blocks there. Use /minegpt protect farm " + region.name() + " on to let it tend crops inside.");
	}

	private static int protectArea(CommandContext<FabricClientCommandSource> ctx) {
		Safety.Region region = MineGPTClient.safety().protect(ctx.getSource().getClient(),
				StringArgumentType.getString(ctx, "name"), IntegerArgumentType.getInteger(ctx, "x1"),
				IntegerArgumentType.getInteger(ctx, "z1"), IntegerArgumentType.getInteger(ctx, "x2"),
				IntegerArgumentType.getInteger(ctx, "z2"), false);
		return feedback(ctx, "Protected " + region.describe() + ", at every height.");
	}

	private static int protectFarm(CommandContext<FabricClientCommandSource> ctx, boolean farm) {
		String name = StringArgumentType.getString(ctx, "name");
		if (!MineGPTClient.safety().setFarm(ctx.getSource().getClient(), name, farm)) {
			return error(ctx, "No protected region called " + name + " in this world.");
		}
		return feedback(ctx, farm ? "MineGPT may now harvest, replant and till inside " + name + "."
				: "MineGPT may no longer farm inside " + name + ".");
	}

	private static int protectRemove(CommandContext<FabricClientCommandSource> ctx) {
		String name = StringArgumentType.getString(ctx, "name");
		if (!MineGPTClient.safety().unprotect(ctx.getSource().getClient(), name)) {
			return error(ctx, "No protected region called " + name + " in this world.");
		}
		return feedback(ctx, "Removed the protected region " + name + ".");
	}

	private static int protectList(CommandContext<FabricClientCommandSource> ctx) {
		List<Safety.Region> regions = MineGPTClient.safety().regions(ctx.getSource().getClient());
		if (regions.isEmpty()) {
			return feedback(ctx, "No protected regions in this world. Add one with /minegpt protect here <name> [radius].");
		}
		feedback(ctx, regions.size() + " protected region" + (regions.size() == 1 ? "" : "s") + ":");
		regions.forEach(r -> ctx.getSource().sendFeedback(Component.literal("  " + r.describe() + " (" + r.dimension() + ")")));
		return 1;
	}

	/** Recurring jobs live in the brain, so the command is passed on to it. */
	private static int job(CommandContext<FabricClientCommandSource> ctx, String text) {
		if (!MineGPTClient.bridgeHandler().sendCommand("job", text)) {
			return error(ctx, "The brain is not connected. Start it first (see the README), then try again.");
		}
		return 1;
	}

	private static int status(CommandContext<FabricClientCommandSource> ctx) {
		Agent agent = agent();
		String goal = agent.goal() == null ? "none" : agent.goal();
		String recording = MineGPTClient.recorder().isRecording() ? ", recording" : "";
		Safety safety = MineGPTClient.safety();
		return feedback(ctx, agent.mode() + recording + ". Goal: " + goal + ". " + agent.status() + ". Actions in the "
				+ "last minute: " + safety.actionsLastMinute() + (safety.maxActionsPerMinute() > 0 ? " of "
				+ safety.maxActionsPerMinute() : "") + ". Protected regions here: " + safety.regions(ctx.getSource().getClient()).size() + ".");
	}

	/** Runs one action from JSON, for trying the action layer by hand. */
	private static int act(CommandContext<FabricClientCommandSource> ctx) {
		Action action;
		try {
			JsonObject json = JsonParser.parseString(StringArgumentType.getString(ctx, "json")).getAsJsonObject();
			action = Action.fromJson(json);
		} catch (JsonParseException | IllegalStateException | IllegalArgumentException e) {
			return error(ctx, "Bad action: " + e.getMessage());
		}
		if (!agent().act(ctx.getSource().getClient(), action)) {
			return error(ctx, "MineGPT is paused. Resume it first.");
		}
		return 1;
	}

	/** Runs one skill by hand, such as {@code /minegpt skill mine {"block": "#minecraft:logs", "count": 4}}. */
	private static int skill(CommandContext<FabricClientCommandSource> ctx, String json) {
		String name = StringArgumentType.getString(ctx, "name");
		Skill skill;
		try {
			skill = Skills.create(name, JsonParser.parseString(json).getAsJsonObject());
		} catch (JsonParseException | IllegalStateException | IllegalArgumentException e) {
			return error(ctx, "Bad skill: " + e.getMessage());
		}
		Minecraft mc = ctx.getSource().getClient();
		boolean started = agent().runSkill(mc, skill, MineGPTCommands.class, result -> {
			if (mc.player != null) {
				mc.player.sendSystemMessage(Component.literal("[MineGPT] " + name
						+ (result.success() ? " done: " : " failed: ") + result.message()));
			}
		});
		if (!started) {
			return error(ctx, "MineGPT is paused. Resume it first.");
		}
		return feedback(ctx, "Running " + name + ". Use /minegpt stop to cancel.");
	}

	/** Writes the current observation to a file, since it is too long for chat. */
	private static int dumpObservation(CommandContext<FabricClientCommandSource> ctx) {
		Minecraft mc = ctx.getSource().getClient();
		JsonObject obs = Observer.observe(mc, true);
		if (obs == null) {
			return error(ctx, "No world loaded.");
		}
		Path file = mc.gameDirectory.toPath().resolve("minegpt").resolve("observation.json");
		try {
			Files.createDirectories(file.getParent());
			Files.writeString(file, new GsonBuilder().setPrettyPrinting().create().toJson(obs), StandardCharsets.UTF_8);
		} catch (IOException e) {
			MineGPT.LOGGER.error("Could not write {}", file, e);
			return error(ctx, "Could not write " + file + ": " + e.getMessage());
		}
		int entities = obs.getAsJsonArray("entities").size();
		int items = obs.getAsJsonObject("inventory").getAsJsonArray("slots").size();
		return feedback(ctx, "Saved observation (" + entities + " entities, " + items + " item stacks) to " + file);
	}

	private static int bridgeStart(CommandContext<FabricClientCommandSource> ctx) {
		int port = MineGPTClient.config().bridgePort;
		try {
			MineGPTClient.bridge().start(port);
		} catch (IOException e) {
			MineGPT.LOGGER.error("Could not start the bridge on port {}", port, e);
			return error(ctx, "Could not open port " + port + ": " + e.getMessage()
					+ ". Change bridge.port in config/minegpt.properties if something else uses it.");
		}
		return feedback(ctx, "Bridge listening on 127.0.0.1:" + port + ". Connect with the Python brain.");
	}

	private static int recordStart(CommandContext<FabricClientCommandSource> ctx) {
		try {
			Path file = MineGPTClient.recorder().start(ctx.getSource().getClient());
			return feedback(ctx, "Recording to " + file.getFileName() + ". Use /minegpt record stop to finish.");
		} catch (IOException e) {
			MineGPT.LOGGER.error("Could not start recording", e);
			return error(ctx, "Could not start recording: " + e.getMessage());
		}
	}

	private static Agent agent() {
		return MineGPTClient.agent();
	}

	private static int feedback(CommandContext<FabricClientCommandSource> ctx, String message) {
		ctx.getSource().sendFeedback(Component.literal("[MineGPT] " + message));
		return 1;
	}

	private static int error(CommandContext<FabricClientCommandSource> ctx, String message) {
		ctx.getSource().sendError(Component.literal("[MineGPT] " + message));
		return 0;
	}
}
