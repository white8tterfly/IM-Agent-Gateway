"""看门狗入口：监听 IM 消息，唤醒完整服务。

只加载 CHANNELS 声明的 IM 通道（不加载 Agent、不建路由），保持轻量；
收到首条消息时回占位、写交接文件、释放长连接并拉起服务，服务退出后
重新监听，形成「睡-醒」闭环。看门狗开关由 WATCH_ENABLED 控制，
是否随开机启动由 WATCH_AUTOSTART 控制；计划任务用 pythonw 启动，
全程没有可见窗口，拉起服务时也用 CREATE_NO_WINDOW 避免弹窗。
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from pathlib import Path

from im_agent_gateway.config import load_config
from im_agent_gateway.gateway.handoff import RESTART_MARKER, dump
from im_agent_gateway.gateway.registry import Gateway
from im_agent_gateway.gateway.watch_http import WatchHttpServer
from im_agent_gateway.i18n import t
from im_agent_gateway.plugins import load_channel

from main import setup_logging

logger = logging.getLogger("watcher")

# Windows 下创建子进程时不新开控制台窗口。pythonw 启动的看门狗本身没有控制台，
# 若不指定，探测进程与拉起服务的 PowerShell / python 都会各弹一个黑窗。
# 非 Windows 平台该值为 0，Popen 同样接受。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _service_running() -> bool:
    """检测是否已有服务进程在运行，避免唤醒时与手动启动的服务抢连接。"""
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\""
             " | Where-Object { $_.CommandLine -like '*main.py*' }"
             " | Measure-Object | Select-Object -ExpandProperty Count"],
            capture_output=True, text=True, timeout=10,
            creationflags=_NO_WINDOW,
        )
        return result.stdout.strip() not in ("", "0")
    except Exception:
        logger.exception(t("检测服务进程失败，按未运行处理",
                           "Failed to detect the service process; assuming it is not running"))
        return False


def _restart_pending(max_age: float = 60.0) -> bool:
    """重启标记是否有效：服务正在换实例，端口即将由新实例接管。"""
    try:
        return time.time() - RESTART_MARKER.stat().st_mtime < max_age
    except OSError:
        return False


async def _await_handoff() -> None:
    """重启交接：等新实例接管端口，运行期间不抢端口、不重复连 IM。

    旧实例退出后看门狗若立刻托管 webui 端口，新实例 bind 会失败并直接退出。
    因此先等重启标记消失（新实例已拿到实例锁），再有服务在跑就让它自己监听，
    等它退出后才回到睡眠态。
    """
    if not _restart_pending():
        return
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and _restart_pending():
        await asyncio.sleep(0.3)
    while _service_running():
        await asyncio.sleep(5)


async def _session(cfg) -> None:
    # 端口交接：服务重启中先让新实例接管，避免看门狗抢回 webui 端口
    await _await_handoff()
    gateway = Gateway()
    wake_msg = None
    wake_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    async def on_message(msg) -> None:
        nonlocal wake_msg
        if not wake_event.is_set():
            wake_msg = msg
            wake_event.set()

    def _on_http_wake() -> None:
        # 由 HTTP 托管线程调用：跨线程安全地触发唤醒（与 IM 消息唤醒同一流程）
        loop.call_soon_threadsafe(wake_event.set)

    # 睡眠态托管：服务睡眠期间占住 webui 端口，页面可打开并提供唤醒按钮；
    # 端口与服务交接——唤醒后先停托管再拉服务，任一时刻只有一个进程占用
    holder = None
    if cfg.watch_http_enabled:
        holder = WatchHttpServer(
            static_dir=cfg.webui_static_resolved,
            host=cfg.webui_host, port=cfg.webui_port,
            on_wake=_on_http_wake,
        )
        try:
            holder.start()
            logger.info(t("睡眠态托管已启动：http://%s:%d（唤醒后自动交还端口）",
                          "Sleep-mode host started: http://%s:%d (port is handed over after wake)"),
                        cfg.webui_host, cfg.webui_port)
        except OSError:
            logger.exception(t("睡眠态托管启动失败（端口 %s 可能被占用）",
                               "Failed to start sleep-mode host (port %s may be in use)"),
                             cfg.webui_port)
            holder = None

    try:
        for spec in cfg.channels:
            channel = load_channel(spec, cfg)
            if not channel.wake_listen:
                # 通道自声明无需看门狗独立监听（如 HTTP 被动型 webui），跳过注册
                logger.info(t("跳过通道 %s（自声明无需看门狗监听）",
                              "Skipping channel %s (declares no watchdog listening needed)"),
                            channel.name)
                continue
            gateway.register(channel)
            channel.on_message = on_message

        tasks = {asyncio.create_task(wake_event.wait())}
        run_task = None
        if len(gateway) > 0:
            run_task = asyncio.create_task(gateway.run())
            tasks.add(run_task)
        else:
            logger.info(t("没有需要监听的 IM 通道，仅提供睡眠态托管",
                          "No IM channels to listen on; providing sleep-mode hosting only"))
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED,
        )

        # 唤醒来源：IM 消息或网页按钮，两者走同一后续流程
        woke = wake_event.is_set()
        already_running = _service_running() if woke else False
        if woke and not already_running and wake_msg is not None:
            # 占位回复须在释放连接之前（此刻连接尚在，必达）
            try:
                await wake_msg.channel.send_text(
                    wake_msg, cfg.wake_reply or t("正在唤醒…", "Waking up…"))
            except Exception:
                logger.exception(t("占位回复失败，继续唤醒",
                                   "Failed to send placeholder reply; continuing to wake the service"))
            dump(wake_msg, cfg.wake_handoff_file_resolved)

        for task in pending:
            task.cancel()
        try:
            if run_task is not None and run_task in done:
                run_task.result()  # 通道异常在此抛出
        except Exception:
            logger.exception(t("看门狗通道退出", "Watchdog channel exited"))
        await gateway.close()
    finally:
        if holder is not None:
            holder.stop()  # 端口交还：先停托管再拉服务，交接无冲突

    if not (woke and not already_running):
        return

    logger.info(t("唤醒服务：%s", "Waking service: %s"), cfg.service_cmd)
    proc = await asyncio.create_subprocess_shell(
        cfg.service_cmd, cwd=str(Path(__file__).parent),
        creationflags=_NO_WINDOW,
    )
    await proc.wait()
    logger.info(t("服务已退出，重新监听", "Service exited; listening again"))


async def amain() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level)
    if not cfg.watch_enabled:
        logger.info(t("看门狗开关未开启，退出", "Watchdog is disabled; exiting"))
        return
    if not cfg.watch_autostart:
        # 计划任务仍会在登录时拉起本进程，这里读配置后立即退出，即「不随开机启动」
        logger.info(t("开机自动启动已关闭，本次不启动看门狗",
                      "Autostart on boot is disabled; not starting the watchdog"))
        return
    logger.info(t("看门狗启动，监听通道：%s", "Watchdog started; listening on channels: %s"),
                ", ".join(cfg.channels))
    while True:
        try:
            await _session(cfg)
        except asyncio.CancelledError:
            raise
        except KeyboardInterrupt:
            raise
        except Exception:
            logger.exception(t("看门狗会话异常，稍后重连",
                               "Watchdog session failed; reconnecting shortly"))
            await asyncio.sleep(5)


def main() -> None:
    # 与 main.py 同理：飞书通道依赖的 lark-oapi 需在 asyncio.run 之前导入，
    # 避免运行时动态加载通道导致跨 loop 缓存报错。
    try:
        import lark_oapi  # noqa: F401
    except ImportError:
        pass
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()