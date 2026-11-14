"""Draws the Handsight icon (an open hand with an eye in the palm) as a 1024px PNG.
Usage: make_icon.py OUT.png"""

import sys

import cv2
import numpy as np

SIZE = 1024
SCALE = 4  # drawn 4x larger, then shrunk, for smooth edges
TILE = (52, 36, 26, 255)       # deep navy (BGRA)
HAND = (245, 240, 236, 255)
IRIS = (120, 200, 60, 255)     # green
PUPIL = (30, 22, 18, 255)


def _rounded_tile(img: np.ndarray, size: int) -> None:
    r, pad = 200 * SCALE, 80 * SCALE
    mask = np.zeros(img.shape[:2], np.uint8)
    cv2.rectangle(mask, (pad + r, pad), (size - pad - r, size - pad), 255, -1)
    cv2.rectangle(mask, (pad, pad + r), (size - pad, size - pad - r), 255, -1)
    for cx in (pad + r, size - pad - r):
        for cy in (pad + r, size - pad - r):
            cv2.circle(mask, (cx, cy), r, 255, -1, cv2.LINE_AA)
    img[mask > 0] = TILE


def _capsule(img: np.ndarray, a: tuple, b: tuple, width: int) -> None:
    cv2.line(img, a, b, HAND, width, cv2.LINE_AA)
    for point in (a, b):
        cv2.circle(img, point, width // 2, HAND, -1, cv2.LINE_AA)


def _hand(img: np.ndarray, s: int) -> None:
    """An open right hand, palm facing you, in the middle of the tile."""
    k = s / 1024
    pt = lambda x, y: (int(x * k), int(y * k))  # noqa: E731
    finger = int(118 * k)
    for (x0, y0), (x1, y1) in (((402, 520), (372, 262)), ((500, 500), (500, 214)),
                               ((598, 510), (628, 250)), ((682, 560), (742, 360))):
        _capsule(img, pt(x0, y0), pt(x1, y1), finger)
    _capsule(img, pt(380, 640), pt(270, 500), int(124 * k))           # thumb
    palm = np.array([pt(352, 470), pt(700, 470), pt(716, 700), pt(640, 830), pt(420, 830),
                     pt(340, 700)], np.int32)
    cv2.fillPoly(img, [palm], HAND, cv2.LINE_AA)
    cv2.circle(img, pt(528, 650), int(178 * k), HAND, -1, cv2.LINE_AA)


def _eye(img: np.ndarray, s: int) -> None:
    k = s / 1024
    cx, cy = int(528 * k), int(655 * k)
    white = np.zeros(img.shape[:2], np.uint8)
    cv2.ellipse(white, (cx, cy + int(120 * k)), (int(190 * k), int(190 * k)), 0, 215, 325, 255, -1, cv2.LINE_AA)
    cv2.ellipse(white, (cx, cy - int(120 * k)), (int(190 * k), int(190 * k)), 0, 35, 145, 255, -1, cv2.LINE_AA)
    lens = np.zeros_like(white)
    cv2.ellipse(lens, (cx, cy), (int(150 * k), int(78 * k)), 0, 0, 360, 255, -1, cv2.LINE_AA)
    img[lens > 0] = TILE
    cv2.circle(img, (cx, cy), int(64 * k), IRIS, -1, cv2.LINE_AA)
    cv2.circle(img, (cx, cy), int(28 * k), PUPIL, -1, cv2.LINE_AA)
    cv2.circle(img, (cx + int(20 * k), cy - int(20 * k)), int(11 * k), HAND, -1, cv2.LINE_AA)


def draw_icon() -> np.ndarray:
    big = SIZE * SCALE
    img = np.zeros((big, big, 4), np.uint8)
    _rounded_tile(img, big)
    _hand(img, big)
    _eye(img, big)
    return cv2.resize(img, (SIZE, SIZE), interpolation=cv2.INTER_AREA)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    cv2.imwrite(sys.argv[1], draw_icon())
