"""A stand-in for the mod's bridge server, speaking the same line protocol."""

import base64
import json
import socket
import threading
import time


DEFAULT_SLOTS = [{"slot": 0, "id": "minecraft:oak_log", "count": 5},
                 {"slot": 4, "id": "minecraft:oak_log", "count": 3}]


def make_obs(x=0.5, y=64.0, z=0.5, slots=None, health=20.0, food=20, entities=(), world=None, air=300,
             in_water=False, game_time=5000, day_time=1000):
    side = 3
    # 3 layers of 3x3: stone floor, then air, with one oak log right next to the player.
    cells = [1] * 9 + [0] * 9 + [0] * 9
    cells[9 + 1 * side + 2] = 2  # dy=0, dz=0, dx=+1
    obs = {
        "schema": 1,
        "dimension": "minecraft:overworld", "biome": "minecraft:plains", "dayTime": day_time, "gameTime": game_time,
        "player": {
            "pos": [x, y, z], "yaw": 0.0, "pitch": 0.0, "velocity": [0.0, 0.0, 0.0],
            "onGround": True, "inWater": in_water, "onFire": False,
            "health": health, "maxHealth": 20.0, "armor": 0, "food": food, "saturation": 5.0,
            "air": air, "maxAir": 300, "xpLevel": 0, "xpProgress": 0.0,
            "creative": False, "usingItem": False, "effects": [],
        },
        "inventory": {"selected": 0, "slots": [dict(s) for s in (DEFAULT_SLOTS if slots is None else slots)]},
        "target": {"type": "none"},
        "entities": list(entities),
        "screen": {"open": False},
        "grid": {"origin": [0, 64, 0], "radius": 1, "below": 1, "above": 1,
                 "palette": ["minecraft:air", "minecraft:stone", "minecraft:oak_log"], "cells": cells},
    }
    if world is not None:
        obs["world"] = world
    return obs


