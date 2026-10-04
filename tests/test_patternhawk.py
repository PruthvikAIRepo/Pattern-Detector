"""Headless tests for PatternHawk's hotkey logic.

Run from the repo root:

    venv\\Scripts\\python -m unittest discover -s tests -v

The suite drives the real window and the real OpenCV detection against generated
images. It never captures the screen and never sends a keystroke: screen capture,
key injection, window focus and the beep are replaced with recorders, settings go
to an in-memory stand-in for the registry, and app data goes to a temp folder.
"""
import ctypes
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PyQt5.QtCore import QPoint, QPointF
from PyQt5.QtGui import QWheelEvent

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The app's file name has a hyphen, so a plain `import` cannot load it.
_spec = importlib.util.spec_from_file_location(
    "patternhawk", os.path.join(REPO, "Pattern-Analysis-Tool.py"))
ph = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ph)

app = ph.QApplication.instance() or ph.QApplication(sys.argv)

REGION = {"x": 0, "y": 0, "w": 240, "h": 120}
FIX = None  # image fixtures, built in setUpModule


def _arrow(color, up):
    tile = np.full((40, 40, 3), 30, np.uint8)
    points = [[20, 5], [35, 30], [5, 30]] if up else [[20, 35], [35, 10], [5, 10]]
    cv2.fillPoly(tile, [np.array(points, np.int32)], color)
    return tile


def setUpModule():
    """Two patterns (bullish / bearish) and four 'screenshots' showing none, one or both."""
    global FIX
    root = tempfile.mkdtemp(prefix="patternhawk-fixtures-")
    bull = _arrow((0, 200, 0), up=True)     # green up-arrow
    bear = _arrow((0, 0, 200), up=False)    # red down-arrow
    background = np.random.default_rng(7).integers(20, 45, (120, 240, 3), dtype=np.uint8)

    def save(name, image):
        path = os.path.join(root, name)
        cv2.imwrite(path, image)
        return path

    def scene(show_bull, show_bear):
        image = background.copy()
        if show_bull:
            image[40:80, 20:60] = bull
        if show_bear:
            image[40:80, 150:190] = bear
        return image

    FIX = types.SimpleNamespace(
        root=root,
        bull=save("bull.png", bull),
        bear=save("bear.png", bear),
        none=save("scene_none.png", scene(False, False)),
        bull_scene=save("scene_bull.png", scene(True, False)),
        bear_scene=save("scene_bear.png", scene(False, True)),
        both_scene=save("scene_both.png", scene(True, True)),
    )


def tearDownModule():
    shutil.rmtree(FIX.root, ignore_errors=True)


def template(path, hotkey=None, similarity=0.8):
    entry = {"path": path, "similarity": similarity}
    if hotkey is not None:
        entry["hotkey"] = hotkey
    return entry


def area(*templates, active=True):
    return {"templates": list(templates), "region": REGION, "active": active}


def both_directions():
    """An area watching for either direction: bullish -> alt+b, bearish -> alt+s."""
    return area(template(FIX.bull, "alt+b"), template(FIX.bear, "alt+s"))


