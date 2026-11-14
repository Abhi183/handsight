"""Small wrappers around macOS features. All AppleScript here is constant text."""

import ctypes
import subprocess
import sys
import time
from pathlib import Path

from handsight.paths import ROOT as PROJECT_ROOT

LOGIN_FRAMEWORK = "/System/Library/PrivateFrameworks/login.framework/Versions/Current/login"
DEFAULT_SOUND = "/System/Library/Sounds/Sosumi.aiff"
QUIET = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


def _osascript_async(script: str) -> None:
    subprocess.Popen(["osascript", "-e", script], **QUIET)


def notify(title: str, subtitle: str, message: str) -> None:
    """Show a notification banner. Arguments must be trusted constant strings."""
    for text in (title, subtitle, message):
        if '"' in text or "\\" in text:
            raise ValueError("notification text cannot contain quotes or backslashes")
    _osascript_async(f'display notification "{message}" with title "{title}" '
                     f'subtitle "{subtitle}"')


_popup_handler = None  # set by the app to draw pop-ups itself (handsight.native_toast)


def set_popup_handler(handler) -> None:
    """handler(title, message, color, seconds, action); safe to call from any thread.
    `action` names what clicking the pop-up should do (e.g. "water"), or None."""
    global _popup_handler
    _popup_handler = handler


def popup(title: str, message: str, color: str = "#e5484d", seconds: float = 5.0,
          action: str | None = None) -> None:
    """Our own on-screen pop-up; shows even when notification banners are hidden.

    Inside the app it is a native panel; run from Terminal it is a small Tk window
    (handsight.toast) in its own process.
    """
    if _popup_handler is not None:
        _popup_handler(title, message, color, seconds, action)
        return
    subprocess.Popen([sys.executable, "-m", "handsight.toast", title, message, color,
                      str(seconds)], cwd=PROJECT_ROOT, **QUIET)


def alert(title: str, subtitle: str, message: str, color: str = "#e5484d") -> None:
    """Pop-up on screen, plus a notification so it is kept in Notification Center."""
    popup(f"{title}: {subtitle}", message, color)
    notify(title, subtitle, message)


def play_sound(path: str = DEFAULT_SOUND) -> None:
    if Path(path).exists():
        subprocess.Popen(["afplay", path], **QUIET)


def lock_screen() -> str:
    """Lock the Mac. Returns how it was done, for the on-screen log."""
    try:
        lib = ctypes.CDLL(LOGIN_FRAMEWORK)
        lib.SACLockScreenImmediate()
        return "locked"
    except (OSError, AttributeError):
        # Falls back to sleeping the display, which locks when
        # "Require password immediately after sleep" is on.
        subprocess.run(["pmset", "displaysleepnow"], check=False, **QUIET)
        return "display slept (fallback)"


NX_KEYTYPE_SOUND_UP, NX_KEYTYPE_SOUND_DOWN, NX_KEYTYPE_MUTE, NX_KEYTYPE_PLAY = 0, 1, 7, 16
NX_KEYTYPE_NEXT, NX_KEYTYPE_PREVIOUS = 17, 18
NS_SYSTEM_DEFINED, MEDIA_KEY_SUBTYPE = 14, 8


def _press_media_key(key: int) -> bool:
    """Press a keyboard media key, so macOS shows its own volume pop-up.

    Returns False when this process may not post key events (no Accessibility
    permission); callers then fall back to AppleScript, which changes the volume
    without the pop-up.
    """
    try:
        import Quartz
        from AppKit import NSEvent
    except ImportError:
        return False
    if not Quartz.CGPreflightPostEventAccess():
        return False
    for down in (True, False):
        state = 0xA if down else 0xB
        event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            NS_SYSTEM_DEFINED, (0, 0), state << 8, 0, 0, None, MEDIA_KEY_SUBTYPE,
            (key << 16) | (state << 8), -1)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event.CGEvent())
    return True


def volume_up() -> bool:
    """One volume-key step up with the macOS pop-up. False = fell back, no pop-up."""
    if _press_media_key(NX_KEYTYPE_SOUND_UP):
        return True
    _osascript_async("set volume output volume ((output volume of (get volume settings)) + 6)")
    return False


def volume_down() -> bool:
    if _press_media_key(NX_KEYTYPE_SOUND_DOWN):
        return True
    _osascript_async("set volume output volume ((output volume of (get volume settings)) - 6)")
    return False


def toggle_mute() -> bool:
    if _press_media_key(NX_KEYTYPE_MUTE):
        return True
    _osascript_async("set volume output muted not (output muted of (get volume settings))")
    return False


def play_pause() -> bool:
    """Press the Play/Pause media key (pauses YouTube in Chrome, Spotify, Music...).

    False when key events are blocked (no Accessibility permission); there is no
    fallback because no AppleScript reaches every player.
    """
    return _press_media_key(NX_KEYTYPE_PLAY)


def next_track() -> bool:
    return _press_media_key(NX_KEYTYPE_NEXT)


def previous_track() -> bool:
    return _press_media_key(NX_KEYTYPE_PREVIOUS)


# Modifier name -> CGEventFlags bit (see gesture_actions.MODIFIERS).
MODIFIER_FLAGS = {"shift": 1 << 17, "ctrl": 1 << 18, "alt": 1 << 19, "cmd": 1 << 20}


def press_keys(key_code: int, modifiers: tuple[str, ...] = ()) -> bool:
    """Press and release one key with modifiers, as if typed. False without Accessibility."""
    try:
        import Quartz
    except ImportError:
        return False
    if not Quartz.CGPreflightPostEventAccess():
        return False
    flags = 0
    for name in modifiers:
        flags |= MODIFIER_FLAGS[name]
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, key_code, down)
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    return True


