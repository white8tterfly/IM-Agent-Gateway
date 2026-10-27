@echo off
chcp 65001 >nul
cd /d %~dp0

rem 首次启动在桌面创建快捷方式：只创建一次，之后不再改动
if not exist "%~dp0logs\.shortcut_created" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\install_shortcut.ps1"

rem 检查是否已有服务实例在运行，避免同一机器人双连接互踢
rem 提示语跟随 .env 的 LANGUAGE（仅 en 输出英文，其余按中文）
set "UI_LANG=zh"
for /f "tokens=1,* delims==" %%a in ('findstr /i /b "LANGUAGE" .env 2^>nul') do set "UI_LANG=%%b"
set "UI_LANG=%UI_LANG:"=%"
set "UI_LANG=%UI_LANG: =%"
if /i "%UI_LANG%"=="en" (set "UI_LANG=en") else (set "UI_LANG=zh")
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*main.py*' }; if ($p) { if ($env:UI_LANG -eq 'en') { Write-Host ('Service is already running (PID=' + $p.ProcessId + '); do not start it again.') } else { Write-Host ('服务已在运行 (PID=' + $p.ProcessId + ')，请勿重复启动') }; exit 1 } else { exit 0 }"
if %errorlevel%==1 goto :eof

rem 依赖自检：首次运行或 requirements.txt 变更时自动安装，避免缺依赖导致功能静默失效
rem 用文件指纹做标记，依赖清单没变就不重复走 pip，不影响每次启动速度
set "DEPS_STAMP=%~dp0logs\.deps_ok"
set "DEPS_HASH="
for /f "usebackq delims=" %%h in (`powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 '%~dp0requirements.txt').Hash.Substring(0,16)"`) do set "DEPS_HASH=%%h"
set "DEPS_OLD=none"
if exist "%DEPS_STAMP%" set /p DEPS_OLD=<"%DEPS_STAMP%"
if not "%DEPS_HASH%"=="%DEPS_OLD%" (
  if /i "%UI_LANG%"=="en" (echo Installing dependencies from requirements.txt ...) else (echo 正在安装依赖（requirements.txt）...)
  python -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    if /i "%UI_LANG%"=="en" (echo Dependency installation failed. Run: python -m pip install -r requirements.txt) else (echo 依赖安装失败，请手动执行: python -m pip install -r requirements.txt)
    pause
    exit /b 1
  )
  if not exist "%~dp0logs" mkdir "%~dp0logs"
  >"%DEPS_STAMP%" echo %DEPS_HASH%
)

python main.py
pause
