# 本地验证 SiliconFlowAgent：列出模型 + 一次流式聊天（验证 .env API_KEY 连通性）
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

from im_agent_gateway.config import load_config
from im_agent_gateway.agents.siliconflow.agent import SiliconFlowAgent


async def main():
    cfg = load_config()
    agent = SiliconFlowAgent(cfg)
    print("available:", agent.available, "| base_url:", cfg.sf_base_url, "| model:", cfg.sf_model)

    try:
        models = await agent.list_models()
        print(f"=== MODELS（共 {len(models)} 个，前 10 个）===")
        for m in models[:10]:
            print(" -", m)
    except Exception as exc:
        print("list_models 失败:", exc)

    async def on_output(text: str):
        print(text, end="", flush=True)

    print("\n=== CHAT ===")
    future = await agent.submit("test-chat", "用一句话介绍你自己", on_output=on_output)
    result = await future
    print("\n=== RESULT ===")
    print("ok:", result.ok)
    print("usage:", result.usage)
    if result.error:
        print("error:", result.error)

    await agent.close()


asyncio.run(main())
