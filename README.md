# PatternHawk

PatternHawk watches parts of your screen for chart patterns you have saved as images, and presses a hotkey in your trading platform when it sees them.

You choose up to five screen areas, give each area one or more pattern images (templates), and give each template the hotkey it stands for. When every active area shows one of its patterns and they all stand for the same hotkey, PatternHawk presses that hotkey. Windows only.

## Download

Get `PatternHawk.exe` from the [latest release](https://github.com/PruthvikAIRepo/Pattern-Detector/releases/latest). It is a single file with nothing to install. To update, replace the old file with the new one; your areas, templates and settings are kept.

The version you are running is shown in the title bar.

## How it decides

On every capture interval PatternHawk takes a screenshot of each active area and looks for that area's templates in it. Then:

1. **Every active area must match.** If one active area shows none of its templates, nothing happens.
2. **Every match must call for the same hotkey.** If one matched template calls for `alt+b` and another for `alt+s`, nothing happens. That holds between areas and inside a single area.
3. **Cooldown.** After a hotkey is pressed, nothing is pressed again until the cooldown has run out, whichever hotkey it would be.

| Area 1 | Area 2 | Area 3 | Area 4 | Result |
|---|---|---|---|---|
| alt+b | alt+b | alt+b | alt+b | presses `alt+b` |
| alt+s | alt+s | alt+s | alt+s | presses `alt+s` |
| alt+b | alt+s | alt+b | alt+b | no action |
| alt+b | alt+b | no match | alt+b | no action |

This lets you load bullish and bearish templates side by side: whichever direction every area agrees on is the one that fires, and a mixed picture fires nothing.

## Setting it up

1. **Capture tab → Select Area.** Drag a rectangle around the part of the screen to watch. Hover over the size next to the button to see the area highlighted on screen. Untick *Active* to leave an area out.
2. **Templates tab → Add Template.** Pick the pattern image, a similarity threshold (0 to 1; higher is stricter) and the hotkey for that template. *Edit Hotkey*, or a double-click on a template, changes the hotkey later. Up to 10 templates per area.
3. **Capture tab.** Set the capture interval and press *Start Capture*, then click on your trading app. PatternHawk remembers the last window you clicked that is not its own (shown as *Target Window*) and brings it to the front before each hotkey.

The result of the last check is drawn at the bottom of the Capture tab, with a box around each match and the hotkey it called for. The Logs tab says why a hotkey was or was not pressed.

### Hotkeys

Write keys joined with `+`, for example `alt+b`, `ctrl+shift+a` or `f5`. Capitals and spaces do not matter: `Alt + B` is the same as `alt+b`. A capital letter does not add Shift; if the hotkey needs Shift, write it out, as in `alt+shift+b`.

- A template with no hotkey of its own uses the **Default Hotkey** from the Settings tab.
- PatternHawk refuses a key name it cannot press (a typo such as `atl+b`), so it never presses half a hotkey.
- **Browser-compatible** mode (Settings) is for platforms that run inside Chrome or another browser. It supports letters, digits, F1 to F12, and Enter, Tab, Esc, Space, Backspace, Delete, Insert, Home, End, Page Up/Down and the arrow keys.

### Template sets

*Save as Set* stores an area's templates, with their thresholds and hotkeys, under a name. Pick a set from the dropdown to load it into any area. Up to 9 sets.

### Scheduler

The Scheduler tab splits the day into 10-minute blocks. For each block you choose which areas are active, the capture interval and the cooldown. With *Enable Schedule* ticked PatternHawk follows the table by itself and the manual Start/Stop controls are switched off. A block with no active areas pauses capturing.

### Settings

| Setting | What it does |
|---|---|
| Default Hotkey | Used by templates that have no hotkey of their own |
| Browser-compatible | Sends keys in a way browser-based platforms accept |
| Key Press Delay | Pause between pressing and releasing the keys, 0 to 5 seconds |
| Sound Alert | Three beeps when a hotkey is pressed |
| Neglect Matched | A template that just fired sits out the next check |
| Screenshots/Output | Keep every screenshot and result image on disk, or only the latest |
| Cooldown Timer | Seconds to wait after a hotkey before another can fire |

## Where things are stored

Everything lives in `%USERPROFILE%\PatternHawk\`:

- `pattern_hawk_data.json` — areas, templates, template sets and the schedule
- `screenshots\` and `output\` — captures and result images
- `error.log` — written only if something goes wrong

## Running from source

Requires Python 3.11 or newer on Windows.

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python Pattern-Analysis-Tool.py
```

Run the tests (they never capture the screen or press a key):

```
venv\Scripts\python -m unittest discover -s tests -v
```

Build the executable into `dist\PatternHawk.exe`:

```
venv\Scripts\pyinstaller PatternHawk.spec --clean
```
