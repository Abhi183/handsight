import subprocess

import pytest

from handsight import permissions as perm


def test_camera_status_codes():
    assert perm.camera_status(lambda: perm.AV_AUTHORIZED) == perm.ALLOWED
    assert perm.camera_status(lambda: perm.AV_NOT_DETERMINED) == perm.NOT_ASKED
    assert perm.camera_status(lambda: perm.AV_DENIED) == perm.DENIED
    assert perm.camera_status(lambda: perm.AV_RESTRICTED) == perm.DENIED


@pytest.mark.parametrize("code,status", [
    (0, "allowed"), (-1743, "denied"), (-1744, "not_asked"), (-600, "not_running"), (7, "unknown")])
def test_automation_codes(code, status):
    assert perm.automation_code_status(code) == status


def test_browsers_worst_running_one_wins():
    codes = {"com.google.Chrome": 0, "com.apple.Safari": -1743, "com.brave.Browser": -1744}
    running = lambda b: b != "com.brave.Browser"  # noqa: E731
    assert perm.browsers_status(running, codes.get) == perm.DENIED
    assert perm.browsers_status(lambda b: b == "com.google.Chrome", codes.get) == perm.ALLOWED
    assert perm.browsers_status(lambda b: False, codes.get) == perm.NOT_RUNNING


def test_statuses_and_missing():
    found = perm.statuses(camera=lambda: perm.ALLOWED, accessibility=lambda: False,
                          running=lambda b: False, code_for=lambda b: -1744)
    assert found == {"camera": "allowed", "accessibility": "denied",
                     "system_events": "not_asked", "browsers": "not_running"}
    assert perm.missing(found) == ["accessibility", "system_events"]
    assert [row["id"] for row in perm.view(found)] == [p.id for p in perm.PERMISSIONS]


def test_reset_calls_tccutil_for_our_app_only():
    calls = []

    def run(args, **_kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    assert perm.reset("accessibility", "io.github.abhi183.handsight", run) is None
    assert calls == [[perm.TCCUTIL, "reset", "Accessibility", "io.github.abhi183.handsight"]]


def test_reset_reports_failure():
    def run(args, **_kw):
        return subprocess.CompletedProcess(args, 70, "", "Failed to reset")

    assert "Failed to reset" in perm.reset("camera", "x", run)

    def boom(args, **_kw):
        raise OSError("no tccutil")

    assert "Could not reset" in perm.reset("camera", "x", boom)


def test_settings_links():
    assert perm.settings_url("camera").endswith("?Privacy_Camera")
    assert perm.settings_url("browsers").endswith("?Privacy_Automation")


def test_poke_commands_are_fixed_scripts():
    assert perm.poke_command(perm.SYSTEM_EVENTS)[-1].startswith('tell application id "com.apple.systemevents"')
    assert perm.poke_command("com.google.Chrome")[-1] == 'tell application id "com.google.Chrome" to count windows'


def test_real_automation_check_doesnt_prompt_or_crash():
    # Talks to macOS but never shows a prompt (askUserIfNeeded is off).
    assert perm.automation_code_status(perm.automation_code("com.example.not.installed")) in (
        perm.NOT_RUNNING, perm.UNKNOWN, perm.NOT_ASKED, perm.DENIED, perm.ALLOWED)
