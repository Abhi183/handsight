"""Screenshots of the real settings window for the README, with sample data (no camera,
nothing from your own settings). Opens the window for a few seconds.

Usage: .venv/bin/python tools/snap_settings.py [out dir]   (default docs/demo)
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from AppKit import (  # noqa: E402
    NSAppearance,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBitmapImageRep,
    NSPNGFileType,
)
from Foundation import NSDate, NSRunLoop  # noqa: E402

from handsight import custom_gestures as cg  # noqa: E402
from handsight import gesture_store as gs  # noqa: E402
from handsight.gesture_actions import Action, Keys  # noqa: E402
from handsight.settings_api import SettingsApi  # noqa: E402
from handsight.settings_panel import SettingsPanel  # noqa: E402

SHOTS = (  # (file, page, JavaScript to run before the picture)
    ("settings-gestures", "gestures",
     "[...document.querySelectorAll('button')]"
     ".filter(b => b.textContent === 'Change')[4].click();"),
    ("settings-permissions", "permissions", ""),
    ("settings-overview", "overview", ""),
)


class SampleHost:
    def status(self):
        return "watching"

    def cameras(self):
        return [{"id": "cam", "name": "FaceTime HD Camera", "built_in": True}]

    def camera_in_use(self):
        return "cam"

    def stats(self):
        today = {"water_glasses": 3, "desk_s": 14400, "good_posture": 0.82,
                 "water_reminders": 4, "breaks": 2, "posture_alerts": 3, "shoulder_alerts": 0,
                 "locks": 4, "company": 0, "gestures": 57, "scrolls": 12}
        return {"today": today, "week": []}

    def permission_statuses(self):
        return {"camera": "allowed", "accessibility": "allowed", "system_events": "allowed",
                "browsers": "not_asked"}

    def apply_settings(self, settings):
        pass

    def apply_gestures(self, store):
        pass


def sample_gestures(path: Path) -> None:
    rng = np.random.default_rng(1)
    base = rng.uniform(-1, 1, cg.FEATURES)
    rock = gs.new_gesture("Rock on", base + rng.normal(0, 1e-3, (30, cg.FEATURES)),
                          Action("open", "/Applications/Safari.app"))
    store = (gs.GestureStore()
             .with_binding("Closed_Fist", Action("keys", Keys("KeyA", ("cmd", "shift"))))
             .with_binding("ILoveYou", Action("keys", Keys("KeyK")))
             .with_custom(rock))
    gs.save(store, path)


def pump(seconds: float) -> None:
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))


def snapshot(panel: SettingsPanel, out: Path) -> None:
    done = {}

    def finished(image, error):
        done["image"], done["error"] = image, error

    panel.web.takeSnapshotWithConfiguration_completionHandler_(None, finished)
    for _ in range(50):
        if done:
            break
        pump(0.1)
    image = done.get("image")
    if image is None:
        raise RuntimeError(f"snapshot failed: {done.get('error')}")
    rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    rep.representationUsingType_properties_(NSPNGFileType, {}).writeToFile_atomically_(
        str(out), True)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "demo"
    out.mkdir(parents=True, exist_ok=True)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    tmp = Path(tempfile.mkdtemp())
    sample_gestures(tmp / "gestures.json")
    api = SettingsApi(tmp / "settings.json", tmp, SampleHost(), lambda _a: None,
                      gestures_file=tmp / "gestures.json")
    for theme in ("light", "dark"):
        name = "NSAppearanceNameAqua" if theme == "light" else "NSAppearanceNameDarkAqua"
        app.setAppearance_(NSAppearance.appearanceNamed_(name))
        panel = SettingsPanel(api)
        panel.show()
        pump(2.0)  # let the page load before switching pages
        for file, page, script in SHOTS:
            panel.web.evaluateJavaScript_completionHandler_(
                f"window.__dgShowPage('{page}'); "
                f"document.getElementById('main').scrollTop = 0; {script}",
                None)
            pump(0.8)
            snapshot(panel, out / f"{file}-{theme}.png")
            print(f"{file}-{theme}.png")
        panel.window.close()


if __name__ == "__main__":
    main()
