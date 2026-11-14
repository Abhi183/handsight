"""What a gesture does: a built-in action, a keyboard shortcut, opening something, an
Apple Shortcut, a Terminal command, or nothing.

Action is immutable and validated; describe() is what the settings page shows. Running
an action is in gesture_runner.py, so this module stays pure and fully unit tested.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlparse

KINDS = ("builtin", "keys", "open", "shortcut", "shell", "none")
BUILTINS = MappingProxyType({
    "volume_up": "Volume up",
    "volume_down": "Volume down",
    "mute": "Mute / unmute",
    "play_pause": "Play / pause",
    "next_track": "Next track",
    "previous_track": "Previous track",
    "selfie": "Take a selfie",
    "next_tab": "Next tab",
    "previous_tab": "Previous tab",
    "lock_screen": "Lock the screen",
})
REPEATING = frozenset({"volume_up", "volume_down"})  # keep firing while the hand is held
MODIFIERS = ("cmd", "ctrl", "alt", "shift")
MODIFIER_SYMBOLS = MappingProxyType({"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "cmd": "⌘"})
URL_SCHEMES = ("http", "https", "mailto")
MAX_TEXT = 500
# "Open" must not become a back door for running code while Terminal commands are off:
# macOS runs these when opened.
RUNNABLE_SUFFIXES = frozenset({".command", ".tool", ".terminal", ".sh", ".zsh", ".bash",
                               ".csh", ".py", ".pl", ".rb", ".workflow", ".scpt",
                               ".applescript", ".scptd", ".pkg", ".mpkg"})
MAX_SHORTCUT_NAME = 120

# Browser KeyboardEvent.code -> macOS virtual key code (US layout positions).
KEY_CODES = MappingProxyType({
    **{f"Key{c}": k for c, k in zip(
        "ASDFHGZXCVBQWERYT", (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17),
        strict=True)},
    "KeyO": 31, "KeyU": 32, "KeyI": 34, "KeyP": 35, "KeyL": 37, "KeyJ": 38, "KeyK": 40,
    "KeyN": 45, "KeyM": 46,
    "Digit1": 18, "Digit2": 19, "Digit3": 20, "Digit4": 21, "Digit6": 22, "Digit5": 23,
    "Digit9": 25, "Digit7": 26, "Digit8": 28, "Digit0": 29,
    "Equal": 24, "Minus": 27, "BracketRight": 30, "BracketLeft": 33, "Quote": 39,
    "Semicolon": 41, "Backslash": 42, "Comma": 43, "Slash": 44, "Period": 47,
    "Backquote": 50, "Enter": 36, "Tab": 48, "Space": 49, "Backspace": 51, "Escape": 53,
    "Delete": 117, "Home": 115, "End": 119, "PageUp": 116, "PageDown": 121,
    "ArrowLeft": 123, "ArrowRight": 124, "ArrowDown": 125, "ArrowUp": 126,
    "F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97, "F7": 98, "F8": 100,
    "F9": 101, "F10": 109, "F11": 103, "F12": 111,
})
KEY_NAMES = MappingProxyType({
    "Enter": "↩", "Tab": "⇥", "Space": "Space", "Backspace": "⌫", "Escape": "Esc",
    "Delete": "⌦", "ArrowLeft": "←", "ArrowRight": "→", "ArrowDown": "↓", "ArrowUp": "↑",
    "Home": "Home", "End": "End", "PageUp": "Page Up", "PageDown": "Page Down",
    "Equal": "=", "Minus": "-", "BracketRight": "]", "BracketLeft": "[", "Quote": "'",
    "Semicolon": ";", "Backslash": "\\", "Comma": ",", "Slash": "/", "Period": ".",
    "Backquote": "`",
})


@dataclass(frozen=True)
class Keys:
    """A key combination, e.g. Keys("Digit4", ("cmd", "shift"))."""
    code: str
    modifiers: tuple[str, ...] = ()

    @property
    def key_code(self) -> int:
        return KEY_CODES[self.code]

    def describe(self) -> str:
        mods = "".join(MODIFIER_SYMBOLS[m] for m in ("ctrl", "alt", "shift", "cmd")
                       if m in self.modifiers)
        return mods + _key_label(self.code)


def _key_label(code: str) -> str:
    if code in KEY_NAMES:
        return KEY_NAMES[code]
    for prefix in ("Key", "Digit"):
        if code.startswith(prefix):
            return code[len(prefix):]
    return code


def parse_keys(value: object) -> Keys:
    """Keys from the settings page: {"code": "KeyK", "modifiers": ["cmd"]}."""
    if not isinstance(value, dict):
        raise ValueError("a shortcut needs a key")
    code, mods = value.get("code"), value.get("modifiers", [])
    if code not in KEY_CODES:
        raise ValueError("that key can't be used in a shortcut")
    if not isinstance(mods, (list, tuple)) or any(m not in MODIFIERS for m in mods):
        raise ValueError("unknown modifier key")
    return Keys(code, tuple(m for m in MODIFIERS if m in mods))


@dataclass(frozen=True)
class Action:
    """kind is one of KINDS. value depends on kind:
    builtin: a BUILTINS key; keys: Keys; open: URL, file/folder path or .app path;
    shortcut: an Apple Shortcuts name; shell: a command line; none: ""."""
    kind: str = "none"
    value: object = ""

    def describe(self) -> str:
        if self.kind == "builtin":
            return BUILTINS[self.value]
        if self.kind == "keys":
            return f"Press {self.value.describe()}"
        if self.kind == "open":
            return f"Open {_short_target(self.value)}"
        if self.kind == "shortcut":
            return f"Run shortcut “{self.value}”"
        if self.kind == "shell":
            return f"Run: {self.value}"
        return "Nothing"

    @property
    def repeats(self) -> bool:
        return self.kind == "builtin" and self.value in REPEATING

    def to_json(self) -> dict:
        if self.kind == "keys":
            return {"kind": "keys", "value": {"code": self.value.code,
                                              "modifiers": list(self.value.modifiers)}}
        return {"kind": self.kind, "value": self.value}


NOTHING = Action()


def _short_target(target: str) -> str:
    if target.endswith(".app"):
        return Path(target).stem
    parsed = urlparse(target)
    if parsed.scheme in URL_SCHEMES:
        return parsed.netloc or target
    return Path(target).name or target


def _clean_text(value: object, what: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} is empty")
    text = value.strip()
    if len(text) > limit or "\x00" in text or "\n" in text or "\r" in text:
        raise ValueError(f"{what} is too long or has line breaks")
    return text


def is_url(target: str) -> bool:
    return urlparse(target).scheme in URL_SCHEMES


def _check_open_target(value: object, strict: bool) -> str:
    """strict: also check the file is there (when you set it; not when loading, so an
    app on an unplugged drive isn't forgotten)."""
    target = _clean_text(value, "what to open")
    parsed = urlparse(target)
    if parsed.scheme in URL_SCHEMES:
        if parsed.scheme != "mailto" and not parsed.netloc:
            raise ValueError("that web address is incomplete")
        return target
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
        raise ValueError("only http, https and mailto links can be opened")
    path = Path(target).expanduser()
    if not path.is_absolute():
        raise ValueError("use a full path, a web address, or Choose…")
    if path.suffix.lower() in RUNNABLE_SUFFIXES:
        raise ValueError("scripts can't be opened by a gesture; use a Terminal command")
    if strict and not path.exists():
        raise ValueError(f"{path} doesn't exist")
    if strict and path.is_file() and path.suffix.lower() != ".app" and os.access(path, os.X_OK):
        raise ValueError("programs can't be opened by a gesture; use a Terminal command")
    return str(path)


def parse_action(data: object, allow_shell: bool, strict: bool = True) -> Action:
    """An Action from JSON. Raises ValueError. The settings page uses strict=True;
    loading gestures.json uses strict=False, which keeps actions that can't run right now
    (a missing file, a Terminal command while those are off) instead of dropping them."""
    if not isinstance(data, dict) or data.get("kind") not in KINDS:
        raise ValueError("unknown action")
    kind, value = data["kind"], data.get("value", "")
    if kind == "none":
        return NOTHING
    if kind == "builtin":
        if value not in BUILTINS:
            raise ValueError("unknown built-in action")
        return Action("builtin", value)
    if kind == "keys":
        return Action("keys", parse_keys(value))
    if kind == "open":
        return Action("open", _check_open_target(value, strict))
    if kind == "shortcut":
        name = _clean_text(value, "the shortcut name", MAX_SHORTCUT_NAME)
        if name.startswith("-"):
            raise ValueError("a shortcut name can't start with -")
        return Action("shortcut", name)
    if strict and not allow_shell:
        raise ValueError("Terminal commands are switched off")
    return Action("shell", _clean_text(value, "the command"))


def _keys(code: str, *mods: str) -> dict:
    return {"kind": "keys", "value": {"code": code, "modifiers": list(mods)}}


def _builtin(name: str) -> dict:
    return {"kind": "builtin", "value": name}


# Ready-made actions for apps people use hands-free. Shortcuts go to the app in front.
PRESETS = (
    ("Chrome", "Next tab", _builtin("next_tab")),
    ("Chrome", "Previous tab", _builtin("previous_tab")),
    ("Chrome", "New tab", _keys("KeyT", "cmd")),
    ("Chrome", "Close tab", _keys("KeyW", "cmd")),
    ("Chrome", "Reopen closed tab", _keys("KeyT", "cmd", "shift")),
    ("Chrome", "Reload", _keys("KeyR", "cmd")),
    ("Chrome", "Back", _keys("BracketLeft", "cmd")),
    ("Chrome", "Forward", _keys("BracketRight", "cmd")),
    ("Chrome", "Find on page", _keys("KeyF", "cmd")),
    ("Chrome", "Full screen", _keys("KeyF", "cmd", "ctrl")),
    ("Zoom", "Mute / unmute", _keys("KeyA", "cmd", "shift")),
    ("Zoom", "Camera on / off", _keys("KeyV", "cmd", "shift")),
    ("Zoom", "Raise / lower hand", _keys("KeyY", "alt")),
    ("Zoom", "Share screen", _keys("KeyS", "cmd", "shift")),
    ("Google Meet", "Mute / unmute", _keys("KeyD", "cmd")),
    ("Google Meet", "Camera on / off", _keys("KeyE", "cmd")),
    ("Google Meet", "Raise / lower hand", _keys("KeyH", "cmd", "ctrl")),
    ("Microsoft Teams", "Mute / unmute", _keys("KeyM", "cmd", "shift")),
    ("Microsoft Teams", "Camera on / off", _keys("KeyO", "cmd", "shift")),
    ("Microsoft Teams", "Raise / lower hand", _keys("KeyK", "cmd", "shift")),
    ("YouTube", "Play / pause", _keys("KeyK")),
    ("YouTube", "Back 10 seconds", _keys("KeyJ")),
    ("YouTube", "Forward 10 seconds", _keys("KeyL")),
    ("YouTube", "Full screen", _keys("KeyF")),
    ("Music & video", "Play / pause", _builtin("play_pause")),
    ("Music & video", "Next track", _builtin("next_track")),
    ("Music & video", "Previous track", _builtin("previous_track")),
)


def catalog() -> dict:
    """What the settings page needs to build its action pickers."""
    return {"kinds": list(KINDS), "builtins": dict(BUILTINS),
            "key_codes": sorted(KEY_CODES), "modifiers": list(MODIFIERS),
            "presets": [{"group": group, "label": label, "action": action}
                        for group, label, action in PRESETS]}
