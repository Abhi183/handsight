"""Hand shapes you record yourself.

A shape is stored as up to MAX_SAMPLES snapshots of the 21 hand landmarks, each turned
into 63 numbers that don't depend on where the hand is or how big it looks: positions
relative to the wrist, divided by the palm length (wrist to middle knuckle), with a left
hand mirrored so either hand works. MediaPipe's world landmarks are used because they
are in metres around the hand, so tilting toward the camera distorts them less.

A frame is that shape when it is close to one of its snapshots (closer than a limit that
comes from how steady the hand was while recording) and clearly closer than to any other
recorded shape. Pure numpy apart from the thread lock, and unit tested.
"""

import threading
import time
from collections import Counter
from dataclasses import dataclass

import numpy as np

LANDMARKS = 21
WRIST, MIDDLE_MCP = 0, 9
FEATURES = LANDMARKS * 3
MAX_SAMPLES = 60
MIN_SAMPLES = 20
# Distance unit: root-mean-square landmark offset, in palm lengths.
MATCH_FLOOR, MATCH_CEILING = 0.10, 0.22
SPREAD_FACTOR = 2.5
MARGIN = 0.8          # best match must be this much closer than the runner-up
SIMILAR_FACTOR = 1.5  # warn when two recordings' centres are this close (in limits)
COUNTDOWN_S, CAPTURE_S = 3.0, 2.0
BUILTIN_MAJORITY = 0.5  # share of recorded frames a built-in label needs to warn about it


def normalize(points: list[tuple[float, float, float]], left_hand: bool) -> np.ndarray | None:
    """63 numbers for one hand, or None if the landmarks are unusable."""
    if len(points) != LANDMARKS:
        return None
    arr = np.asarray(points, dtype=np.float64)
    if not np.all(np.isfinite(arr)):
        return None
    arr = arr - arr[WRIST]
    palm = float(np.linalg.norm(arr[MIDDLE_MCP]))
    if palm < 1e-6:
        return None
    arr = arr / palm
    if left_hand:
        arr[:, 0] = -arr[:, 0]
    return arr.reshape(FEATURES)


def distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.sqrt(np.mean((a - b) ** 2) * 3))  # RMS over landmarks, not coordinates


@dataclass(frozen=True)
class CustomGesture:
    id: str
    name: str
    samples: np.ndarray  # (n, FEATURES), read-only
    limit: float         # match distance for this shape

    @property
    def centre(self) -> np.ndarray:
        return self.samples.mean(axis=0)

    def nearest(self, features: np.ndarray) -> float:
        diffs = self.samples - features
        return float(np.sqrt((diffs ** 2).mean(axis=1) * 3).min())


def make_gesture(gesture_id: str, name: str, samples: np.ndarray) -> CustomGesture:
    """A CustomGesture with its match limit worked out from the samples' spread."""
    data = np.array(samples, dtype=np.float64).reshape(-1, FEATURES)[:MAX_SAMPLES]
    if len(data) < MIN_SAMPLES:
        raise ValueError(f"need at least {MIN_SAMPLES} samples")
    centre = data.mean(axis=0)
    spread = float(np.median([distance(row, centre) for row in data]))
    limit = min(max(spread * SPREAD_FACTOR, MATCH_FLOOR), MATCH_CEILING)
    data.setflags(write=False)
    return CustomGesture(gesture_id, name, data, limit)


def best_match(features: np.ndarray | None, gestures: tuple[CustomGesture, ...]) -> str | None:
    """The id of the recorded shape this hand is making, or None."""
    if features is None or not gestures:
        return None
    scored = sorted((g.nearest(features), g) for g in gestures)
    best_d, best = scored[0][0], scored[0][1]
    if best_d > best.limit:
        return None
    if len(scored) > 1 and best_d > scored[1][0] * MARGIN:
        return None  # too close to call between two recorded shapes
    return best.id


def similar_to(new: CustomGesture, others: tuple[CustomGesture, ...]) -> str | None:
    """Name of an existing shape the new one could be confused with, or None."""
    for other in others:
        if other.id == new.id:
            continue
        if distance(new.centre, other.centre) < SIMILAR_FACTOR * max(new.limit, other.limit):
            return other.name
    return None


class Recorder:
    """Records one new shape. start() from the settings window; feed() from the watcher
    thread every frame; status() from anywhere."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._state = "idle"  # idle | countdown | capturing | done | failed
        self._started = 0.0
        self._samples: list[np.ndarray] = []
        self._labels: Counter = Counter()
        self._error = ""
        self.seen = ""  # what the gesture code saw last, for the settings page

    def start(self) -> None:
        with self._lock:
            self._state, self._started = "countdown", self._clock()
            self._samples, self._labels, self._error = [], Counter(), ""

    def cancel(self) -> None:
        with self._lock:
            self._state = "idle"
            self._samples = []

    @property
    def active(self) -> bool:
        with self._lock:
            self._advance()
            return self._state in ("countdown", "capturing")

    def feed(self, features: np.ndarray | None, label: str | None) -> None:
        with self._lock:
            self._advance()
            if self._state != "capturing" or features is None:
                return
            if len(self._samples) < MAX_SAMPLES:
                self._samples.append(features)
                if label:
                    self._labels[label] += 1

    def _advance(self) -> None:
        """Move on from countdown or capturing once their time is up (lock held)."""
        elapsed = self._clock() - self._started
        if self._state == "countdown" and elapsed >= COUNTDOWN_S:
            self._state = "capturing"
        if self._state == "capturing" and elapsed >= COUNTDOWN_S + CAPTURE_S:
            if len(self._samples) >= MIN_SAMPLES:
                self._state = "done"
            else:
                self._state, self._error = "failed", (
                    "Couldn't see your hand well enough. Hold it up in front of the camera, "
                    "near your face, and try again.")

    def status(self) -> dict:
        with self._lock:
            self._advance()
            elapsed = self._clock() - self._started
            if self._state == "countdown":
                left = max(0, int(np.ceil(COUNTDOWN_S - elapsed)))
                return {"state": "countdown", "seconds": left, "error": ""}
            if self._state == "capturing":
                share = min((elapsed - COUNTDOWN_S) / CAPTURE_S, 1.0)
                return {"state": "capturing", "progress": round(share, 2),
                        "samples": len(self._samples), "error": ""}
            return {"state": self._state, "samples": len(self._samples), "error": self._error}

    def result(self) -> tuple[np.ndarray, str | None] | None:
        """(samples, a built-in label most frames showed) once done, else None."""
        with self._lock:
            self._advance()
            if self._state != "done":
                return None
            label = None
            if self._labels:
                name, count = self._labels.most_common(1)[0]
                if count >= BUILTIN_MAJORITY * len(self._samples):
                    label = name
            return np.array(self._samples), label