class FakeSettings:
    """In-memory stand-in for QSettings so tests never read or write the registry."""

    def __init__(self, *args):
        self.values = {}

    def value(self, key, default=None, type=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class PatternHawkTestCase(unittest.TestCase):

    def setUp(self):
        self.sent = []      # hotkeys handed to the OS key-injection layer
        self.warnings = []  # text of every warning dialog
        self.logs = []
        self.scenes = {}    # area index -> image the area "captures" this cycle

        self.enterContext(mock.patch.object(ph, "QSettings", FakeSettings))
        self.enterContext(mock.patch.object(
            ph.pyautogui, "hotkey", lambda *keys, **kw: self.sent.append("+".join(keys))))
        self.enterContext(mock.patch.object(ph.winsound, "Beep", lambda *a: None))
        self.enterContext(mock.patch.object(
            ph.QMessageBox, "warning",
            lambda parent, title, text, *a: self.warnings.append(text)))
        self.enterContext(mock.patch.object(ph.QMessageBox, "information", lambda *a: None))
        self.enterContext(mock.patch.object(
            ph.QMessageBox, "critical",
            lambda parent, title, text, *a: self.fail(f"error dialog: {text}")))

    def make_window(self, data=None, default_hotkey="", areas=None):
        """A window loaded from `data` (a raw data file) or from areas + default hotkey."""
        if data is None and areas is not None:
            data = {"global_hotkey": default_hotkey, "areas": areas}
        self.profile = tempfile.mkdtemp(prefix="patternhawk-profile-")
        self.addCleanup(shutil.rmtree, self.profile, ignore_errors=True)
        self.data_file = os.path.join(self.profile, "PatternHawk", "pattern_hawk_data.json")
        if data is not None:
            os.makedirs(os.path.dirname(self.data_file))
            with open(self.data_file, "w") as f:
                json.dump(data, f)

        with mock.patch.dict(os.environ, {"USERPROFILE": self.profile}):
            window = ph.ScreenCapturePatternDetector()
        self.addCleanup(window.close)
        self.assertTrue(window.data_file.startswith(self.profile), "test must not use the real profile")

        window._focus_target_window = lambda: False
        window._perform_hotkey_win32 = lambda hotkey, delay=0.1: self.sent.append("win32:" + hotkey)
        window.capture_screen = lambda region, i: self.scenes[i]
        window.process_combined_visualization = lambda *a: None
        window.log_message = self.logs.append
        self.window = window
        return window

    def cycle(self, *scenes):
        """Run one capture cycle with one scene per area. Returns the hotkeys it sent."""
        self.scenes = dict(enumerate(scenes))
        before = len(self.sent)
        self.window.capture_in_progress = True  # a check only acts while capture is running
        self.window._capture_and_detect_thread()
        app.processEvents()
        self.assertEqual([m for m in self.logs if m.startswith("Error")], [])
        return self.sent[before:]

    def status(self):
        return self.window.status_label.text()

    def list_rows(self, area_index):
        rows = self.window.template_lists[area_index]
        return [rows.item(i).text() for i in range(rows.count())]

    def saved(self):
        with open(self.data_file) as f:
            return json.load(f)


class HotkeyStringTests(unittest.TestCase):

    def test_normalize_ignores_case_and_spacing(self):
        self.assertEqual(ph.normalize_hotkey("Alt + B"), "alt+b")
        self.assertEqual(ph.normalize_hotkey("  ctrl+ Shift +A "), "ctrl+shift+a")

    def test_normalize_puts_modifiers_first(self):
        self.assertEqual(ph.normalize_hotkey("shift+ctrl+a"), "ctrl+shift+a")
        self.assertEqual(ph.normalize_hotkey("b+alt"), "alt+b")

    def test_normalize_empty(self):
        self.assertEqual(ph.normalize_hotkey(""), "")
        self.assertEqual(ph.normalize_hotkey("   "), "")
        self.assertEqual(ph.normalize_hotkey(None), "")

    def test_normalize_survives_a_non_text_value(self):
        # A hand-edited data file can hold anything in the hotkey field.
        self.assertEqual(ph.normalize_hotkey(5), "5")
        self.assertEqual(ph.normalize_hotkey(["alt", "b"]), "['alt', 'b']")

    def test_stray_plus_is_noticed(self):
        for text in ["alt+", "ctrl++a", "+b", "+", "alt + + b"]:
            self.assertTrue(ph.has_stray_plus(text), text)
        for text in ["alt+b", "Alt + B", "b", "ctrl+shift+f5"]:
            self.assertFalse(ph.has_stray_plus(text), text)

    def test_corner_fail_safe_is_off(self):
        # pyautogui would otherwise refuse to press anything while the mouse is in a corner.
        self.assertFalse(ph.pyautogui.FAILSAFE)

    def test_unknown_keys_flags_typos(self):
        self.assertEqual(ph.unknown_hotkey_keys("alt+b"), [])
        self.assertEqual(ph.unknown_hotkey_keys("ctrl+shift+f5"), [])
        self.assertEqual(ph.unknown_hotkey_keys("atl+b"), ["atl"])
        self.assertEqual(ph.unknown_hotkey_keys("alt+bb"), ["bb"])

    def test_unknown_keys_flags_names_windows_cannot_press(self):
        # pyautogui lists these for macOS; on Windows it would silently skip them.
        self.assertEqual(ph.unknown_hotkey_keys("command+b"), ["command"])
        self.assertEqual(ph.unknown_hotkey_keys("option+s"), ["option"])

    def test_browser_mode_key_table(self):
        self.assertEqual(ph.WIN32_VK_CODES["a"], 0x41)
        self.assertEqual(ph.WIN32_VK_CODES["z"], 0x5A)
        self.assertEqual(ph.WIN32_VK_CODES["alt"], 0x12)
        self.assertEqual(ph.WIN32_VK_CODES["f12"], 0x7B)
        # Anything browser mode can press must also pass the general key check.
        self.assertEqual(ph.unknown_hotkey_keys("+".join(ph.WIN32_VK_CODES)), [])


class AgreementGateTests(PatternHawkTestCase):

    def test_fixture_patterns_are_distinct(self):
        w = self.make_window(areas=[both_directions()])
        templates = w.areas[0]["templates"]
        for scene, expected in [(FIX.none, []), (FIX.bull_scene, ["alt+b"]),
                                (FIX.bear_scene, ["alt+s"]), (FIX.both_scene, ["alt+b", "alt+s"])]:
            matched, patterns, neglected = w.detect_pattern_for_area(scene, templates, {})
            self.assertEqual([p[5] for p in patterns], expected, scene)
            self.assertEqual(matched, bool(expected))
            self.assertEqual(neglected, [])

    def test_all_areas_agree_fires_that_hotkey(self):
        self.make_window(areas=[both_directions(), both_directions()])
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bull_scene), ["alt+b"])
        self.assertIn("Hotkey executed: alt+b", self.status())
        self.assertIn("ALL areas matched! Hotkey executed: alt+b", self.logs)

    def test_other_direction_fires_other_hotkey(self):
        self.make_window(areas=[both_directions(), both_directions()])
        self.assertEqual(self.cycle(FIX.bear_scene, FIX.bear_scene), ["alt+s"])

    def test_areas_disagree_no_action(self):
        self.make_window(areas=[both_directions(), both_directions()])
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bear_scene), [])
        self.assertIn("hotkeys disagree (alt+b vs alt+s)", self.status())
        self.assertFalse(self.window.in_cooldown)

    def test_conflict_inside_one_area_no_action(self):
        self.make_window(areas=[both_directions()])
        self.assertEqual(self.cycle(FIX.both_scene), [])
        self.assertIn("hotkeys disagree", self.status())

    def test_and_gate_still_applies(self):
        self.make_window(areas=[both_directions(), both_directions()])
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.none), [])
        self.assertEqual(self.status(), "Matched: Area [1], No match: Area [2].")

    def test_four_zones_must_all_agree(self):
        self.make_window(areas=[both_directions() for _ in range(4)])
        self.window.neglect_matched.setChecked(False)
        three_agree = [FIX.bull_scene, FIX.bull_scene, FIX.bull_scene, FIX.bear_scene]
        self.assertEqual(self.cycle(*three_agree), [])
        self.assertEqual(self.cycle(*[FIX.bull_scene] * 4), ["alt+b"])

    def test_inactive_area_is_ignored(self):
        self.make_window(areas=[both_directions(), area(template(FIX.bear, "alt+s"), active=False)])
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bear_scene), ["alt+b"])

    def test_case_and_spacing_do_not_cause_conflict(self):
        self.make_window(areas=[area(template(FIX.bull, "Alt + B")), area(template(FIX.bull, "alt+b"))])
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bull_scene), ["alt+b"])

    def test_template_without_hotkey_uses_default(self):
        self.make_window(default_hotkey="alt+b",
                         areas=[area(template(FIX.bull)), area(template(FIX.bull))])
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bull_scene), ["alt+b"])

    def test_default_and_own_hotkey_can_agree(self):
        self.make_window(default_hotkey="ALT + B",
                         areas=[area(template(FIX.bull)), area(template(FIX.bull, "alt+b"))])
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bull_scene), ["alt+b"])

    def test_default_and_own_hotkey_can_conflict(self):
        self.make_window(default_hotkey="alt+b",
                         areas=[area(template(FIX.bull)), area(template(FIX.bull, "alt+s"))])
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bull_scene), [])

    def test_no_hotkey_anywhere_no_action(self):
        self.make_window(areas=[area(template(FIX.bull))])
        self.assertEqual(self.cycle(FIX.bull_scene), [])
        self.assertIn("No action: matched template has no hotkey (Area 1 bull.png).", self.logs)

    def test_conflict_logged_once_while_it_persists(self):
        self.make_window(areas=[both_directions(), both_directions()])
        conflict = "No action: hotkeys disagree. Area 1 bull.png: alt+b | Area 2 bear.png: alt+s"
        for _ in range(3):
            self.cycle(FIX.bull_scene, FIX.bear_scene)
        self.assertEqual(self.logs.count(conflict), 1)

        self.cycle(FIX.bull_scene, FIX.none)       # conflict clears
        self.cycle(FIX.bull_scene, FIX.bear_scene)  # and comes back
        self.assertEqual(self.logs.count(conflict), 2)

    def test_cooldown_is_shared_between_hotkeys(self):
        self.make_window(areas=[both_directions(), both_directions()])
        self.window.neglect_matched.setChecked(False)
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bull_scene), ["alt+b"])
        self.assertTrue(self.window.in_cooldown)

        self.assertEqual(self.cycle(FIX.bear_scene, FIX.bear_scene), [])
        self.assertIn("cooldown active", self.status())

        self.window.reset_cooldown()
        self.assertEqual(self.cycle(FIX.bear_scene, FIX.bear_scene), ["alt+s"])

    def test_browser_mode_sends_agreed_hotkey_via_win32(self):
        self.make_window(areas=[both_directions(), both_directions()])
        self.window.browser_hotkey_mode.setChecked(True)
        self.assertEqual(self.cycle(FIX.bear_scene, FIX.bear_scene), ["win32:alt+s"])

    def test_unpressable_hotkey_is_never_sent(self):
        # A hotkey that got past the Start check (hand-edited data file, or changed while
        # capturing). Sending 'atl+b' would press a bare 'b' in the trading app.
        self.make_window(areas=[area(template(FIX.bull, "atl+b"))])
        for _ in range(3):
            self.assertEqual(self.cycle(FIX.bull_scene), [])
        self.assertIn("cannot be pressed", self.status())
        self.assertEqual(
            self.logs.count("No action: hotkey 'atl+b' cannot be pressed (unknown key atl)."), 1)
        self.assertFalse(self.window.in_cooldown)
        self.assertEqual(self.window.areas[0]["neglect_count"], {})

    def test_browser_mode_never_sends_key_it_cannot_press(self):
        self.make_window(areas=[area(template(FIX.bull, "alt+f13"))])
        self.window.neglect_matched.setChecked(False)
        self.window.browser_hotkey_mode.setChecked(True)
        self.assertEqual(self.cycle(FIX.bull_scene), [])
        self.assertIn("No action: hotkey 'alt+f13' cannot be pressed "
                      "(Browser-compatible mode cannot press f13).", self.logs)

        self.window.browser_hotkey_mode.setChecked(False)
        self.assertEqual(self.cycle(FIX.bull_scene), ["alt+f13"])

    def test_bare_modifier_is_never_sent(self):
        # A bare Alt would put the trading platform into menu mode.
        self.make_window(areas=[area(template(FIX.bull, "alt"))])
        self.assertEqual(self.cycle(FIX.bull_scene), [])
        self.assertIn("No action: hotkey 'alt' cannot be pressed "
                      "(it needs a key besides ctrl, alt, shift or win).", self.logs)

    def test_every_area_is_captured_before_any_detection(self):
        # Detection takes seconds per area. The gates must compare screens from one moment.
        w = self.make_window(areas=[both_directions(), both_directions()])
        order = []
        detect = w.detect_pattern_for_area

        def capture(region, i):
            order.append(f"capture {i + 1}")
            return FIX.bull_scene

        def recording_detect(*args):
            order.append("detect")
            return detect(*args)
        w.capture_screen = capture
        w.detect_pattern_for_area = recording_detect
        self.assertEqual(self.cycle(), ["alt+b"])
        self.assertEqual(order, ["capture 1", "capture 2", "detect", "detect"])

    def test_failed_send_is_not_reported_as_executed(self):
        self.make_window(areas=[both_directions()])
        self.scenes = {0: FIX.bull_scene}
        self.window.capture_in_progress = True
        with mock.patch.object(ph.pyautogui, "hotkey", side_effect=RuntimeError("blocked")):
            self.window._capture_and_detect_thread()
            app.processEvents()
        self.assertIn("could not be sent", self.status())
        self.assertIn("Error performing hotkey alt+b: blocked", self.logs)
        self.assertNotIn("ALL areas matched! Hotkey executed: alt+b", self.logs)
        self.assertFalse(self.window.in_cooldown)

        # Nothing was pressed, so the template does not sit out: the next check tries again.
        self.logs.clear()
        self.assertEqual(self.cycle(FIX.bull_scene), ["alt+b"])
        self.assertIn("ALL areas matched! Hotkey executed: alt+b", self.logs)


