@echo off
rem Win7 兼容包打包（Python 3.8 + PySide2/Qt5 + PyInstaller 5.13）
rem 先用 build_exe.cmd 的 Python 3.8 环境（C:\py38 或 PATH 里的 python 3.8）
setlocal
set "BASE=%~dp0"
set "PY=C:\py38\python.exe"
if not exist "%PY%" set "PY=python"
set "PYTHONIOENCODING=utf-8"
cd /d "%BASE%"

"%PY%" -c "import sys; assert sys.version_info[:2]==(3,8), 'needs Python 3.8, got %s' % sys.version" >nul 2>nul
if errorlevel 1 (
    echo [错误] 需要用 Python 3.8（PySide2 才能在 Win7 上跑）
    echo        请先安装 Python 3.8 x64，并把本脚本里的 PY 改成它的路径
    pause
    exit /b 1
)

"%PY%" -c "import PyInstaller, PySide2" >nul 2>nul
if errorlevel 1 (
    echo [错误] 缺依赖，请先执行：
    echo        %PY% -m pip install numpy==1.21.6 opencv-python==4.5.5.64 PySide2==5.15.2.1 pyserial==3.5 pyinstaller==5.13.2
    pause
    exit /b 1
)

echo == 正在打包 Win7 兼容版 ==
"%PY%" -m PyInstaller packaging\VisionAppWin7.spec --noconfirm --clean
if errorlevel 1 (
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo.
echo == 完成: %BASE%dist\VisionApp-Win7\VisionApp-Win7.exe ==
echo    Win7 SP1 x64 需要先装 KB2999226（UCRT 补丁）
echo.
pause