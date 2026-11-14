"""Makes the README demo GIFs in docs/demo/ from illustrated scenes.

The hand is a drawn pose and the person is a cartoon, but everything Handsight draws (the
hand skeleton, the gesture strip and hold bar) and decides (which gesture fires, what it
does) comes from the real GestureApp, with the Mac calls swapped for the mock-ups.

Usage: .venv/bin/python tools/make_demo.py [out dir]   (default docs/demo)
"""

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_scene as ds  # noqa: E402

from handsight import gesture_store as gs  # noqa: E402
from handsight import gestures, macos  # noqa: E402
from handsight.gesture_actions import Action, Keys  # noqa: E402

FPS = 12
MAX_BYTES = 2_500_000


class Mac:
    """Stands in for the real Mac: records what the gestures did."""

    def __init__(self) -> None:
        self.volume, self.tab, self.muted, self.scroll = 6, 1, False, 0
        self.pointer, self.opened = (700.0, 450.0), None
        self.hud_until = -1.0
        self.now = 0.0

    def install(self) -> None:
        def vol(delta):
            def press():
                self.volume = max(0, min(16, self.volume + delta))
                self.hud_until = self.now + 1.0
                return True
            return press
        macos.volume_up, macos.volume_down = vol(1), vol(-1)
        macos.press_keys = lambda code, mods: self._keys(code, mods)
        macos.next_tab = lambda: self._tab(1)
        macos.previous_tab = lambda: self._tab(-1)
        macos.scroll = lambda px: self._scroll(px)
        macos.mouse_position = lambda: self.pointer
        macos.desktop_bounds = lambda: (0.0, 0.0, 1440.0, 900.0)
        macos.move_mouse = lambda pos: self._move(pos)
        macos.click_mouse = lambda pos, right=False: True
        macos.natural_scrolling = lambda: True

    def _keys(self, code, mods) -> bool:
        if (code, set(mods)) == (0, {"cmd", "shift"}):  # Zoom: mute
            self.muted = not self.muted
        return True

    def _tab(self, step) -> None:
        self.tab = (self.tab + step) % 4

    def _scroll(self, px) -> bool:
        self.scroll = max(0, self.scroll + px)
        return True

    def _move(self, pos) -> bool:
        self.pointer = pos
        return True


def new_app(mac: Mac, store: gs.GestureStore | None = None) -> gestures.GestureApp:
    mac.install()
    app = gestures.GestureApp(argparse.Namespace(no_keys=False, no_cursor=False,
                                                 gestures_off="", gesture_hold=0.4))
    app.runner.start = lambda action: setattr(mac, "opened", action.describe())
    if store is not None:
        app.configure(disabled=set(), cursor=True, hold=0.4, store=store)
    return app


def camera(app, result, now: float, present: bool = True, shift: float = 0.0) -> np.ndarray:
    img = ds.person(present, shift)
    if present:
        ds.face_box(img, shift)
    img = app.draw_effects(img, result, now)
    app.draw_status_strip(img, result, now, top=0, height=34)
    return img


# ---------- what the Mac shows ----------

def paint_volume(mac: Mac):
    def paint(draw: ImageDraw.ImageDraw, box) -> None:
        x0, y0, x1, y1 = box
        draw.text((x0 + 20, y0 + 20), "Music", font=ds.font(20), fill=ds.INK)
        draw.text((x0 + 20, y0 + 50), "Now playing · Lo-fi beats", font=ds.font(13), fill=ds.MUTED)
        if mac.now <= mac.hud_until:
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2 + 10
            draw.rounded_rectangle((cx - 90, cy - 70, cx + 90, cy + 70), 18, fill=(60, 60, 64))
            draw.polygon([(cx - 22, cy - 30), (cx - 8, cy - 30), (cx + 10, cy - 46),
                          (cx + 10, cy - 2), (cx - 8, cy - 18), (cx - 22, cy - 18)], fill="white")
            for i in range(16):
                fill = "white" if i < mac.volume else (110, 110, 115)
                draw.rectangle((cx - 72 + i * 9, cy + 30, cx - 66 + i * 9, cy + 40), fill=fill)
    return paint


TABS = ("Mail", "YouTube", "Docs", "News")