class StoppedMidCheckTests(PatternHawkTestCase):
    """Detection takes seconds. A check still running when capture stops must not press."""

    def setUp(self):
        super().setUp()
        w = self.make_window(areas=[both_directions()])
        w.neglect_matched.setChecked(False)
        w.capture_seconds_input.setValue(30)
        w.capture_in_progress = True
        self.scenes = {0: FIX.bull_scene}
        self.detecting, self.go_on = threading.Event(), threading.Event()
        detect = w.detect_pattern_for_area

        def slow_detect(*args):
            result = detect(*args)
            self.detecting.set()
            self.go_on.wait(10)
            return result
        w.detect_pattern_for_area = slow_detect
        self.addCleanup(w.pause_timer.stop)

        self.check = threading.Thread(target=w._capture_and_detect_thread)
        self.check.start()
        self.assertTrue(self.detecting.wait(10))  # the pattern is found; the press is next

    def finish_check(self):
        self.go_on.set()
        self.check.join(10)
        app.processEvents()
        return self.sent

    def test_check_presses_when_nothing_stops_it(self):
        self.assertEqual(self.finish_check(), ["alt+b"])

    def test_stop(self):
        self.window.stop_capture()
        self.assertEqual(self.finish_check(), [])
        self.assertEqual(self.status(), "Capture stopped")
        self.assertEqual([line for line in self.logs if "Hotkey" in line], [])

    def test_pause(self):
        self.window.pause_capture()
        self.assertEqual(self.finish_check(), [])
        self.assertIn("Capture paused", self.status())

    def test_reset(self):
        with mock.patch.object(ph.QMessageBox, "question", lambda *a, **kw: ph.QMessageBox.Yes):
            self.window.reset_process()
        self.assertEqual(self.finish_check(), [])

    def test_closing_the_window(self):
        self.window.close()
        self.assertEqual(self.finish_check(), [])
        self.assertFalse(self.window.timer.isActive())

    def test_schedule_moving_to_another_block(self):
        self.window.current_block_index += 1
        self.assertEqual(self.finish_check(), [])


class Win32SenderTests(PatternHawkTestCase):

    def test_presses_keys_down_in_order_and_releases_in_reverse(self):
        w = self.make_window(areas=[])
        del w._perform_hotkey_win32  # use the real one, with the OS call recorded instead
        events = []

        def record(vk, scan, flags, extra):
            events.append((vk, flags))

        with mock.patch.object(ph.ctypes.windll.user32, "keybd_event", record):
            # The sender looks the function up the same way, so no real key can be sent.
            self.assertIs(ph.ctypes.windll.user32.keybd_event, record)
            w._perform_hotkey_win32("alt+b", 0)
        key_up = 0x0002
        self.assertEqual(events, [(0x12, 0), (0x42, 0), (0x42, key_up), (0x12, key_up)])


