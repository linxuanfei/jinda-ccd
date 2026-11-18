# -*- mode: python ; coding: utf-8 -*-
"""VisionApp 的 **Win7 兼容包**（PySide2 / Qt5 / Python 3.8）打包配置。

用途：老检测机是 Windows 7 SP1 时用这个包（Qt6 不支持 Win7）。
Win10/11 也能跑这个包，但优先用主包 `VisionApp.spec`（Qt6，画面更顺）。

构建（需要 Python 3.8 + PySide2 5.15 + PyInstaller 5.13）：
    build_exe_win7.cmd
或：
    C:\\py38\\python.exe -m PyInstaller packaging\\VisionAppWin7.spec --noconfirm --clean

产物：dist/VisionApp-Win7/VisionApp-Win7.exe
"""

import os

PROJECT = os.path.dirname(SPECPATH)     # noqa: F821  (SPECPATH 由 PyInstaller 注入)

DATAS = [
    (os.path.join(PROJECT, 'config'), 'config'),
    (os.path.join(PROJECT, 'recipes'), 'recipes'),
    (os.path.join(PROJECT, 'samples'), 'samples'),
    (os.path.join(PROJECT, 'docs'), 'docs'),
]
import struct as _struct
for _sdk in ('sdk/mindvision', 'sdk'):
    _p = os.path.join(PROJECT, *_sdk.split('/'))
    if os.path.isdir(_p):
        DATAS.append((_p, 'sdk' if _sdk == 'sdk' else 'sdk/mindvision'))
        break

a = Analysis(
    [os.path.join(PROJECT, 'tools', 'visionapp_gui.py')],
    pathex=[PROJECT],
    binaries=[],
    datas=DATAS,
    hiddenimports=[
        'serial',
        # 下面这些是「函数内延迟 import」或动态导入，PyInstaller 静态分析看不到，必须显式声明
        'visionapp.sources_mindvision',
        'visionapp.tracking',
        'visionapp.align',
        'visionapp.imgio',
        'visionapp.paths',
        'visionapp.qtcompat',
        'visionapp.ui.ng_gallery',
        'visionapp.ui.general_settings',
        'visionapp.diskguard',
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
    name='VisionApp-Win7',
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
    name='VisionApp-Win7',
)
