package com.xzier9.minegpt.client.bridge;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;
import com.google.gson.JsonParser;
import com.xzier9.minegpt.MineGPT;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.atomic.AtomicReference;

/**
 * The network side of the bridge: a TCP server on localhost that exchanges one JSON object
 * per line with a single Python client.
 *
 * All socket work happens on background threads. Incoming messages and connection changes
 * are queued for {@link BridgeHandler} to process on the client thread, and outgoing
 * messages are queued for a writer thread, so a slow client never stalls the game.
 */
public final class BridgeServer {
	/** What the client thread sees, in arrival order. */
	public sealed interface Event {
		record Connected(String remote) implements Event {
		}

		record Message(JsonObject json) implements Event {
		}

		record Disconnected() implements Event {
		}
	}

	// serializeNulls keeps fields such as "goal": null, which clients expect to be present.
	private static final Gson GSON = new GsonBuilder().serializeNulls().create();
	private static final JsonObject POISON = new JsonObject();

	private final ConcurrentLinkedQueue<Event> incoming = new ConcurrentLinkedQueue<>();
	private volatile BlockingQueue<JsonObject> outgoing;
	private volatile ServerSocket serverSocket;
	private final AtomicReference<Socket> client = new AtomicReference<>();
	private volatile int port;

	public synchronized void start(int port) throws IOException {
		if (serverSocket != null) {
			return;
		}
		// Bound to loopback only: nothing outside this computer can connect.
		ServerSocket socket = new ServerSocket();
		socket.bind(new InetSocketAddress(InetAddress.getLoopbackAddress(), port));
		serverSocket = socket;
		this.port = port;

		Thread acceptor = new Thread(() -> acceptLoop(socket), "MineGPT bridge");
		acceptor.setDaemon(true);
		acceptor.start();
		MineGPT.LOGGER.info("Bridge listening on 127.0.0.1:{}", port);
	}

	public synchronized void stop() {
		ServerSocket socket = serverSocket;
		serverSocket = null;
		closeQuietly(socket);
		closeQuietly(client.get());
	}

	public boolean isRunning() {
		return serverSocket != null;
	}

	public boolean isConnected() {
		Socket c = client.get();
		return c != null && !c.isClosed();
	}

	public int port() {
		return port;
	}

	public Event poll() {
		return incoming.poll();
	}

	/** Queues a message for the connected client. Dropped if nobody is connected. */
	public void send(JsonObject message) {
		BlockingQueue<JsonObject> queue = outgoing;
		if (queue != null) {
			queue.offer(message);
		}
	}

	private void acceptLoop(ServerSocket socket) {
		while (!socket.isClosed()) {
			Socket accepted;
			try {
				accepted = socket.accept();
			} catch (IOException e) {
				if (!socket.isClosed()) {
					MineGPT.LOGGER.error("Bridge stopped accepting connections", e);
				}
				return;
			}
			if (isConnected()) {
				// One brain at a time; a second client would fight over the controls.
				MineGPT.LOGGER.warn("Refused a second bridge client from {}", accepted.getRemoteSocketAddress());
				closeQuietly(accepted);
				continue;
			}
			// Claim the slot before handing off, so the next accept sees it taken.
			client.set(accepted);
			Thread reader = new Thread(() -> serve(accepted), "MineGPT bridge reader");
			reader.setDaemon(true);
			reader.start();
		}
	}

	private void serve(Socket socket) {
		BlockingQueue<JsonObject> queue = new LinkedBlockingQueue<>();
		outgoing = queue;
		incoming.add(new Event.Connected(String.valueOf(socket.getRemoteSocketAddress())));
		MineGPT.LOGGER.info("Bridge client connected from {}", socket.getRemoteSocketAddress());

		Thread writer = new Thread(() -> writeLoop(socket, queue), "MineGPT bridge writer");
		writer.setDaemon(true);
		writer.start();

		try (BufferedReader reader = new BufferedReader(
				new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8))) {
			socket.setTcpNoDelay(true);
			String line;
			while ((line = reader.readLine()) != null) {
				if (line.isBlank()) {
					continue;
				}
				try {
					incoming.add(new Event.Message(JsonParser.parseString(line).getAsJsonObject()));
				} catch (JsonParseException | IllegalStateException e) {
					JsonObject error = new JsonObject();
					error.addProperty("op", "error");
					error.addProperty("message", "not a JSON object: " + e.getMessage());
					queue.offer(error);
				}
			}
		} catch (IOException e) {
			if (!socket.isClosed()) {
				MineGPT.LOGGER.warn("Bridge connection lost: {}", e.getMessage());
			}
		} finally {
			outgoing = null;
			queue.offer(POISON);
			closeQuietly(socket);
			// Only clear our own slot; a new client may already have taken it.
			client.compareAndSet(socket, null);
			incoming.add(new Event.Disconnected());
			MineGPT.LOGGER.info("Bridge client disconnected");
		}
	}

	private static void writeLoop(Socket socket, BlockingQueue<JsonObject> queue) {
		try (BufferedWriter writer = new BufferedWriter(
				new OutputStreamWriter(socket.getOutputStream(), StandardCharsets.UTF_8))) {
			while (true) {
				JsonObject message = queue.take();
				if (message == POISON) {
					return;
				}
				writer.write(GSON.toJson(message));
				writer.write('\n');
				if (queue.isEmpty()) {
					writer.flush();
				}
			}
		} catch (IOException | InterruptedException e) {
			closeQuietly(socket);
		}
	}

	private static void closeQuietly(AutoCloseable closeable) {
		if (closeable == null) {
			return;
		}
		try {
			closeable.close();
		} catch (Exception ignored) {
			// Already closing; nothing useful to do.
		}
	}
}