class NeglectTests(PatternHawkTestCase):
    """'Neglect Matched' is on by default: a template that fired sits out one cycle."""

    def setUp(self):
        super().setUp()
        self.make_window(areas=[both_directions(), both_directions()])
        self.assertTrue(self.window.neglect_matched.isChecked())

    def fire_bullish(self):
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bull_scene), ["alt+b"])
        self.window.reset_cooldown()  # keep cooldown out of the neglect tests

    def test_areas_matching_on_different_cycles_still_fire(self):
        # Regression: area 1 matched a cycle before area 2 and the two never lined up.
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.none), [])
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bull_scene), ["alt+b"])

    def test_fired_template_skips_one_cycle(self):
        self.fire_bullish()
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bull_scene), [])
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bull_scene), ["alt+b"])

    def test_conflict_does_not_neglect(self):
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bear_scene), [])
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bull_scene), ["alt+b"])

    def test_opposite_signal_blocked_while_fired_pattern_still_shows(self):
        # The bullish templates are sitting out this cycle, but they still match, so a
        # bearish match alongside them is a disagreement and must not fire a sell.
        self.fire_bullish()
        self.assertEqual(self.cycle(FIX.both_scene, FIX.both_scene), [])
        self.assertIn("hotkeys disagree", self.status())
        self.assertEqual(self.cycle(FIX.both_scene, FIX.both_scene), [])

    def test_opposite_signal_fires_once_fired_pattern_is_gone(self):
        self.fire_bullish()
        self.assertEqual(self.cycle(FIX.bear_scene, FIX.bear_scene), ["alt+s"])

    def test_neglect_off_fires_every_cycle(self):
        self.window.neglect_matched.setChecked(False)
        for _ in range(3):
            self.fire_bullish()


class TemplateHotkeyUiTests(PatternHawkTestCase):

    def add_template(self, *hotkey_answers, path=None):
        """Drive Add Template: pick a file, accept 0.8, then answer the hotkey prompt(s)."""
        ask = mock.Mock(side_effect=list(hotkey_answers))
        with mock.patch.object(ph.QFileDialog, "getOpenFileName",
                               lambda *a, **kw: (path or FIX.bull, "")), \
                mock.patch.object(ph.QInputDialog, "getDouble", lambda *a, **kw: (0.8, True)), \
                mock.patch.object(ph.QInputDialog, "getText", ask):
            self.window.addTemplate(0)
        return ask

    def test_add_template_stores_normalized_hotkey(self):
        self.make_window(areas=[])
        self.add_template(("Alt + B", True))
        self.assertEqual(self.window.areas[0]["templates"],
                         [{"path": FIX.bull, "similarity": 0.8, "hotkey": "alt+b"}])
        self.assertEqual(self.saved()["areas"][0]["templates"][0]["hotkey"], "alt+b")
        self.assertEqual(self.list_rows(0), ["1. bull.png (Confidence: 0.8, Hotkey: alt+b)"])

    def test_add_template_blank_uses_default(self):
        self.make_window(default_hotkey="alt+b", areas=[])
        ask = self.add_template(("", True))
        self.assertIn("Leave blank to use the Default Hotkey (alt+b)", ask.call_args.args[2])
        self.assertEqual(self.window.areas[0]["templates"][0]["hotkey"], "")
        self.assertEqual(self.list_rows(0), ["1. bull.png (Confidence: 0.8, Hotkey: alt+b (default))"])

    def test_add_template_rejects_typo_then_accepts(self):
        self.make_window(areas=[])
        ask = self.add_template(("atl+b", True), ("alt+b", True))
        self.assertEqual(ask.call_count, 2)
        self.assertIn("'atl+b' cannot be used: unknown key atl.", self.warnings[0])
        self.assertEqual(ask.call_args.kwargs["text"], "atl+b")  # typed text is kept for fixing
        self.assertEqual(self.window.areas[0]["templates"][0]["hotkey"], "alt+b")

    def test_add_template_in_browser_mode_rejects_key_it_cannot_press(self):
        self.make_window(areas=[])
        self.window.browser_hotkey_mode.setChecked(True)
        self.add_template(("alt+f13", True), ("alt+b", True))
        self.assertIn("'alt+f13' cannot be used: Browser-compatible mode cannot press f13.",
                      self.warnings[0])
        self.assertEqual(self.window.areas[0]["templates"][0]["hotkey"], "alt+b")

    def test_add_template_rejects_stray_plus_and_bare_modifier(self):
        self.make_window(default_hotkey="alt+b", areas=[])
        ask = self.add_template(("alt+", True), ("ctrl++a", True), ("+", True),
                                ("ctrl + shift", True), ("altleft", True), ("alt+s", True))
        self.assertEqual(ask.call_count, 6)
        self.assertEqual([w.splitlines()[0] for w in self.warnings], [
            "'alt+' cannot be used: a key is missing next to a +.",
            "'ctrl++a' cannot be used: a key is missing next to a +.",
            "'+' cannot be used: a key is missing next to a +.",
            "'ctrl + shift' cannot be used: it needs a key besides ctrl, alt, shift or win.",
            "'altleft' cannot be used: it needs a key besides ctrl, alt, shift or win.",
        ])
        self.assertEqual(self.window.areas[0]["templates"][0]["hotkey"], "alt+s")

    def test_add_template_requires_hotkey_when_no_default(self):
        self.make_window(areas=[])
        ask = self.add_template(("", True), ("alt+s", True))
        self.assertNotIn("Leave blank", ask.call_args.args[2])
        self.assertEqual(self.warnings, ["Hotkey cannot be empty. Please try again."])
        self.assertEqual(self.window.areas[0]["templates"][0]["hotkey"], "alt+s")

    def test_add_template_cancel_adds_nothing(self):
        self.make_window(areas=[])
        self.add_template(("alt+b", False))
        self.assertEqual(self.window.areas[0]["templates"], [])

    def test_edit_hotkey(self):
        self.make_window(default_hotkey="alt+b", areas=[area(template(FIX.bull, "alt+b"))])
        self.window.template_lists[0].setCurrentRow(0)
        ask = mock.Mock(return_value=("alt+s", True))
        with mock.patch.object(ph.QInputDialog, "getText", ask):
            self.window.editTemplateHotkey(0)
        self.assertEqual(ask.call_args.kwargs["text"], "alt+b")
        self.assertEqual(self.window.areas[0]["templates"][0]["hotkey"], "alt+s")
        self.assertEqual(self.saved()["areas"][0]["templates"][0]["hotkey"], "alt+s")
        self.assertEqual(self.window.template_lists[0].currentRow(), 0)

        with mock.patch.object(ph.QInputDialog, "getText", lambda *a, **kw: ("", True)):
            self.window.editTemplateHotkey(0)
        self.assertEqual(self.list_rows(0), ["1. bull.png (Confidence: 0.8, Hotkey: alt+b (default))"])

    def test_edit_hotkey_needs_selection(self):
        self.make_window(areas=[area(template(FIX.bull, "alt+b"))])
        with mock.patch.object(ph.QInputDialog, "getText",
                               lambda *a, **kw: self.fail("prompt shown without a selection")):
            self.window.editTemplateHotkey(0)
        self.assertEqual(self.window.areas[0]["templates"][0]["hotkey"], "alt+b")

    def test_list_shows_own_default_and_missing_hotkeys(self):
        self.make_window(areas=[area(template(FIX.bull, "alt+b"), template(FIX.bear))])
        self.assertEqual(self.list_rows(0), [
            "1. bull.png (Confidence: 0.8, Hotkey: alt+b)",
            "2. bear.png (Confidence: 0.8, Hotkey: not set)",
        ])
        self.window.global_hotkey_input.setText("Alt+S")
        self.window.save_settings()
        self.assertEqual(self.list_rows(0)[1], "2. bear.png (Confidence: 0.8, Hotkey: alt+s (default))")

    def test_settings_reject_default_hotkey_with_typo(self):
        self.make_window(default_hotkey="alt+b", areas=[area(template(FIX.bull))])
        self.window.global_hotkey_input.setText("atl+b")
        self.window.save_settings()
        self.assertIn("Default Hotkey 'atl+b' cannot be used: unknown key atl.", self.warnings[0])
        self.assertEqual(self.window.global_hotkey, "alt+b")
        self.assertEqual(self.saved()["global_hotkey"], "alt+b")

    def test_settings_reject_stray_plus_and_bare_modifier(self):
        self.make_window(default_hotkey="alt+b", areas=[both_directions()])
        for text, reason in [("ctrl++", "a key is missing next to a +"),
                             ("alt", "it needs a key besides ctrl, alt, shift or win")]:
            self.window.global_hotkey_input.setText(text)
            self.window.save_settings()
            self.assertIn(f"Default Hotkey '{text}' cannot be used: {reason}.", self.warnings[-1])
            self.assertEqual(self.window.global_hotkey, "alt+b")
        self.assertEqual(self.saved()["global_hotkey"], "alt+b")

    def test_mouse_wheel_cannot_load_a_template_set(self):
        # The Templates tab scrolls. A wheel turn with the cursor over the dropdown used to
        # replace the area's templates and hotkeys, with no question asked.
        self.make_window(areas=[both_directions(), area(template(FIX.bull, "alt+b"))])
        with mock.patch.object(ph.QInputDialog, "getText", lambda *a, **kw: ("day", True)):
            self.window.save_template_set(0)
        combo = self.window.template_set_combos[1]
        for delta in (-120, 120):
            wheel = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, delta),
                                ph.Qt.NoButton, ph.Qt.NoModifier, ph.Qt.NoScrollPhase, False)
            app.sendEvent(combo, wheel)
            self.assertFalse(wheel.isAccepted())  # left for the page to scroll
        self.assertEqual(combo.currentIndex(), 0)
        self.assertEqual(self.window.areas[1]["templates"], [template(FIX.bull, "alt+b")])

        combo.setCurrentIndex(1)  # choosing a set on purpose still works
        self.assertEqual([t["hotkey"] for t in self.window.areas[1]["templates"]], ["alt+b", "alt+s"])

    def test_settings_accept_blank_default_hotkey(self):
        self.make_window(default_hotkey="alt+b", areas=[both_directions()])
        self.window.global_hotkey_input.setText("")
        self.window.save_settings()
        self.assertEqual(self.warnings, [])
        self.assertEqual(self.saved()["global_hotkey"], "")

    def test_template_set_carries_hotkeys(self):
        self.make_window(areas=[both_directions()])
        with mock.patch.object(ph.QInputDialog, "getText", lambda *a, **kw: ("day", True)):
            self.window.save_template_set(0)
        self.window.load_template_set(1, 1)
        self.assertEqual([t["hotkey"] for t in self.window.areas[1]["templates"]], ["alt+b", "alt+s"])
        self.assertEqual(self.list_rows(1), self.list_rows(0))


