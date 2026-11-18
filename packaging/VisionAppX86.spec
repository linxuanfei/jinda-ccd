# -*- mode: python ; coding: utf-8 -*-
"""VisionApp 的 **32 位（x86）包** —— 给现场老主机用。

为什么需要这个包：
    现场原厂软件（In-Vision / 2pin.exe）**全是 32 位**，它带的相机 SDK 是
    `MVCAMSDK.dll`（x86）。厂商没有随机器装 `MVCAMSDK_X64.dll`，
    所以 **64 位程序加载不了这份 DLL**；出 32 位包就能直接用现场那份 SDK。

一个包通吃：
    Windows 7 SP1 x86 / x64、Windows 10 / 11 x64 都能跑（32 位程序在 x64 系统上正常）。

构建（需要 32 位 Python 3.8 + PySide2 5.15 + PyInstaller 5.13）：
    build_exe_x86.cmd
或：
    C:\\py38x86\\python.exe -m PyInstaller packaging\\VisionAppX86.spec --noconfirm --clean

产物：dist_x86/VisionApp-x86/VisionApp-x86.exe
"""

import os

PROJECT = os.path.dirname(SPECPATH)     # noqa: F821  (SPECPATH 由 PyInstaller 注入)

DATAS = [
    (os.path.join(PROJECT, 'config'), 'config'),
    (os.path.join(PROJECT, 'recipes'), 'recipes'),
    (os.path.join(PROJECT, 'samples'), 'samples'),
    (os.path.join(PROJECT, 'docs'), 'docs'),
]
# 相机 SDK（现场那份 x86 MVCAMSDK.dll 放这里，一起打进去）
for _rel in ('sdk/mindvision', 'sdk'):
    _p = os.path.join(PROJECT, *_rel.split('/'))
    if os.path.isdir(_p):
        DATAS.append((_p, _rel))
        break

a = Analysis(
    [os.path.join(PROJECT, 'tools', 'visionapp_gui.py')],
    pathex=[PROJECT],
    binaries=[],
    datas=DATAS,
    hiddenimports=[
        'serial',
        'visionapp.sources_mindvision',
        'visionapp.tracking',
        'visionapp.align',
        'visionapp.imgio',
        'visionapp.paths',
        'visionapp.qtcompat',
        'visionapp.ui.ng_gallery',
        'visionapp.ui.general_settings',
        'visionapp.diskguard',
        'visionapp.tuning',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VisionApp-x86',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='VisionApp-x86',
)
