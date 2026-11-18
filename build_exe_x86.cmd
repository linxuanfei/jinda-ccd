@echo off
rem 32 位包打包（Python 3.8 x86 + PySide2/Qt5 + PyInstaller 5.13）
rem 现场老主机的原厂软件和相机 SDK 都是 32 位，所以这个包能直接用现场的 MVCAMSDK.dll
setlocal
set "BASE=%~dp0"
set "PY=C:\py38x86\python.exe"
if not exist "%PY%" set "PY=python"
set "PYTHONIOENCODING=utf-8"
cd /d "%BASE%"

"%PY%" -c "import sys; assert sys.maxsize < 2**32, 'needs 32-bit Python'" >nul 2>nul
if errorlevel 1 (
    echo [错误] 需要用 **32 位** Python 3.8
    echo        请安装 python-3.8.10.exe（32-bit，不是 amd64），再把本脚本里的 PY 改成它的路径
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

echo == 正在打包 32 位版 ==
"%PY%" -m PyInstaller packaging\VisionAppX86.spec --noconfirm --clean
if errorlevel 1 (
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo.
echo == 完成: %BASE%dist_x86\VisionApp-x86\VisionApp-x86.exe ==
echo    相机 SDK 已随包带走（sdk\mindvision\MVCAMSDK.dll）
echo.
pause