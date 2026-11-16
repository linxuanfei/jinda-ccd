# -*- mode: python ; coding: utf-8 -*-
"""VisionApp 打包配置（PyInstaller onedir）。

产出：dist/VisionApp/VisionApp.exe —— 免安装、绿色目录，整个 VisionApp 文件夹拷到
检测机任意位置（建议纯英文路径，如 D:\\VisionApp）双击即可。

首次启动时会把内置的 config/recipes/samples/docs 释放到 exe 旁边，
之后配置、配方、结果、日志都写在 exe 所在目录（便携）。

用法（项目根目录）：
    build_exe.cmd
或自行安装依赖后：
    pip install pyinstaller
    pyinstaller packaging/VisionApp.spec --noconfirm --clean
"""

import os

PROJECT = os.path.dirname(SPECPATH)     # noqa: F821  (SPECPATH 由 PyInstaller 注入)

# 不把厂商 SDK 二进制打进包。现场装官方 MVSDK，用 MINDVISION_SDK 指向 MVCAMSDK_X64.dll / mvsdk.py。
DATAS = [
    (os.path.join(PROJECT, 'config'), 'config'),
    (os.path.join(PROJECT, 'recipes'), 'recipes'),
    (os.path.join(PROJECT, 'samples'), 'samples'),
    (os.path.join(PROJECT, 'docs'), 'docs'),
]

a = Analysis(
    [os.path.join(PROJECT, 'tools', 'visionapp_gui.py')],
    pathex=[PROJECT],
    binaries=[],
    datas=DATAS,
    hiddenimports=[
        'serial',
        # 下面这些是「函数内延迟 import」或动态导入，PyInstaller 静态分析看不到，必须显式声明
        'visionapp.mindvision',
        'visionapp.tracking',
        'visionapp.align',
        'visionapp.imgio',
        'visionapp.paths',
        'visionapp.qtcompat',
    ],           # outputs.py 里是函数内 import，静态分析找不到
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VisionApp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                          # 不开 UPX：省得杀软误报
    console=False,                      # 无控制台窗口；日志见 logs/gui.log
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
    name='VisionApp',
)
