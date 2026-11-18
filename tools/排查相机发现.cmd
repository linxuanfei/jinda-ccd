@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0diag_camera_discovery.ps1" -KeepFirewallOff