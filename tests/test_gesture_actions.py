import pytest

from handsight import gesture_actions as ga


def test_builtin_round_trip_and_description():
    action = ga.parse_action({"kind": "builtin", "value": "play_pause"}, allow_shell=False)
    assert action == ga.Action("builtin", "play_pause")
    assert action.describe() == "Play / pause"
    assert ga.parse_action(action.to_json(), allow_shell=False) == action


def test_only_volume_repeats():
    assert ga.Action("builtin", "volume_up").repeats
    assert not ga.Action("builtin", "mute").repeats
    keys = ga.parse_action({"kind": "keys", "value": {"code": "Tab", "modifiers": ["cmd"]}},
                           allow_shell=False)
    assert not keys.repeats


def test_keys_are_validated_and_described_in_mac_order():
    action = ga.parse_action(
        {"kind": "keys", "value": {"code": "Digit4", "modifiers": ["cmd", "shift"]}},
        allow_shell=False)
    assert action.value.key_code == 21
    assert action.describe() == "Press ⇧⌘4"
    assert ga.parse_action(action.to_json(), allow_shell=False) == action


def test_single_letter_key():
    action = ga.parse_action({"kind": "keys", "value": {"code": "KeyK"}}, allow_shell=False)
    assert action.value.key_code == 40 and action.describe() == "Press K"


@pytest.mark.parametrize("value", [
    None, {"code": "Fn"}, {"code": "KeyA", "modifiers": ["hyper"]},
    {"code": "KeyA", "modifiers": "cmd"},
])
def test_bad_keys_are_refused(value):
    with pytest.raises(ValueError):
        ga.parse_action({"kind": "keys", "value": value}, allow_shell=False)


def test_open_web_address_and_existing_path(tmp_path):
    web = ga.parse_action({"kind": "open", "value": "https://youtube.com"}, allow_shell=False)
    assert web.describe() == "Open youtube.com"
    app = tmp_path / "Safari.app"
    app.mkdir()
    opened = ga.parse_action({"kind": "open", "value": str(app)}, allow_shell=False)
    assert opened.describe() == "Open Safari"


@pytest.mark.parametrize("value", [
    "javascript:alert(1)", "file:///etc/passwd", "https://", "relative/path",
    "/no/such/file/here", "", "  ", "https://a.com\nrm -rf /", 5,
])
def test_bad_open_targets_are_refused(value):
    with pytest.raises(ValueError):
        ga.parse_action({"kind": "open", "value": value}, allow_shell=False)


def test_shell_needs_permission():
    data = {"kind": "shell", "value": "say hello"}
    with pytest.raises(ValueError, match="switched off"):
        ga.parse_action(data, allow_shell=False)
    assert ga.parse_action(data, allow_shell=True).describe() == "Run: say hello"


def test_shortcut_name():
    action = ga.parse_action({"kind": "shortcut", "value": " Focus mode "}, allow_shell=False)
    assert action.value == "Focus mode"
    with pytest.raises(ValueError):
        ga.parse_action({"kind": "shortcut", "value": "x" * 500}, allow_shell=False)


@pytest.mark.parametrize("data", [None, [], {"kind": "rm"}, {"kind": "builtin", "value": "nuke"}])
def test_unknown_actions_are_refused(data):
    with pytest.raises(ValueError):
        ga.parse_action(data, allow_shell=True)


def test_nothing():
    assert ga.parse_action({"kind": "none"}, allow_shell=False) is ga.NOTHING
    assert ga.NOTHING.describe() == "Nothing"


def test_key_codes_are_unique():
    assert len(set(ga.KEY_CODES.values())) == len(ga.KEY_CODES)


# ---------- gesture_runner ----------

import subprocess  # noqa: E402

from handsight import gesture_runner as gr  # noqa: E402


def test_commands_are_argument_lists(tmp_path):
    app = tmp_path / "Notes.app"
    app.mkdir()
    assert gr.command_for(ga.Action("open", str(app))) == [gr.OPEN, "-a", str(app)]
    assert gr.command_for(ga.Action("open", "https://a.com/x y")) == [gr.OPEN, "https://a.com/x y"]
    assert gr.command_for(ga.Action("shortcut", "Mode; rm -rf")) == [
        gr.SHORTCUTS, "run", "Mode; rm -rf"]
    assert gr.command_for(ga.Action("shell", "echo hi")) == [gr.SHELL, "-c", "echo hi"]
    with pytest.raises(ValueError):
        gr.command_for(ga.Action("builtin", "mute"))


def test_run_program_reports_success_failure_and_timeout(monkeypatch):
    ok = gr.run_program(ga.Action("shell", "true"))
    assert ok.startswith("done")
    bad = gr.run_program(ga.Action("shell", "echo oops >&2; exit 3"))
    assert bad.startswith("failed (3)") and "oops" in bad

    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired("x", gr.TIMEOUT_S)

    monkeypatch.setattr(gr.subprocess, "run", slow)
    assert gr.run_program(ga.Action("shell", "sleep 99")).startswith("gave up")


def test_runner_reports_in_the_background():
    import threading
    done = threading.Event()
    lines = []
    runner = gr.Runner(lambda line: (lines.append(line), done.set()))
    runner.start(ga.Action("shell", "true"))
    assert done.wait(5)
    runner.close()
    assert lines[0].startswith("done")


def test_list_shortcuts_without_the_tool(monkeypatch):
    monkeypatch.setattr(gr.shutil, "which", lambda _name: None)
    assert gr.list_shortcuts() == []


# ---------- review fixes ----------

@pytest.mark.parametrize("name", ["run.command", "x.sh", "flow.workflow", "a.scpt", "i.pkg"])
def test_open_refuses_scripts(tmp_path, name):
    target = tmp_path / name
    target.write_text("echo hi")
    with pytest.raises(ValueError, match="Terminal command"):
        ga.parse_action({"kind": "open", "value": str(target)}, allow_shell=True)


def test_open_refuses_executables(tmp_path):
    tool = tmp_path / "tool"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    with pytest.raises(ValueError, match="programs"):
        ga.parse_action({"kind": "open", "value": str(tool)}, allow_shell=True)


def test_url_ending_in_app_opens_as_a_url():
    assert gr.command_for(ga.Action("open", "https://example.com/get.app")) == [
        gr.OPEN, "https://example.com/get.app"]


def test_shortcut_name_cant_look_like_an_option():
    with pytest.raises(ValueError):
        ga.parse_action({"kind": "shortcut", "value": "--help"}, allow_shell=False)


def test_background_child_doesnt_hang_the_runner():
    import time
    start = time.monotonic()
    result = gr.run_program(ga.Action("shell", "sleep 5 >/dev/null 2>&1 & echo started"))
    assert result.startswith("done") and time.monotonic() - start < 3


def test_every_preset_is_a_valid_action():
    for group, label, data in ga.PRESETS:
        action = ga.parse_action(data, allow_shell=False)
        assert action.kind in ("keys", "builtin"), (group, label)
    assert {p["group"] for p in ga.catalog()["presets"]} >= {
        "Chrome", "Zoom", "Google Meet", "Microsoft Teams", "YouTube"}


def test_meeting_presets_describe_like_macos():
    zoom_mute = next(d for g, label, d in ga.PRESETS if g == "Zoom" and label == "Mute / unmute")
    assert ga.parse_action(zoom_mute, allow_shell=False).describe() == "Press ⇧⌘A"
