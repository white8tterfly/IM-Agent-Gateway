# 首次启动时在桌面创建「IM-Agent Gateway」快捷方式，只创建一次。
# 之后不论快捷方式是否还在都不再改动：标记文件 logs\.shortcut_created 存在即跳过。
# 快捷方式直接调用 pythonw.exe 运行 main.py：不经过脚本宿主，也不会弹命令行窗口，
# 日志仍写入 logs/im_agent_gateway.log。
# 需要强制重建时加 -Force（忽略标记并覆盖同名快捷方式），或双击 create_shortcut.bat。

param([switch]$Force)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Marker = Join-Path $Root "logs\.shortcut_created"
if (-not $Force -and (Test-Path $Marker)) { exit 0 }

$Entry = Join-Path $Root "main.py"
if (-not (Test-Path $Entry)) { exit 0 }

$Desktop = [Environment]::GetFolderPath("Desktop")
if (-not $Desktop) { exit 0 }

$Link = Join-Path $Desktop "IM-Agent Gateway.lnk"
if (-not (Test-Path (Join-Path $Root "logs"))) {
    New-Item -ItemType Directory -Path (Join-Path $Root "logs") -Force | Out-Null
}
if ((Test-Path $Link) -and (-not $Force)) {
    # 首次运行时已有同名快捷方式：只记录标记，不再生成
    New-Item -ItemType File -Path $Marker -Force | Out-Null
    exit 0
}

# 优先 pythonw.exe（无控制台）；缺失时退回 python.exe 并以最小化窗口启动
$Pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
$Python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $Pythonw -and -not $Python) { exit 0 }

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($Link)
$Target = if ($Pythonw) { $Pythonw } else { $Python }
$Shortcut.TargetPath = $Target
$Shortcut.Arguments = '"' + $Entry + '"'
$Shortcut.WorkingDirectory = $Root
$Shortcut.WindowStyle = 7
$Shortcut.Description = "IM-Agent Gateway"

# 专用图标；缺失时退回 Python 自带图标
$Icon = Join-Path $Root "assets\icon.ico"
$Shortcut.IconLocation = $(if (Test-Path $Icon) { $Icon + ",0" } else { $Target + ",0" })
$Shortcut.Save()

New-Item -ItemType File -Path $Marker -Force | Out-Null
Write-Host "已在桌面创建快捷方式：$Link"
