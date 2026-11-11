# 首次启动时在桌面创建「IM-Agent Gateway」快捷方式，只创建一次。
# 之后不论快捷方式是否还在都不再改动：标记文件 logs\.shortcut_created 存在即跳过。
# 快捷方式目标指向项目根目录的「启动.cmd」：由它运行 scripts\launch_console.py，
# 弹出启动进度窗口，服务就绪后自动打开 Web 控制台并关闭窗口。目标放在项目内
# 而不是 python.exe，资源管理器右键「打开文件位置」才会定位到项目目录。
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

# 目标用项目根目录的启动脚本：文件在项目内，右键「打开文件位置」才会进项目目录
$Launcher = Join-Path $Root "启动.cmd"
if (-not (Test-Path $Launcher)) { exit 0 }

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($Link)
$Shortcut.TargetPath = $Launcher
$Shortcut.Arguments = ""
$Shortcut.WorkingDirectory = $Root
$Shortcut.WindowStyle = 1
$Shortcut.Description = "IM-Agent Gateway"

# 专用图标；缺失时保留系统默认图标
$Icon = Join-Path $Root "assets\icon.ico"
if (Test-Path $Icon) { $Shortcut.IconLocation = $Icon + ",0" }
$Shortcut.Save()

New-Item -ItemType File -Path $Marker -Force | Out-Null
Write-Host "已在桌面创建快捷方式：$Link"
