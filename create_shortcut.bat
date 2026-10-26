@echo off
chcp 65001 >nul
rem 强制重建桌面快捷方式：忽略“只创建一次”的标记，并覆盖已有的同名快捷方式
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_shortcut.ps1" -Force
pause
