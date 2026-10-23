"""WorkBuddy Agent 冒烟测试：CLI 未安装时的降级行为 + stream-json 解析复用。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.agents.qoder.runner import _StreamParser
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.agents.workbuddy.agent import WorkBuddyAgent
from im_agent_gateway.config import load_config


async def amain() -> None:
    cfg = load_config()
    a = WorkBuddyAgent(cfg)
    registry = AgentRegistry()
    registry.register(a, active=True)

    print(f"name={a.name} kind={a.kind} label={a.label}")
    print(f"available={a.available} bin={cfg.workbuddy_bin} cwd={cfg.workbuddy_cwd}")
    print(f"permission_mode={cfg.workbuddy_permission_mode} timeout={cfg.workbuddy_timeout_sec}")
    print(f"active={registry.is_active('workbuddy')}")

    # CLI 未安装：list_models 降级空列表 + submit 可读错误
    if not a.available:
        models = await a.list_models()
        assert models == [], models
        print("list_models 降级返回 [] OK")
        fut = await a.submit("s:u1", "帮我整理这个表格", "", None, "")
        res = await fut
        assert not res.ok and "npm install" in (res.error or ""), res
        print(f"submit 降级错误 OK: {res.error}")
    else:
        # CLI 已安装：list_models 应可调用（登录态异常时降级空列表）
        models = await a.list_models()
        print(f"list_models OK: {models[:5]}")

    # 复用 stream-json 解析（Claude Code 底座协议）
    parser = _StreamParser()
    sid, text = parser.parse(
        '{"type":"assistant","session_id":"s1","message":{"id":"m1",'
        '"content":[{"type":"text","text":"已完成"}]}}'
    )
    assert sid == "s1" and text == "已完成", (sid, text)
    print("stream-json 解析复用 OK")

    print("SMOKE_OK")


if __name__ == "__main__":
    asyncio.run(amain())
