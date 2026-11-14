"""Handsight, the app: menu bar icon, Dock icon, video window, settings window and the
watcher, all in one process.

    python -m handsight.menubar        (Handsight.app runs exactly this)

Settings apply live: nothing restarts when you flip a switch. The watcher runs on
background threads (handsight.engine); everything that touches AppKit runs here on the
main thread.
"""

import argparse
import fcntl
import os
import subprocess
import sys
import threading
import time

import AVFoundation
import objc
import rumps
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSEventModifierFlagCommand,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSMenu,
    NSMenuItem,
    NSModalResponseOK,
    NSOpenPanel,
)
from Foundation import NSURL, NSBundle, NSObject, NSUserDefaults
from PyObjCTools import AppHelper
from rumps import rumps as rumps_core

from handsight import macos
from handsight import settings as cfg
from handsight.dock import QUIT_ALL_REQUEST, use_app_icon
from handsight.cameras import describe, list_cameras
from handsight import gesture_store, personal
from handsight import permissions as perm
from handsight.custom_gestures import Recorder
from handsight.gesture_actions import Action
from handsight.gestures import GestureApp
from handsight.engine import Watcher
from handsight.native_toast import Toasts
from handsight.recognition import forget_profile
from handsight.settings_api import SettingsApi
from handsight.settings_panel import SettingsPanel
from handsight.stats import StatsStore
from handsight.video_window import WATCHER_KEYS, VideoWindow
from handsight.vision import DATA

SETTINGS_FILE = DATA / "menubar_settings.json"
LOCK_FILE = DATA / ".menubar.lock"
STATS_FILE = DATA / "stats.json"
STATS_PUSH_S = 15.0  # refresh the stats page this often while it is open
SHOW_REQUEST = DATA / ".show_request"  # a second copy started from Terminal
ICON_ON, ICON_OFF = "👁", "◌"
FRAME_TICK_S = 1 / 30
POLL_S = 1.0
RETRY_S = 6.0
ITEM_AUTOSAVE_NAME = "HandsightEye"
ITEM_PREFERRED_POSITION = 260.0
PERMISSIONS_CACHE_S = 3.0
NOT_THE_APP = ("Permissions belong to Handsight.app. Open the app from Applications "
               "(not ./run in Terminal) to ask again.")
KEY_TEST_DELAY_S = 0.6  # let the app behind come to the front before pressing keys
CAMERA_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"
AV_NOT_DETERMINED, AV_AUTHORIZED = 0, 3
AMBER, GREEN = "#f5a623", "#30a46c"
STATUS_LINE = {"watching": "Watching", "starting": "Starting…", "stopped": "Stopped",
               "camera blocked": "Camera not allowed", "no camera": "No camera connected"}
VIDEO_MESSAGE = {"starting": "Starting camera…", "stopped": "Not watching",
                 "camera blocked": "Handsight needs the camera. Allow it in System Settings.",
                 "no camera": "No camera connected"}

START, STOP = "Start Watching", "Stop Watching"
VIDEO = "Show Video Window"
ENABLE_ALL, DISABLE_ALL = "Enable All Features", "Disable All Features"
SETTINGS, QUIT = "Settings…", "Quit Handsight"
WATER = "Log a Glass of Water"

_controller = None  # the running Handsight, for the app delegate and menu targets


class _AppDelegate(rumps_core.NSApp):
    """rumps' delegate plus: Dock click opens settings, quitting stops the camera."""

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _flag):
        if _controller is not None:
            _controller.open_settings()
        return True

    def applicationWillTerminate_(self, _notification):
        if _controller is not None:
            _controller.shutdown()


rumps_core.NSApp = _AppDelegate  # rumps instantiates this class inside run()


class _MenuTarget(NSObject):
    """Target for the main menu bar items (they need an Objective-C object)."""

    def openSettings_(self, _sender):
        _controller.open_settings()

    def toggleVideo_(self, _sender):
        _controller.set_flag("show_video", not _controller.settings.flags["show_video"])

    def toggleWatching_(self, _sender):
        _controller.toggle_watching()


