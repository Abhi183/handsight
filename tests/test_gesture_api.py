"""Settings window: gesture methods."""

import json

import numpy as np
import pytest

from handsight import custom_gestures as cg
from handsight import gesture_store as gs
from handsight.settings_api import SettingsApi


class Host:
    def __init__(self):
        self.applied, self.tested, self.current = [], [], "watching"
        self.calls = []
        self.picked = "/Applications/Safari.app"

    def status(self):
        return self.current

    def cameras(self):
        return []

    def camera_in_use(self):
        return None

    def stats(self):
        return {}

    def apply_settings(self, settings):
        pass

    def apply_gestures(self, store):
        self.applied.append(store)

    def test_action(self, action):
        self.tested.append(action)
        return f"ran {action.describe()}"

    def choose_app(self):
        return self.picked

    def permission_statuses(self):
        return {"camera": "allowed", "accessibility": "denied", "system_events": "not_asked",
                "browsers": "not_running"}

    def ask_permission(self, permission_id):
        self.calls.append(("ask", permission_id))
        return f"asked {permission_id}"

    def open_permission_settings(self, permission_id):
        self.calls.append(("open", permission_id))

    def reset_permissions(self):
        self.calls.append("reset")
        return "restarting"


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def setup(tmp_path):
    host, clock = Host(), Clock()
    recorder = cg.Recorder(clock)
    api = SettingsApi(tmp_path / "settings.json", tmp_path, host, lambda _a: None,
                      gestures_file=tmp_path / "gestures.json", recorder=recorder)
    return api, host, clock, recorder, tmp_path


def _record(api, clock, recorder, label=None):
    assert api.call("start_recording", [])["ok"]
    clock.t += cg.COUNTDOWN_S
    base = np.random.default_rng(1).uniform(-1, 1, cg.FEATURES)
    for _ in range(30):
        recorder.feed(base + np.random.default_rng().normal(0, 1e-3, cg.FEATURES), label)
    clock.t += cg.CAPTURE_S


def test_state_lists_shapes_without_samples(setup):
    api = setup[0]
    g = api.get_state()["gestures"]
    assert [s["id"] for s in g["shapes"]] == list(gs.SHAPES)
    assert g["shapes"][4]["action"]["describe"] == "Play / pause"
    assert "samples" not in json.dumps(g)


def test_set_binding_saves_and_applies(setup):
    api, host, _, _, tmp = setup
    reply = api.call("set_binding", ["ILoveYou", {"kind": "keys", "value": {"code": "KeyK"}}])
    assert reply["ok"]
    assert reply["state"]["gestures"]["shapes"][4]["action"]["describe"] == "Press K"
    assert gs.load(tmp / "gestures.json").action_for("ILoveYou").kind == "keys"
    assert host.applied[-1].action_for("ILoveYou").kind == "keys"
    assert api.call("reset_bindings", [])["state"]["gestures"]["shapes"][4]["action"][
        "describe"] == "Play / pause"


@pytest.mark.parametrize("args", [
    ["Open_Palm", {"kind": "none"}],
    ["Thumb_Up", {"kind": "shell", "value": "rm -rf ~"}],
    ["Thumb_Up", {"kind": "open", "value": "javascript:alert(1)"}],
    ["Thumb_Up", "not an action"],
])
def test_bad_bindings_are_refused(setup, args):
    api, host = setup[0], setup[1]
    reply = api.call("set_binding", args)
    assert not reply["ok"] and reply["error"]
    assert host.applied == []


def test_shell_after_allowing_it(setup):
    api = setup[0]
    assert api.call("set_allow_shell", [True])["state"]["gestures"]["allow_shell"]
    assert api.call("set_binding", ["Victory", {"kind": "shell", "value": "say hi"}])["ok"]
    assert not api.call("set_allow_shell", ["yes"])["ok"]


