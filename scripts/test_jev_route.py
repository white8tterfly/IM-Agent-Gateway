# 本地验证 JevRouter：决策路由连通性（验证上一级目录 .env 的 MAKERS_MODELS_KEY）
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

from im_agent_gateway.agents.qoder.runner import QoderRunner
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.agents.siliconflow.agent import SiliconFlowAgent
from im_agent_gateway.agents.workbuddy.agent import WorkBuddyAgent
from im_agent_gateway.config import load_config
from im_agent_gateway.router.dispatcher import MessageRouter


async def main():
    cfg = load_config()
    registry = AgentRegistry()
    registry.register(SiliconFlowAgent(cfg), active=True)
    registry.register(QoderRunner(cfg), active=True)
    registry.register(WorkBuddyAgent(cfg), active=True)
    router = MessageRouter(cfg, registry)

    print("jev available:", router.jev.available,
          "| base_url:", cfg.jev_base_url, "| model:", cfg.jev_model,
          "| min_confidence:", cfg.jev_min_confidence)

    samples = [
        "帮我修复一下 main.py 的 bug，运行报错了",
        "今天天气怎么样？周末有什么好去处？",
        "帮我把这个 Word 文档的排版改一下",
        "git push 到 main 分支",
        "给我讲个笑话",
        "项目依赖冲突了，编译不过",
    ]
    for q in samples:
        agent = await router._resolve_agent("s:test", q)
        print(f"{q!r} -> {agent.name if agent else None}")

    await router.close()
    await registry.close()


asyncio.run(main())
