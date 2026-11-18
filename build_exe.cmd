@echo off
rem 一键打包 VisionApp.exe（onedir，免安装）。需要 pyinstaller。
setlocal
set "BASE=%~dp0"
set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"
if exist "%BASE%_deps\cv\cv2" set "PYTHONPATH=%BASE%_deps\cv;%BASE%_deps\qt;%PYTHONPATH%"
set "PYTHONIOENCODING=utf-8"
cd /d "%BASE%"

"%PY%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [错误] 缺少 pyinstaller，请先执行: pip install pyinstaller
    pause
    exit /b 1
)

echo == 正在打包 VisionApp.exe ==
"%PY%" -m PyInstaller packaging\VisionApp.spec --noconfirm --clean
if errorlevel 1 (
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo.
echo == 打包完成: %BASE%dist\VisionApp\VisionApp.exe ==
echo    把整个 dist\VisionApp 文件夹拷到检测机，双击 VisionApp.exe 即可
echo.
pause