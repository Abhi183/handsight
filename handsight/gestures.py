"""Control the Mac with hand gestures.

  thumbs up / down (hold)   volume up / down
  fist (hold a little longer) mute / unmute
  open palm (hold)          scroll: move the hand up / down like two fingers on a trackpad;
                            lower the hand to let go (needs Accessibility)
  two fingers up (hold)     take a webcam selfie
  love-you sign (hold)      play / pause media (YouTube, Spotify, Music...)
  point left / right (hold)  previous / next browser tab (needs Accessibility)
  point up (hold)           take the mouse pointer: it follows your hand. Pinch thumb and
                            index to click; fist to lift the mouse; open your palm to switch
                            to scrolling; any other gesture, or lowering the hand, lets go
                            (needs Accessibility)

Keys: q quit
"""

import argparse
import time
from typing import Callable

import cv2
import mediapipe as mp
import numpy as np

from handsight import custom_gestures, macos
from handsight import gesture_store as store_mod
from handsight.cursor import (
    INDEX_MCP,
    POINTER_SHAPES,
    CursorDriver,
    CursorMode,
    CursorStep,
    pinch_ratio,
)
from handsight.gesture_actions import NOTHING, Action
from handsight.gesture_runner import Runner
from handsight.gesture_trigger import HoldTrigger
from handsight.scroll import MIDDLE_MCP, ScrollMode, wheel_delta
from handsight.pointing import classify_point
from handsight.vision import DATA, GESTURE_MODEL, open_camera, put_text, require_model

WINDOW = "Gestures"
MAX_HANDS = 2  # so a visitor's hand in view doesn't hide yours from the recognizer
SNAP_DIR = DATA / "snaps"
WHITE, CYAN, GREEN = (255, 255, 255), (255, 220, 0), (80, 200, 80)
POINTER_LABEL = "hold to take the pointer"
VOLUME_REPEAT_S = 0.4
CURSOR_HELP = "pointer: pinch = click | fist = lift | open palm = scroll | lower hand = stop"
CURSOR_LIFTED = "pointer lifted: move your hand, then point again"
NO_MOUSE_ACCESS = "pointer blocked: allow Handsight / Python in Accessibility"
NO_SCROLL_ACCESS = "scroll blocked: allow Handsight in Accessibility"
SCROLL_HELP = "scrolling: move your open hand up / down | point up = pointer | lower = stop"
# Which setting switches each gesture on and off (see handsight.settings).
GESTURE_ACTIONS = {"Thumb_Up": "volume", "Thumb_Down": "volume", "Closed_Fist": "mute",
                   "Victory": "selfie", "ILoveYou": "media", "Point_Right": "tabs",
                   "Point_Left": "tabs"}
# Which built-in action each old per-gesture switch covered (for stats and --gestures-off).
ACTION_SWITCH = {"volume_up": "volume", "volume_down": "volume", "mute": "mute",
                 "selfie": "selfie", "play_pause": "media", "next_track": "media",
                 "previous_track": "media", "next_tab": "tabs", "previous_tab": "tabs"}
# Gestures that are a mode you hold rather than a one-off action: open palm scrolls.
MODE_ACTIONS = frozenset({"scroll"})
NO_KEY_ACCESS = "allow Handsight in Accessibility"
# action -> (macos function name, what to say). Looked up when used, so tests can swap them.
AUDIO_KEYS = {"volume_up": ("volume_up", "volume +"), "volume_down": ("volume_down", "volume -"),
              "mute": ("toggle_mute", "mute toggled")}
MEDIA_KEYS = {"play_pause": ("play_pause", "play / pause"),
              "next_track": ("next_track", "next track"),
              "previous_track": ("previous_track", "previous track")}
