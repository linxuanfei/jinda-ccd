@echo off
rem CLI demo: synthetic camera, no camera hardware needed.
chcp 65001 >nul
setlocal
set "BASE=%~dp0"
set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"
set "PYTHONPATH=%BASE%_deps\cv;%BASE%_deps\qt"
set "PYTHONIOENCODING=utf-8"
cd /d "%BASE%"
"%PY%" tools\demo_sim_camera.py --frames 30 --jitter 1 %*
pause
