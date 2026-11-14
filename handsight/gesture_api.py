"""The settings window's gesture methods: change what gestures do, record new ones.

Mixed into SettingsApi (settings_api.py), which supplies get_state(), _load_gestures(),
_host, _recorder and _gestures_file. Pure Python, unit tested with a fake host.
"""

import sys
import threading
from collections.abc import Callable

from handsight import custom_gestures as cg
from handsight import gesture_runner
from handsight import gesture_store as gs
from handsight import settings as cfg
from handsight.gesture_actions import Action, catalog, parse_action


def ok(state: dict) -> dict:
    return {"ok": True, "state": state, "error": None}


def fail(error: str) -> dict:
    return {"ok": False, "state": None, "error": error}


_ok, _fail = ok, fail
PROGRAM_KINDS = ("open", "shortcut", "shell")


class Background:
    """Slow work for the settings window, off the main thread: testing an action that
    starts a program (up to 30 s) and listing Apple Shortcuts. The page polls for results."""

    def __init__(self, run=gesture_runner.run_program, lister=gesture_runner.list_shortcuts):
        self._run, self._lister = run, lister
        self._lock = threading.Lock()
        self._test_result: str | None = None
        self._shortcuts: list[str] | None = None
        self._listing = False

    def start_test(self, action: Action) -> None:
        def work() -> None:
            result = self._run(action)
            with self._lock:
                self._test_result = result
        threading.Thread(target=work, name="test-action", daemon=True).start()

    def take_test_result(self) -> str | None:
        with self._lock:
            result, self._test_result = self._test_result, None
            return result

    def shortcuts(self) -> list[str] | None:
        """The list, or None while it is still being read (ask again shortly)."""
        with self._lock:
            if self._shortcuts is not None:
                return self._shortcuts
            if self._listing:
                return None
            self._listing = True

        def work() -> None:
            names = self._lister()
            with self._lock:
                self._shortcuts, self._listing = names, False
        threading.Thread(target=work, name="list-shortcuts", daemon=True).start()
        return None


def gesture_state(store: gs.GestureStore) -> dict:
    """What the Gestures page shows (no recorded samples: they stay on the Python side)."""
    shapes = [{"id": shape, "glyph": glyph, "name": name,
               "action": _action_view(store.bindings[shape])}
              for shape, (glyph, name, _default) in gs.SHAPES.items()]
    custom = [{"id": g.id, "name": g.name, "action": _action_view(g.action)}
              for g in store.custom]
    return {"shapes": shapes, "custom": custom, "allow_shell": store.allow_shell,
            "max_custom": gs.MAX_CUSTOM, "actions": catalog()}


def _action_view(action: Action) -> dict:
    return {**action.to_json(), "describe": action.describe()}


class GestureMethods:
    """Settings-window methods for gestures, mixed into SettingsApi."""

    def _change_gestures(self, update: Callable[[gs.GestureStore], gs.GestureStore],
                         extra: dict | None = None) -> dict:
        try:
            new = update(self._load_gestures())
        except (KeyError, ValueError) as e:
            return _fail(str(e).strip("'\""))
        try:
            gs.save(new, self._gestures_file)
        except OSError as e:
            print(f"gestures: could not save: {e}", file=sys.stderr, flush=True)
            return _fail(f"Could not save gestures: {e.strerror or e}")
        self._host.apply_gestures(new)
        reply = _ok(self.get_state())
        reply.update(extra or {})
        return reply

    def _parse(self, action: object) -> Action:
        return parse_action(action, self._load_gestures().allow_shell)

    def set_binding(self, shape: str, action: dict) -> dict:
        return self._change_gestures(lambda g: g.with_binding(shape, self._parse(action)))

    def reset_bindings(self) -> dict:
        return self._change_gestures(lambda g: g.with_default_bindings())

    def set_custom_action(self, gesture_id: str, action: dict) -> dict:
        return self._change_gestures(
            lambda g: g.with_custom_action(gesture_id, self._parse(action)))

    def rename_custom(self, gesture_id: str, name: str) -> dict:
        return self._change_gestures(lambda g: g.renamed(gesture_id, name))

    def delete_custom(self, gesture_id: str) -> dict:
        return self._change_gestures(lambda g: g.without(gesture_id))

    def set_allow_shell(self, on: bool) -> dict:
        if not isinstance(on, bool):
            return _fail("on must be true or false")
        return self._change_gestures(lambda g: g.with_allow_shell(on))

    # ---------- recording ----------
    def start_recording(self) -> dict:
        if self._host.status() != "watching":
            return _fail("Start watching first: Handsight records through the camera.")
        if not cfg.load(self._file).flags["gestures"]:
            return _fail("Turn on Gesture control first.")
        self._recorder.start()
        return self.gesture_live()

    def cancel_recording(self) -> dict:
        self._recorder.cancel()
        return self.gesture_live()

    def gesture_live(self) -> dict:
        """Polled by the page: the recording's progress and the gesture seen right now."""
        return {"ok": True, "error": None, "state": None,
                "live": {"recording": self._recorder.status(), "seen": self._recorder.seen,
                         "test": self._background.take_test_result()}}

    def save_recording(self, name: str, action: dict, replace_id: str = "") -> dict:
        """Keep the shape just recorded, as a new gesture or in place of `replace_id`."""
        done = self._recorder.result()
        if done is None:
            return _fail("Nothing recorded yet.")
        samples, looked_like = done
        try:
            parsed = self._parse(action)
            store = self._load_gestures()
            existing = store.find(replace_id) if replace_id else None
            if replace_id and existing is None:
                raise KeyError("that gesture no longer exists")
            gesture = gs.new_gesture(name or (existing.name if existing else ""), samples,
                                     parsed, gesture_id=replace_id or None)
        except (KeyError, ValueError) as e:
            return _fail(str(e).strip("'\""))
        warning = _recording_warning(gesture, store, looked_like)
        reply = self._change_gestures(lambda g: g.with_custom(gesture), {"warning": warning})
        if reply["ok"]:
            self._recorder.cancel()  # used up: back to idle
        return reply

    # ---------- actions ----------
    def test_action(self, action: dict) -> dict:
        try:
            parsed = self._parse(action)
        except ValueError as e:
            return _fail(str(e))
        if parsed.kind in PROGRAM_KINDS:  # can take a while: the result comes via gesture_live
            self._background.start_test(parsed)
            return {"ok": True, "error": None, "state": None, "pending": True,
                    "message": f"{parsed.describe()}…"}
        return {"ok": True, "error": None, "state": None,
                "message": self._host.test_action(parsed)}

    def list_shortcuts(self) -> dict:
        names = self._background.shortcuts()
        return {"ok": True, "error": None, "state": None, "shortcuts": names,
                "loading": names is None}

    def choose_app(self) -> dict:
        return {"ok": True, "error": None, "state": None, "path": self._host.choose_app()}


def _recording_warning(gesture: gs.StoredGesture, store: gs.GestureStore,
                       looked_like: str | None) -> str:
    others = tuple(g.shape for g in store.custom if g.id != gesture.id)
    similar = cg.similar_to(gesture.shape, others)
    if similar:
        return (f"This looks a lot like “{similar}”. If they get mixed up, re-record one "
                "with a more different hand shape.")
    if looked_like in gs.SHAPES:
        glyph, label, _ = gs.SHAPES[looked_like]
        return (f"This looks like {glyph} {label}. Your gesture takes over that shape "
                "while it is saved.")
    return ""
