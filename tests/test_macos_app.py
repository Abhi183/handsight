import ctypes

from handsight import macos


def test_bundle_id_outside_an_app_is_the_checkout_build():
    # pytest runs from python, whose bundle (if any) is not a Handsight one
    assert macos.bundle_id() == macos.DEV_BUNDLE_ID


def test_bundle_id_inside_the_downloadable_app(monkeypatch):
    import Foundation

    class _Bundle:
        @staticmethod
        def mainBundle():
            return _Bundle()

        def bundleIdentifier(self):
            return macos.RELEASE_BUNDLE_ID

    monkeypatch.setattr(Foundation, "NSBundle", _Bundle)
    assert macos.bundle_id() == macos.RELEASE_BUNDLE_ID


def test_accessibility_unknown_counts_as_allowed(monkeypatch):
    def broken(_path):
        raise OSError("no framework")

    monkeypatch.setattr(ctypes, "CDLL", broken)
    assert macos.accessibility_allowed(prompt=True) is True


def test_accessibility_returns_a_bool():
    assert isinstance(macos.accessibility_allowed(), bool)
