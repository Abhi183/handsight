"""GestureApp with remapped and recorded gestures."""

import argparse

import numpy as np

from handsight import custom_gestures as cg
from handsight import gesture_store as gs
from handsight import gestures
from handsight.gesture_actions import NOTHING, Action, Keys

RNG = np.random.default_rng(11)
SHAPE = RNG.uniform(-0.1, 0.1, size=(21, 3))
SHAPE[cg.WRIST] = 0
SHAPE[cg.MIDDLE_MCP] = (0, -0.08, 0)


class _Lm:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z


class _Cat:
    def __init__(self, name):
        self.category_name = name


class _Result:
    """One hand: MediaPipe's label, screen landmarks, and world landmarks of a shape."""

    def __init__(self, label, world=None):
        self.gestures = [[_Cat(label)]]
        pts = [(0.5, 0.8)] * 21
        pts[9], pts[5], pts[4], pts[8] = (0.5, 0.6), (0.45, 0.6), (0.3, 0.6), (0.45, 0.4)
        self.hand_landmarks = [[_Lm(x, y) for x, y in pts]]
        world = SHAPE if world is None else world
        self.hand_world_landmarks = [[_Lm(*p) for p in world]]
        self.handedness = [[_Cat("Right")]]


def _app(monkeypatch, store=None):
    calls = []
    monkeypatch.setattr(gestures.macos, "press_keys",
                        lambda code, mods: calls.append(("keys", code, mods)) or True)
    monkeypatch.setattr(gestures.macos, "volume_up", lambda: calls.append("vol+") or True)
    monkeypatch.setattr(gestures.macos, "play_pause", lambda: calls.append("play") or True)
    monkeypatch.setattr(gestures.macos, "natural_scrolling", lambda: True)
    app = gestures.GestureApp(argparse.Namespace(no_keys=False, no_cursor=False,
                                                 gestures_off="", gesture_hold=0.4))
    if store is not None:
        app.configure(disabled=set(), cursor=True, hold=0.4, store=store)
    return app, calls


def _hold(app, result, seconds, start=0.0):
    for i in range(int(seconds * 30)):
        app.step(result, None, start + i / 30)
    return start + seconds


def _custom_store(action):
    samples = np.array([cg.normalize([tuple(p) for p in SHAPE + RNG.normal(0, 1e-4, SHAPE.shape)],
                                     False) for _ in range(30)])
    g = gs.new_gesture("rock", samples, action)
    return gs.GestureStore().with_custom(g), g


def test_default_thumbs_up_repeats_volume(monkeypatch):
    app, calls = _app(monkeypatch)
    _hold(app, _Result("Thumb_Up", world=np.zeros((21, 3))), 1.5)
    assert calls.count("vol+") >= 3


def test_remapped_thumbs_up_presses_keys_once(monkeypatch):
    store = gs.GestureStore().with_binding("Thumb_Up", Action("keys", Keys("Tab", ("cmd",))))
    app, calls = _app(monkeypatch, store)
    _hold(app, _Result("Thumb_Up", world=np.zeros((21, 3))), 1.5)
    assert calls == [("keys", 48, ("cmd",))]


def test_love_you_set_to_k_key(monkeypatch):
    store = gs.GestureStore().with_binding("ILoveYou", Action("keys", Keys("KeyK")))
    app, calls = _app(monkeypatch, store)
    _hold(app, _Result("ILoveYou", world=np.zeros((21, 3))), 0.8)
    assert calls == [("keys", 40, ())]


def test_nothing_means_switched_off(monkeypatch):
    app, calls = _app(monkeypatch, gs.GestureStore().with_binding("Thumb_Up", NOTHING))
    _hold(app, _Result("Thumb_Up", world=np.zeros((21, 3))), 1.0)
    assert calls == [] and app.last_off == "Thumb_Up"
    assert "switched off" in app.status_text(_Result("Thumb_Up"))[0]


def test_recorded_shape_runs_its_action_and_beats_mediapipe(monkeypatch):
    store, g = _custom_store(Action("builtin", "play_pause"))
    app, calls = _app(monkeypatch, store)
    _hold(app, _Result("Pointing_Up"), 1.0)  # MediaPipe thinks it's pointing up
    assert calls == ["play"]
    assert not app.cursor_mode.active        # so it must not take the pointer


def test_recording_feeds_the_recorder_and_fires_nothing(monkeypatch):
    app, calls = _app(monkeypatch)
    clock = [0.0]
    app.recorder = cg.Recorder(lambda: clock[0])
    app.recorder.start()
    clock[0] = cg.COUNTDOWN_S
    _hold(app, _Result("Thumb_Up"), 1.0)
    assert calls == []
    clock[0] = cg.COUNTDOWN_S + cg.CAPTURE_S
    samples, label = app.recorder.result()
    assert len(samples) == 30 and label == "Thumb_Up"


def test_seen_label_for_the_settings_page(monkeypatch):
    store, g = _custom_store(NOTHING)
    app, _ = _app(monkeypatch, store)
    app.recorder = cg.Recorder()
    app.step(_Result("None"), None, 0.0)
    assert app.recorder.seen == "rock"
    app.step(_Result("Thumb_Up", world=np.zeros((21, 3))), None, 0.1)
    assert app.recorder.seen == "Thumbs up"


def test_ascii_text_for_opencv():
    assert gestures.ascii_text("Press ⇧⌘4") == "Press Shift+Cmd+4"
    assert gestures.ascii_text("Run “Focus”…") == 'Run "Focus"...'
