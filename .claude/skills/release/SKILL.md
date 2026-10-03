---
name: release
description: Build PatternHawk.exe and publish it as a GitHub release. Use when the user asks to release, ship, publish a new version, or rebuild the EXE for the client.
---

# Release PatternHawk

A release is one file, `dist\PatternHawk.exe`, attached to a GitHub release tagged from `development`. The client downloads it from the Releases page, so everything written here is read by the client.

Publishing is outward-facing. Do it when the user has asked for a release in this conversation, not on your own initiative.

## 1. Get the tree ready

1. Run the tests and stop if anything fails:
   `venv\Scripts\python -m unittest discover -s tests`
2. Pick the version. `gh release list --limit 3` shows the last tag. A new feature bumps the minor number (1.7.0 to 1.8.0), fixes alone bump the patch (1.7.0 to 1.7.1).
3. Set `APP_VERSION` in `Pattern-Analysis-Tool.py`. The title bar shows it, and that is how the client tells one build from another.
4. Commit on `development`. `git status --short` must be empty.

## 2. Build

1. PatternHawk must not be running, or the build cannot replace the EXE:
   `Get-Process PatternHawk -ErrorAction SilentlyContinue`
   If one is running and you did not start it, ask the user to close it. It may be capturing live.
2. `venv\Scripts\pyinstaller PatternHawk.spec --clean`
   It takes a few minutes and ends with `Building EXE from EXE-00.toc completed successfully.`
3. `dist\PatternHawk.exe` should be about 125 MB with a fresh timestamp.

## 3. Smoke test the EXE

Start it against an empty temp profile. With no saved areas or schedule it cannot start capturing or press anything.

```powershell
$env:USERPROFILE = Join-Path $env:TEMP "patternhawk-smoke"
Remove-Item -Recurse -Force $env:USERPROFILE -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $env:USERPROFILE | Out-Null
Start-Process dist\PatternHawk.exe
Start-Sleep 20
Get-Process PatternHawk | Select-Object Id, MainWindowTitle
Test-Path (Join-Path $env:USERPROFILE "PatternHawk\error.log")
Stop-Process -Name PatternHawk
Remove-Item -Recurse -Force $env:USERPROFILE
```

Run it as one command: the changed `USERPROFILE` must not leak into later commands.

It passes when one process has the title `PatternHawk v<version> — Idle` with the version you just set, and `error.log` does not exist (`False`). A one-file build shows two processes and only one has a title. The window shows on the user's screen for those 20 seconds.

This proves the EXE starts and is the new build. It does not prove that a hotkey reaches the trading platform. Only the user or the client can check that, so say it in the handover.

Afterwards delete `build\`.

## 4. Publish

1. `git push origin development`
2. Write the notes to a file (see below), then:
   `gh release create v<version> dist/PatternHawk.exe --target development --title "PatternHawk v<version> — <headline>" --notes-file <file>`
3. Check it: `gh release view v<version> --json url,targetCommitish,assets`. The asset is `PatternHawk.exe`, its size matches the local file, and the target is `development`.
4. Comment on the issue with what was done and the release link, then close it.

Never stop processes named `python` at any point. MCP servers run as python.

## Release notes

The reader is a trader, not a developer. Write the way you would explain it to them across a desk.

- Lead with what they can do now, in their words ("each template has its own hotkey"), not with how it was built.
- Short sentences, plain words. No marketing tone, no filler, nothing about effort or time spent.
- Explain behaviour that changed, including fixes. If something that used to fire will now stay quiet, or the other way round, say so.
- Give steps they can follow to try it, and say what they should see.
- No internal names: no function names, no test counts, no file paths apart from the EXE.

Shape that has worked:

```markdown
## PatternHawk v<version> — <headline>

### What's new
<the change, as the user experiences it; a small table helps for rules>

### How to use it
1. ...

### Fixed
- <problem as the user saw it> — <what happens now>

### Please check
1. <a concrete thing to try and what should happen>

### Everything from previous versions is included

### Download
Download `PatternHawk.exe` below and replace your existing copy. Your areas, templates and settings are kept.
```
