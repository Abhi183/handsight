"""Who made Handsight, shown in About Handsight and on the settings page."""

AUTHOR = "Abhishek Shekhar"
AUTHOR_URL = "https://github.com/Abhi183"
PROJECT_URL = "https://github.com/Abhi183/handsight"
LICENSE_NAME = "Apache License 2.0"
YEAR = 2026
COPYRIGHT = f"© {YEAR} {AUTHOR}. {LICENSE_NAME}."


def version() -> str:
    """The app's version (from Info.plist), or "dev" when run from a checkout."""
    try:
        from Foundation import NSBundle
        value = NSBundle.mainBundle().objectForInfoDictionaryKey_("CFBundleShortVersionString")
    except ImportError:
        value = None
    return str(value) if value else "dev"


def info() -> dict:
    return {"author": AUTHOR, "author_url": AUTHOR_URL, "project_url": PROJECT_URL,
            "license": LICENSE_NAME, "copyright": COPYRIGHT, "version": version()}