def _menu_item(title: str, action: str | None, key: str = "", target=None,
               modifiers: int = NSEventModifierFlagCommand) -> NSMenuItem:
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
    item.setKeyEquivalentModifierMask_(modifiers)
    if target is not None:
        item.setTarget_(target)
    return item


def single_instance_or_exit():
    handle = open(LOCK_FILE, "w")  # noqa: SIM115 - stays open to hold the lock
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        SHOW_REQUEST.touch()  # ask the running copy to show its settings, then leave
        sys.exit(0)
    return handle


class Handsight(rumps.App):
    def __init__(self) -> None:
        super().__init__("Handsight", title=ICON_OFF, quit_button=None)
        self.settings = cfg.load(SETTINGS_FILE)
        self.stats_store = StatsStore(STATS_FILE)
        self.recorder = Recorder()
        self.gestures = gesture_store.load(gesture_store.FILE, cfg.saved_flags(SETTINGS_FILE))
        self._save_migrated_gestures()
        self._tester: GestureApp | None = None  # runs an action once for the Test button
        self._permissions_cache: tuple[float, dict[str, str]] | None = None
        self.watcher = Watcher(self.settings, on_status=self._status_from_watcher,
                               on_camera=lambda: AppHelper.callAfter(self._camera_changed),
                               stats=self.stats_store, gestures=self.gestures,
                               recorder=self.recorder)
        self.stats_push_in = STATS_PUSH_S
        self.known_cameras: list[dict] = []
        self.api = SettingsApi(SETTINGS_FILE, DATA, host=self, opener=subprocess.Popen,
                               gestures_file=gesture_store.FILE, recorder=self.recorder)
        self.state = "stopped"
        self.user_stopped = False
        self.quitting = False
        self.start_pending = False  # the last run is still shutting down; start when it has
        self.warned_camera = False
        self.retry_in = 0.0
        self.last_seq = 0
        self.placed = False
        self.video: VideoWindow | None = None
        self.panel: SettingsPanel | None = None
        self._target = _MenuTarget.alloc().init()
        self.status_item = rumps.MenuItem(STATUS_LINE["stopped"])
        self.toggle_item = rumps.MenuItem(START, callback=lambda _: self.toggle_watching())
        self.video_item = rumps.MenuItem(VIDEO, callback=lambda _: self.set_flag(
            "show_video", not self.settings.flags["show_video"]))
        self.menu = [self.status_item, self.toggle_item, self.video_item, None,
                     rumps.MenuItem(ENABLE_ALL, callback=lambda _: self.set_all(True)),
                     rumps.MenuItem(DISABLE_ALL, callback=lambda _: self.set_all(False)),
                     rumps.MenuItem(WATER, callback=lambda _: self.log_water()),
                     rumps.MenuItem(SETTINGS, callback=lambda _: self.open_settings(), key=","),
                     None, rumps.MenuItem(QUIT, callback=lambda _: rumps.quit_application())]
        self.frame_timer = rumps.Timer(self.on_frame_tick, FRAME_TICK_S)
        self.poll_timer = rumps.Timer(self.on_poll, POLL_S)
        rumps.events.before_start.register(self.on_launch)

    # ---------- start-up ----------
    def on_launch(self) -> None:
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        use_app_icon()  # matters when started from Terminal; the .app has its own
        self._build_main_menu(app)
        self.video = VideoWindow(on_settings=self.open_settings,
                                 on_pin=lambda on: self.set_flag("video_on_top", on),
                                 on_close=lambda: self.set_flag("show_video", False),
                                 on_key=self.on_video_key)
        self.panel = SettingsPanel(self.api)
        toasts = Toasts()
        actions = {"water": self.log_water}
        macos.set_popup_handler(
            lambda title, message, color, seconds, action=None: AppHelper.callAfter(
                toasts.show, title, message, color, seconds, actions.get(action)))
        self._sync_ui()
        self.frame_timer.start()
        self.poll_timer.start()
        self.start_watching()
        app.activateIgnoringOtherApps_(True)

    def _build_main_menu(self, app) -> None:
        cmd, opt, shift = (NSEventModifierFlagCommand, NSEventModifierFlagOption,
                           NSEventModifierFlagShift)
        menus = (
            ("Handsight", [
                _menu_item("About Handsight", "orderFrontStandardAboutPanel:"), None,
                _menu_item(SETTINGS, "openSettings:", ",", self._target), None,
                _menu_item("Hide Handsight", "hide:", "h"),
                _menu_item("Hide Others", "hideOtherApplications:", "h", modifiers=cmd | opt),
                _menu_item("Show All", "unhideAllApplications:"), None,
                _menu_item(QUIT, "terminate:", "q")]),
            ("Edit", [
                _menu_item("Undo", "undo:", "z"),
                _menu_item("Redo", "redo:", "z", modifiers=cmd | shift), None,
                _menu_item("Cut", "cut:", "x"), _menu_item("Copy", "copy:", "c"),
                _menu_item("Paste", "paste:", "v"), _menu_item("Select All", "selectAll:", "a")]),
            ("View", [
                _menu_item(VIDEO, "toggleVideo:", "1", self._target),
                _menu_item("Start / Stop Watching", "toggleWatching:", "", self._target)]),
            ("Window", [
                _menu_item("Minimize", "performMiniaturize:", "m"),
                _menu_item("Close", "performClose:", "w"), None,
                _menu_item("Bring All to Front", "arrangeInFront:")]),
        )
        main = NSMenu.alloc().init()
        for title, items in menus:
            menu = NSMenu.alloc().initWithTitle_(title)
            for item in items:
                menu.addItem_(item or NSMenuItem.separatorItem())
            holder = NSMenuItem.alloc().init()
            holder.setSubmenu_(menu)
            main.addItem_(holder)
            if title == "Window":
                app.setWindowsMenu_(menu)
        app.setMainMenu_(main)

    # ---------- watching ----------
    def start_watching(self) -> None:
        """Ask for the camera the macOS way (main thread), then start the watcher."""
        if self.quitting:
            return
        self.user_stopped = False
        av = AVFoundation.AVCaptureDevice
        status = av.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeVideo)
        if status == AV_AUTHORIZED:
            self._start_watcher()
        elif status == AV_NOT_DETERMINED:
            self._show_state("starting")
            av.requestAccessForMediaType_completionHandler_(
                AVFoundation.AVMediaTypeVideo,
                lambda granted: AppHelper.callAfter(self._after_camera_prompt, bool(granted)))
        else:
            self._show_state("camera blocked")

    def _after_camera_prompt(self, granted: bool) -> None:
        if self.quitting or self.user_stopped:
            return
        if granted:
            self._start_watcher()
        else:
            self._show_state("camera blocked")

    def _start_watcher(self) -> None:
        self.start_pending = not self.watcher.start()
        if self.start_pending:
            self._show_state("starting")

    def stop_watching(self) -> None:
        self.user_stopped = True
        self.start_pending = False
        self.watcher.stop()

    def shutdown(self) -> None:
        self.quitting = True
        self.frame_timer.stop()
        self.poll_timer.stop()
        self.watcher.stop()
        self._save_stats()

    def _save_stats(self) -> None:
        try:
            self.stats_store.save()
        except OSError as e:
            print(f"could not save stats: {e}", file=sys.stderr, flush=True)

    def _status_from_watcher(self, status: str) -> None:
        AppHelper.callAfter(self._show_state, status)  # watcher thread -> main thread

    def _show_state(self, status: str) -> None:
        self.state = status
        if status == "camera blocked" and not self.warned_camera:
            self.warned_camera = True
            macos.popup("Handsight needs the camera",
                        "Turn on Handsight under Camera in the window that just opened. "
                        "It starts by itself once allowed.", AMBER, 10)
            subprocess.Popen(["open", CAMERA_SETTINGS_URL])
        if status == "watching" and self.warned_camera:
            self.warned_camera = False
            macos.popup("Handsight", "Camera allowed. Watching now.", GREEN)
        self.retry_in = RETRY_S if status == "camera blocked" else 0.0
        self._sync_ui()

    # ---------- timers ----------
    def on_frame_tick(self, _) -> None:
        if self.video is None or not self.video.visible or self.state != "watching":
            return
        frame, seq = self.watcher.latest(self.last_seq)
        if frame is not None:
            self.last_seq = seq
            self.video.show_frame(frame)

    def on_poll(self, _) -> None:
        if QUIT_ALL_REQUEST.exists():
            QUIT_ALL_REQUEST.unlink(missing_ok=True)
            rumps.quit_application()
            return
        if SHOW_REQUEST.exists():
            SHOW_REQUEST.unlink(missing_ok=True)
            self.open_settings()
        self._place_icon()
        self._check_cameras()
        self.stats_push_in -= POLL_S
        if self.stats_push_in <= 0:
            self.stats_push_in = STATS_PUSH_S
            if self.panel is not None:
                self.panel.push_state()
        if self.start_pending and not self.user_stopped:
            self._start_watcher()
        if self.state == "camera blocked" and not self.user_stopped:
            self.retry_in -= POLL_S
            if self.retry_in <= 0:
                self.retry_in = RETRY_S
                self.start_watching()

    def _check_cameras(self) -> None:
        """Show cameras being plugged in or out in the settings window straight away."""
        if self.panel is None or not self.panel.visible:
            return
        cameras = describe(list_cameras())
        if cameras != self.known_cameras:
            self.known_cameras = cameras
            self.panel.push_state()

    def _camera_changed(self) -> None:
        if self.panel is not None:
            self.panel.push_state()

    # ---------- the Host interface used by the settings window ----------
    def status(self) -> str:
        return self.state

    def cameras(self) -> list[dict]:
        self.known_cameras = describe(list_cameras())
        return self.known_cameras

    def stats(self) -> dict:
        return self.stats_store.snapshot()

    def log_water(self) -> None:
        self.stats_store.add("water_glasses")
        if self.watcher.running:
            self.watcher.command("water")  # restart the reminder interval
        self._save_stats()
        macos.popup("Handsight", "Logged a glass of water. Nice.", "#3b82f6", 3)
        if self.panel is not None:
            self.panel.push_state()

    def camera_in_use(self) -> str | None:
        camera = self.watcher.in_use
        return camera.uid if camera is not None and self.state == "watching" else None

    def apply_settings(self, settings: cfg.Settings) -> None:
        self.settings = settings
        self.watcher.apply(settings)
        self._sync_ui()

    def apply_gestures(self, store: gesture_store.GestureStore) -> None:
        self.gestures = store
        self.watcher.apply_gestures(store)

    # ---------- permissions (Settings > Permissions) ----------
    def permission_statuses(self) -> dict[str, str]:
        """Cached for a few seconds: asking macOS about Automation is an Apple Event round
        trip, and the settings page asks on every refresh."""
        now = time.monotonic()
        if self._permissions_cache is None or now - self._permissions_cache[0] > PERMISSIONS_CACHE_S:
            found = perm.statuses(camera=perm.camera_status,
                                  accessibility=macos.accessibility_allowed,
                                  running=lambda bundle: bool(personal._running(bundle)))
            self._permissions_cache = (now, found)
        return self._permissions_cache[1]

    def ask_permission(self, permission_id: str) -> str:
        """Make macOS ask again. Returns at once with what to look for; the reset and the
        prompt happen on a worker thread so the window never freezes."""
        self._permissions_cache = None
        if permission_id == "camera" and self.permission_statuses()["camera"] == perm.NOT_ASKED:
            self.start_watching()  # asks the macOS way
            return "macOS is asking for the camera now."
        if self._app_path() is None:
            return NOT_THE_APP
        threading.Thread(target=self._ask_in_background, args=(permission_id,),
                         name="ask-permission", daemon=True).start()
        if permission_id == "camera":
            return "Restarting Handsight so macOS can ask for the camera…"
        if permission_id == "accessibility":
            return "Click Open System Settings in the macOS prompt and switch Handsight on."
        return "Click OK in the macOS prompts that come up."

    def _ask_in_background(self, permission_id: str) -> None:
        error = perm.reset(permission_id, macos.bundle_id())
        if error:
            macos.popup("Handsight", error, AMBER)
            return
        if permission_id == "camera":
            AppHelper.callAfter(self.relaunch)
        elif permission_id == "accessibility":
            macos.accessibility_allowed(prompt=True)
        else:  # the reset cleared every Automation answer: ask System Events and browsers
            targets = [perm.SYSTEM_EVENTS] + [b for b in perm.BROWSERS if personal._running(b)]
            for bundle in targets:
                subprocess.Popen(perm.poke_command(bundle), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        self._permissions_cache = None

    def open_permission_settings(self, permission_id: str) -> None:
        subprocess.Popen(["open", perm.settings_url(permission_id)])

    def reset_permissions(self) -> str:
        """Forget every answer and start again; macOS asks for each one as it's needed."""
        if self._app_path() is None:
            return NOT_THE_APP
        threading.Thread(target=self._reset_in_background, name="reset-permissions",
                         daemon=True).start()
        return "Restarting Handsight…"

    def _reset_in_background(self) -> None:
        failed = [pid for pid in ("camera", "accessibility", "system_events")
                  if perm.reset(pid, macos.bundle_id())]
        if failed:
            macos.popup("Handsight", "macOS wouldn't reset: " + ", ".join(
                perm.BY_ID[pid].label for pid in failed), AMBER)
        AppHelper.callAfter(self.relaunch)

    @staticmethod
    def _app_path() -> str | None:
        """Handsight.app's path, or None when running from Terminal (./run), where the
        permissions belong to Terminal, not to Handsight."""
        path = str(NSBundle.mainBundle().bundlePath())
        return path if path.endswith(".app") and macos.bundle_id() in macos.OWN_BUNDLE_IDS \
            and macos.bundle_id() == str(NSBundle.mainBundle().bundleIdentifier()) else None

    def relaunch(self) -> bool:
        """Quit and start again (macOS only re-asks for the camera at start-up). The new
        copy waits until this one has fully quit, or it would find the single-instance
        lock still taken and leave."""
        path = self._app_path()
        if path is None:
            macos.popup("Handsight", "Restart Handsight yourself to see the camera prompt.", AMBER)
            return False
        wait = ('pid=$1; i=0; while kill -0 "$pid" 2>/dev/null && [ $i -lt 150 ]; '
                'do sleep 0.2; i=$((i+1)); done; /usr/bin/open -n "$0"')
        subprocess.Popen(["/bin/sh", "-c", wait, path, str(os.getpid())],
                         start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        NSApplication.sharedApplication().terminate_(None)
        return True

    def _save_migrated_gestures(self) -> None:
        """First start of this version: write gestures.json now, so gestures switched off in
        the old settings stay off even after the old switches leave the settings file."""
        if gesture_store.FILE.exists():
            return
        try:
            gesture_store.save(self.gestures, gesture_store.FILE)
        except OSError as e:
            print(f"gestures: could not save: {e}", file=sys.stderr, flush=True)

    def test_action(self, action: Action) -> str:
        """Run a built-in or key action once from the settings window (no camera frame,
        so no selfie). Keys go to the app behind Handsight, not to Handsight itself."""
        if self._tester is None:
            self._tester = GestureApp(argparse.Namespace(
                no_keys=False, no_cursor=True, gestures_off="", gesture_hold=0.4))
        if action.kind == "keys" or action.value in ("next_tab", "previous_tab"):
            NSApplication.sharedApplication().hide_(None)
            AppHelper.callLater(KEY_TEST_DELAY_S, lambda: self._tester.run_action(
                action, None, time.monotonic()))
            return f"{action.describe()} in the app behind Handsight"
        now = time.monotonic()
        self._tester.run_action(action, None, now)
        return self._tester.message_now(now) or action.describe()

    def choose_app(self) -> str | None:
        """macOS's own Open panel, in Applications, for picking an app to open."""
        panel = NSOpenPanel.openPanel()
        panel.setTitle_("Choose an app for this gesture")
        panel.setPrompt_("Choose")
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(False)
        panel.setDirectoryURL_(NSURL.fileURLWithPath_("/Applications"))
        panel.setAllowedFileTypes_(["app"])
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if panel.runModal() != NSModalResponseOK:
            return None
        urls = panel.URLs()
        return str(urls[0].path()) if urls and len(urls) else None

    def toggle_watching(self) -> None:
        if self.watcher.running or self.start_pending:
            self.stop_watching()
        else:
            self.start_watching()

    def relearn_face(self) -> None:
        if self.watcher.running:
            self.watcher.key(ord("f"))
        else:
            try:
                forget_profile()
            except OSError as e:
                macos.popup("Handsight", f"Could not forget the saved face: {e}", AMBER)
                return
        macos.popup("Handsight", "Look at the camera on your own for a few seconds so I can "
                    "learn your face.", GREEN)

    def recalibrate_posture(self) -> None:
        self.watcher.key(ord("c"))

    # ---------- changes from the menus and the video window ----------
    def change(self, new: cfg.Settings) -> None:
        if new == self.settings:
            return
        try:
            cfg.save(new, SETTINGS_FILE)
        except OSError as e:
            print(f"could not save settings: {e}", file=sys.stderr, flush=True)
            macos.popup("Handsight", f"Settings not saved: {e.strerror or e}", AMBER)
        self.apply_settings(new)

    def set_flag(self, key: str, on: bool) -> None:
        self.change(self.settings.with_flags({key: on}))

    def set_all(self, on: bool) -> None:
        self.change(cfg.with_all(self.settings, on))

    def open_settings(self) -> None:
        if self.panel is None:
            return
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.panel.show()

    def on_video_key(self, char: str) -> bool:
        if char == "s":
            self.open_settings()
            return True
        if char in WATCHER_KEYS:
            self.watcher.key(ord(char))
            return True
        return False

    # ---------- display ----------
    def _sync_ui(self) -> None:
        flags = self.settings.flags
        on = self.state == "watching"
        self.title = ICON_ON if on else ICON_OFF
        self.status_item.title = STATUS_LINE.get(self.state, self.state)
        self.toggle_item.title = STOP if self.watcher.running else START
        self.video_item.state = flags["show_video"]
        if self.video is not None:
            self.video.set_on_top(flags["video_on_top"])
            self.video.set_message(None if on else VIDEO_MESSAGE.get(self.state))
            if flags["show_video"] and not self.video.visible:
                self.last_seq = 0
                self.video.show()
            elif not flags["show_video"] and self.video.visible:
                self.video.hide()
        if self.panel is not None:
            self.panel.push_state()

    def _place_icon(self) -> None:
        nsapp = getattr(self, "_nsapp", None)
        if self.placed or nsapp is None:
            return
        defaults = NSUserDefaults.standardUserDefaults()
        key = f"NSStatusItem Preferred Position {ITEM_AUTOSAVE_NAME}"
        if defaults.objectForKey_(key) is None:
            defaults.setObject_forKey_(ITEM_PREFERRED_POSITION, key)
        nsapp.nsstatusitem.setAutosaveName_(ITEM_AUTOSAVE_NAME)
        self.placed = True


def main() -> None:
    global _controller
    _lock = single_instance_or_exit()
    QUIT_ALL_REQUEST.unlink(missing_ok=True)
    objc.options.verbose = True  # print Objective-C exceptions from callbacks to the log
    _controller = Handsight()
    if not macos.accessibility_allowed(prompt=True):  # gestures press keys, move the pointer
        print("Accessibility not allowed yet: asked macOS to show its prompt", flush=True)
    _controller.run()


if __name__ == "__main__":
    main()