class RealInputDialogTests(PatternHawkTestCase):
    """The hotkey prompt through Qt's real input dialog, which the other tests replace.

    Message boxes stay replaced: a real QMessageBox crashes offscreen Qt on Windows.
    """

    def play_user(self, *answers):
        """Type each answer into the input dialog that opens and press OK.

        Returns the (label, pre-filled text) of every hotkey prompt that was shown.
        """
        answers = list(answers)
        shown = []

        def respond():
            dialog = app.activeModalWidget()
            if not isinstance(dialog, ph.QInputDialog):
                return
            if dialog.inputMode() == ph.QInputDialog.TextInput:
                shown.append((dialog.labelText(), dialog.textValue()))
                if not answers:
                    dialog.reject()  # an unexpected prompt must not hang the suite
                    return
                dialog.setTextValue(answers.pop(0))
            dialog.accept()

        timer = ph.QTimer()
        timer.timeout.connect(respond)
        timer.start(20)
        self.addCleanup(timer.stop)
        return shown

    def test_edit_hotkey_through_the_real_prompt(self):
        self.make_window(default_hotkey="alt+b", areas=[area(template(FIX.bull, "alt+b"))])
        shown = self.play_user("atl+s", "Alt + S")
        self.window.template_lists[0].setCurrentRow(0)
        self.window.editTemplateHotkey(0)

        label = "Enter hotkey (e.g., alt+b):\nLeave blank to use the Default Hotkey (alt+b)."
        self.assertEqual(shown, [(label, "alt+b"), (label, "atl+s")])
        self.assertEqual(self.warnings, ["'atl+s' cannot be used: unknown key atl.\n"
                                         "Use key names like alt+b or ctrl+shift+a."])
        self.assertEqual(self.list_rows(0), ["1. bull.png (Confidence: 0.8, Hotkey: alt+s)"])

    def test_add_template_through_the_real_prompts(self):
        self.make_window(default_hotkey="alt+b", areas=[])
        shown = self.play_user("")  # blank: use the Default Hotkey
        with mock.patch.object(ph.QFileDialog, "getOpenFileName",
                               lambda *a, **kw: (FIX.bear, "")):
            self.window.addTemplate(0)
        self.assertEqual(len(shown), 1)
        self.assertEqual(self.window.areas[0]["templates"],
                         [{"path": FIX.bear, "similarity": 0.8, "hotkey": ""}])
        self.assertEqual(self.list_rows(0),
                         ["1. bear.png (Confidence: 0.8, Hotkey: alt+b (default))"])

    def test_double_click_opens_the_hotkey_prompt(self):
        self.make_window(areas=[both_directions()])
        shown = self.play_user("f5")
        rows = self.window.template_lists[0]
        rows.setCurrentRow(1)
        rows.itemDoubleClicked.emit(rows.item(1))
        self.assertEqual([text for _, text in shown], ["alt+s"])
        self.assertEqual(self.window.areas[0]["templates"][1]["hotkey"], "f5")


