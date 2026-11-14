"""Where Handsight finds its own files and where it keeps yours.

ROOT is the folder that holds the handsight package, models/ and assets/. In a checkout
that is the project folder; in the downloadable app it is inside Handsight.app.

DATA is where your files go: settings, stats, the face profile, photos and the log. Run
from a checkout it is the project folder, as before. The downloadable app is read-only,
so its launcher sets DESK_GUARD_DATA to ~/Library/Application Support/Handsight.
"""

import os
from pathlib import Path

DATA_ENV = "DESK_GUARD_DATA"
ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    custom = os.environ.get(DATA_ENV, "").strip()
    if not custom:
        return ROOT
    path = Path(custom).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA = data_dir()