class FakeMod:
    def __init__(self):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.received = []
        self.paused = False
        self.tick = 0
        self.goal = None
        self.status = "Waiting for a goal"
        self.conn = None
        #: Seconds a skill takes before its result is sent.
        self.skill_delay = 0.0
        self.mode = "idle"
        #: Inventory slots, changed by the default skill handler: [{"slot", "id", "count"}].
        self.slots = [dict(s) for s in DEFAULT_SLOTS]
        self.health = 20.0
        self.food = 20
        self.entities = []
        self.world = "singleplayer:Test World"
        #: What find replies with: [{"pos": [x, y, z], "block": id, "distance": d}].
        self.blocks = []
        #: Optional fn(mod, name, args) -> (success, message, data) replacing the canned results.
        self.skill_handler = None
        #: Send goal and mode events right after hello, as the mod does.
        self.announce = False
        self.game_time = 5000
        self.day_time = 1000
        #: Protected regions the safety op reports.
        self.regions = []
        #: Lines sent with notify, and commands sent with run_command.
        self.notes = []
        self.commands = []
        #: Block ids by (x, y, z) for the blocks op; anything else is air.
        self.world_blocks = {}
        #: False acts like an older mod without the audit op.
        self.audits = True
        #: Spots whose block stands turned the wrong way, with how it is turned, for the audit op.
        self.turned_wrong = {}
        #: Spots holding a stray block (scaffolding) the audit op reports when they lie in its box.
        self.strays = set()
        #: Spots whose block the agent did not place itself.
        self.foreign = set()
        #: Sites the survey op returns.
        self.sites = []
        #: Where the player stands.
        self.pos = (0.5, 64.0, 0.5)
        #: False acts like an older mod without the screenshot op.
        self.screenshots = True
        self._send_lock = threading.Lock()
        threading.Thread(target=self._serve, daemon=True).start()

    def send(self, message):
        with self._send_lock:
            self.conn.sendall((json.dumps(message) + "\n").encode())

    def _serve(self):
        self.conn, _ = self.server.accept()
        buffer = b""
        while True:
            chunk = self.conn.recv(65536)
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                self._handle(json.loads(line))

    def _handle(self, req):
        self.received.append(req)
        rid = req.get("id")
        op = req["op"]
        if op == "hello":
            self.send({"op": "hello", "id": rid, "protocol": 1, "schema": 1, "version": "test",
                       "mode": self.mode, "goal": self.goal, "status": self.status, "tick": self.tick,
                       "inWorld": True})
            if self.announce:
                self.send_event("goal", goal=self.goal)
                self.send_event("mode", mode=self.mode)
        elif op == "observe":
            self.send({"op": "obs", "id": rid, "tick": self.tick, "mode": self.mode, "obs": self.obs()})
        elif op == "act":
            self.send({"op": "ack", "id": rid, "applied": not self.paused})
        elif op == "step":
            self.tick += req.get("ticks", 1)
            self.send({"op": "obs", "id": rid, "tick": self.tick, "mode": "running",
                       "obs": make_obs(x=0.5 + self.tick * 0.1), "applied": not self.paused})
        elif op in ("set_status", "finish_goal", "subscribe"):
            if op == "set_status":
                self.status = req["text"]
            if op == "finish_goal":
                # State first, so a test reading it right after the reply sees the change.
                self.status = req.get("message", "Goal finished")
                self.goal = None
                self.mode = "idle"
            self.send({"op": "ack", "id": rid})
            if op == "finish_goal" and self.conn is not None:
                self.send_event("goal", goal=None)
        elif op == "set_goal":
            self.send({"op": "ack", "id": rid})
            self.set_goal(req.get("goal"))
        elif op == "find":
            specs = req["block"] if isinstance(req["block"], list) else [req["block"]]
            wanted = [b for b in self.blocks if b["block"] in specs or b.get("tag") in specs]
            self.send({"op": "found", "id": rid,
                       "blocks": [{k: v for k, v in b.items() if k != "tag"} for b in wanted][:req.get("limit", 10)]})
        elif op == "skill":
            threading.Thread(target=self._finish_skill, args=(req,), daemon=True).start()
        elif op == "safety":
            self.send({"op": "safety", "id": rid, "safety": {"regions": self.regions, "maxActionsPerMinute": 150,
                                                              "stopHealth": 4}})
        elif op == "notify":
            self.notes.append(req["text"])
            self.send({"op": "ack", "id": rid})
        elif op == "run_command":
            self.commands.append(req["command"])
            self.send({"op": "ack", "id": rid})
        elif op == "blocks":
            self.send({"op": "blocks", "id": rid,
                       "blocks": [self.world_blocks.get(tuple(p), "minecraft:air") for p in req["positions"]]})
        elif op == "audit":
            if not self.audits:
                self.send({"op": "error", "id": rid, "message": "unknown op \"audit\""})
            else:
                self.send({"op": "audit", "id": rid, **self._audit(req)})
        elif op == "screenshot":
            if self.screenshots:
                self.send({"op": "screenshot", "id": rid, "width": req.get("width", 640), "height": 360,
                           "format": "jpeg", "data": base64.b64encode(b"\xff\xd8fake-jpeg").decode()})
            else:
                self.send({"op": "error", "id": rid, "message": "unknown op \"screenshot\""})
        elif op == "survey":
            self.send({"op": "survey", "id": rid, "sites": list(self.sites)})
        elif op == "cancel_skill":
            self.send({"op": "ack", "id": rid})
        elif op == "recipes":
            recipes = [{"result": "minecraft:stick", "count": 4, "needsTable": False, "craftable": True,
                        "ingredients": [["minecraft:oak_planks"], ["minecraft:oak_planks"]]}]
            if req.get("item") not in (None, "minecraft:stick"):
                recipes = []
            self.send({"op": "recipes", "id": rid, "recipes": recipes})
        elif op == "status":
            self.send({"op": "status", "id": rid, "mode": "idle", "goal": self.goal,
                       "status": self.status, "tick": self.tick})
        else:
            self.send({"op": "error", "id": rid, "message": f"unknown op \"{op}\""})

    def _audit(self, req):
        """What the mod's audit op answers: the world against a list of blocks, as the real one counts it."""
        counts = dict.fromkeys(("missing", "wrong", "turned", "blocked", "extra"), 0)
        problems, right, listed = [], 0, set()

        def add(kind, pos, want):
            counts[kind] += 1
            there = self.world_blocks.get(pos, "minecraft:air")
            if pos in self.turned_wrong and kind == "turned":
                there += f"[{self.turned_wrong[pos]}]"
            if len(problems) < req.get("limit", 40):
                problems.append({"kind": kind, "x": pos[0], "y": pos[1], "z": pos[2], "want": want, "is": there,
                                 "own": there != "minecraft:air" and pos not in self.foreign})

        for entry in req["blocks"]:
            pos, item = tuple(entry[:3]), entry[3]
            listed.add(pos)
            there = self.world_blocks.get(pos)
            if item == "minecraft:air":
                if there is None:
                    right += 1
                else:
                    add("blocked", pos, "empty")
            elif there is None:
                add("missing", pos, item)
            elif there != item:
                add("wrong", pos, item)
            elif pos in self.turned_wrong:
                add("turned", pos, item + (f"[facing={entry[4]}]" if len(entry) > 4 else ""))
            else:
                right += 1
        box = req.get("box")
        if box:
            for pos in sorted(self.strays):
                inside = all(box[i] <= pos[i] <= box[i + 3] for i in range(3))
                if inside and pos not in listed and pos in self.world_blocks:
                    add("extra", pos, "empty")
        return {"checked": len(req["blocks"]), "right": right, "unloaded": 0, **counts, "problems": problems}

    def _finish_skill(self, req):
        time.sleep(self.skill_delay)
        name = req["name"]
        if self.skill_handler is not None:
            ok, message, data = self.skill_handler(self, name, req["args"])
        else:
            ok = name != "fight"
            message = "done" if ok else "health is down to 5, stopping the fight"
            data = {"broken": req["args"].get("count", 0)} if name == "mine" else {}
        self.send({"op": "skill_result", "id": req["id"], "skill": name, "success": ok, "message": message,
                   "data": data})

    # Helpers for agent tests --------------------------------------------------------------

    def obs(self):
        return make_obs(x=self.pos[0], y=self.pos[1], z=self.pos[2], slots=self.slots, health=self.health, food=self.food, entities=self.entities,
                        world=self.world, game_time=self.game_time, day_time=self.day_time)

    def send_event(self, name, **fields):
        self.send({"op": "event", "event": name, **fields})

    def set_goal(self, goal):
        self.goal = goal
        if self.conn is not None:
            self.send_event("goal", goal=goal)

    def set_mode(self, mode):
        self.mode = mode
        self.send_event("mode", mode=mode)

    def give(self, item, count, slot=None):
        item = item if ":" in item else f"minecraft:{item}"
        for s in self.slots:
            if s["id"] == item and slot is None and s["slot"] < 36:
                s["count"] += count
                return
        used = {s["slot"] for s in self.slots}
        if slot is None:
            slot = next(i for i in range(36) if i not in used)
        self.slots.append({"slot": slot, "id": item, "count": count})

    def take(self, item, count):
        item = item if ":" in item else f"minecraft:{item}"
        for s in list(self.slots):
            if s["id"] == item and count > 0:
                used = min(count, s["count"])
                s["count"] -= used
                count -= used
                if s["count"] == 0:
                    self.slots.remove(s)
