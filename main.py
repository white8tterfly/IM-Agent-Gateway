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
    """启动前检查 CLI 可用性与工作目录。"""
    logger = logging.getLogger("main")
    bin_path = shutil.which(cfg.qoder_bin)
    if bin_path is None:
        logger.error("未找到 %s，请先安装 Qoder CN CLI（npm install -g @qodercn-ai/qoderclicn）", cfg.qoder_bin)
        sys.exit(1)
    logger.info("Qoder CN CLI: %s", bin_path)
    if not cfg.qoder_cwd_resolved.is_dir():
        logger.warning("工作目录不存在: %s（可用 /cd 切换）", cfg.qoder_cwd_resolved)
    if shutil.which(cfg.workbuddy_bin) is None:
        logger.warning("未找到 %s，WorkBuddy（办公）不可用；安装：npm install -g @tencent-ai/codebuddy-code", cfg.workbuddy_bin)


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
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
