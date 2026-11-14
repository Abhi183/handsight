"""Drawing pieces for the README demo GIFs (tools/make_demo.py): a cartoon person, hand
poses as MediaPipe-style landmarks, and simple mock-ups of what happens on the Mac.

No real face or photo is used anywhere.
"""

import math

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

CAM_W, CAM_H = 400, 300
CANVAS = (820, 430)
BG = (236, 238, 243)
INK = (29, 29, 31)
MUTED = (110, 110, 115)
ACCENT = (10, 132, 255)
GREEN = (48, 180, 90)
RED = (229, 72, 77)
FONT_PATHS = ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc")

# fingers: (MCP offset from the wrist, in palm lengths; spread angle in degrees)
FINGERS = {"index": ((-0.34, -0.95), -8), "middle": ((-0.1, -1.0), -2),
           "ring": ((0.14, -0.95), 4), "pinky": ((0.36, -0.84), 10)}
SEGMENTS = (0.45, 0.3, 0.24)
POSES = {  # extended fingers, MediaPipe's label, rotation (deg), thumb direction on screen
    "open_palm": ({"thumb", "index", "middle", "ring", "pinky"}, "Open_Palm", 0, (-0.8, -0.5)),
    "thumbs_up": ({"thumb"}, "Thumb_Up", 90, (0.0, -1.0)),
    "point_up": ({"index"}, "Pointing_Up", 0, None),
    "point_right": ({"index"}, "None", 90, None),
    "fist": (set(), "Closed_Fist", 0, None),
    "love_you": ({"thumb", "index", "pinky"}, "ILoveYou", 0, (-0.9, -0.35)),
    "rock": ({"index", "pinky"}, "None", 0, None),
    "relaxed": ({"index", "middle"}, "None", 25, None),
}


def font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ---------- hands ----------

