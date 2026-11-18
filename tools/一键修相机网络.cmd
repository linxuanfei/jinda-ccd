@echo off
rem 双击这个：会自动弹管理员权限，然后修相机网络并枚举
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix_camera_net.ps1"