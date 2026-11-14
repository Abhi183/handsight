"""Which action each gesture runs, and the shapes you recorded: gestures.json.

GestureStore is immutable; every change returns a new one. Loading checks every entry and
falls back to the default for anything it can't use, so a damaged file never stops the app.
"""

import json
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import numpy as np

from handsight import custom_gestures as cg
from handsight.gesture_actions import NOTHING, Action, parse_action
from handsight.paths import DATA

FILE = DATA / "gestures.json"

CUSTOM_PREFIX = "custom:"
MAX_CUSTOM = 20
MAX_NAME = 30
DEFAULT_NAME = "My gesture"
SAMPLE_DECIMALS = 4

# Built-in hand shapes you can give an action: id -> (glyph, label, default action).
SHAPES = MappingProxyType({
    "Thumb_Up": ("👍", "Thumbs up", Action("builtin", "volume_up")),
    "Thumb_Down": ("👎", "Thumbs down", Action("builtin", "volume_down")),
    "Closed_Fist": ("✊", "Fist", Action("builtin", "mute")),
    "Victory": ("✌️", "Two fingers", Action("builtin", "selfie")),
    "ILoveYou": ("🤟", "Love-you sign", Action("builtin", "play_pause")),
    "Point_Left": ("👈", "Point left", Action("builtin", "previous_tab")),
    "Point_Right": ("👉", "Point right", Action("builtin", "next_tab")),
})
# Old per-gesture switches (settings.py) and the shapes each one covered.
OLD_SWITCHES = MappingProxyType({"volume": ("Thumb_Up", "Thumb_Down"), "mute": ("Closed_Fist",),
                                 "selfie": ("Victory",), "media": ("ILoveYou",),
                                 "tabs": ("Point_Left", "Point_Right")})


def default_bindings() -> Mapping[str, Action]:
    return MappingProxyType({shape: entry[2] for shape, entry in SHAPES.items()})


def clean_gesture_name(name: object) -> str:
    text = " ".join(name.split()) if isinstance(name, str) else ""
    text = "".join(ch for ch in text if ch.isprintable())[:MAX_NAME]
    return text or DEFAULT_NAME


@dataclass(frozen=True)
class StoredGesture:
    shape: cg.CustomGesture
    action: Action = NOTHING

    @property
    def id(self) -> str:
        return self.shape.id

    @property
    def name(self) -> str:
        return self.shape.name

    @property
    def label(self) -> str:
        return CUSTOM_PREFIX + self.shape.id


@dataclass(frozen=True)
class GestureStore:
    bindings: Mapping[str, Action] = field(default_factory=default_bindings)
    custom: tuple[StoredGesture, ...] = ()
    allow_shell: bool = False

    # ---------- reading ----------
    @property
    def shapes(self) -> tuple[cg.CustomGesture, ...]:
        return tuple(g.shape for g in self.custom)

    def find(self, gesture_id: str) -> StoredGesture | None:
        return next((g for g in self.custom if g.id == gesture_id), None)

    def action_for(self, label: str | None) -> Action:
        """The action for a built-in shape name or a custom label ("custom:<id>")."""
        if not label:
            return NOTHING
        if label.startswith(CUSTOM_PREFIX):
            found = self.find(label[len(CUSTOM_PREFIX):])
            action = found.action if found else NOTHING
        else:
            action = self.bindings.get(label, NOTHING)
        if action.kind == "shell" and not self.allow_shell:
            return NOTHING
        return action

    def name_of(self, label: str | None) -> str:
        if label and label.startswith(CUSTOM_PREFIX):
            found = self.find(label[len(CUSTOM_PREFIX):])
            return found.name if found else ""
        return SHAPES[label][1] if label in SHAPES else ""

    # ---------- changing ----------
    def with_binding(self, shape: str, action: Action) -> "GestureStore":
        if shape not in SHAPES:
            raise KeyError(f"unknown gesture: {shape}")
        return replace(self, bindings=MappingProxyType({**self.bindings, shape: action}))

    def with_default_bindings(self) -> "GestureStore":
        return replace(self, bindings=default_bindings())

    def with_custom(self, gesture: StoredGesture) -> "GestureStore":
        """Adds a new recorded gesture, or replaces the one with the same id."""
        others = tuple(g for g in self.custom if g.id != gesture.id)
        if len(others) == len(self.custom) and len(self.custom) >= MAX_CUSTOM:
            raise ValueError(f"you can keep up to {MAX_CUSTOM} gestures of your own")
        position = next((i for i, g in enumerate(self.custom) if g.id == gesture.id),
                        len(self.custom))
        updated = (*others[:position], gesture, *others[position:])
        return replace(self, custom=updated)

    def with_custom_action(self, gesture_id: str, action: Action) -> "GestureStore":
        return self.with_custom(replace(self._require(gesture_id), action=action))

    def renamed(self, gesture_id: str, name: str) -> "GestureStore":
        found = self._require(gesture_id)
        shape = replace(found.shape, name=clean_gesture_name(name))
        return self.with_custom(replace(found, shape=shape))

    def without(self, gesture_id: str) -> "GestureStore":
        self._require(gesture_id)
        return replace(self, custom=tuple(g for g in self.custom if g.id != gesture_id))

    def with_allow_shell(self, on: bool) -> "GestureStore":
        return replace(self, allow_shell=bool(on))

    def _require(self, gesture_id: str) -> StoredGesture:
        found = self.find(gesture_id)
        if found is None:
            raise KeyError("that gesture no longer exists")
        return found


