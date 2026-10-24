"""服务入口：组装网关、路由、Agent，启动桥接服务。"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import time
from pathlib import Path

from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import Config, load_config
from im_agent_gateway.gateway.base import IMChannel, InboundMessage
from im_agent_gateway.gateway.handoff import load as load_handoff
from im_agent_gateway.gateway.registry import Gateway
from im_agent_gateway.manage.api import create_router
from im_agent_gateway.manage.service import ManageService
from im_agent_gateway.plugins import load_agent, load_channel
from im_agent_gateway.router.dispatcher import MessageRouter

LOG_DIR = Path(__file__).parent / "logs"


def setup_logging(level: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    # Windows 控制台可能是 GBK 编码，避免日志中的特殊字符导致崩溃
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_DIR / "im_agent_gateway.log", encoding="utf-8"),
        ],
    )


def check_prereqs(cfg) -> None:
    """按平台接入声明（AGENTS）检查已声明 Agent 的 CLI 可用性。

    注册制下，Agent 通过 AGENTS 声明参与接入；这里只对「已声明」的 Agent 提示
    CLI 缺失（warning，不退出），实际启停由各 Agent 的 available 属性决定。
    未在 AGENTS 中声明的 Agent 不检查、不加载、不参与路由。
    """
    logger = logging.getLogger("main")
    if "qoder" in cfg.agents and shutil.which(cfg.qoder_bin) is None:
        logger.warning("未找到 %s，Qoder CN（编码）不可用；安装：npm install -g @qodercn-ai/qoderclicn", cfg.qoder_bin)
    if "workbuddy" in cfg.agents and shutil.which(cfg.workbuddy_bin) is None:
        logger.warning("未找到 %s，WorkBuddy（办公）不可用；安装：npm install -g @tencent-ai/codebuddy-code", cfg.workbuddy_bin)
    if not cfg.qoder_cwd_resolved.is_dir():
        logger.warning("工作目录不存在: %s（可用 /cd 切换）", cfg.qoder_cwd_resolved)


async def replay_pending(router: MessageRouter, gateway: Gateway, cfg: Config) -> None:
    """重放看门狗交接的唤醒消息：重建 InboundMessage 并交给路由处理。"""
    raw = load_handoff(cfg.wake_handoff_file_resolved)
    if raw is None:
        return
    logger = logging.getLogger("main")
    channel = gateway.get(raw.get("im", ""))
    if channel is None:
        logger.warning("交接消息的通道 %s 未注册，忽略唤醒消息", raw.get("im"))
        return
    msg = InboundMessage(
        channel=channel,
        im=raw.get("im", ""),
        msg_id=raw.get("msg_id", ""),
        user_id=raw.get("user_id", ""),
        chat_id=raw.get("chat_id", ""),
        chat_type=raw.get("chat_type", "single"),
        msg_type=raw.get("msg_type", "text"),
        content=raw.get("content", ""),
        reply_token=raw.get("reply_token", ""),
        replay=True,
    )
    # 等服务长连接就绪（有限等待），避免结果回传时尚未连上
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not channel.connected:
        await asyncio.sleep(0.5)
    logger.info("重放唤醒消息 im=%s chat=%s", msg.im, msg.chat_id)
    await router.handle_msg(msg)


async def idle_monitor(router: MessageRouter, gateway: Gateway, cfg: Config) -> None:
    """空闲自动退：空闲超阈值且无任务时关闭网关，触发服务退出。"""
    logger = logging.getLogger("main")
    while True:
        await asyncio.sleep(10)
        if router.is_idle(cfg.idle_exit_min * 60):
            logger.info("空闲超过 %.0f 分钟，服务自动退出", cfg.idle_exit_min)
            await gateway.close()
            return


# ---------- 启动流程 ----------

async def amain() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level)
    logger = logging.getLogger("main")
    check_prereqs(cfg)

    # Agent 接入：按 .env 的 AGENTS 声明加载，实现 BaseAgent 并暴露 Agent 类即可接入
    registry = AgentRegistry()
    for spec in cfg.agents:
        agent = load_agent(spec, cfg)
        registry.register(agent, active=cfg.agent_enabled(agent.name) and agent.available)
    # 用途/档位覆写：全部注册完成后按 AGENT_KINDS / AGENT_COMPLEXITY 统一应用一次
    registry.apply_meta_overrides(cfg.agent_kinds, cfg.agent_complexity)
    router = MessageRouter(cfg, registry)

    # IM 接入：按 .env 的 CHANNELS 声明加载，实现 IMChannel 并暴露 Channel 类即可接入
    gateway = Gateway()
    for spec in cfg.channels:
        channel = load_channel(spec, cfg)
        gateway.register(channel)
        channel.on_message = router.handle_msg
        channel.on_event = router.handle_event

    # 管理服务：聚合平台状态供 Web 管理端点与 CLI 查询；
    # 路由层任务结束时上报最近任务（环形缓冲），webui 通道开启时挂载 REST 端点
    manage = ManageService(cfg, registry, router, gateway=gateway)
    router.on_task_event = manage.on_task_event

    # 管理端点注入：通道自声明 mount_admin 能力即挂载（可选契约，webui 实现；
    # 未实现该方法的通道不受影响，与 send_push 的可选覆写模式一致）
    admin_router = create_router(manage)
    for ch_name in gateway.channel_names:
        mount = getattr(gateway.get(ch_name), "mount_admin", None)
        if callable(mount):
            mount(admin_router)

    logger.info("服务已启动：%d 个 IM 通道（%s），Agent：%s，工作目录 %s",
                len(gateway), ", ".join(gateway.channel_names),
                ", ".join(f"{n}{'启用' if registry.is_active(n) else '停用'}" for n in registry.names),
                cfg.qoder_cwd_resolved)

    try:
        run_task = asyncio.create_task(gateway.run())
        # 看门狗唤醒交接：等服务建立长连接后重放待处理消息
        await replay_pending(router, gateway, cfg)
        if cfg.idle_exit:
            idle_task = asyncio.create_task(idle_monitor(router, gateway, cfg))
            done, pending = await asyncio.wait(
                {run_task, idle_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                task.result()  # 抛出异常（如有）
        else:
            await run_task
    except KeyboardInterrupt:
        logger.info("收到退出信号")
    finally:
        await gateway.close()
        await router.close()
        await registry.close()


def main() -> None:
    # 飞书通道依赖的 lark-oapi 必须在 asyncio.run 之前导入：其长连接客户端
    # 在 import 时会用 asyncio.get_event_loop() 缓存一个 loop，若等到 asyncio.run
    # 运行期间（动态加载通道时）才导入，会缓存到正在运行的 loop，导致启动报
    # "This event loop is already running"。提前导入可让它缓存到尚未运行的独立 loop。
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
