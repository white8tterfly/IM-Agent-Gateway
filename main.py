"""服务入口：组装网关、路由、Agent，启动桥接服务。"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path

from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import load_config
from im_agent_gateway.gateway.registry import Gateway
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
    router = MessageRouter(cfg, registry)

    # IM 接入：按 .env 的 CHANNELS 声明加载，实现 IMChannel 并暴露 Channel 类即可接入
    gateway = Gateway()
    for spec in cfg.channels:
        channel = load_channel(spec, cfg)
        gateway.register(channel)
        channel.on_message = router.handle_msg
        channel.on_event = router.handle_event

    logger.info("服务已启动：%d 个 IM 通道（%s），Agent：%s，工作目录 %s",
                len(gateway), ", ".join(gateway.channel_names),
                ", ".join(f"{n}{'启用' if registry.is_active(n) else '停用'}" for n in registry.names),
                cfg.qoder_cwd_resolved)

    try:
        await gateway.run()
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