def test_record_save_rename_rebind_delete(setup):
    api, host, clock, recorder, tmp = setup
    _record(api, clock, recorder)
    reply = api.call("save_recording", ["rock", {"kind": "builtin", "value": "mute"}])
    assert reply["ok"] and reply["warning"] == ""
    custom = reply["state"]["gestures"]["custom"]
    assert [c["name"] for c in custom] == ["rock"]
    gid = custom[0]["id"]
    assert recorder.status()["state"] == "idle"
    assert api.call("rename_custom", [gid, "metal"])["state"]["gestures"]["custom"][0][
        "name"] == "metal"
    assert api.call("set_custom_action", [gid, {"kind": "none"}])["ok"]
    assert api.call("delete_custom", [gid])["state"]["gestures"]["custom"] == []
    assert not api.call("delete_custom", [gid])["ok"]


def test_rerecord_replaces_in_place(setup):
    api, _, clock, recorder, _ = setup
    _record(api, clock, recorder)
    gid = api.call("save_recording", ["rock", {"kind": "none"}])["state"]["gestures"][
        "custom"][0]["id"]
    _record(api, clock, recorder)
    custom = api.call("save_recording", ["", {"kind": "none"}, gid])["state"]["gestures"][
        "custom"]
    assert [(c["id"], c["name"]) for c in custom] == [(gid, "rock")]


def test_recording_that_looks_like_a_builtin_warns(setup):
    api, _, clock, recorder, _ = setup
    _record(api, clock, recorder, label="Thumb_Up")
    warning = api.call("save_recording", ["up", {"kind": "none"}])["warning"]
    assert "Thumbs up" in warning


def test_recording_needs_the_camera(setup):
    api, host = setup[0], setup[1]
    host.current = "stopped"
    assert "watching" in api.call("start_recording", [])["error"]


def test_save_without_a_recording(setup):
    assert "Nothing recorded" in setup[0].call("save_recording", ["x", {"kind": "none"}])["error"]


def test_live_status_and_cancel(setup):
    api, _, _, recorder, _ = setup
    recorder.seen = "Thumbs up"
    api.call("start_recording", [])
    live = api.call("gesture_live", [])["live"]
    assert live["recording"]["state"] == "countdown" and live["seen"] == "Thumbs up"
    assert api.call("cancel_recording", [])["live"]["recording"]["state"] == "idle"


def test_test_action_and_choose_app(setup):
    api, host = setup[0], setup[1]
    reply = api.call("test_action", [{"kind": "builtin", "value": "mute"}])
    assert reply["message"] == "ran Mute / unmute" and len(host.tested) == 1
    assert not api.call("test_action", [{"kind": "builtin", "value": "nope"}])["ok"]
    assert api.call("choose_app", [])["path"] == "/Applications/Safari.app"


def test_old_switches_carry_over_until_gestures_json_exists(setup):
    api, _, _, _, tmp = setup
    (tmp / "settings.json").write_text(json.dumps({"media": False}))
    shapes = api.get_state()["gestures"]["shapes"]
    assert shapes[4]["action"]["describe"] == "Nothing"


# ---------- review fixes ----------

import threading  # noqa: E402
import time  # noqa: E402

from handsight.gesture_api import Background  # noqa: E402


def test_testing_a_program_reports_its_real_result_later(setup):
    api = setup[0]
    api._background = Background(run=lambda action: "failed (1): no such shortcut")
    reply = api.call("test_action", [{"kind": "shortcut", "value": "Nope"}])
    assert reply["ok"] and reply["pending"]
    deadline = time.monotonic() + 2
    result = None
    while result is None and time.monotonic() < deadline:
        result = api.call("gesture_live", [])["live"]["test"]
    assert result == "failed (1): no such shortcut"
    assert api.call("gesture_live", [])["live"]["test"] is None  # handed over once


def test_shortcut_list_doesnt_block(setup):
    api = setup[0]
    gate = threading.Event()
    api._background = Background(lister=lambda: gate.wait(2) and ["Focus"])
    first = api.call("list_shortcuts", [])
    assert first["loading"] and first["shortcuts"] is None
    gate.set()
    deadline = time.monotonic() + 2
    while api.call("list_shortcuts", [])["loading"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert api.call("list_shortcuts", [])["shortcuts"] == ["Focus"]
