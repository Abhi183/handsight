"""The macOS permissions Handsight needs: what they are now, asking again, and links to
the right pane of System Settings.

People often click "Don't Allow" or skip a prompt. macOS never asks again by itself, so
"Ask again" clears our earlier answer with tccutil (which works on the calling app's own
entries without admin rights) and then makes macOS show its prompt.

Checking never shows a prompt: Automation uses AEDeterminePermissionToAutomateTarget with
askUserIfNeeded off. Everything that touches macOS is passed in or wrapped, so the
decisions are unit tested.
"""

import ctypes
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

ALLOWED, DENIED, NOT_ASKED, NOT_RUNNING, UNKNOWN = (
    "allowed", "denied", "not_asked", "not_running", "unknown")
TCCUTIL = "/usr/bin/tccutil"
PANE = "x-apple.systempreferences:com.apple.preference.security?"
SYSTEM_EVENTS = "com.apple.systemevents"
BROWSERS = {"com.google.Chrome": "Chrome", "com.brave.Browser": "Brave",
            "com.apple.Safari": "Safari"}
CORE_SERVICES = "/System/Library/Frameworks/CoreServices.framework/CoreServices"
NO_ERR, NOT_PERMITTED, NEEDS_CONSENT, PROC_NOT_FOUND = 0, -1743, -1744, -600
UNKNOWN_CODE = 1  # not an OSStatus macOS returns: we couldn't ask
AV_NOT_DETERMINED, AV_RESTRICTED, AV_DENIED, AV_AUTHORIZED = 0, 1, 2, 3


@dataclass(frozen=True)
class Permission:
    id: str
    label: str
    detail: str
    service: str        # tccutil service name
    pane: str           # System Settings > Privacy & Security section


PERMISSIONS = (
    Permission("camera", "Camera",
               "Needed for everything: Handsight reads your hands and face through it.",
               "Camera", "Privacy_Camera"),
    Permission("accessibility", "Accessibility",
               "Lets gestures press keys and shortcuts, move the pointer and scroll.",
               "Accessibility", "Privacy_Accessibility"),
    Permission("system_events", "Automation: System Events",
               "Lets pointing left and right switch browser tabs.",
               "AppleEvents", "Privacy_Automation"),
    Permission("browsers", "Automation: your browser",
               "Lets Handsight close personal tabs (Instagram, WhatsApp) when someone sits "
               "with you.", "AppleEvents", "Privacy_Automation"),
)
BY_ID = {p.id: p for p in PERMISSIONS}


# ---------- reading ----------

def camera_status(read: Callable[[], int] | None = None) -> str:
    code = (read or _av_camera_code)()
    return {AV_AUTHORIZED: ALLOWED, AV_NOT_DETERMINED: NOT_ASKED}.get(code, DENIED)


def _av_camera_code() -> int:
    try:
        import AVFoundation
    except ImportError:
        return AV_NOT_DETERMINED
    return int(AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
        AVFoundation.AVMediaTypeVideo))


def automation_code_status(code: int) -> str:
    return {NO_ERR: ALLOWED, NOT_PERMITTED: DENIED, NEEDS_CONSENT: NOT_ASKED,
            PROC_NOT_FOUND: NOT_RUNNING}.get(code, UNKNOWN)


class _AEDesc(ctypes.Structure):
    _fields_ = [("descriptorType", ctypes.c_uint32), ("dataHandle", ctypes.c_void_p)]


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


def automation_code(bundle_id: str) -> int:
    """macOS's answer for sending Apple Events to that app, without asking the user."""
    try:
        lib = ctypes.CDLL(CORE_SERVICES)
    except OSError:
        return UNKNOWN_CODE
    desc = _AEDesc()
    data = bundle_id.encode("utf-8")
    lib.AECreateDesc.restype = ctypes.c_int16
    lib.AECreateDesc.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_long,
                                 ctypes.POINTER(_AEDesc)]
    lib.AEDisposeDesc.argtypes = [ctypes.POINTER(_AEDesc)]
    if lib.AECreateDesc(_fourcc("bund"), data, len(data), ctypes.byref(desc)) != 0:
        return UNKNOWN_CODE
    try:
        check = lib.AEDeterminePermissionToAutomateTarget
        check.restype = ctypes.c_int32
        check.argtypes = [ctypes.POINTER(_AEDesc), ctypes.c_uint32, ctypes.c_uint32,
                          ctypes.c_bool]
        return int(check(ctypes.byref(desc), _fourcc("****"), _fourcc("****"), False))
    finally:
        lib.AEDisposeDesc(ctypes.byref(desc))


def browsers_status(running: Callable[[str], bool],
                    code_for: Callable[[str], int] = automation_code) -> str:
    """Allowed if every running browser is; not running if none is open."""
    statuses = [automation_code_status(code_for(b)) for b in BROWSERS if running(b)]
    if not statuses:
        return NOT_RUNNING
    for worst in (DENIED, NOT_ASKED, UNKNOWN):
        if worst in statuses:
            return worst
    return ALLOWED


def statuses(camera: Callable[[], str], accessibility: Callable[[], bool],
             running: Callable[[str], bool],
             code_for: Callable[[str], int] = automation_code) -> dict[str, str]:
    return {
        "camera": camera(),
        "accessibility": ALLOWED if accessibility() else DENIED,
        "system_events": automation_code_status(code_for(SYSTEM_EVENTS)),
        "browsers": browsers_status(running, code_for),
    }


def view(found: dict[str, str]) -> list[dict]:
    """What the settings page shows, in order."""
    return [{"id": p.id, "label": p.label, "detail": p.detail, "status": found.get(p.id, UNKNOWN)}
            for p in PERMISSIONS]


def missing(found: dict[str, str]) -> list[str]:
    """Permissions that block a feature right now (a closed browser doesn't count)."""
    return [pid for pid, status in found.items() if status in (DENIED, NOT_ASKED)]


# ---------- changing ----------

def reset(permission_id: str, bundle_id: str,
          run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str | None:
    """Forget our earlier answer for this permission. Returns an error message or None."""
    service = BY_ID[permission_id].service
    try:
        result = run([TCCUTIL, "reset", service, bundle_id], capture_output=True, text=True,
                     timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"Could not reset {BY_ID[permission_id].label}: {e}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        return f"macOS wouldn't reset {BY_ID[permission_id].label}{suffix}"
    return None


def settings_url(permission_id: str) -> str:
    return PANE + BY_ID[permission_id].pane


def poke_command(bundle_id: str) -> list[str]:
    """A harmless Apple Event; sending it makes macOS show the Automation prompt."""
    if bundle_id == SYSTEM_EVENTS:
        return ["osascript", "-e",
                'tell application id "com.apple.systemevents" to count processes']
    return ["osascript", "-e", f'tell application id "{bundle_id}" to count windows']