def paint_tabs(mac: Mac):
    def paint(draw, box) -> None:
        x0, y0, x1, y1 = box
        w = (x1 - x0 - 20) // len(TABS)
        for i, name in enumerate(TABS):
            active = i == mac.tab
            draw.rounded_rectangle((x0 + 10 + i * w, y0 + 8, x0 + 8 + (i + 1) * w, y0 + 36), 8,
                                   fill=(255, 255, 255) if active else (230, 230, 235),
                                   outline=ds.ACCENT if active else None, width=2)
            draw.text((x0 + 10 + i * w + w // 2, y0 + 22), name, font=ds.font(13),
                      fill=ds.INK if active else ds.MUTED, anchor="mm")
        draw.text(((x0 + x1) // 2, (y0 + y1) // 2 + 20), TABS[mac.tab], font=ds.font(34),
                  fill=ds.INK, anchor="mm")
    return paint


def paint_meeting(mac: Mac):
    def paint(draw, box) -> None:
        x0, y0, x1, y1 = box
        draw.rectangle(box, fill=(32, 33, 36))
        cx, cy = (x0 + x1) // 2, y0 + 105
        draw.ellipse((cx - 44, cy - 44, cx + 44, cy + 44), fill=(90, 120, 200))
        draw.text((cx, cy), "AS", font=ds.font(28), fill="white", anchor="mm")
        color, text = (ds.RED, "Muted") if mac.muted else (ds.GREEN, "Mic on")
        draw.rounded_rectangle((cx - 70, y1 - 70, cx + 70, y1 - 30), 20, fill=color)
        draw.text((cx, y1 - 50), text, font=ds.font(16), fill="white", anchor="mm")
    return paint


def paint_page(mac: Mac):
    def paint(draw, box) -> None:
        x0, y0, x1, y1 = box
        top = y0 + 14 - int(mac.scroll * 0.25) % 400
        for i in range(24):
            y = top + i * 30
            if y0 + 4 < y < y1 - 14:
                width = (x1 - x0 - 40) if i % 4 else (x1 - x0 - 140)
                draw.rounded_rectangle((x0 + 20, y, x0 + 20 + width, y + 10), 5,
                                       fill=(60, 60, 64) if i % 4 == 0 else (210, 210, 216))
    return paint


def paint_pointer(mac: Mac):
    def paint(draw, box) -> None:
        x0, y0, x1, y1 = box
        for i, name in enumerate(("Notes", "Photos", "Files")):
            draw.rounded_rectangle((x1 - 70, y0 + 20 + i * 70, x1 - 30, y0 + 60 + i * 70), 10,
                                   fill=(120 + i * 40, 160, 230 - i * 40))
            draw.text((x1 - 50, y0 + 70 + i * 70), name, font=ds.font(11), fill=ds.MUTED,
                      anchor="mm")
        px = x0 + (mac.pointer[0] / 1440) * (x1 - x0)
        py = y0 + (mac.pointer[1] / 900) * (y1 - y0)
        draw.polygon([(px, py), (px, py + 26), (px + 7, py + 20), (px + 12, py + 31),
                      (px + 17, py + 29), (px + 12, py + 18), (px + 21, py + 18)],
                     fill="black", outline="white")
    return paint


def paint_custom(mac: Mac):
    def paint(draw, box) -> None:
        x0, y0, x1, y1 = box
        if mac.opened:
            draw.rectangle(box, fill=(250, 250, 252))
            draw.rounded_rectangle((x0 + 30, y0 + 14, x1 - 30, y0 + 40), 13, fill=(232, 232, 237))
            draw.text(((x0 + x1) // 2, y0 + 27), "Search or enter website", font=ds.font(12),
                      fill=ds.MUTED, anchor="mm")
            draw.text(((x0 + x1) // 2, (y0 + y1) // 2 + 10), "Safari", font=ds.font(32),
                      fill=ds.ACCENT, anchor="mm")
        else:
            draw.text(((x0 + x1) // 2, (y0 + y1) // 2), "Desktop", font=ds.font(18),
                      fill=ds.MUTED, anchor="mm")
    return paint


def paint_lock(locked: bool):
    def paint(draw, box) -> None:
        x0, y0, x1, y1 = box
        if not locked:
            draw.text(((x0 + x1) // 2, (y0 + y1) // 2), "Your work", font=ds.font(22),
                      fill=ds.INK, anchor="mm")
            return
        draw.rectangle(box, fill=(40, 46, 60))
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2 - 10
        draw.arc((cx - 22, cy - 46, cx + 22, cy + 2), 180, 360, fill="white", width=6)
        draw.rounded_rectangle((cx - 32, cy - 20, cx + 32, cy + 30), 8, fill="white")
        draw.text((cx, cy + 62), "Locked", font=ds.font(18), fill="white", anchor="mm")
    return paint


# ---------- scenes ----------

def run(app, mac: Mac, steps, title: str, sub: str, mac_title: str, painter) -> list:
    """steps: [(seconds, pose or None, (cx0, cy0), (cx1, cy1))]"""
    frames, now = [], 0.0
    for seconds, pose, start, end in steps:
        n = max(1, int(seconds * FPS))
        for i in range(n):
            share = i / max(1, n - 1)
            cx = start[0] + (end[0] - start[0]) * share
            cy = start[1] + (end[1] - start[1]) * share
            result = ds.FakeResult(pose, cx, cy)
            mac.now = now
            app.step(result, None, now)
            frames.append(ds.compose(camera(app, result, now), title, sub, mac_title, painter(mac)))
            now += 1 / FPS
    return frames


def scene_volume() -> list:
    mac = Mac()
    app = new_app(mac)
    still = (0.62, 0.6)
    return run(app, mac, [(0.6, None, still, still), (2.0, "thumbs_up", still, still),
                          (0.6, None, still, still)],
               "Thumbs up: volume up", "Hold it and the volume keeps rising.", "Music",
               paint_volume)


def scene_tabs() -> list:
    mac = Mac()
    app = new_app(mac)
    p = (0.6, 0.6)
    return run(app, mac, [(0.4, None, p, p), (0.9, "point_right", p, p), (0.4, None, p, p),
                          (0.9, "point_right", p, p), (0.5, None, p, p)],
               "Point right: next tab", "Point left or right to move between Chrome tabs.",
               "Chrome", paint_tabs)


def scene_meeting() -> list:
    mac = Mac()
    zoom_mute = Action("keys", Keys("KeyA", ("cmd", "shift")))
    store = gs.GestureStore().with_binding("Closed_Fist", zoom_mute)
    app = new_app(mac, store)
    p = (0.62, 0.62)
    return run(app, mac, [(0.4, None, p, p), (1.6, "fist", p, p), (0.6, None, p, p)],
               "Fist: mute in Zoom", "Ready-made actions for Zoom, Meet, Teams and Chrome.",
               "Zoom Meeting", paint_meeting)


def scene_scroll() -> list:
    mac = Mac()
    app = new_app(mac)
    return run(app, mac, [(0.3, None, (0.62, 0.45), (0.62, 0.45)),
                          (0.6, "open_palm", (0.62, 0.45), (0.62, 0.45)),
                          (1.4, "open_palm", (0.62, 0.45), (0.62, 0.75)),
                          (0.4, None, (0.62, 0.75), (0.62, 0.75))],
               "Open palm: scroll", "Move your open hand up or down, like a trackpad.",
               "Article", paint_page)


def scene_pointer() -> list:
    mac = Mac()
    app = new_app(mac)
    return run(app, mac, [(0.3, None, (0.55, 0.6), (0.55, 0.6)),
                          (0.6, "point_up", (0.55, 0.6), (0.55, 0.6)),
                          (1.2, "point_up", (0.55, 0.6), (0.72, 0.45)),
                          (1.2, "point_up", (0.72, 0.45), (0.6, 0.7)),
                          (0.5, None, (0.6, 0.7), (0.6, 0.7))],
               "Point up: move the pointer", "Your finger becomes the mouse. Pinch to click.",
               "Desktop", paint_pointer)


def scene_custom() -> list:
    mac = Mac()
    samples = np.array([gestures.hand_features(ds.FakeResult("rock", 0.62, 0.6 + i * 0.001), 0)
                        for i in range(30)])
    rock = gs.new_gesture("Rock on", samples, Action("open", "/Applications/Safari.app"))
    app = new_app(mac, gs.GestureStore().with_custom(rock))
    p = (0.62, 0.6)
    return run(app, mac, [(0.4, None, p, p), (1.6, "rock", p, p), (0.8, None, p, p)],
               "Your own gesture: open Safari", "Record any hand shape and give it a job.",
               "Mac", paint_custom)


def scene_lock() -> list:
    frames = []
    app = new_app(Mac())
    none = ds.FakeResult(None)
    total = int(3.4 * FPS)
    for i in range(total):
        t = i / FPS
        shift = 0.0 if t < 0.8 else min(260.0, (t - 0.8) * 320)
        present = shift < 250
        img = camera(app, none, t, present, shift)
        if not present:
            left = max(0, 2 - int(t - 1.6))
            text = f"Locking in {left}s" if t < 2.6 else "Locked"
            import cv2
            cv2.putText(img, text, (12, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 170, 255), 2,
                        cv2.LINE_AA)
        frames.append(ds.compose(img, "Walk away, it locks", "Comes back to you, not to "
                                 "strangers. Plus a shoulder-surfer alarm.", "Mac",
                                 paint_lock(t >= 2.6)))
    return frames


GIFS = {
    "hero": (scene_volume, scene_tabs, scene_meeting, scene_scroll),
    "pointer": (scene_pointer,),
    "custom-gesture": (scene_custom,),
    "auto-lock": (scene_lock,),
}


def save_gif(frames: list, path: Path) -> int:
    palette = frames[len(frames) // 2].quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    quantized = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    quantized[0].save(path, save_all=True, append_images=quantized[1:], loop=0,
                      duration=int(1000 / FPS), optimize=True, disposal=1)
    return path.stat().st_size


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "demo"
    out.mkdir(parents=True, exist_ok=True)
    for name, scenes in GIFS.items():
        frames = [frame for scene in scenes for frame in scene()]
        size = save_gif(frames, out / f"{name}.gif")
        flag = "" if size <= MAX_BYTES else "  (too big!)"
        print(f"{name}.gif: {len(frames)} frames, {size / 1e6:.1f} MB{flag}")


if __name__ == "__main__":
    main()
