"""Runs gesture actions that start other programs: opening things, Apple Shortcuts and
Terminal commands. They run one at a time on a background thread, so a slow one never
holds up the camera. Every program gets its arguments as a list, never through a shell,
except a Terminal command, which the user typed and switched on themselves.
"""

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from handsight.gesture_actions import Action, is_url

OPEN = "/usr/bin/open"
SHORTCUTS = "/usr/bin/shortcuts"
SHELL = "/bin/zsh"
TIMEOUT_S = 30
LIST_TIMEOUT_S = 5
MAX_OUTPUT = 300  # characters of a command's output kept for the log


def command_for(action: Action) -> list[str]:
    """The program and arguments for an open, shortcut or shell action."""
    if action.kind == "open":
        target = str(action.value)
        if not is_url(target) and target.endswith(".app"):
            return [OPEN, "-a", target]
        return [OPEN, target]
    if action.kind == "shortcut":
        return [SHORTCUTS, "run", str(action.value)]
    if action.kind == "shell":
        return [SHELL, "-c", str(action.value)]
    raise ValueError(f"{action.kind} actions don't start a program")


def _outcome(action: Action, result: subprocess.CompletedProcess) -> str:
    if result.returncode == 0:
        return f"done: {action.describe()}"
    output = (result.stderr or result.stdout or "").strip().replace("\n", " ")
    detail = f": {output[:MAX_OUTPUT]}" if output else ""
    return f"failed ({result.returncode}): {action.describe()}{detail}"


def run_program(action: Action) -> str:
    """Runs it and waits; returns a line for the log and the status strip.

    Output goes to temporary files, not pipes: a command that leaves something running in
    the background (`app &`) would otherwise hold a pipe open and look like it hung.
    """
    with tempfile.TemporaryFile("w+") as out, tempfile.TemporaryFile("w+") as err:
        try:
            done = subprocess.run(command_for(action), stdout=out, stderr=err,
                                  stdin=subprocess.DEVNULL, timeout=TIMEOUT_S, check=False,
                                  start_new_session=True)
        except subprocess.TimeoutExpired:
            return f"gave up after {TIMEOUT_S}s: {action.describe()}"
        except OSError as e:
            return f"could not start: {action.describe()} ({e.strerror or e})"
        out.seek(0)
        err.seek(0)
        result = subprocess.CompletedProcess(done.args, done.returncode,
                                             out.read(MAX_OUTPUT * 4), err.read(MAX_OUTPUT * 4))
    return _outcome(action, result)


class Runner:
    """Starts program actions in the background and reports back through `report`."""

    def __init__(self, report: Callable[[str], None]) -> None:
        self._report = report
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gesture-action")

    def start(self, action: Action) -> None:
        future = self._pool.submit(run_program, action)
        future.add_done_callback(lambda f: self._report(
            f.result() if f.exception() is None else f"failed: {f.exception()}"))

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def list_shortcuts() -> list[str]:
    """Names of the user's Apple Shortcuts (empty if the Shortcuts tool isn't there)."""
    if not shutil.which(SHORTCUTS):
        return []
    try:
        result = subprocess.run([SHORTCUTS, "list"], capture_output=True, text=True,
                                timeout=LIST_TIMEOUT_S, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return sorted({line.strip() for line in result.stdout.splitlines() if line.strip()},
                  key=str.lower)
