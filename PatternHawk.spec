# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['Pattern-Analysis-Tool.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('PatternHawk.png', '.'),
        ('PatternHawk.ico', '.'),
    ],
    hiddenimports=['skimage.metrics'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tensorflow', 'torch', 'torchvision', 'keras',
        'pandas', 'tkinter',
        'PyQt5.QtWebEngine', 'PyQt5.QtWebEngineWidgets',
        'PyQt5.QtMultimedia', 'PyQt5.QtBluetooth',
        'PyQt5.QtDesigner', 'PyQt5.QtNfc',
        'PyQt5.QtQml', 'PyQt5.QtQuick',
        'PyQt5.QtSensors', 'PyQt5.QtSerialPort',
        'PyQt5.QtSql', 'PyQt5.QtTest',
    ],
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PatternHawk',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='PatternHawk.ico',
)
