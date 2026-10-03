# PatternHawk

Windows desktop app for traders (PyQt5 + OpenCV). It watches up to 5 screen areas for saved pattern images and presses a hotkey in the user's trading platform when every active area matches and all matches call for the same hotkey.

It runs against live trading platforms. A wrong, partial, repeated or unexpected key press is the worst outcome, worse than not pressing at all. Where a case is ambiguous the app does nothing and says why in the Logs tab. Keep it that way.

## Commands

Run from the repo root with the project venv (Windows).

| | |
|---|---|
| Run the app | `venv\Scripts\python Pattern-Analysis-Tool.py` |
| Run the tests | `venv\Scripts\python -m unittest discover -s tests -v` |
| Build the EXE | `venv\Scripts\pyinstaller PatternHawk.spec --clean` (writes `dist\PatternHawk.exe`) |

Tests are stdlib `unittest` (pytest is not installed), need Python 3.11+, and take about 30 seconds.

Skills: `/verify` after any behaviour change, `/release` to build and publish a version.

## Layout

- `Pattern-Analysis-Tool.py` is the whole app. The hyphen means it cannot be imported by name; tests load it with `importlib.util.spec_from_file_location`.
- `tests/test_patternhawk.py` is a headless suite that drives the real window and the real OpenCV detection.
- `PatternHawk.spec` is the PyInstaller build (one file, windowed).
- `PatternHawk.png` is the window and tray icon, `PatternHawk.ico` the EXE icon.
- `dist/`, `build/`, `venv/`, `.idea/` and `docs/commercial-plan/` are gitignored. `docs/commercial-plan/` is private and must never be committed: this repo is public.

## How the app file is organised

Sections are marked with `# ───` banners, in this order: module constants and hotkey helpers, then `ScreenCapturePatternDetector` (system tray, cooldown, UI setup for the five tabs, area status, hover highlight, target window tracking, data persistence, template management, template hotkeys, template sets, area selection, scheduler, capture control, capture & detection, hotkey execution, settings/reset/logging), then `AreaSelector`, `AreaHighlighter` and `__main__` (dark Fusion theme).

Use the constants, not literals: `NUM_AREAS` (5), `SCHEDULE_BLOCK_MINUTES` (10), `SCHEDULE_BLOCKS` (144), `APP_VERSION`.

## Data model

```python
self.areas[i] = {
    "region": QRect | None,
    "templates": [{"path": str, "similarity": float, "hotkey": str}],
    "active": bool,
    "neglect_count": {template_path: cycles_left},   # runtime only, never saved
}
self.global_hotkey    # the "Default Hotkey" in the UI; JSON key is "global_hotkey"
self.template_sets    # up to 9: [{"name": str, "templates": deep copy}], shared by all areas
self.schedule         # 144 blocks: {"areas_active": [bool] * 5, "capture_interval": s, "cooldown": s}
```

A template's `hotkey` may be `""` or missing, which means "use the Default Hotkey". Never read it directly: use `effective_hotkey(template)`.

## Persistence

