---
name: verify
description: Check a PatternHawk change without touching the user's screen, keyboard or saved data. Use after changing detection, hotkeys, the scheduler, capture control, settings or the UI, and before committing or releasing.
---

# Verify a PatternHawk change

PatternHawk presses keys in a live trading platform. "It starts" proves nothing, and trying a change for real on the user's desktop is not allowed. Verification here has four parts: the tests pass, the tests would notice the bug, the UI looks right, and the handover says what a person still has to check.

## 1. Run the suite

```
venv\Scripts\python -m unittest discover -s tests -v
```

About 30 seconds. Everything must pass before anything else.

## 2. Cover what changed

Add tests to `tests/test_patternhawk.py`. They drive the real window and the real OpenCV detection against small generated images, with the outside world replaced:

| Real thing | In tests |
|---|---|
| Screen capture | `capture_screen` returns the scene image you pass to `cycle()` |
| Key presses | `pyautogui.hotkey` and `_perform_hotkey_win32` append to `self.sent` |
| Registry settings | `FakeSettings`, in memory |
| `%USERPROFILE%\PatternHawk` | a temp folder per test |
| Dialogs | warnings collected in `self.warnings` |
| Log lines | collected in `self.logs` |

What `PatternHawkTestCase` gives you:

- `make_window(areas=[...], default_hotkey="")` builds a window from data. `area(template(FIX.bull, "alt+b"))` builds one area; `both_directions()` is an area with a bullish `alt+b` and a bearish `alt+s` template.
- `cycle(scene, scene, ...)` runs one capture cycle with one scene per area and returns the hotkeys sent. Scenes: `FIX.none`, `FIX.bull_scene`, `FIX.bear_scene`, `FIX.both_scene`.
- `status()`, `list_rows(area_index)`, `saved()` read the status label, a template list and the data file.

```python
def test_areas_disagree_no_action(self):
    self.make_window(areas=[both_directions(), both_directions()])
    self.assertEqual(self.cycle(FIX.bull_scene, FIX.bear_scene), [])
    self.assertIn("hotkeys disagree (alt+b vs alt+s)", self.status())
```

Assert on what the user would see: keys sent, status text, log lines, list rows, the saved file. For timers (pause, delayed start, schedule), let the real timers run with the shortest settings and pump the event loop, as `CaptureControlTests` does.

## 3. Prove the test can fail

A test that still passes with the logic removed is not a test.

- For a bug: write a small script that reproduces it first, and keep its output. Fix, then rerun it.
- For new logic: break it on purpose (flip the condition, delete the line), run the suite, and confirm a test fails for the right reason. Do it on a scratch copy of the app file and `tests/`, or restore straight after.

## 4. Look at the UI

For anything that changes a tab, render the tabs and read the images:

```
venv\Scripts\python .claude\skills\verify\render_tabs.py <output folder> [data.json]
```

It writes `tab_capture.png`, `tab_templates.png`, `tab_scheduler.png`, `tab_settings.png` and `tab_logs.png` using offscreen Qt and a temp profile. Nothing appears on screen. Check labels, alignment, truncated text and that new controls sit where a user would look for them.

## 5. Say what was not checked

No automated check covers real screen capture, real key delivery into the trading platform, or window focus. State that in the handover and give the user a short list to try by hand, for example:

1. Two areas, both showing the bullish pattern: the bullish hotkey arrives in the target app.
2. One area bullish, one bearish: nothing is pressed and the Logs tab says the hotkeys disagree.

## Hard limits

- Never send a real keystroke or mouse click on the user's desktop.
- Never read or write the real `%USERPROFILE%\PatternHawk` folder or the app's registry settings from a test or script.
- Never stop processes named `python`. MCP servers run as python.
