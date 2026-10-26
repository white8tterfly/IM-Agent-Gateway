# 首次启动时在桌面创建「IM-Agent Gateway」快捷方式，只创建一次。
# 之后不论快捷方式是否还在都不再改动：标记文件 logs\.shortcut_created 存在即跳过。
# 快捷方式用 python.exe 运行 scripts\launch_console.py：弹出启动进度窗口，
# 服务就绪后自动打开 Web 控制台并关闭窗口；服务本身以无窗口方式在后台运行。
# 需要强制重建时加 -Force（忽略标记并覆盖同名快捷方式），或双击 create_shortcut.bat。

param([switch]$Force)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Marker = Join-Path $Root "logs\.shortcut_created"
if (-not $Force -and (Test-Path $Marker)) { exit 0 }

$Entry = Join-Path $Root "scripts\launch_console.py"
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

# 优先 python.exe（显示启动进度窗口，就绪后自动关闭）；缺失时退回 pythonw.exe
$Python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
$Pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $Python -and -not $Pythonw) { exit 0 }

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($Link)
$Target = if ($Python) { $Python } else { $Pythonw }
$Shortcut.TargetPath = $Target
$Shortcut.Arguments = '"' + $Entry + '"'
$Shortcut.WorkingDirectory = $Root
$Shortcut.WindowStyle = 1
$Shortcut.Description = "IM-Agent Gateway"

# 专用图标；缺失时退回 Python 自带图标
$Icon = Join-Path $Root "assets\icon.ico"
$Shortcut.IconLocation = $(if (Test-Path $Icon) { $Icon + ",0" } else { $Target + ",0" })
$Shortcut.Save()

New-Item -ItemType File -Path $Marker -Force | Out-Null
Write-Host "已在桌面创建快捷方式：$Link"