def mouse_position() -> tuple[float, float] | None:
    """Where the pointer is, in screen points (origin top-left of the main display)."""
    try:
        import Quartz
    except ImportError:
        return None
    location = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return (location.x, location.y)


def desktop_bounds() -> tuple[float, float, float, float] | None:
    """(left, top, right, bottom) of the box around all displays, in screen points."""
    try:
        import Quartz
    except ImportError:
        return None
    error, displays, _count = Quartz.CGGetActiveDisplayList(16, None, None)
    if error or not displays:
        return None
    rects = [Quartz.CGDisplayBounds(d) for d in displays]
    return (min(r.origin.x for r in rects), min(r.origin.y for r in rects),
            max(r.origin.x + r.size.width for r in rects),
            max(r.origin.y + r.size.height for r in rects))


CLICK_PRESS_S = 0.015  # how long the button stays down


def _post_mouse(kinds: tuple, position: tuple[float, float], button: int,
                click: bool = False) -> bool:
    """Post mouse events. False when this process may not (no Accessibility permission).

    For a click the events carry click count 1 and the button is held for a moment.
    Without the count, apps receive a "click number zero" and buttons and links ignore
    it (context menus don't check, which is why only right-click used to work).
    """
    try:
        import Quartz
    except ImportError:
        return False
    if not Quartz.CGPreflightPostEventAccess():
        return False
    for i, kind in enumerate(kinds):
        if click and i:
            time.sleep(CLICK_PRESS_S)
        event = Quartz.CGEventCreateMouseEvent(None, kind, position, button)
        if click:
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, 1)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    return True


def move_mouse(position: tuple[float, float]) -> bool:
    try:
        import Quartz
    except ImportError:
        return False
    return _post_mouse((Quartz.kCGEventMouseMoved,), position, Quartz.kCGMouseButtonLeft)


def click_mouse(position: tuple[float, float], right: bool = False) -> bool:
    """Press and release a mouse button at `position`."""
    try:
        import Quartz
    except ImportError:
        return False
    if right:
        return _post_mouse((Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp),
                           position, Quartz.kCGMouseButtonRight, click=True)
    return _post_mouse((Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp),
                       position, Quartz.kCGMouseButtonLeft, click=True)


def scroll(pixels: int) -> bool:
    """Scroll whatever is under the pointer by `pixels` (positive = up, like a wheel).
    False when this process may not post events (no Accessibility permission)."""
    if pixels == 0:
        return True
    try:
        import Quartz
    except ImportError:
        return False
    if not Quartz.CGPreflightPostEventAccess():
        return False
    event = Quartz.CGEventCreateScrollWheelEvent2(
        None, Quartz.kCGScrollEventUnitPixel, 1, int(pixels), 0, 0)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    return True


APPLICATION_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
DEV_BUNDLE_ID = "local.handsight.dev"  # Handsight.app built by tools/make_app.sh
RELEASE_BUNDLE_ID = "io.github.abhi183.handsight"  # the downloadable app (tools/build_dmg.sh)
OWN_BUNDLE_IDS = (RELEASE_BUNDLE_ID, DEV_BUNDLE_ID)


def bundle_id() -> str:
    """The running Handsight.app's bundle ID; the checkout build's ID outside an app."""
    try:
        from Foundation import NSBundle
        own = NSBundle.mainBundle().bundleIdentifier()
    except ImportError:
        own = None
    return str(own) if own and str(own) in OWN_BUNDLE_IDS else DEV_BUNDLE_ID


def accessibility_allowed(prompt: bool = False) -> bool:
    """Whether macOS lets this app press keys and move the pointer (Accessibility).

    With prompt=True and no permission yet, macOS shows its own "would like to control
    this computer" dialog and adds the app to the Accessibility list, switched off.
    """
    try:
        from Foundation import NSDictionary
        import objc
        lib = ctypes.CDLL(APPLICATION_SERVICES)
        check = lib.AXIsProcessTrustedWithOptions
        check.argtypes, check.restype = [ctypes.c_void_p], ctypes.c_bool
        options = NSDictionary.dictionaryWithObject_forKey_(bool(prompt),
                                                            "AXTrustedCheckOptionPrompt")
        return bool(check(objc.pyobjc_id(options)))
    except (OSError, AttributeError, ImportError):
        return True  # can't tell: let the actions themselves report a failure


def natural_scrolling() -> bool:
    """The "Natural scrolling" setting (on unless you turned it off)."""
    try:
        from Foundation import NSUserDefaults
    except ImportError:
        return True
    value = NSUserDefaults.standardUserDefaults().objectForKey_("com.apple.swipescrolldirection")
    return True if value is None else bool(value)


def keystroke(script_body: str) -> str | None:
    """Send keys via System Events. Returns an error message, or None on success.

    Needs Accessibility permission for the terminal app.
    """
    result = subprocess.run(
        ["osascript", "-e", f'tell application "System Events" to {script_body}'],
        capture_output=True, text=True, timeout=3, check=False,
    )
    if result.returncode != 0:
        return ("Keystrokes blocked: allow Handsight / Python (or Terminal) in System "
                "Settings > Privacy & Security > Accessibility")
    return None


def next_tab() -> str | None:
    return keystroke("key code 48 using control down")  # ctrl+tab


def previous_tab() -> str | None:
    return keystroke("key code 48 using {control down, shift down}")  # ctrl+shift+tab