def new_gesture(name: str, samples: np.ndarray, action: Action = NOTHING,
                gesture_id: str | None = None) -> StoredGesture:
    shape = cg.make_gesture(gesture_id or uuid.uuid4().hex[:8], clean_gesture_name(name),
                            samples)
    return StoredGesture(shape, action)


# ---------- file ----------

def load(path: Path, old_flags: Mapping[str, bool] | None = None) -> GestureStore:
    """The saved gestures. Without a file yet, gestures switched off with the old
    per-gesture switches start as "Nothing"."""
    try:
        saved = json.loads(path.read_text())
    except FileNotFoundError:
        return _migrated(old_flags or {})
    except OSError:
        return GestureStore()
    except ValueError:
        saved = None
    if not isinstance(saved, dict):
        _keep_damaged(path)
        return GestureStore()
    allow_shell = saved.get("allow_shell") is True
    return GestureStore(bindings=_load_bindings(saved.get("bindings"), allow_shell),
                        custom=_load_custom(saved.get("custom"), allow_shell),
                        allow_shell=allow_shell)


def _keep_damaged(path: Path) -> None:
    """Set a damaged gestures.json aside as gestures.bad.json (instead of letting the next
    save overwrite your recorded gestures for good)."""
    try:
        path.replace(path.with_name(path.stem + ".bad" + path.suffix))
    except OSError:
        pass


def _migrated(old_flags: Mapping[str, bool]) -> GestureStore:
    store = GestureStore()
    for switch, shapes in OLD_SWITCHES.items():
        if old_flags.get(switch) is False:
            for shape in shapes:
                store = store.with_binding(shape, NOTHING)
    return store


def _parse_or_nothing(data: object, allow_shell: bool) -> Action:
    """Lenient: an action that can't run right now is kept (see parse_action strict)."""
    try:
        return parse_action(data, allow_shell, strict=False)
    except ValueError:
        return NOTHING


def _load_bindings(data: object, allow_shell: bool) -> Mapping[str, Action]:
    bindings = dict(default_bindings())
    if isinstance(data, dict):
        for shape, action in data.items():
            if shape in SHAPES:
                bindings[shape] = _parse_or_nothing(action, allow_shell)
    return MappingProxyType(bindings)


def _load_custom(data: object, allow_shell: bool) -> tuple[StoredGesture, ...]:
    if not isinstance(data, list):
        return ()
    loaded: list[StoredGesture] = []
    for entry in data[:MAX_CUSTOM]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        try:
            samples = np.asarray(entry.get("samples"), dtype=np.float64)
            if samples.ndim != 2 or samples.shape[1] != cg.FEATURES:
                continue
            if not np.all(np.isfinite(samples)):
                continue
            gesture = new_gesture(entry.get("name"), samples,
                                  _parse_or_nothing(entry.get("action"), allow_shell),
                                  gesture_id=entry["id"][:16])
        except (ValueError, TypeError):
            continue
        if all(g.id != gesture.id for g in loaded):
            loaded.append(gesture)
    return tuple(loaded)


def to_json(store: GestureStore) -> dict:
    return {
        "bindings": {shape: action.to_json() for shape, action in store.bindings.items()},
        "custom": [{"id": g.id, "name": g.name, "action": g.action.to_json(),
                    "samples": np.round(g.shape.samples, SAMPLE_DECIMALS).tolist()}
                   for g in store.custom],
        "allow_shell": store.allow_shell,
    }


def save(store: GestureStore, path: Path) -> None:
    """Raises OSError if the file can't be written. Written to a temporary file first so
    a crash halfway never leaves a broken gestures.json."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(to_json(store)) + "\n")
    tmp.replace(path)
