"""服务侧的看门狗接线：注册开机自启与退出时拉起待命进程。

WATCH_ENABLED 只有看门狗自己会读，而看门狗没运行时没人读它——本模块让正在
运行的服务代为完成「注册 + 拉起」，使打开开关后不再依赖手动执行安装脚本。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from ..config import PROJECT_ROOT, Config
from ..i18n import t

logger = logging.getLogger(__name__)

# 拉起待命进程时不新开控制台窗口（pythonw 本身无窗口，这里再兜一层）
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 当前用户开机启动项：写在 HKCU\...\Run，无需管理员权限
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "IM-Agent-Gateway-Watcher"
_WATCHER = PROJECT_ROOT / "watcher.py"


def _launcher() -> Path:
    """无控制台解释器：优先同目录 pythonw.exe，缺失时退回当前解释器。"""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return pythonw if pythonw.exists() else exe


def _set_run_entry(enabled: bool) -> None:
    """增删开机启动项；非 Windows 平台不处理。"""
    if os.name != "nt":
        return
    import winreg

    command = f'"{_launcher()}" "{_WATCHER}"'
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, _RUN_VALUE)
                except FileNotFoundError:
                    pass
    except OSError:
        logger.exception(t("看门狗开机自启注册失败", "Failed to register watchdog autostart"))
        return
    logger.info(
        t("看门狗开机自启已%s", "Watchdog autostart %s"),
        t("开启", "enabled") if enabled else t("关闭", "disabled"),
    )


def sync_autostart(cfg: Config) -> None:
    """按开关维护开机自启：WATCH_ENABLED 与 WATCH_AUTOSTART 同时开启才注册。"""
    _set_run_entry(cfg.watch_enabled and cfg.watch_autostart)


def spawn_watcher() -> None:
    """拉起待命进程（无窗口）；已有实例会被看门狗自身的进程锁挡掉。"""
    if not _WATCHER.is_file():
        return
    try:
        subprocess.Popen(
            [str(_launcher()), str(_WATCHER)],
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # pythonw 本身无控制台；CREATE_NO_WINDOW 再兜一层，且不能与
            # DETACHED_PROCESS 同时使用（两个控制台标志互斥，CreateProcess 会拒绝）
            creationflags=_NO_WINDOW,
            start_new_session=(os.name != "nt"),
            close_fds=True,
        )
    except OSError:
        logger.exception(t("拉起看门狗失败", "Failed to spawn the watchdog"))
        return
    logger.info(t("服务退出，已拉起看门狗待命", "Service exited; watchdog spawned for standby"))
