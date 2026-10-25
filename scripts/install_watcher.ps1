# 安装/卸载 Windows 开机自启的看门狗（watcher.py）。
#
# 用法（在项目根目录，用 PowerShell 执行）：
#   安装：  powershell -ExecutionPolicy Bypass -File scripts\install_watcher.ps1
#   卸载：  powershell -ExecutionPolicy Bypass -File scripts\install_watcher.ps1 -Uninstall
#   指定解释器：powershell -ExecutionPolicy Bypass -File scripts\install_watcher.ps1 -Python "C:\Python312\python.exe"
#
# 原理：注册一个「当前用户登录时」触发的计划任务，登录后自动在项目根目录后台
# 运行 `pythonw watcher.py`。看门狗本身只监听 IM 消息、不启动完整服务，收到消息
# 才唤醒服务；是否真正生效还要看 .env 里 WATCH_ENABLED 是否为 1，
# 是否随开机启动还要看 WATCH_AUTOSTART（0 = 登录后读配置即退出）。
# 用 pythonw 而不是 python：它不带控制台，开机不会弹黑色命令行窗口。
param(
    [switch]$Uninstall,          # 提供该开关时卸载计划任务
    [string]$Python = ""         # Python 解释器完整路径；留空则从 PATH 里找 python
)

$ErrorActionPreference = "Stop"

$TaskName = "IM-Agent-Gateway-Watcher"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Watcher = Join-Path $Root "watcher.py"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已卸载计划任务：$TaskName"
    } else {
        Write-Host "未找到计划任务 $TaskName，无需卸载"
    }
    exit 0
}

# 确定 python 解释器：显式传入优先，否则从 PATH 里找
if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Write-Error "PATH 里未找到 python，请先安装并加入 PATH，或用 -Python 指定完整路径"
        exit 1
    }
    $Python = $cmd.Source
}
if (-not (Test-Path $Python)) {
    Write-Error "指定的 Python 不存在：$Python"
    exit 1
}

# 登录时触发；StartWhenAvailable 保证错过触发点（如休眠）后补跑；
# ExecutionTimeLimit 设 0 表示不限制运行时长（看门狗需要长期驻留）。
# 优先用同目录的 pythonw.exe：无控制台，开机静默启动，不会弹窗打扰。
$Pythonw = Join-Path (Split-Path $Python) "pythonw.exe"
if (Test-Path $Pythonw) {
    $Launch = $Pythonw
} else {
    Write-Host "未找到 pythonw.exe，回退用 python.exe（开机可能出现命令行窗口）"
    $Launch = $Python
}
$Action   = New-ScheduledTaskAction -Execute $Launch -Argument "`"$Watcher`"" -WorkingDirectory $Root
$Trigger  = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null

Write-Host "已注册开机自启计划任务：$TaskName"
Write-Host "  执行：$Launch `"$Watcher`""
Write-Host "  工作目录：$Root"
Write-Host "提示：还需在 .env 里设置 WATCH_ENABLED = `"1`"（并可设 IDLE_EXIT 实现睡-醒闭环），否则看门狗启动即退出；"
Write-Host "      不需要开机自启时把 WATCH_AUTOSTART 设为 `"0`"（计划任务保留，登录后读配置即退出）。"