class PersistenceTests(PatternHawkTestCase):

    def test_data_saved_before_this_feature_keeps_working(self):
        # Shape of a real v1.6 data file: one default hotkey, templates without their own.
        old = {
            "global_hotkey": "ctrl + a",
            "areas": [area(template(FIX.bull, similarity=0.5)), area(), area()],
            "template_sets": [{"name": "old set", "templates": [template(FIX.bull)]}],
            "schedule_enabled": False,
            "schedule": [{"areas_active": [True, False, False], "capture_interval": 30, "cooldown": 5}],
        }
        self.make_window(data=old)
        self.assertEqual(self.list_rows(0), ["1. bull.png (Confidence: 0.5, Hotkey: ctrl+a (default))"])
        self.assertEqual(self.cycle(FIX.bull_scene), ["ctrl+a"])

        self.window.load_template_set(1, 1)
        self.window.areas[0]["active"] = False
        self.window.reset_cooldown()
        self.assertEqual(self.cycle(FIX.none, FIX.bull_scene), ["ctrl+a"])

        self.window.saveData()
        self.assertEqual(self.saved()["areas"][0]["templates"], [template(FIX.bull, similarity=0.5)])

    def test_legacy_list_format_keeps_template_hotkeys(self):
        self.make_window(data=[
            {"path": FIX.bull, "similarity": 0.8, "hotkey": "Ctrl+B"},
            {"path": FIX.bear, "similarity": 0.7, "hotkey": "ctrl+s"},
        ])
        self.assertEqual([t["hotkey"] for t in self.window.areas[0]["templates"]], ["ctrl+b", "ctrl+s"])
        self.assertEqual(self.window.global_hotkey, "Ctrl+B")

        # That format never stored the screen region; the user selects it again.
        self.window.areas[0]["region"] = ph.QRect(0, 0, 240, 120)
        self.assertEqual(self.cycle(FIX.bear_scene), ["ctrl+s"])

    def test_failed_save_keeps_the_previous_file(self):
        self.make_window(areas=[both_directions()])
        before = self.saved()
        self.window.global_hotkey = "alt+x"
        with mock.patch.object(ph.json, "dump", side_effect=OSError("disk full")):
            self.window.saveData()
        self.assertEqual(self.saved(), before)  # still complete and readable
        self.assertEqual(self.logs, ["Error saving data: disk full"])

        self.window.saveData()
        self.assertEqual(self.saved()["global_hotkey"], "alt+x")
        self.assertFalse(os.path.exists(self.data_file + ".tmp"))

    def test_save_still_works_when_the_swap_is_blocked(self):
        # Another program can hold the data file open in a way that stops it being replaced.
        self.make_window(areas=[both_directions()])
        self.window.global_hotkey = "alt+x"
        with mock.patch.object(ph.os, "replace", side_effect=PermissionError("file in use")):
            self.window.saveData()
        self.assertEqual(self.saved()["global_hotkey"], "alt+x")
        self.assertEqual(self.logs, [])

    def test_hotkey_field_with_a_number_does_not_crash_the_launch(self):
        hand_edited = area(template(FIX.bull))
        hand_edited["templates"][0]["hotkey"] = 5
        self.make_window(areas=[hand_edited])
        self.assertEqual(self.list_rows(0), ["1. bull.png (Confidence: 0.8, Hotkey: 5)"])

    def test_hotkeys_survive_save_and_reload(self):
        self.make_window(areas=[both_directions()])
        self.window.saveData()
        saved = self.saved()
        self.make_window(data=saved)
        self.assertEqual([t["hotkey"] for t in self.window.areas[0]["templates"]], ["alt+b", "alt+s"])


class StartGateTests(PatternHawkTestCase):

    def start(self):
        self.window.capture_seconds_input.setValue(5)
        self.window.start_capture()
        started = self.window.capture_in_progress
        self.window.stop_capture()
        return started

    def test_start_blocked_when_template_has_no_hotkey(self):
        self.make_window(areas=[both_directions(), area(template(FIX.bull))])
        self.assertFalse(self.start())
        self.assertIn("Area 2 template 'bull.png' has no hotkey.", self.warnings[0])

    def test_start_blocked_on_unknown_key(self):
        self.make_window(areas=[area(template(FIX.bull))])
        self.window.global_hotkey_input.setText("atl+b")
        self.assertFalse(self.start())
        self.assertIn("Area 1 template 'bull.png' has hotkey 'atl+b': unknown key atl.",
                      self.warnings[0])

    def test_start_blocked_when_browser_mode_cannot_press_the_key(self):
        self.make_window(areas=[area(template(FIX.bull, "alt+f13"))])
        self.assertTrue(self.start())  # fine for the normal sender

        self.window.browser_hotkey_mode.setChecked(True)
        self.assertFalse(self.start())
        self.assertIn("has hotkey 'alt+f13': Browser-compatible mode cannot press f13.",
                      self.warnings[0])

    def test_start_blocked_on_bare_modifier(self):
        self.make_window(areas=[area(template(FIX.bull, "alt"))])
        self.assertFalse(self.start())
        self.assertIn("Area 1 template 'bull.png' has hotkey 'alt': "
                      "it needs a key besides ctrl, alt, shift or win.", self.warnings[0])

    def test_start_blocked_when_template_image_is_missing(self):
        # A missing image never matches, so it could not veto the opposite signal.
        gone = os.path.join(FIX.root, "moved_away.png")
        self.make_window(areas=[area(template(FIX.bull, "alt+b"), template(gone, "alt+s"))])
        self.assertFalse(self.start())
        self.assertIn("Area 1 template 'moved_away.png' cannot be found:", self.warnings[0])

    def test_blocked_start_keeps_the_working_default_hotkey(self):
        self.make_window(default_hotkey="alt+b", areas=[area(template(FIX.bull))])
        self.window.global_hotkey_input.setText("atl+b")  # typed in Settings, not saved
        self.assertFalse(self.start())
        self.assertEqual(self.window.global_hotkey, "alt+b")
        self.window.saveData()
        self.assertEqual(self.saved()["global_hotkey"], "alt+b")
        self.assertEqual(self.cycle(FIX.bull_scene), ["alt+b"])

    def test_start_uses_default_hotkey_typed_but_not_saved(self):
        self.make_window(areas=[area(template(FIX.bull))])
        self.window.global_hotkey_input.setText("Alt + S")
        self.assertTrue(self.start())
        self.assertEqual(self.cycle(FIX.bull_scene), ["alt+s"])

    def test_start_allowed_without_default_when_templates_have_hotkeys(self):
        self.make_window(areas=[both_directions()])
        self.assertTrue(self.start())
        self.assertEqual(self.warnings, [])

    def test_start_ignores_inactive_area_without_hotkey(self):
        self.make_window(areas=[both_directions(), area(template(FIX.bull), active=False)])
        self.assertTrue(self.start())

    def test_scheduler_blocked_when_template_has_no_hotkey(self):
        self.make_window(areas=[area(template(FIX.bull))])
        self.window.schedule_enable_checkbox.setChecked(True)
        self.assertFalse(self.window.schedule_enabled)
        self.assertFalse(self.window.schedule_enable_checkbox.isChecked())
        self.assertIn("Area 1 template 'bull.png' has no hotkey.", self.warnings[0])

    def test_scheduler_allowed_when_templates_have_hotkeys(self):
        self.make_window(areas=[both_directions()])
        self.window.schedule_enable_checkbox.setChecked(True)
        self.assertTrue(self.window.schedule_enabled)
        self.window.schedule_enable_checkbox.setChecked(False)
        self.assertEqual(self.warnings, [])


