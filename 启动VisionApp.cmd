@echo off
rem VisionApp launcher (6-station line). Deps are bundled in .\_deps, so no install needed.
rem If you prefer your own Python 3.12 + venv, see README.md / setup.md.
chcp 65001 >nul
setlocal
set "BASE=%~dp0"
set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"
set "PYTHONPATH=%BASE%_deps\cv;%BASE%_deps\qt"
set "PYTHONIOENCODING=utf-8"
cd /d "%BASE%"
"%PY%" tools\visionapp_gui.py %*
if errorlevel 1 pause
