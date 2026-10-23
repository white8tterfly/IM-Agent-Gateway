@echo off
chcp 65001 >nul
cd /d %~dp0

rem 检查是否已有服务实例在运行，避免同一机器人双连接互踢
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*main.py*' -and $_.CommandLine -like '*IM-Agent*' }; if ($p) { Write-Host '服务已在运行 (PID=' $p.ProcessId ')，请勿重复启动'; exit 1 } else { exit 0 }"
if %errorlevel%==1 goto :eof

python main.py
pause