def _rotate(p: tuple[float, float], deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    return (p[0] * math.cos(a) - p[1] * math.sin(a), p[0] * math.sin(a) + p[1] * math.cos(a))


def hand_points(pose: str, cx: float, cy: float, size: float) -> list[tuple[float, float]]:
    """21 landmarks in 0..1 image coordinates. size: palm length as a share of the width."""
    extended, _label, turn, thumb_dir = POSES[pose]
    local = [(0.0, 0.0)] * 21
    for i, (name, (base, spread)) in enumerate(FINGERS.items()):
        first = 5 + i * 4
        local[first] = base
        if name in extended:
            d = _rotate((0.0, -1.0), spread)
            for j, seg in enumerate(SEGMENTS, start=1):
                prev = local[first + j - 1]
                local[first + j] = (prev[0] + d[0] * seg, prev[1] + d[1] * seg)
        else:  # curled into the palm: tip ends nearer the wrist than the middle joint
            local[first + 1] = (base[0], base[1] - 0.3)
            local[first + 2] = (base[0] + 0.03, base[1] - 0.12)
            local[first + 3] = (base[0] + 0.04, base[1] + 0.08)
    cmc = (-0.32, -0.22)
    rotated = [_rotate(p, turn) for p in local]
    rotated[1] = _rotate(cmc, turn)
    if thumb_dir is not None:
        n = math.hypot(*thumb_dir)
        d = (thumb_dir[0] / n, thumb_dir[1] / n)
        for j, seg in enumerate((0.35, 0.3, 0.25), start=2):
            prev = rotated[j - 1]
            rotated[j] = (prev[0] + d[0] * seg, prev[1] + d[1] * seg)
    else:  # thumb folded across the fingers
        target = rotated[9]
        for j, share in zip((2, 3, 4), (0.4, 0.65, 0.85), strict=True):
            rotated[j] = (rotated[1][0] + (target[0] - rotated[1][0]) * share,
                          rotated[1][1] + (target[1] - rotated[1][1]) * share)
    aspect = CAM_W / CAM_H
    return [(cx + x * size, cy + y * size * aspect) for x, y in rotated]


def label_for(pose: str) -> str:
    return POSES[pose][1]


class Landmark:
    def __init__(self, x: float, y: float, z: float = 0.0) -> None:
        self.x, self.y, self.z = x, y, z


class Category:
    def __init__(self, name: str) -> None:
        self.category_name = name


class FakeResult:
    """Looks like a MediaPipe GestureRecognizerResult with one hand (or none)."""

    def __init__(self, pose: str | None, cx: float = 0.62, cy: float = 0.62,
                 size: float = 0.16) -> None:
        if pose is None:
            self.gestures, self.hand_landmarks = [], []
            self.hand_world_landmarks, self.handedness = [], []
            return
        pts = hand_points(pose, cx, cy, size)
        self.gestures = [[Category(label_for(pose))]]
        self.hand_landmarks = [[Landmark(x, y) for x, y in pts]]
        # world landmarks: same shape, centred and in rough metres, for recorded gestures
        self.hand_world_landmarks = [[Landmark((x - cx) * 0.6, (y - cy) * 0.45) for x, y in pts]]
        self.handedness = [[Category("Right")]]


# ---------- the person ----------

def person(present: bool = True, shift: float = 0.0) -> np.ndarray:
    """A flat cartoon person at a desk, seen from the webcam (BGR)."""
    img = np.full((CAM_H, CAM_W, 3), (196, 186, 170), np.uint8)       # wall
    cv2.rectangle(img, (0, 236), (CAM_W, CAM_H), (120, 140, 160), -1)  # desk
    cv2.rectangle(img, (300, 40), (370, 120), (215, 225, 235), -1)    # window
    cv2.rectangle(img, (300, 40), (370, 120), (160, 170, 180), 3)
    if not present:
        return img
    x = int(150 + shift)
    cv2.ellipse(img, (x, 300), (110, 80), 0, 180, 360, (170, 110, 60), -1, cv2.LINE_AA)  # shirt
    cv2.rectangle(img, (x - 18, 175), (x + 18, 225), (150, 185, 225), -1)               # neck
    cv2.circle(img, (x, 140), 52, (150, 185, 225), -1, cv2.LINE_AA)                     # head
    cv2.ellipse(img, (x, 118), (56, 38), 0, 180, 360, (60, 50, 45), -1, cv2.LINE_AA)    # hair
    for ex in (x - 18, x + 18):
        cv2.circle(img, (ex, 140), 5, (50, 40, 35), -1, cv2.LINE_AA)
    cv2.ellipse(img, (x, 160), (16, 9), 0, 10, 170, (60, 60, 140), 3, cv2.LINE_AA)       # smile
    return img


def face_box(img: np.ndarray, shift: float = 0.0, label: str = "you") -> None:
    """The green box and name, drawn the way the app draws them."""
    x = int(150 + shift)
    cv2.rectangle(img, (x - 62, 80), (x + 62, 200), (80, 200, 80), 2)
    cv2.putText(img, label, (x - 62, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 200, 80), 2,
                cv2.LINE_AA)


# ---------- the Mac side ----------

def window(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], title: str) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, 12, fill=(255, 255, 255), outline=(215, 215, 220))
    draw.rounded_rectangle((x0, y0, x1, y0 + 30), 12, fill=(242, 242, 245))
    draw.rectangle((x0, y0 + 18, x1, y0 + 30), fill=(242, 242, 245))
    for i, color in enumerate(((255, 95, 87), (254, 188, 46), (40, 200, 64))):
        draw.ellipse((x0 + 12 + i * 18, y0 + 10, x0 + 24 + i * 18, y0 + 22), fill=color)
    draw.text(((x0 + x1) // 2, y0 + 15), title, font=font(13), fill=MUTED, anchor="mm")


def caption(draw: ImageDraw.ImageDraw, title: str, sub: str) -> None:
    draw.text((24, 22), title, font=font(26), fill=INK)
    draw.text((24, 58), sub, font=font(15), fill=MUTED)


def compose(cam_bgr: np.ndarray, title: str, sub: str, mac_title: str,
            paint_mac) -> Image.Image:
    """One frame: caption on top, Handsight's camera view on the left, the Mac on the right."""
    frame = Image.new("RGB", CANVAS, BG)
    draw = ImageDraw.Draw(frame)
    caption(draw, title, sub)
    cam = Image.fromarray(cv2.cvtColor(cam_bgr, cv2.COLOR_BGR2RGB))
    mask = Image.new("L", cam.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *cam.size), 12, fill=255)
    frame.paste(cam, (24, 100), mask)
    draw.text((24 + CAM_W // 2, 412), "Handsight sees your hand", font=font(12), fill=MUTED,
              anchor="mm")
    box = (450, 100, 796, 400)
    window(draw, box, mac_title)
    paint_mac(draw, (box[0], box[1] + 30, box[2], box[3]))
    return frame
