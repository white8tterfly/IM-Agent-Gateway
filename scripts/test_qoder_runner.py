# 本地验证 QoderRunner：提交一个轻量编码任务，打印流式输出与最终结果
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
from im_agent_gateway.agents.qoder.runner import QoderRunner


async def main():
    cfg = load_config()
    runner = QoderRunner(cfg)

    async def on_output(text: str):
        print(f"[chunk] {text[:200]}", flush=True)

    future = await runner.submit(
        "test-chat", "查看当前目录下的文件列表，用一句话总结每个文件的用途", str(cfg.qoder_cwd_resolved),
        on_output=on_output,
    )
    result = await future
    print("=== RESULT ===")
    print("ok:", result.ok)
    print("session_id:", result.session_id)
    if result.error:
        print("error:", result.error)
    print("---text---")
    print(result.text)


asyncio.run(main())
