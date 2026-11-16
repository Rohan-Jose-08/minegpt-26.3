"""Client for the MineGPT mod's bridge: one JSON object per line over a local TCP socket.

The mod side is ``BridgeServer`` / ``BridgeHandler``; the message format is in
docs/protocol.md.

Example::

    from minegpt_brain.bridge import Bridge

    with Bridge.connect() as mc:
        obs = mc.observe()
        obs = mc.step([{"type": "move", "keys": ["forward"], "ticks": 20}], ticks=20)
"""

from __future__ import annotations

import itertools
import base64
import json
import queue
import socket
import threading
from typing import Any, Iterable, Optional

from .observation import Observation

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5577
PROTOCOL_VERSION = 1


class BridgeError(RuntimeError):
    """The mod answered a request with an error."""


class Bridge:
    """A connection to the mod. Safe to call from one thread at a time."""

    def __init__(self, sock: socket.socket, timeout: float = 30.0):
        self._sock = sock
        self._timeout = timeout
        self._ids = itertools.count(1)
        self._replies: dict[int, dict[str, Any]] = {}
        self._replies_cond = threading.Condition()
        self._closed = False
        #: Messages the mod sends on its own: goal and mode changes, and streamed observations.
        self.events: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, name="minegpt-bridge", daemon=True)
        self._reader.start()
        self.info = self._request({"op": "hello", "protocol": PROTOCOL_VERSION})
        if self.info.get("protocol") != PROTOCOL_VERSION:
            raise BridgeError(
                f"mod speaks bridge protocol {self.info.get('protocol')}, this client speaks {PROTOCOL_VERSION}"
            )

    @classmethod
    def connect(cls, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 30.0) -> "Bridge":
        """Connects to a running game. Start the bridge in game with ``/minegpt bridge start``."""
        try:
            sock = socket.create_connection((host, port), timeout=10.0)
        except ConnectionRefusedError as e:
            raise ConnectionRefusedError(
                f"nothing is listening on {host}:{port}. Is the game running, and did you run /minegpt bridge start?"
            ) from e
        sock.settimeout(None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return cls(sock, timeout)

    # Requests -------------------------------------------------------------------------

    def observe(self, grid: bool = True) -> Observation:
        """The current observation, without doing anything."""
        return self._observation(self._request({"op": "observe", "grid": grid}))

    def act(self, actions: Iterable[dict[str, Any]]) -> bool:
        """Starts actions and returns at once. False means the agent is paused and nothing ran."""
        return bool(self._request({"op": "act", "actions": list(actions)}).get("applied", False))

    def step(self, actions: Iterable[dict[str, Any]] = (), ticks: int = 1, grid: bool = True) -> Observation:
        """Starts actions, lets ``ticks`` game ticks pass, and returns the observation after them."""
        reply = self._request({"op": "step", "actions": list(actions), "ticks": ticks, "grid": grid})
        return self._observation(reply)

    def status(self) -> dict[str, Any]:
        """Mode (idle, running, paused), goal, status text and tick counter."""
        return self._request({"op": "status"})

    def set_status(self, text: str) -> None:
        """Shows a line of text in the in-game HUD."""
        self._request({"op": "set_status", "text": text})

    def finish_goal(self, message: str = "Goal finished") -> None:
        """Clears the goal, releases the controls and shows ``message`` in the HUD."""
        self._request({"op": "finish_goal", "message": message})

    def set_goal(self, goal: Optional[str]) -> None:
        """Sets the goal, as ``/minegpt goal`` does in game, or clears it with None.

        The mod then sends a ``goal`` event, like it does for a goal typed in game.
        """
        self._request({"op": "set_goal", "goal": goal})

    def find(self, block: str | list[str], radius: Optional[int] = None,
             limit: Optional[int] = None) -> list[dict[str, Any]]:
        """Blocks a player could see within ``radius`` (32, at most 64), nearest first.

        ``block`` is an id, a ``#tag`` or a list of them. Each result has ``pos`` [x, y, z],
        ``block`` and ``distance``. At most ``limit`` (10, at most 64) are returned.
        """
        message: dict[str, Any] = {"op": "find", "block": block}
        if radius is not None:
            message["radius"] = radius
        if limit is not None:
            message["limit"] = limit
        return self._request(message)["blocks"]

    def blocks(self, positions: Iterable[Iterable[int]], states: bool = False) -> list[Optional[str]]:
        """The block id at each [x, y, z], or None where the chunk is not loaded (at most 4096).

        With ``states``, a block that can be turned comes with how it is turned, like
        ``minecraft:oak_stairs[facing=east,half=bottom]`` (a mod older than 0.8 ignores this).
        """
        message: dict[str, Any] = {"op": "blocks", "positions": [list(p) for p in positions]}
        if states:
            message["states"] = True
        return self._request(message)["blocks"]

    def audit(self, blocks: list[list[Any]], box: Optional[list[int]] = None,
              limit: Optional[int] = None, ground: Optional[str] = None) -> dict[str, Any]:
        """Compares the world with a blueprint: ``blocks`` as for the build skill (at most 8192).

        The reply counts ``checked``, ``right``, ``missing``, ``wrong`` (another block there),
        ``turned`` (the right block the wrong way round), ``blocked`` (should be empty) and, with a
        ``box`` [x1, y1, z1, x2, y2, z2], ``extra`` (stray blocks elsewhere in it). ``problems``
        lists up to ``limit`` of them (40, at most 400), each with ``kind``, ``x``, ``y``, ``z``,
        ``want``, ``is`` and ``own`` (whether the agent placed what stands there). ``ground`` names
        the footing item: spots wanting it that hold solid natural ground count as right.
        """
        message: dict[str, Any] = {"op": "audit", "blocks": blocks}
        if ground:
            message["ground"] = ground
        if box is not None:
            message["box"] = list(box)
        if limit is not None:
            message["limit"] = limit
        return self._request(message)

    def survey(self, width: int, depth: int, radius: Optional[int] = None,
               limit: Optional[int] = None) -> list[dict[str, Any]]:
        """Flat, open sites of ``width`` (x) by ``depth`` (z) blocks near the player, best first.

        Each has ``x``, ``y``, ``z`` (the west, north corner, at the level to walk on), ``work``
        (blocks to dig or fill to level it), ``trees`` and ``water`` (columns of each) and
        ``distance``. Sites over something built, lava or a protected region are left out.
        """
        message: dict[str, Any] = {"op": "survey", "width": width, "depth": depth}
        if radius is not None:
            message["radius"] = radius
        if limit is not None:
            message["limit"] = limit
        return self._request(message)["sites"]

    def screenshot(self, width: int = 640, hide_hud: bool = True, timeout: float = 20.0) -> dict[str, Any]:
        """A picture of what the player sees: ``data`` (the image file's bytes), ``format`` (jpeg or png),
        ``width`` and ``height``. The mod hides the HUD for a moment while it is taken."""
        reply = self._request({"op": "screenshot", "width": int(width), "hideHud": hide_hud}, timeout=timeout)
        return {"data": base64.b64decode(reply["data"]), "format": reply.get("format", "jpeg"),
                "width": reply.get("width", 0), "height": reply.get("height", 0)}

    def safety(self) -> dict[str, Any]:
        """The safety rails: ``regions`` the agent may not break or build in (set in game with
        ``/minegpt protect``), ``maxActionsPerMinute`` and ``stopHealth``."""
        return self._request({"op": "safety"})["safety"]

    def notify(self, text: str) -> None:
        """Shows a chat line to the player. It only appears on their screen, never in server chat."""
        self._request({"op": "notify", "text": text})

    def run_command(self, command: str) -> None:
        """Runs a server command as the player, like ``/clear``. Only works in singleplayer, or on a
        server with ``bridge.allowCommands=true`` in the mod's config. The command's own reply
        is not returned."""
        self._request({"op": "run_command", "command": command})

    def subscribe(self, every: int, grid_every: int = 0) -> None:
        """Asks for an observation every ``every`` ticks on :attr:`events` (0 turns it off)."""
        self._request({"op": "subscribe", "every": every, "gridEvery": grid_every})

    def release(self) -> None:
        """Lets go of every key."""
        self.act([{"type": "stop"}])

    def skill(self, name: str, args: Optional[dict[str, Any]] = None, timeout: Optional[float] = None,
              tuning: Optional[dict[str, float]] = None) -> dict[str, Any]:
        """Runs a skill in game and waits for its ``skill_result`` reply.

        The mod enforces each skill's own time limit, so ``timeout`` here is only a backstop
        in case the reply never comes. ``tuning`` sets the mod's skill settings for this call
        and later ones (see :mod:`minegpt_brain.practice.tuner`); ``None`` leaves them as they are.
        Most code should use :class:`minegpt_brain.skills.Skills`.
        """
        message: dict[str, Any] = {"op": "skill", "name": name, "args": args or {}}
        if tuning is not None:
            message["tuning"] = tuning
        try:
            return self._request(message, timeout=3600.0 if timeout is None else timeout)
        except KeyboardInterrupt:
            # Ctrl+C in the script should stop the player too.
            self.cancel_skill()
            raise

    def cancel_skill(self) -> None:
        """Stops the running skill; its result reports it as cancelled."""
        self._request({"op": "cancel_skill"})

    def tuning(self) -> list[dict[str, Any]]:
        """The mod's skill settings: name, default, range and the value in force."""
        return self._request({"op": "tuning"})["knobs"]

    def recipes(self, item: Optional[str] = None, craftable: bool = False) -> list[dict[str, Any]]:
        """Crafting recipes the player has unlocked, optionally only those making ``item``."""
        message: dict[str, Any] = {"op": "recipes", "craftable": craftable}
        if item is not None:
            message["item"] = item
        return self._request(message)["recipes"]

    # Connection -----------------------------------------------------------------------

    def close(self) -> None:
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
        with self._replies_cond:
            self._replies_cond.notify_all()

    def __enter__(self) -> "Bridge":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # Internals ------------------------------------------------------------------------

    def _request(self, message: dict[str, Any], timeout: Optional[float] = None) -> dict[str, Any]:
        if self._closed:
            raise ConnectionError("bridge is closed")
        request_id = next(self._ids)
        message = {**message, "id": request_id}
        self._sock.sendall((json.dumps(message) + "\n").encode("utf-8"))

        timeout = self._timeout if timeout is None else timeout
        with self._replies_cond:
            ok = self._replies_cond.wait_for(
                lambda: request_id in self._replies or self._closed, timeout=timeout
            )
            if request_id not in self._replies:
                if self._closed:
                    raise ConnectionError("the game closed the bridge connection")
                if not ok:
                    raise TimeoutError(f"no reply to {message['op']} after {timeout}s")
            reply = self._replies.pop(request_id)
        if reply.get("op") == "error":
            raise BridgeError(reply.get("message", "unknown error"))
        return reply

    @staticmethod
    def _observation(reply: dict[str, Any]) -> Observation:
        return Observation(reply["obs"], tick=reply.get("tick", 0), mode=reply.get("mode", "idle"),
                           applied=reply.get("applied", True))

    def _read_loop(self) -> None:
        buffer = b""
        try:
            while True:
                chunk = self._sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.strip():
                        self._dispatch(json.loads(line))
        except OSError:
            pass
        finally:
            self._closed = True
            with self._replies_cond:
                self._replies_cond.notify_all()

    def _dispatch(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if isinstance(request_id, int):
            with self._replies_cond:
                self._replies[request_id] = message
                self._replies_cond.notify_all()
        else:
            self.events.put(message)
