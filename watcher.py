"""看门狗入口：监听 IM 消息，唤醒完整服务。

只加载 CHANNELS 声明的 IM 通道（不加载 Agent、不建路由），保持轻量；
收到首条消息时回占位、写交接文件、释放长连接并拉起服务，服务退出后
重新监听，形成「睡-醒」闭环。看门狗开关由 WATCH_ENABLED 控制。
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from im_agent_gateway.config import load_config
from im_agent_gateway.gateway.handoff import dump
from im_agent_gateway.gateway.registry import Gateway
from im_agent_gateway.plugins import load_channel

from main import setup_logging

logger = logging.getLogger("watcher")


def _service_running() -> bool:
    """检测是否已有服务进程在运行，避免唤醒时与手动启动的服务抢连接。"""
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\""
             " | Where-Object { $_.CommandLine -like '*main.py*' -and $_.CommandLine -like '*IM-Agent*' }"
             " | Measure-Object | Select-Object -ExpandProperty Count"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() not in ("", "0")
    except Exception:
        logger.exception("检测服务进程失败，按未运行处理")
        return False


async def _session(cfg) -> None:
    gateway = Gateway()
    wake_msg = None
    wake_event = asyncio.Event()

    async def on_message(msg) -> None:
        nonlocal wake_msg
        if not wake_event.is_set():
            wake_msg = msg
            wake_event.set()

    for spec in cfg.channels:
        channel = load_channel(spec, cfg)
        gateway.register(channel)
        channel.on_message = on_message

    run_task = asyncio.create_task(gateway.run())
    wake_task = asyncio.create_task(wake_event.wait())
    done, pending = await asyncio.wait(
        {run_task, wake_task}, return_when=asyncio.FIRST_COMPLETED,
    )

    woke = wake_task in done and wake_msg is not None
    already_running = _service_running() if woke else False
    if woke and not already_running:
        # 占位回复须在释放连接之前（此刻连接尚在，必达）
        try:
            await wake_msg.channel.send_text(wake_msg, cfg.wake_reply)
        except Exception:
            logger.exception("占位回复失败，继续唤醒")
        dump(wake_msg, cfg.wake_handoff_file_resolved)

    for task in pending:
        task.cancel()
    try:
        if run_task in done:
            run_task.result()  # 通道异常在此抛出
    except Exception:
        logger.exception("看门狗通道退出")
    await gateway.close()

    if not (woke and not already_running):
        return

    logger.info("唤醒服务：%s", cfg.service_cmd)
    proc = await asyncio.create_subprocess_shell(
        cfg.service_cmd, cwd=str(Path(__file__).parent),
    )
    await proc.wait()
    logger.info("服务已退出，重新监听")


async def amain() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level)
    if not cfg.watch_enabled:
        logger.info("看门狗开关未开启，退出")
        return
    logger.info("看门狗启动，监听通道：%s", ", ".join(cfg.channels))
    while True:
        try:
            await _session(cfg)
        except asyncio.CancelledError:
            raise
        except KeyboardInterrupt:
            raise
        except Exception:
            logger.exception("看门狗会话异常，稍后重连")
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