class CaptureControlTests(PatternHawkTestCase):
    """Start / Pause / Stop / Reset against the real timers. Cycles are not run."""

    def setUp(self):
        super().setUp()
        w = self.make_window(areas=[both_directions()])
        w._capture_and_detect_thread = lambda: None
        w.capture_seconds_input.setValue(30)
        w.pause_duration_input.setValue(1)  # the shortest pause the UI allows
        self.resumes = 0

        def counting_resume():
            self.resumes += 1
            w.resume_capture()
        w.pause_timer.timeout.disconnect()
        w.pause_timer.timeout.connect(counting_resume)
        self.addCleanup(self.stop_timers)

    def stop_timers(self):
        for timer in (self.window.timer, self.window.progress_timer, self.window.pause_timer,
                      self.window.countdown_timer, self.window.schedule_timer):
            timer.stop()

    def pump(self, seconds):
        """Let the event loop run so timers can fire."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)

    def assert_not_capturing(self):
        self.assertFalse(self.window.capture_in_progress)
        self.assertFalse(self.window.timer.isActive())

    def test_pause_resumes_once(self):
        # Regression: the resume timer repeated forever, restarting the capture timer each
        # time, so a capture interval longer than the pause never fired again.
        self.window.start_capture()
        self.window.pause_capture()
        self.assert_not_capturing()
        self.pump(2.4)
        self.assertEqual(self.resumes, 1)
        self.assertTrue(self.window.capture_in_progress)
        self.assertTrue(self.window.timer.isActive())

    def test_stop_cancels_pending_resume(self):
        self.window.start_capture()
        self.window.pause_capture()
        self.window.stop_capture()
        self.pump(1.4)
        self.assertEqual(self.resumes, 0)
        self.assert_not_capturing()

    def test_stop_sticks_after_pausing_twice(self):
        # Regression: every Pause left a timer behind that restarted capture after Stop.
        self.window.start_capture()
        self.window.pause_capture()
        self.pump(1.5)
        self.window.pause_capture()
        self.pump(1.5)
        self.assertEqual(self.resumes, 2)
        self.window.stop_capture()
        self.pump(1.4)
        self.assertEqual(self.resumes, 2)
        self.assert_not_capturing()

    def test_reset_cancels_pending_resume(self):
        self.window.start_capture()
        self.window.pause_capture()
        with mock.patch.object(ph.QMessageBox, "question", lambda *a, **kw: ph.QMessageBox.Yes):
            self.window.reset_process()
        self.pump(1.4)
        self.assertEqual(self.resumes, 0)
        self.assert_not_capturing()

    def test_stop_cancels_delayed_start(self):
        # Regression: Stop during the Delay Start countdown did not cancel the start.
        self.window.delay_timer.setTime(ph.QTime(0, 0, 1))
        self.window.start_capture()
        self.assertTrue(self.window.countdown_timer.isActive())
        self.window.stop_capture()
        self.pump(1.5)
        self.assert_not_capturing()

    def test_reset_cancels_delayed_start(self):
        self.window.delay_timer.setTime(ph.QTime(0, 0, 1))
        self.window.start_capture()
        with mock.patch.object(ph.QMessageBox, "question", lambda *a, **kw: ph.QMessageBox.Yes):
            self.window.reset_process()
        self.pump(1.5)
        self.assert_not_capturing()

    def test_enabling_schedule_cancels_delayed_start(self):
        self.window.delay_timer.setTime(ph.QTime(0, 0, 1))
        self.window.start_capture()
        self.window.schedule_enable_checkbox.setChecked(True)  # no block has an active area
        self.pump(1.5)
        self.assert_not_capturing()

    def test_closing_the_window_stops_every_timer(self):
        self.window.start_capture()
        self.window.pause_capture()
        self.window.resume_capture()
        self.assertTrue(self.window.timer.isActive())
        self.assertTrue(self.window.progress_timer.isActive())
        self.window.close()
        self.assertFalse(self.window.capture_in_progress)
        for name in ("timer", "progress_timer", "pause_timer", "countdown_timer",
                     "schedule_timer", "_fg_tracker"):
            self.assertFalse(getattr(self.window, name).isActive(), name)

    def test_closing_the_window_cancels_a_delayed_start(self):
        self.window.delay_timer.setTime(ph.QTime(0, 0, 1))
        self.window.start_capture()
        self.window.close()
        self.pump(1.5)
        self.assert_not_capturing()

    def test_delayed_start_still_starts(self):
        self.window.delay_timer.setTime(ph.QTime(0, 0, 1))
        self.window.start_capture()
        self.assert_not_capturing()
        self.pump(1.5)
        self.assertTrue(self.window.capture_in_progress)
        self.assertTrue(self.window.timer.isActive())

    def test_enabling_schedule_cancels_pending_resume(self):
        self.window.start_capture()
        self.window.pause_capture()
        self.window.schedule_enable_checkbox.setChecked(True)  # no block has an active area
        self.assertTrue(self.window.schedule_enabled)
        self.pump(1.4)
        self.assertEqual(self.resumes, 0)
        self.assert_not_capturing()


class ScheduleControlTests(PatternHawkTestCase):
    """Turning the schedule on and off around a manual capture."""

    def setUp(self):
        super().setUp()
        # Area 2 is inactive, so manual Start ignores its template, which has no hotkey.
        # Enable Schedule checks every area and therefore refuses.
        w = self.make_window(areas=[both_directions(), area(template(FIX.bull), active=False)])
        w._capture_and_detect_thread = lambda: None
        w.capture_seconds_input.setValue(30)
        self.addCleanup(self.stop_timers)

    def stop_timers(self):
        for timer in (self.window.timer, self.window.progress_timer, self.window.pause_timer,
                      self.window.countdown_timer, self.window.schedule_timer):
            timer.stop()

    def refused_enable(self):
        self.window.schedule_enable_checkbox.setChecked(True)
        self.assertFalse(self.window.schedule_enabled)
        self.assertFalse(self.window.schedule_enable_checkbox.isChecked())
        self.assertFalse(self.window.schedule_timer.isActive())
        self.assertIn("Area 2 template 'bull.png' has no hotkey.", self.warnings[-1])

    def test_refused_enable_leaves_a_running_capture_alone(self):
        self.window.start_capture()
        self.refused_enable()
        self.assertTrue(self.window.capture_in_progress)
        self.assertTrue(self.window.timer.isActive())
        self.assertTrue(self.window.stop_capture_button.isEnabled())
        self.assertFalse(self.window.start_capture_button.isEnabled())
        self.assertNotEqual(self.status(), "Ready (manual mode)")

    def test_refused_enable_leaves_a_paused_capture_alone(self):
        self.window.start_capture()
        self.window.pause_capture()
        self.refused_enable()
        self.assertTrue(self.window.pause_timer.isActive())
        self.assertTrue(self.window.stop_capture_button.isEnabled())
        self.assertIn("Capture paused", self.status())

    def test_refused_enable_leaves_a_delayed_start_alone(self):
        self.window.delay_timer.setTime(ph.QTime(0, 0, 30))
        self.window.start_capture()
        self.refused_enable()
        self.assertTrue(self.window.countdown_timer.isActive())
        self.assertTrue(self.window.stop_capture_button.isEnabled())

    def test_refused_enable_does_not_save_an_unsaved_default_hotkey(self):
        before = self.saved()
        self.window.global_hotkey_input.setText("atl+b")
        self.window.schedule_enable_checkbox.setChecked(True)
        self.assertFalse(self.window.schedule_enabled)
        self.assertIn("unknown key atl", self.warnings[-1])
        self.assertEqual(self.window.global_hotkey, "")
        self.assertEqual(self.saved(), before)

    def test_tray_stop_while_scheduled_switches_the_schedule_off(self):
        # Stop used to leave the schedule on, so the next block started capture again.
        self.window.areas[1]["templates"][0]["hotkey"] = "alt+b"
        self.window.schedule_toggle_area(0, True)  # Area 1 active in every block
        self.window.schedule_enable_checkbox.setChecked(True)
        self.assertTrue(self.window.schedule_enabled)
        self.assertTrue(self.window.capture_in_progress)

        self.window.stop_capture()  # what the tray's Stop Capture calls
        self.assertFalse(self.window.schedule_enabled)
        self.assertFalse(self.window.schedule_enable_checkbox.isChecked())
        self.assertFalse(self.window.capture_in_progress)
        self.assertFalse(self.saved()["schedule_enabled"])

        self.window.save_schedule_from_table()
        self.window.check_schedule()
        self.assertFalse(self.window.capture_in_progress)
        self.assertFalse(self.window.timer.isActive())
        self.assertTrue(self.window.start_capture_button.isEnabled())

    def test_launch_with_schedule_on_locks_the_manual_controls(self):
        self.make_window(data={"global_hotkey": "alt+b", "areas": [both_directions()],
                               "schedule_enabled": True})
        self.assertTrue(self.window.schedule_enabled)
        self.assertFalse(self.window.start_capture_button.isEnabled())
        self.assertFalse(self.window.capture_seconds_input.isEnabled())
        self.assertEqual([cb.isEnabled() for cb in self.window.area_active_checkboxes],
                         [False] * ph.NUM_AREAS)


class TargetWindowTests(PatternHawkTestCase):
    """Hotkeys go to the last window the user had in front that is not PatternHawk's."""

    def test_own_dialogs_never_become_the_target(self):
        w = self.make_window(areas=[])
        w._fg_tracker.stop()
        with mock.patch.object(ph.ctypes.windll.user32, "GetForegroundWindow", lambda: 4242):
            w._is_own_window = lambda hwnd: False  # another application is in front
            w._track_foreground_window()
            self.assertEqual(w.target_window_handle, 4242)

            w.target_window_handle = 1111
            w._is_own_window = lambda hwnd: True  # one of our dialogs is in front
            w._track_foreground_window()
            self.assertEqual(w.target_window_handle, 1111)

    def test_own_window_check_goes_by_the_owning_process(self):
        w = self.make_window(areas=[])
        user32 = ctypes.WinDLL("user32")
        user32.CreateWindowExW.restype = ctypes.c_void_p
        user32.DestroyWindow.argtypes = [ctypes.c_void_p]
        ours = user32.CreateWindowExW(0, "STATIC", "patternhawk-test", 0, 0, 0, 0, 0,
                                      None, None, None, None)  # never shown
        self.assertTrue(ours)
        self.addCleanup(user32.DestroyWindow, ours)
        self.assertTrue(w._is_own_window(ours))
        self.assertFalse(w._is_own_window(user32.GetShellWindow()))  # the Windows desktop


