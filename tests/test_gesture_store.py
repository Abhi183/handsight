import json

import numpy as np
import pytest

from handsight import custom_gestures as cg
from handsight import gesture_store as gs
from handsight.gesture_actions import NOTHING, Action

RNG = np.random.default_rng(3)


def _samples(n=30):
    base = RNG.uniform(-1, 1, cg.FEATURES)
    return base + RNG.normal(0, 0.001, size=(n, cg.FEATURES))


def test_defaults_are_todays_gestures():
    store = gs.GestureStore()
    assert store.action_for("Thumb_Up") == Action("builtin", "volume_up")
    assert store.action_for("ILoveYou") == Action("builtin", "play_pause")
    assert store.action_for("Point_Right") == Action("builtin", "next_tab")
    assert store.action_for("Open_Palm") is NOTHING  # scrolling is a mode, not an action
    assert store.action_for(None) is NOTHING


def test_rebinding_returns_a_new_store():
    store = gs.GestureStore()
    keys = Action("builtin", "lock_screen")
    changed = store.with_binding("ILoveYou", keys)
    assert changed.action_for("ILoveYou") == keys
    assert store.action_for("ILoveYou") == Action("builtin", "play_pause")
    assert changed.with_default_bindings().bindings == store.bindings
    with pytest.raises(KeyError):
        store.with_binding("Open_Palm", keys)


def test_custom_gesture_add_rename_rebind_delete():
    g = gs.new_gesture("  rock   on ", _samples(), Action("builtin", "mute"))
    assert g.name == "rock on"
    store = gs.GestureStore().with_custom(g)
    assert store.action_for(g.label) == Action("builtin", "mute")
    store = store.renamed(g.id, "")
    assert store.find(g.id).name == gs.DEFAULT_NAME
    store = store.with_custom_action(g.id, NOTHING)
    assert store.action_for(g.label) is NOTHING
    assert len(store.custom) == 1  # replaced in place, not added
    assert store.without(g.id).custom == ()
    with pytest.raises(KeyError):
        store.without("missing")


def test_custom_limit():
    store = gs.GestureStore()
    for _ in range(gs.MAX_CUSTOM):
        store = store.with_custom(gs.new_gesture("g", _samples()))
    with pytest.raises(ValueError):
        store.with_custom(gs.new_gesture("one more", _samples()))


def test_shell_only_runs_while_allowed():
    store = gs.GestureStore(allow_shell=True).with_binding("Victory", Action("shell", "say hi"))
    assert store.action_for("Victory").kind == "shell"
    assert store.with_allow_shell(False).action_for("Victory") is NOTHING


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "gestures.json"
    g = gs.new_gesture("rock", _samples(), Action("open", "https://example.com"))
    store = (gs.GestureStore().with_binding("Closed_Fist", NOTHING).with_custom(g)
             .with_allow_shell(True))
    gs.save(store, path)
    back = gs.load(path)
    assert back.bindings == store.bindings
    assert back.allow_shell
    assert back.find(g.id).action == g.action
    assert np.allclose(back.find(g.id).shape.samples, g.shape.samples, atol=1e-4)


def test_missing_file_migrates_old_switches(tmp_path):
    store = gs.load(tmp_path / "gestures.json", {"volume": False, "tabs": True})
    assert store.action_for("Thumb_Up") is NOTHING
    assert store.action_for("Thumb_Down") is NOTHING
    assert store.action_for("Point_Left") == Action("builtin", "previous_tab")


@pytest.mark.parametrize("text", ["", "[]", "{bad json", '{"bindings": 5, "custom": "x"}'])
def test_damaged_file_gives_defaults(tmp_path, text):
    path = tmp_path / "gestures.json"
    path.write_text(text)
    assert gs.load(path) == gs.GestureStore()


def test_bad_entries_are_skipped(tmp_path):
    path = tmp_path / "gestures.json"
    good = gs.new_gesture("ok", _samples())
    data = gs.to_json(gs.GestureStore().with_custom(good))
    data["bindings"]["Thumb_Up"] = {"kind": "shell", "value": "rm -rf ~"}  # shell is off
    data["bindings"]["Nope"] = {"kind": "none"}
    data["custom"] += [{"id": "x", "samples": [[1, 2]]}, {"id": 5}, "junk",
                       {"id": "y", "samples": [[float("nan")] * cg.FEATURES] * 30}]
    path.write_text(json.dumps(data))
    store = gs.load(path)
    assert store.action_for("Thumb_Up") is NOTHING
    assert "Nope" not in store.bindings
    assert [g.id for g in store.custom] == [good.id]


def test_names_for_the_status_line():
    g = gs.new_gesture("rock", _samples())
    store = gs.GestureStore().with_custom(g)
    assert store.name_of("Thumb_Up") == "Thumbs up"
    assert store.name_of(g.label) == "rock"
    assert store.name_of("custom:gone") == "" and store.name_of(None) == ""


# ---------- review fixes: never lose a setup ----------

def test_actions_that_cant_run_now_are_kept_on_load(tmp_path):
    path = tmp_path / "gestures.json"
    data = gs.to_json(gs.GestureStore())
    data["bindings"]["Victory"] = {"kind": "open", "value": "/Volumes/Gone/Tool.app"}
    data["bindings"]["Thumb_Up"] = {"kind": "shell", "value": "say hi"}
    path.write_text(json.dumps(data))  # allow_shell is false in this file
    store = gs.load(path)
    assert store.bindings["Victory"] == Action("open", "/Volumes/Gone/Tool.app")
    assert store.bindings["Thumb_Up"] == Action("shell", "say hi")  # kept...
    assert store.action_for("Thumb_Up") is NOTHING                   # ...but doesn't run
    gs.save(store.with_binding("Closed_Fist", NOTHING), path)        # an unrelated change
    again = gs.load(path)
    assert again.bindings["Victory"].kind == "open" and again.bindings["Thumb_Up"].kind == "shell"


def test_damaged_file_is_set_aside_not_overwritten(tmp_path):
    path = tmp_path / "gestures.json"
    path.write_text("{not json")
    assert gs.load(path) == gs.GestureStore()
    assert (tmp_path / "gestures.bad.json").read_text() == "{not json"
    assert not path.exists()