- `%USERPROFILE%\PatternHawk\pattern_hawk_data.json` holds `global_hotkey`, `areas` (templates, region as x/y/w/h, active), `template_sets`, `schedule_enabled` and `schedule`.
- Template dicts are written and read as they are, so a new template field persists without touching `saveData` or `loadData`.
- Old files must keep loading. Read new fields with `.get()` and a default. `loadData` also accepts the original format, a bare list of templates with no regions.
- The Settings tab values live in `QSettings('PatternHawk', 'PatternHawk')`, which is the registry. They are written only by Save Settings.
- `screenshots\` and `output\` sit next to the data file. With "Save to disk" off, each cycle overwrites `_temp_*.png`. `error.log` appears only after an error.

## One capture cycle

1. `self.timer` ticks on the main thread and `capture_and_detect` starts a worker thread running `_capture_and_detect_thread`. One cycle runs at a time (`_cycle_lock`); a tick that arrives during a cycle is skipped.
2. For each area that is active and has a region and templates: `capture_screen` (Qt `grabWindow`, saved as PNG), then `detect_pattern_for_area`.
3. Per template, `cv2.matchTemplate` (`TM_CCOEFF_NORMED`) runs at 21 scales from 0.5x to 2.0x and keeps the best one, so a template matches once at most. It matches when that score reaches the template's similarity. SSIM and a colour histogram are blended into the score drawn on the result image; they do not decide the match.
4. The decision, below.
5. `perform_hotkey` refocuses the tracked target window, sends the keys (pyautogui, or Win32 `keybd_event` in browser-compatible mode), starts the cooldown and beeps.
6. `process_combined_visualization` draws the result image for the Capture tab.

### The decision

Checked in this order. Every case but the last is "no action".

1. An active area has no match (AND gate).
2. A matched template has no hotkey, neither its own nor a default.
3. Matched templates call for different hotkeys, between areas or inside one area.
4. The agreed hotkey contains a key that cannot be pressed.
5. Cooldown is active.
6. Otherwise the hotkey is pressed once.

Cases 2 to 4 are logged once while they last (`_last_block_reason`), not every cycle. The Logs tab is capped at 500 lines and is what the user reads to find out why nothing fired, so log decisions, not cycles.

### Neglect and cooldown

- "Neglect Matched" (on by default): templates that fired are marked in `neglect_count` and sit out the next cycle. They cannot satisfy their area's AND, but while they still match, their hotkey still counts toward agreement. Otherwise an opposite signal could fire alone one cycle after a fire.
- Mark neglect only when the hotkey fires. Marking every match put areas out of step and the AND never met.
- Cooldown is one shared flag (`in_cooldown`) for all hotkeys. It is set on the main thread through `start_cooldown_signal` after the keys are sent.

## Hotkey strings

- Compare and store hotkeys through `normalize_hotkey()`: lowercase, no spaces, modifiers first in the order ctrl, alt, shift, win.
- `_key_problem(hotkey)` says why a hotkey cannot be pressed: a name pyautogui does not know on Windows, or in browser-compatible mode a key outside `WIN32_VK_CODES`.
- Both senders skip a key they do not know and press the rest. An unchecked typo such as `atl+b` would type a bare `b` into the trading platform.
- The check runs in the template hotkey prompt, Save Settings, Start Capture, Enable Schedule, and again right before firing. The last one matters: a schedule that was on at exit resumes at launch without passing through Start, and data files can be edited by hand.

## Threading and timers

- Detection runs in a worker thread. Do not touch widgets from it. Use `update_status` (signal), `log_message` (queued call), `start_cooldown_signal` and `_update_area_status_slot`.
- Existing exceptions: `display_image` and a few checkbox and spinbox reads run on the worker thread. They work; do not add more.
- All timers are created once in `__init__`. Never create a timer inside a handler: pressing the button again leaves the old one running.
- Anything that stops capture (`stop_capture`, `reset_process`, enabling the schedule) must also cancel `pause_timer` and `countdown_timer`, or capture starts again by itself.

## Scheduler

`check_schedule` runs every 5 seconds. On a block change it sets each area's `active`, the capture interval and the cooldown from the block, then starts or stops capture. Manual controls are disabled while the schedule is on. A schedule that was enabled at exit starts again on launch.

## Hotkey delivery

Keys go to whichever window has focus, so the app tracks the last foreground window that is not its own (`_track_foreground_window`, every 500 ms) and `perform_hotkey` refocuses it before sending. Anything that pulls PatternHawk to the front at fire time breaks delivery. The hover highlight overlay must keep `WA_ShowWithoutActivating` (it must not take focus) and `WA_TransparentForMouseEvents` (it sits under the cursor and would otherwise swallow the hover and flicker).

## Testing rules

- Tests never capture the screen, never send a key, and never touch the real `%USERPROFILE%\PatternHawk` folder or the registry. The base class in `tests/test_patternhawk.py` sets that up; build on it.
- Real screen capture and real key delivery into a trading platform can only be checked by a person at the machine. Say so plainly instead of implying it was tested.
- Never send real keystrokes on the user's desktop to "try it out".

## Build and release gotchas

- The build cannot overwrite `dist\PatternHawk.exe` while PatternHawk is running. Ask the user to close it, since it may be capturing live.
- Never stop processes named `python`: MCP servers run as python. Stop only `PatternHawk`, or a PID you started.
- Do not add `unittest` or `pydoc` to the spec's `excludes`. scipy imports them and the EXE dies at startup.
- The spec builds with `optimize=2`, which strips `assert`. Never use `assert` for app logic.
- Rendering the UI offscreen (`QT_QPA_PLATFORM=offscreen`) shows no text unless `QT_QPA_FONTDIR=C:/Windows/Fonts` is set.
- `os.path.expanduser('~')` follows `USERPROFILE`. Point it at a temp folder to launch the app or the EXE without touching real data.

## Workflow

- All work lands on `development`, and releases are tagged from it. `main` is the default branch but is not where work goes.
- One GitHub issue per feature or bug, then implement, `/verify`, commit with the issue number, `/release`, comment on the issue and close it.
- The repo is public and the user's client downloads the EXE from Releases. Issues, commit messages and release notes are read by the client: write them plainly, describe behaviour, and leave out anything internal.
- Bump `APP_VERSION` with every release so the title bar shows which build is running.
