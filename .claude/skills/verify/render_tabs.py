"""Render PatternHawk's tabs to PNG files without opening a window.

    venv\\Scripts\\python .claude\\skills\\verify\\render_tabs.py <output folder> [data.json]

Uses offscreen Qt, a temp profile and an in-memory stand-in for the registry, so it
never shows on screen and never reads or writes the user's real PatternHawk data.
Pass a data.json to render a specific configuration; otherwise a small sample is used.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")  # offscreen Qt has no fonts otherwise

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
TABS = ["capture", "templates", "scheduler", "settings", "logs"]

SAMPLE = {
    "global_hotkey": "alt+b",
    "areas": [
        {"templates": [{"path": "bull_flag.png", "similarity": 0.8, "hotkey": "alt+b"},
                       {"path": "bear_flag.png", "similarity": 0.8, "hotkey": "alt+s"}],
         "region": {"x": 100, "y": 100, "w": 500, "h": 300}, "active": True},
        {"templates": [{"path": "bull_flag.png", "similarity": 0.75, "hotkey": "alt+b"},
                       {"path": "doji.png", "similarity": 0.9}],
         "region": {"x": 700, "y": 100, "w": 500, "h": 300}, "active": True},
    ],
}


class FakeSettings:
    def __init__(self, *args):
        self.values = {}

    def value(self, key, default=None, type=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    out = os.path.abspath(sys.argv[1])
    os.makedirs(out, exist_ok=True)

    profile = tempfile.mkdtemp(prefix="patternhawk-render-")
    data_file = os.path.join(profile, "PatternHawk", "pattern_hawk_data.json")
    os.makedirs(os.path.dirname(data_file))
    if len(sys.argv) > 2:
        shutil.copy(sys.argv[2], data_file)
    else:
        with open(data_file, "w") as f:
            json.dump(SAMPLE, f)

    spec = importlib.util.spec_from_file_location(
        "patternhawk", os.path.join(REPO, "Pattern-Analysis-Tool.py"))
    ph = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ph)

    app = ph.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ph.QFont("Arial", 10))
    palette = ph.QPalette()
    for role, color in [(ph.QPalette.Window, ph.QColor(53, 53, 53)),
                        (ph.QPalette.WindowText, ph.Qt.white),
                        (ph.QPalette.Base, ph.QColor(25, 25, 25)),
                        (ph.QPalette.Text, ph.Qt.white),
                        (ph.QPalette.Button, ph.QColor(53, 53, 53)),
                        (ph.QPalette.ButtonText, ph.Qt.white),
                        (ph.QPalette.Highlight, ph.QColor(42, 130, 218))]:
        palette.setColor(role, color)
    app.setPalette(palette)

    try:
        with mock.patch.dict(os.environ, {"USERPROFILE": profile}), \
                mock.patch.object(ph, "QSettings", FakeSettings), \
                mock.patch.object(ph.pyautogui, "hotkey", lambda *a, **kw: None):
            window = ph.ScreenCapturePatternDetector()
            if window.data_file != data_file:
                sys.exit("refusing to run against the real profile")
            # A data file with the schedule enabled starts capturing on launch: stop it,
            # and make sure nothing can be captured or pressed while rendering.
            window.schedule_timer.stop()
            window.timer.stop()
            window._capture_and_detect_thread = lambda: None
            window._perform_hotkey_win32 = lambda *a, **kw: None
            window.resize(1000, 800)
            window.show()
            for index, name in enumerate(TABS):
                window.tab_widget.setCurrentIndex(index)
                app.processEvents()
                path = os.path.join(out, f"tab_{name}.png")
                window.grab().save(path)
                print(path)
            window.close()
    finally:
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