DEFAULT_HOLD_S = 0.4
MIN_HOLD_S, MAX_HOLD_S = 0.1, 2.0
# A fist also shows up while you lower or relax your hand, so mute needs a longer hold.
MUTE_HOLD_S = 0.8
# Lowering your hand after using the pointer passes through fists and palms; ignore them.
AFTER_POINTER_S = 0.8
HAND_CONNECTIONS = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
                    (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
                    (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-keys", action="store_true", help="don't switch tabs when pointing")
    p.add_argument("--no-cursor", action="store_true",
                   help="don't let a raised index finger take the mouse pointer")
    add_gesture_options(p)
    p.add_argument("--camera", type=int, default=0)
    return check_gesture_options(p, p.parse_args())


def add_gesture_options(p: argparse.ArgumentParser) -> None:
    """Options shared with desk.py."""
    names = ", ".join(sorted(set(GESTURE_ACTIONS.values()) | MODE_ACTIONS))
    p.add_argument("--gestures-off", default="", metavar="LIST",
                   help=f"comma-separated gestures to ignore: {names}")
    p.add_argument("--gesture-hold", type=float, default=DEFAULT_HOLD_S, metavar="SECONDS",
                   help=f"how long to hold a gesture before it fires (default {DEFAULT_HOLD_S})")


def check_gesture_options(p: argparse.ArgumentParser,
                          args: argparse.Namespace) -> argparse.Namespace:
    known = set(GESTURE_ACTIONS.values()) | MODE_ACTIONS
    unknown = parse_off(args.gestures_off) - known
    if unknown:
        p.error(f"--gestures-off: unknown gesture {', '.join(sorted(unknown))}; "
                f"choose from {', '.join(sorted(known))}")
    if not MIN_HOLD_S <= args.gesture_hold <= MAX_HOLD_S:
        p.error(f"--gesture-hold must be between {MIN_HOLD_S} and {MAX_HOLD_S} seconds")
    return args


def parse_off(text: str) -> set[str]:
    return {part.strip() for part in text.split(",") if part.strip()}


def gesture_action(gesture: str | None) -> str | None:
    """The setting that controls this gesture, or None if it has none."""
    return GESTURE_ACTIONS.get(gesture)


def make_recognizer():
    vision = mp.tasks.vision
    options = vision.GestureRecognizerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(require_model(GESTURE_MODEL))),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=MAX_HANDS,
        min_hand_detection_confidence=0.6,
    )
    return vision.GestureRecognizer.create_from_options(options)


def draw_hand(img: np.ndarray, landmarks) -> None:
    h, w = img.shape[:2]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in HAND_CONNECTIONS:
        cv2.line(img, pts[a], pts[b], CYAN, 2, cv2.LINE_AA)
    for p in pts:
        cv2.circle(img, p, 4, WHITE, -1, cv2.LINE_AA)


def hand_features(result, hand: int | None):
    """Position- and size-free numbers for hand number `hand` (see custom_gestures)."""
    worlds = getattr(result, "hand_world_landmarks", None) or []
    if hand is None or hand >= len(worlds):
        return None
    sides = getattr(result, "handedness", None) or []
    left = hand < len(sides) and bool(sides[hand]) and sides[hand][0].category_name == "Left"
    return custom_gestures.normalize([(lm.x, lm.y, lm.z) for lm in worlds[hand]], left)


def ascii_text(text: str) -> str:
    """OpenCV can only draw plain ASCII; symbols and emoji become words or vanish."""
    for symbol, word in (("⌘", "Cmd+"), ("⇧", "Shift+"), ("⌥", "Opt+"), ("⌃", "Ctrl+"),
                         ("…", "..."), ("“", '"'), ("”", '"'), ("←", "Left"),
                         ("→", "Right"), ("↑", "Up"), ("↓", "Down"), ("↩", "Return")):
        text = text.replace(symbol, word)
    return text.encode("ascii", "ignore").decode()


def recording_text(status: dict) -> str:
    if status["state"] == "countdown":
        return f"get ready: hold your new gesture up in {status['seconds']}..."
    return f"recording your gesture... keep it still ({status.get('samples', 0)} frames)"


class GestureApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.send_keys = not getattr(args, "no_keys", False)
        self.pointer_released_at = float("-inf")
        self.last_off: str | None = None  # a gesture you're making that is switched off
        self.cursor_mode = CursorMode()
        self.cursor_driver = CursorDriver()
        self.cursor_lifted = False
        self.scroll_mode = ScrollMode()
        self.natural_scrolling = macos.natural_scrolling()
        self.scroll_blocked = False
        self.pointer_blocked = False
        self.on_used: Callable[[str], None] | None = None  # for the daily stats
        self.hold: float | None = None
        self.cursor_enabled = False
        self.store = store_mod.GestureStore()
        self.recorder: custom_gestures.Recorder | None = None  # set by the app
        self.runner = Runner(lambda line: self.say(line, time.monotonic()))
        self.configure(disabled=parse_off(getattr(args, "gestures_off", "")),
                       cursor=not getattr(args, "no_cursor", False),
                       hold=getattr(args, "gesture_hold", DEFAULT_HOLD_S))
        self.flash_until = 0.0
        self.message, self.message_until = "", 0.0
        self.ignoring_hand = False  # a hand is in view but it isn't yours

    def configure(self, disabled: set[str], cursor: bool, hold: float,
                  store: "store_mod.GestureStore | None" = None) -> None:
        """Change which gestures work, what they do and how long to hold them, while
        running. `disabled` holds old switch names (--gestures-off); the app uses `store`."""
        self.disabled = frozenset(disabled)
        if self.cursor_enabled and not cursor:  # let go of the pointer if just switched off
            self.cursor_mode = CursorMode()
        self.cursor_enabled = cursor
        if "scroll" in self.disabled:
            self.scroll_mode.reset()
        if store is not None and store is not self.store:
            self.store = store
            self.hold = None  # what repeats may have changed: rebuild the trigger
        if hold != self.hold:
            self.hold = hold
            repeat = {label: VOLUME_REPEAT_S for label in self._labels()
                      if self.store.action_for(label).repeats}
            self.trigger = HoldTrigger(
                hold_s=hold, repeat_s=repeat,
                hold_overrides={"Closed_Fist": max(MUTE_HOLD_S, hold * 2)})

    def _labels(self) -> list[str]:
        return [*store_mod.SHAPES, *(g.label for g in self.store.custom)]

    def action_for(self, gesture: str | None) -> Action:
        """What this gesture does now; NOTHING if it is switched off either way."""
        action = self.store.action_for(gesture)
        if action.kind == "builtin" and ACTION_SWITCH.get(action.value) in self.disabled:
            return NOTHING
        return action

    def say(self, text: str, now: float) -> None:
        self.message, self.message_until = text, now + 2.5
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} gesture: {text}", flush=True)

    def _used(self, what: str) -> None:
        if self.on_used is not None:
            self.on_used(what)

    def act(self, gesture: str, frame: np.ndarray, now: float) -> None:
        action = self.action_for(gesture)
        if action.kind == "builtin":
            self._used(ACTION_SWITCH.get(action.value, action.value))
        elif action.kind != "none":
            self._used(action.kind)
        self.run_action(action, frame, now)

    def run_action(self, action: Action, frame: np.ndarray | None, now: float) -> None:
        """Do it. Programs (open, shortcut, Terminal) start in the background."""
        if action.kind == "builtin":
            self.run_builtin(action.value, frame, now)
        elif action.kind == "keys":
            pressed = macos.press_keys(action.value.key_code, action.value.modifiers)
            self.say(action.describe() if pressed else f"{action.describe()}: {NO_KEY_ACCESS}",
                     now)
        elif action.kind in ("open", "shortcut", "shell"):
            self.say(f"{action.describe()}…", now)
            self.runner.start(action)

    def run_builtin(self, name: str, frame: np.ndarray | None, now: float) -> None:
        if name in AUDIO_KEYS:
            self.adjust_audio(name, now)
        elif name == "selfie":
            if frame is None:
                self.say("selfie: the camera isn't on", now)
                return
            SNAP_DIR.mkdir(exist_ok=True)
            path = SNAP_DIR / time.strftime("selfie_%Y%m%d_%H%M%S.jpg")
            cv2.imwrite(str(path), frame)
            self.flash_until = now + 0.25
            self.say(f"saved {path.name}", now)
        elif name in MEDIA_KEYS:
            function, label = MEDIA_KEYS[name]
            pressed = getattr(macos, function)()
            self.say(label if pressed else f"{label} blocked: {NO_KEY_ACCESS}", now)
        elif name in ("next_tab", "previous_tab"):
            self.switch_tab(name == "next_tab", now)
        elif name == "lock_screen":
            self.say(f"screen {macos.lock_screen()}", now)

    def adjust_audio(self, name: str, now: float) -> None:
        """Presses the real volume/mute keys so the macOS volume pop-up shows."""
        function, label = AUDIO_KEYS[name]
        if getattr(macos, function)():
            self.say(label, now)
        else:
            self.say(f"{label} (no pop-up: allow Handsight / Python in Accessibility)", now)

    def switch_tab(self, to_right: bool, now: float) -> None:
        label = "next tab ->" if to_right else "<- previous tab"
        if not self.send_keys:
            self.say(f"{label} (keys off)", now)
            return
        error = macos.next_tab() if to_right else macos.previous_tab()
        self.say(error or label, now)

    def step(self, result, frame: np.ndarray, now: float, hand: int | None = 0) -> str | None:
        """Act on the gesture of hand number `hand`; None means no hand in view is yours."""
        gesture, points = None, None
        self.ignoring_hand = hand is None and bool(result.hand_landmarks)
        if hand is not None and hand < len(result.gestures) and result.gestures[hand]:
            gesture = result.gestures[hand][0].category_name
        if hand is not None and hand < len(result.hand_landmarks):
            points = [(lm.x, lm.y) for lm in result.hand_landmarks[hand]]
        features = hand_features(result, hand)
        custom = custom_gestures.best_match(features, self.store.shapes)
        if self._recording(features, gesture, points, now):
            return gesture
        if custom:  # your own shape wins over what MediaPipe calls it
            gesture = store_mod.CUSTOM_PREFIX + custom
        self._report_seen(gesture, points)
        if self.cursor_enabled and self._step_cursor(gesture, points, now):
            self.trigger.update(None, now)  # other gestures are off while you hold the pointer
            return gesture
        if "scroll" not in self.disabled and self._step_scroll(gesture, points, now):
            self.trigger.update(None, now)  # and while you scroll
            return gesture
        if points is not None and not custom:  # sideways pointing isn't a built-in gesture
            gesture = classify_point(points) or gesture
        if now - self.pointer_released_at < AFTER_POINTER_S:
            self.trigger.update(None, now)
            return gesture
        actionable = gesture in store_mod.SHAPES or bool(custom)
        self.last_off = gesture if actionable and self.action_for(gesture) is NOTHING else None
        if self.last_off:
            self.trigger.update(None, now)  # switched off in settings: don't even charge
            return gesture
        fired = self.trigger.update(gesture, now)
        if fired:
            self.act(fired, frame, now)
        return gesture

    def _recording(self, features, gesture: str | None, points: list | None,
                   now: float) -> bool:
        """While a new shape is being recorded, feed it and do nothing else."""
        if self.recorder is None or not self.recorder.active:
            return False
        if points is not None:
            gesture = classify_point(points) or gesture
        self.recorder.feed(features, gesture if gesture in store_mod.SHAPES else None)
        self.recorder.seen = "recording…"
        self.trigger.update(None, now)
        return True

    def _report_seen(self, gesture: str | None, points: list | None) -> None:
        if self.recorder is None:
            return
        if points is not None and gesture and not gesture.startswith(store_mod.CUSTOM_PREFIX):
            gesture = classify_point(points) or gesture
        self.recorder.seen = self.display_name(gesture) if points is not None else ""

    def display_name(self, gesture: str | None) -> str:
        if gesture in ("Pointing_Up", "Open_Palm"):
            return "Point up" if gesture == "Pointing_Up" else "Open palm"
        return self.store.name_of(gesture) or ("hand, no gesture" if gesture in (None, "None")
                                               else gesture.replace("_", " "))

    def _step_scroll(self, gesture: str | None, points: list | None, now: float) -> bool:
        """Two-finger scrolling for this frame. True while your hand holds the page."""
        hand_y = points[MIDDLE_MCP][1] if points else None
        step = self.scroll_mode.update(gesture, hand_y, now)
        if step.entered:
            self.scroll_blocked = False
            self._used("scroll")
            self.say("scrolling", now)
        if step.left:
            self.pointer_released_at = now  # lowering the hand shouldn't fire a gesture
        if step.pixels and not self.scroll_blocked:
            if not macos.scroll(wheel_delta(step.pixels, self.natural_scrolling)):
                self.scroll_blocked = True  # say it once, not every frame
                self.say(NO_SCROLL_ACCESS, now)
        return step.active

    def _step_cursor(self, gesture: str | None, points: list | None, now: float) -> bool:
        """Pointer control for this frame. True while your hand holds the pointer."""
        step = self.cursor_mode.update(gesture, pinch_ratio(points) if points else None, now)
        if step.left and step.to_scroll and points is not None and "scroll" not in self.disabled:
            self.scroll_mode.start(points[MIDDLE_MCP][1], now)  # palm: scroll at once
            self._used("scroll")
            self.say("scrolling", now)
            return True  # the hand is busy scrolling this frame
        if step.left:
            if gesture in POINTER_SHAPES:  # hand lowered: ignore the shapes it passes
                self.pointer_released_at = now
            self.say("pointer released", now)
        if not step.active or points is None:
            return step.active
        if step.entered:
            self.scroll_mode.reset()  # pointing up again takes over from scrolling
        knuckle = points[INDEX_MCP]  # steadier than the fingertip, and it stays put in a pinch
        if step.entered:
            self.pointer_blocked = False
            self.cursor_driver.start(knuckle, macos.mouse_position() or (0.0, 0.0), now)
            self.say("pointer taken", now)
        self._apply_cursor(step, knuckle, now)
        return True

    def _apply_cursor(self, step: CursorStep, knuckle: tuple[float, float], now: float) -> None:
        position = macos.mouse_position()
        bounds = macos.desktop_bounds()
        if position is None or bounds is None:
            return
        self.cursor_driver.sync(position)
        self.cursor_lifted = step.lifted
        if not step.move:
            self.cursor_driver.hold(knuckle, now)
        elif (not macos.move_mouse(self.cursor_driver.move(knuckle, bounds, now))
              and not self.pointer_blocked):
            self.pointer_blocked = True  # say it once, not every frame
            self.say(NO_MOUSE_ACCESS, now)
        if step.left_click:
            self.say("click" if macos.click_mouse(position) else NO_MOUSE_ACCESS, now)

    # ---------- drawing pieces, shared with the desk app ----------
    def status_text(self, result) -> tuple[str, bool]:
        """(text, is_gesture_held) for a status line."""
        if self.cursor_mode.active:
            return (CURSOR_LIFTED if self.cursor_lifted else CURSOR_HELP), True
        if self.scroll_mode.active:
            return SCROLL_HELP, True
        if self.recorder is not None and self.recorder.active:
            return recording_text(self.recorder.status()), True
        held = self.trigger.current
        if held:
            what = POINTER_LABEL if held == "Pointing_Up" else self.action_for(held).describe()
            return f"{ascii_text(self.display_name(held))}: {ascii_text(what)}", True
        if self.ignoring_hand:
            return "not your hand, ignored", False
        if self.last_off:
            return f"{ascii_text(self.display_name(self.last_off))}: switched off in settings", False
        if result.hand_landmarks:
            return "hand seen, no gesture yet", False
        return "no hand in view", False

    def message_now(self, now: float) -> str | None:
        return self.message if self.message and now < self.message_until else None

    def draw_effects(self, img: np.ndarray, result, now: float) -> np.ndarray:
        """Hand skeleton and selfie flash. Returns the (possibly new) image."""
        for hand in result.hand_landmarks:
            draw_hand(img, hand)
        if now < self.flash_until:
            img = cv2.addWeighted(img, 0.3, np.full_like(img, 255), 0.7, 0)
        return img

    def draw_status_strip(self, img: np.ndarray, result, now: float, top: int,
                          height: int = 28) -> None:
        """Gesture name plus a hold-progress bar, as a strip starting at y=top."""
        text, held = self.status_text(result)
        cv2.rectangle(img, (0, top), (img.shape[1], top + height), (40, 40, 40), -1)
        put_text(img, text, (12, top + height - 9), GREEN if held else WHITE, scale=0.55,
                 thickness=1)
        bar = int(img.shape[1] * self.trigger.progress(now))
        cv2.rectangle(img, (0, top + height - 4), (bar, top + height), GREEN, -1)

    def draw_legend(self, img: np.ndarray, top: int) -> None:
        y = top
        for label in self._labels():
            action = self.action_for(label)
            if action is NOTHING:
                continue
            text = f"{self.display_name(label)}: {action.describe()}"
            put_text(img, ascii_text(text), (12, y), WHITE, scale=0.45, thickness=1)
            y += 20
        put_text(img, "open palm: scroll", (12, y), WHITE, scale=0.45, thickness=1)

    def render(self, frame: np.ndarray, result, now: float) -> np.ndarray:
        img = self.draw_effects(frame.copy(), result, now)
        self.draw_status_strip(img, result, now, top=0, height=50)
        self.draw_legend(img, top=76)
        message = self.message_now(now)
        if message:
            put_text(img, message, (14, img.shape[0] - 16), WHITE, scale=0.6)
        return img


class GestureEngine:
    """MediaPipe recognizer plus the strictly increasing timestamps it needs."""

    def __init__(self) -> None:
        self._recognizer = make_recognizer()
        self._last_ts = 0

    def process(self, frame_bgr: np.ndarray, now: float):
        ts = max(int(now * 1000), self._last_ts + 1)
        self._last_ts = ts
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        return self._recognizer.recognize_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)

    def close(self) -> None:
        self._recognizer.close()


def main() -> None:
    args = parse_args()
    app = GestureApp(args)
    app.configure(disabled=app.disabled, cursor=app.cursor_enabled, hold=args.gesture_hold,
                  store=store_mod.load(store_mod.FILE))
    cap = open_camera(args.camera)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    engine = GestureEngine()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Lost the camera feed.")
                break
            frame = cv2.flip(frame, 1)
            now = time.monotonic()
            result = engine.process(frame, now)
            app.step(result, frame, now)
            cv2.imshow(WINDOW, app.render(frame, result, now))
            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                break
    except KeyboardInterrupt:
        pass
    finally:
        engine.close()
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
