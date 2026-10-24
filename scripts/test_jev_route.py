# Jev 决策路由测试：
# 1) 离线断言段（httpx MockTransport 打假 API，回归可跑）：两级 choice 请求与解析、
#    单用途/单档位短路、低置信退化、HTTP 失败降级
# 2) 尾部真实冒烟段（验证上一级目录 .env 的 MAKERS_MODELS_KEY，无 Key 时只打印降级结果）
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

import httpx

from im_agent_gateway.config import Config


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


async def offline_tests() -> None:
    """无网络断言：两级 choice 请求与解析、短路与低置信退化。"""
    from im_agent_gateway.router.jev_router import JevRouter

    def jev_cfg() -> Config:
        cfg = Config(bot_id="", secret="")
        cfg.jev_api_key = "test-key"
        cfg.jev_route = True
        cfg.jev_base_url = "http://fake"
        cfg.jev_model = "@makers/jev"
        cfg.jev_min_confidence = 0.5
        return cfg

    # 假 API：记录收到的 payload，按应答表返回
    seen: list[dict] = []

    def handler(answer: dict) -> httpx.MockTransport:
        def _h(request: httpx.Request) -> httpx.Response:
            import json
            seen.append(json.loads(request.read()))
            return httpx.Response(200, json={"answers": answer})
        return httpx.MockTransport(_h)

    # 未配置 Key：available=False，直接 None（不发请求）
    cfg0 = jev_cfg()
    cfg0.jev_api_key = ""
    r = JevRouter(cfg0)
    check(await r.route_kind("修 bug", ["chat", "coding"]) is None,
          "jev 未配置 Key：route_kind 直接返回 None")
    check(await r.route_complexity("复杂问题", ["simple", "complex"]) is None,
          "jev 未配置 Key：route_complexity 直接返回 None")
    await r.close()

    # 第一问（用途决策）：正常解析 / 未知候选 / 低置信 / 短路
    cfg = jev_cfg()
    r = JevRouter(cfg, transport=handler({"kind": {
        "choice": "office", "probabilities": {"office": 0.9, "coding": 0.1}}}))
    check(await r.route_kind("帮我把这个表格整理成文档", ["chat", "coding", "office"]) == "office",
          "用途决策：概率最高且过阈值的用途胜出")
    payload = seen[-1]
    q = payload["questions"]["kind"]
    check(q["type"] == "choice" and set(q["criteria"]) == {"chat", "coding", "office"},
          "用途决策：choice 问题带全部候选用途 criteria")
    check(q["criteria"]["office"] != "office",
          "用途决策：criteria 是用途的一句话描述而非键名")
    await r.close()

    r = JevRouter(cfg, transport=handler({"kind": {
        "choice": "travel", "probabilities": {"travel": 0.99}}}))
    check(await r.route_kind("去哪玩", ["chat", "coding"]) is None,
          "用途决策：返回未知候选忽略（None）")
    await r.close()

    r = JevRouter(cfg, transport=handler({"kind": {
        "choice": "coding", "probabilities": {"coding": 0.3, "chat": 0.2}}}))
    check(await r.route_kind("写个脚本", ["chat", "coding"]) is None,
          "用途决策：最高置信低于阈值返回 None（调用方降级关键词）")
    await r.close()

    seen.clear()
    r = JevRouter(cfg, transport=handler({}))
    check(await r.route_kind("闲聊", ["chat"]) == "chat",
          "用途决策：单一用途短路返回，不发请求")
    check(seen == [], "用途决策：短路路径未产生 HTTP 请求")
    check(await r.route_kind("闲聊", []) is None, "用途决策：空候选返回 None")
    await r.close()

    # 第二问（复杂度档位决策）：正常解析 / 短路 / 低置信
    seen.clear()
    r = JevRouter(cfg, transport=handler({"complexity": {
        "choice": "complex", "probabilities": {"complex": 0.8, "simple": 0.2}}}))
    check(await r.route_complexity("重构整个系统并做性能优化",
                                   ["simple", "mid", "complex"]) == "complex",
          "档位决策：概率最高且过阈值的档位胜出")
    payload = seen[-1]
    q = payload["questions"]["complexity"]
    check(q["type"] == "choice" and set(q["criteria"]) == {"simple", "mid", "complex"},
          "档位决策：choice 问题带全部候选档位 criteria")
    check("复杂" in q["criteria"]["complex"],
          "档位决策：criteria 是档位语义描述而非键名")
    await r.close()

    seen.clear()
    r = JevRouter(cfg, transport=handler({}))
    check(await r.route_complexity("简单问题", ["mid"]) == "mid",
          "档位决策：单一档位短路返回，不发请求")
    check(seen == [], "档位决策：短路路径未产生 HTTP 请求")
    check(await r.route_complexity("问题", []) is None, "档位决策：空候选返回 None")
    await r.close()

    r = JevRouter(cfg, transport=handler({"complexity": {
        "choice": "mid", "probabilities": {"mid": 0.4, "simple": 0.3}}}))
    check(await r.route_complexity("中等问题", ["simple", "mid"]) is None,
          "档位决策：低置信返回 None（调用方取声明顺序第一个）")
    await r.close()

    # HTTP 失败（连接拒绝）与 5xx：两级都返回 None
    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    r = JevRouter(cfg, transport=httpx.MockTransport(dead))
    check(await r.route_kind("修 bug", ["chat", "coding"]) is None,
          "用途决策：连接失败降级返回 None")
    check(await r.route_complexity("复杂问题", ["simple", "complex"]) is None,
          "档位决策：连接失败降级返回 None")
    await r.close()

    r = JevRouter(cfg, transport=httpx.MockTransport(
        lambda req: httpx.Response(500, text="boom")))
    check(await r.route_kind("修 bug", ["chat", "coding"]) is None,
          "用途决策：HTTP 500 降级返回 None")
    await r.close()


async def main():
    # 第一段：离线断言（无网络依赖，回归门槛）
    await offline_tests()
    print("全部通过")

    # 第二段：真实冒烟（验证上一级目录 .env 的 MAKERS_MODELS_KEY；无 Key 时走降级打印）
    from im_agent_gateway.agents.qoder.runner import QoderRunner
    from im_agent_gateway.agents.registry import AgentRegistry
    from im_agent_gateway.agents.siliconflow.agent import SiliconFlowAgent
    from im_agent_gateway.agents.workbuddy.agent import WorkBuddyAgent
    from im_agent_gateway.config import load_config
    from im_agent_gateway.router.dispatcher import MessageRouter

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