class CycleOverlapTests(PatternHawkTestCase):
    """A check that outlasts the capture interval must not run alongside the next one."""

    def test_tick_is_skipped_while_previous_cycle_runs(self):
        self.make_window(areas=[both_directions()])
        self.window.neglect_matched.setChecked(False)
        self.window.capture_in_progress = True
        entered, release = threading.Event(), threading.Event()
        captures = []

        def slow_capture(region, i):
            captures.append(i)
            entered.set()
            release.wait(10)
            return FIX.bull_scene
        self.window.capture_screen = slow_capture

        first = threading.Thread(target=self.window._capture_and_detect_thread)
        first.start()
        self.assertTrue(entered.wait(10))

        self.window._capture_and_detect_thread()  # the next two ticks arrive meanwhile
        self.window._capture_and_detect_thread()
        self.assertEqual(captures, [0])
        self.assertEqual(self.sent, [])

        release.set()
        first.join(10)
        app.processEvents()
        self.assertEqual(self.sent, ["alt+b"])  # pressed once, not once per tick
        self.assertEqual(sum("Skipped a check" in line for line in self.logs), 1)

        # The finished cycle frees the way for the next one.
        self.window.reset_cooldown()
        self.assertEqual(self.cycle(FIX.bull_scene), ["alt+b"])

    def test_stuck_cycle_does_not_block_detection_for_good(self):
        self.make_window(areas=[both_directions()])
        self.window.neglect_matched.setChecked(False)
        self.window.capture_in_progress = True
        working_capture = self.window.capture_screen
        entered, release = threading.Event(), threading.Event()

        def stuck_capture(region, i):
            entered.set()
            release.wait(10)
            return FIX.bull_scene
        self.window.capture_screen = stuck_capture
        stuck = threading.Thread(target=self.window._capture_and_detect_thread)
        stuck.start()
        self.assertTrue(entered.wait(10))
        self.window.capture_screen = working_capture

        with mock.patch.object(ph, "CYCLE_STUCK_SECONDS", 0.3):
            self.assertEqual(self.cycle(FIX.bull_scene), [])  # not stuck yet: skipped
            time.sleep(0.4)
            self.assertEqual(self.cycle(FIX.bull_scene), ["alt+b"])  # given up on: carry on
        self.assertEqual(sum("looks stuck" in line for line in self.logs), 1)

        # The stuck cycle wakes up late with a stale screenshot. It must not press anything,
        # even though nothing else (cooldown) would stop it.
        self.window.reset_cooldown()
        release.set()
        stuck.join(10)
        app.processEvents()
        self.assertEqual(self.sent, ["alt+b"])
        self.assertEqual(self.cycle(FIX.bull_scene), ["alt+b"])

    def test_failed_cycle_does_not_block_later_cycles(self):
        self.make_window(areas=[both_directions()])
        working_capture = self.window.capture_screen

        def broken_capture(region, i):
            raise RuntimeError("capture failed")
        self.window.capture_screen = broken_capture
        self.window._capture_and_detect_thread()
        self.assertEqual(self.logs, ["Error during capture and detect: capture failed"])

        self.logs.clear()
        self.window.capture_screen = working_capture
        self.assertEqual(self.cycle(FIX.bull_scene), ["alt+b"])


class VisualizationTests(PatternHawkTestCase):

    def test_result_image_is_drawn_with_hotkey_labels(self):
        w = self.make_window(areas=[both_directions(), both_directions()])
        del w.process_combined_visualization  # use the real one
        w.save_files_checkbox.setChecked(False)
        self.assertEqual(self.cycle(FIX.bull_scene, FIX.bull_scene), ["alt+b"])
        self.assertTrue(os.path.exists(os.path.join(w.output_folder, "_temp_detection.png")))


if __name__ == "__main__":
    unittest.main()
