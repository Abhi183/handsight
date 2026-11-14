import numpy as np
import pytest

from handsight import custom_gestures as cg

RNG = np.random.default_rng(7)


def _hand(seed: int) -> list[tuple[float, float, float]]:
    """A made-up hand: wrist at the origin, middle knuckle 0.08 m away, other points fixed."""
    rng = np.random.default_rng(seed)
    pts = rng.uniform(-0.1, 0.1, size=(21, 3))
    pts[cg.WRIST] = 0
    pts[cg.MIDDLE_MCP] = (0, -0.08, 0)
    return [tuple(p) for p in pts]


def _samples(shape: list, jitter: float, n: int = 40, left=False) -> np.ndarray:
    base = np.asarray(shape)
    rows = []
    for _ in range(n):
        noisy = base + RNG.normal(0, jitter, size=base.shape)
        noisy[cg.WRIST] = 0
        rows.append(cg.normalize([tuple(p) for p in noisy], left))
    return np.array(rows)


def test_normalize_ignores_position_and_size():
    hand = np.asarray(_hand(1))
    a = cg.normalize([tuple(p) for p in hand], False)
    b = cg.normalize([tuple(p) for p in hand * 1.7 + 0.3], False)
    assert np.allclose(a, b)


def test_left_hand_is_mirrored_to_match_the_right():
    hand = np.asarray(_hand(2))
    mirrored = hand.copy()
    mirrored[:, 0] = -mirrored[:, 0]
    right = cg.normalize([tuple(p) for p in hand], left_hand=False)
    left = cg.normalize([tuple(p) for p in mirrored], left_hand=True)
    assert np.allclose(right, left)


@pytest.mark.parametrize("points", [[], [(0, 0, 0)] * 21, [(float("nan"), 0, 0)] * 21])
def test_normalize_rejects_bad_landmarks(points):
    assert cg.normalize(points, False) is None


def test_matches_its_own_shape_and_not_another():
    rock, three = _hand(10), _hand(11)
    g_rock = cg.make_gesture("a", "rock", _samples(rock, 0.002))
    g_three = cg.make_gesture("b", "three", _samples(three, 0.002))
    both = (g_rock, g_three)
    assert cg.best_match(_samples(rock, 0.002, n=1)[0], both) == "a"
    assert cg.best_match(_samples(three, 0.002, n=1)[0], both) == "b"
    assert cg.best_match(cg.normalize(_hand(99), False), both) is None


def test_limit_stays_between_floor_and_ceiling():
    steady = cg.make_gesture("a", "x", _samples(_hand(3), 0.0001))
    shaky = cg.make_gesture("b", "y", _samples(_hand(3), 0.05))
    assert steady.limit == cg.MATCH_FLOOR
    assert shaky.limit == cg.MATCH_CEILING


def test_too_few_samples_refused():
    with pytest.raises(ValueError):
        cg.make_gesture("a", "x", _samples(_hand(4), 0.001, n=cg.MIN_SAMPLES - 1))


def test_ambiguous_between_two_close_shapes_matches_nothing():
    shape = _hand(5)
    g1 = cg.make_gesture("a", "one", _samples(shape, 0.001))
    g2 = cg.make_gesture("b", "two", _samples(shape, 0.001))
    assert cg.best_match(_samples(shape, 0.001, n=1)[0], (g1, g2)) is None
    assert cg.similar_to(g2, (g1, g2)) == "one"


def test_no_gestures_or_no_hand():
    assert cg.best_match(None, ()) is None
    assert cg.best_match(np.zeros(cg.FEATURES), ()) is None


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_recorder_counts_down_captures_and_finishes():
    clock = _Clock()
    rec = cg.Recorder(clock)
    rec.start()
    assert rec.status() == {"state": "countdown", "seconds": 3, "error": ""}
    rec.feed(np.zeros(cg.FEATURES), "Thumb_Up")  # ignored during the countdown
    clock.t += cg.COUNTDOWN_S
    assert rec.status()["state"] == "capturing" and rec.active
    for _ in range(30):
        rec.feed(np.zeros(cg.FEATURES), "Thumb_Up")
    clock.t += cg.CAPTURE_S
    assert rec.status()["state"] == "done" and not rec.active
    samples, label = rec.result()
    assert samples.shape == (30, cg.FEATURES) and label == "Thumb_Up"


def test_recorder_fails_without_a_hand():
    clock = _Clock()
    rec = cg.Recorder(clock)
    rec.start()
    clock.t += cg.COUNTDOWN_S + cg.CAPTURE_S
    status = rec.status()
    assert status["state"] == "failed" and "hand" in status["error"]
    assert rec.result() is None


def test_recorder_no_builtin_label_when_mixed():
    clock = _Clock()
    rec = cg.Recorder(clock)
    rec.start()
    clock.t += cg.COUNTDOWN_S
    for i in range(30):
        rec.feed(np.zeros(cg.FEATURES), "Victory" if i < 10 else None)
    clock.t += cg.CAPTURE_S
    assert rec.result()[1] is None


def test_recorder_cancel():
    rec = cg.Recorder(_Clock())
    rec.start()
    rec.cancel()
    assert rec.status()["state"] == "idle" and not rec.active
