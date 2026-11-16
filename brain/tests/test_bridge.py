import pytest

from minegpt_brain.bridge import Bridge, BridgeError
from fake_mod import FakeMod


@pytest.fixture
def mod_and_bridge():
    mod = FakeMod()
    bridge = Bridge.connect(port=mod.port, timeout=5)
    yield mod, bridge
    bridge.close()


def test_hello_handshake(mod_and_bridge):
    mod, bridge = mod_and_bridge
    assert bridge.info["version"] == "test"
    assert mod.received[0]["op"] == "hello"


def test_step_sends_actions_and_returns_observation(mod_and_bridge):
    mod, bridge = mod_and_bridge
    obs = bridge.step([{"type": "move", "keys": ["forward"], "ticks": 10}], ticks=10)
    assert obs.tick == 10
    assert obs.applied
    assert obs.pos[0] == pytest.approx(1.5)
    sent = mod.received[-1]
    assert sent["op"] == "step" and sent["ticks"] == 10
    assert sent["actions"][0]["keys"] == ["forward"]


def test_act_reports_paused(mod_and_bridge):
    mod, bridge = mod_and_bridge
    mod.paused = True
    assert bridge.act([{"type": "attack"}]) is False


def test_errors_raise(mod_and_bridge):
    _, bridge = mod_and_bridge
    with pytest.raises(BridgeError, match="unknown op"):
        bridge._request({"op": "nonsense"})


def test_events_arrive_on_queue(mod_and_bridge):
    mod, bridge = mod_and_bridge
    bridge.status()  # make sure the fake has accepted the connection
    mod.send({"op": "event", "event": "goal", "goal": "get wood"})
    event = bridge.events.get(timeout=5)
    assert event == {"op": "event", "event": "goal", "goal": "get wood"}


def test_set_status(mod_and_bridge):
    mod, bridge = mod_and_bridge
    bridge.set_status("Chopping trees")
    assert bridge.status()["status"] == "Chopping trees"


def test_connect_refused_explains_what_to_do():
    with pytest.raises(ConnectionRefusedError, match="minegpt bridge start"):
        Bridge.connect(port=1)
