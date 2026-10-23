# Trae Agent 接入链路测试：验证「新增包 + AGENTS 声明」接入后注册、路由与真实任务执行。
# 开源版 trae-agent 无需登录，API 凭证经环境变量注入；离线部分无需网络。
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import load_config
from im_agent_gateway.plugins import load_agent
from im_agent_gateway.router.dispatcher import MessageRouter


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


async def main() -> None:
    cfg = load_config()
    cfg.allowed_users = set()
    cfg.jev_route = False

    # ===== 一、零框架改动接入：仅凭 AGENTS 声明加载 =====
    agent = load_agent("trae", cfg)
    check(agent.name == "trae" and agent.label == "Trae（编码）", "短名声明加载 TraeAgent")
    check("trae" in cfg.agents, ".env 的 AGENTS 声明包含 trae")

    registry = AgentRegistry()
    registry.register(agent, active=agent.available)
    check(registry.is_active("trae") == agent.available, "注册开关与 available 一致")
    if not agent.available:
        print("SKIP - trae-cli 不可用（检查 TRAE_BIN/TRAE_CONFIG 配置）")
        return

    router = MessageRouter(cfg, registry)
    help_text = router._help_text()
    check("【Trae（编码）】" in help_text, "/help 自动出现 Trae 指令分组")

    # ===== 二、真实任务（联网，经 SiliconFlow 端点） =====
    first = await agent.submit("s:smoke", "用一句话回答：1+1等于几？不要使用任何工具，直接回答。")
    result = await asyncio.wait_for(first, timeout=600)
    check(result.ok, "真实任务执行成功")
    print("--- text ---")
    print(result.text[:500])
    if result.usage:
        print("--- usage ---")
        print(result.usage)

    # 第二条验证网关层拼接的会话记忆（开源版无内置 resume）
    second = await agent.submit("s:smoke", "我上一条消息问了什么？用一句话回答。")
    result2 = await asyncio.wait_for(second, timeout=600)
    check(result2.ok, "会话记忆（历史拼接）正常")
    print("--- memory text ---")
    print(result2.text[:300])

    await agent.reset("s:smoke")
    await router.close()
    await registry.close()

    print("\nSMOKE_OK")


asyncio.run(main())
