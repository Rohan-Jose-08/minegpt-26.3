package com.xzier9.minegpt.client.command;

import static net.fabricmc.fabric.api.client.command.v2.ClientCommands.argument;
import static net.fabricmc.fabric.api.client.command.v2.ClientCommands.literal;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;
import com.google.gson.JsonParser;
import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import com.xzier9.minegpt.MineGPT;
import com.xzier9.minegpt.client.MineGPTClient;
import com.xzier9.minegpt.client.act.Action;
import com.xzier9.minegpt.client.agent.Agent;
import com.xzier9.minegpt.client.observe.Observer;
import com.xzier9.minegpt.client.record.Recorder;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import net.fabricmc.fabric.api.client.command.v2.FabricClientCommandSource;
import net.minecraft.client.Minecraft;
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
				.then(literal("obs").executes(MineGPTCommands::dumpObservation))
				.then(literal("record")
						.then(literal("start").executes(MineGPTCommands::recordStart))
						.then(literal("stop").executes(ctx -> {
							Recorder recorder = MineGPTClient.recorder();
							if (!recorder.isRecording()) {
								return error(ctx, "Not recording.");
							}
							recorder.stop();
							return feedback(ctx, "Saved " + recorder.ticks() + " ticks to " + recorder.file().getFileName());
						}))));
	}

	private static int goal(CommandContext<FabricClientCommandSource> ctx) {
		String text = StringArgumentType.getString(ctx, "text");
		agent().setGoal(text);
		agent().setStatus("Goal received, waiting for the brain to connect");
		return feedback(ctx, "Goal set: " + text);
	}

	private static int status(CommandContext<FabricClientCommandSource> ctx) {
		Agent agent = agent();
		String goal = agent.goal() == null ? "none" : agent.goal();
		String recording = MineGPTClient.recorder().isRecording() ? ", recording" : "";
		return feedback(ctx, agent.mode() + recording + ". Goal: " + goal + ". " + agent.status());
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
