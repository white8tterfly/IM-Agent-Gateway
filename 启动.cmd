@echo off
rem Desktop shortcut target. Keep this file in the project root so that
rem Explorer's "Open file location" lands on the project directory.
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python not found. Install Python 3.11+ and add it to PATH.
    pause
    exit /b 1
)

python "%~dp0scripts\launch_console.py"
