# 两级路由编排测试：用途决策 → 用途内档位/顺序选择（MockTransport 打假 Jev，无网络依赖）
# 覆盖：固定路由不受影响、单用途短路、Jev/关键词退化、档位决策、声明顺序兜底
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from im_agent_gateway.agents.base import AgentResult, BaseAgent
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import Config
from im_agent_gateway.gateway.base import InboundMessage
from im_agent_gateway.router.dispatcher import MessageRouter
from im_agent_gateway.router.jev_router import JevRouter


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


class FakeAgent(BaseAgent):
    """最小 Agent 实现：仅携带路由元数据（用途 / 档位 / 名称）。"""

    def __init__(self, name: str, kind: str = "chat", complexity: str = "") -> None:
        super().__init__()
        self.name = name
        self.label = name
        self.kind = kind
        self.complexity = complexity

    async def submit(self, chat_key, query, cwd="", on_output=None, model=""):
        async def _run() -> AgentResult:
            return AgentResult(ok=True, text=query)
        return _run()

    async def stop(self, chat_key) -> bool:
        return False

    async def reset(self, chat_key) -> None:
        pass

    def busy(self, chat_key) -> bool:
        return False


def make_cfg(jev_key: str = "", complexity_route: bool = False,
             default_agent: str = "", auto_route: bool = True) -> Config:
    """干净测试配置：无真实凭证，Jev 与复杂度开关按参数注入。"""
    cfg = Config(bot_id="", secret="")
    cfg.allowed_users = set()
    cfg.auto_route = auto_route
    cfg.jev_route = True
    cfg.jev_api_key = jev_key
    cfg.jev_base_url = "http://fake"
    cfg.jev_model = "@makers/jev"
    cfg.jev_min_confidence = 0.5
    cfg.complexity_route = complexity_route
    cfg.default_agent = default_agent
    return cfg


def fake_jev(answer: dict) -> httpx.MockTransport:
    """假 Jev 网关：按请求里的 answers 键区分第一问 / 第二问。"""
    def _h(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        if "kind" in payload["questions"]:
            return httpx.Response(200, json={"answers": {"kind": answer["kind"]}})
        if "complexity" in payload["questions"]:
            return httpx.Response(200, json={"answers": {"complexity": answer["complexity"]}})
        return httpx.Response(200, json={"answers": {}})
    return httpx.MockTransport(_h)


def test_webui_command() -> None:
    """IM 指令 /webui：返回控制台地址，未开启时给出开启方式。"""
    cfg = make_cfg()
    cfg.webui_enabled = False
    cfg.channels = []
    router = MessageRouter(cfg, AgentRegistry())
    out = router._webui_text()
    check("WEBUI_ENABLED 1" in out, "webui 指令：未开启时提示开启方式")
    check("/webui" in router._help_text(), "帮助文本包含 /webui")

    # 通配绑定地址换回环地址，电脑端与手机端分开给出
    cfg2 = make_cfg()
    cfg2.webui_enabled = True
    cfg2.channels = ["webui"]
    cfg2.webui_host = "0.0.0.0"
    cfg2.webui_port = 8790
    out = MessageRouter(cfg2, AgentRegistry())._webui_text()
    check("电脑端 管理台：http://127.0.0.1:8790" in out, "webui 指令：电脑端管理台地址")
    check("http://127.0.0.1:8790/chat.html" in out, "webui 指令：电脑端聊天台地址")
    check("手机端" in out, "webui 指令：单独列出手机端地址")

    # 显式监听局域网地址时按配置输出，电脑端与手机端同一地址
    cfg2.webui_host = "192.168.1.8"
    out = MessageRouter(cfg2, AgentRegistry())._webui_text()
    check("手机端 聊天台：http://192.168.1.8:8790/chat.html" in out,
          "webui 指令：监听局域网地址时按配置输出手机端地址")


class FakeChannel:
    """只记录回复文本的假通道，用于验证 IM 指令分发。"""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, msg: InboundMessage, text: str) -> None:
        self.sent.append(text)


async def test_webui_dispatch() -> None:
    """发送 /webui 应直接回复控制台地址，不进入 Agent 路由。"""
    cfg = make_cfg()
    cfg.webui_enabled = True
    cfg.channels = ["webui"]
    router = MessageRouter(cfg, AgentRegistry())
    channel = FakeChannel()
    msg = InboundMessage(
        channel=channel, im="feishu", msg_id="m1", user_id="u1", chat_id="",
        chat_type="single", msg_type="text", content="/webui", reply_token="t1",
    )
    await router.handle_msg(msg)
    check(len(channel.sent) == 1 and "管理台" in channel.sent[0],
          "/webui 经消息入口分发并回复控制台地址")


async def main() -> None:
    test_webui_command()
    await test_webui_dispatch()

    # ===== 一、固定路由不受两级改造影响 =====
    cfg = make_cfg(default_agent="chat1")
    registry = AgentRegistry()
    chat1 = FakeAgent("chat1", kind="chat")
    coder = FakeAgent("coder", kind="coding")
    worker = FakeAgent("worker", kind="office")
    for a in (chat1, coder, worker):
        registry.register(a, active=True)
    router = MessageRouter(cfg, registry)
    router.chat_agents["s:u1"] = "coder"
    picked = await router._resolve_agent("s:u1", "帮我写个会议纪要")
    check(picked.name == "coder", "会话固定路由优先，不进两级决策")
    router.chat_agents.pop("s:u1")

    # ===== 二、单用途短路：全部同用途时跳过用途决策 =====
    cfg2 = make_cfg(default_agent="a")
    reg2 = AgentRegistry()
    reg2.register(FakeAgent("a", kind="office"), active=True)
    reg2.register(FakeAgent("b", kind="office"), active=True)
    router2 = MessageRouter(cfg2, reg2)
    picked = await router2._resolve_agent("s:u", "随便什么")
    check(picked.name == "a", "单用途：跳过用途决策，直接用途内选择")

    # ===== 三、多用途 + Jev 不可用 → 关键词用途级退化 =====
    # 无 Key：coding / office 消息按词表命中；无命中落 DEFAULT_AGENT 所在用途
    picked = await router._resolve_agent("s:u2", "这段代码有 bug，修一下")
    check(picked.name == "coder", "Jev 不可用：编码消息退化关键词 → coding Agent")
    picked = await router._resolve_agent("s:u2", "帮我把这个表格整理成文档")
    check(picked.name == "worker", "Jev 不可用：办公消息退化关键词 → office Agent")
    picked = await router._resolve_agent("s:u2", "晚上吃什么好")
    check(picked.name == "chat1", "Jev 不可用：无命中落 DEFAULT_AGENT 所在用途")

    # ===== 四、多用途 + Jev 可用：第一问选用途 =====
    cfg3 = make_cfg(jev_key="k", default_agent="chat1")
    reg3 = AgentRegistry()
    reg3.register(chat1, active=True)      # 同一实例重复用于不同 registry
    reg3.register(coder, active=True)
    reg3.register(worker, active=True)
    router3 = MessageRouter(cfg3, reg3)
    # Jev 判为 office → office 用途（worker）
    router3.jev = JevRouter(cfg3, transport=fake_jev({"kind": {
        "choice": "office", "probabilities": {"office": 0.9}}}))
    picked = await router3._resolve_agent("s:u3", "任意消息")
    check(picked.name == "worker", "Jev 第一问选用途 → 该用途唯一 Agent")

    # Jev 低置信 → 退化关键词计分
    router3.jev = JevRouter(cfg3, transport=fake_jev({"kind": {
        "choice": "coding", "probabilities": {"coding": 0.3}}}))
    picked = await router3._resolve_agent("s:u3", "这段代码有 bug，修一下")
    check(picked.name == "coder", "Jev 低置信：退化关键词用途计分")

    # Jev 全部失败（连接拒绝）→ 退化关键词
    router3.jev = JevRouter(cfg3, transport=httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused"))))
    picked = await router3._resolve_agent("s:u3", "帮我把这个表格整理成文档")
    check(picked.name == "worker", "Jev 请求失败：退化关键词用途计分")

    # ===== 五、用途内多 Agent：档位选择 =====
    # 同用途两个 Agent：COMPLEXITY_ROUTE=0 → 声明顺序第一个
    cfg4 = make_cfg(jev_key="k", default_agent="lite")
    lite = FakeAgent("lite", kind="chat")
    pro = FakeAgent("pro", kind="chat")
    reg4 = AgentRegistry()
    reg4.register(lite, active=True)
    reg4.register(pro, active=True)
    router4 = MessageRouter(cfg4, reg4)
    picked = await router4._resolve_agent("s:u4", "复杂的多步推理任务")
    check(picked.name == "lite", "单用途多 Agent：开关未开取声明顺序第一个")

    # COMPLEXITY_ROUTE=1 但 Jev 不可用 → 声明顺序第一个
    cfg5 = make_cfg(default_agent="lite", complexity_route=True)
    reg5 = AgentRegistry()
    reg5.register(FakeAgent("lite", kind="chat", complexity="simple"), active=True)
    reg5.register(FakeAgent("pro", kind="chat", complexity="complex"), active=True)
    router5 = MessageRouter(cfg5, reg5)
    picked = await router5._resolve_agent("s:u5", "很复杂的问题")
    check(picked.name == "lite", "复杂度开关开但 Jev 不可用：取声明顺序第一个")

    # COMPLEXITY_ROUTE=1 + Jev 可用 + 候选都已配档位 → 第二问选档位
    cfg6 = make_cfg(jev_key="k", default_agent="lite", complexity_route=True)
    reg6 = AgentRegistry()
    reg6.register(FakeAgent("lite", kind="chat", complexity="simple"), active=True)
    reg6.register(FakeAgent("mid", kind="chat", complexity="mid"), active=True)
    reg6.register(FakeAgent("pro", kind="chat", complexity="complex"), active=True)
    router6 = MessageRouter(cfg6, reg6)
    router6.jev = JevRouter(cfg6, transport=fake_jev({"kind": {}, "complexity": {
        "choice": "complex", "probabilities": {"complex": 0.9}}}))
    picked = await router6._resolve_agent("s:u6", "任意消息")
    check(picked.name == "pro", "复杂度第二问：Jev 判 complex → 档位 Agent")
    router6.jev = JevRouter(cfg6, transport=fake_jev({"kind": {}, "complexity": {
        "choice": "simple", "probabilities": {"simple": 0.9}}}))
    picked = await router6._resolve_agent("s:u6", "任意消息")
    check(picked.name == "lite", "复杂度第二问：Jev 判 simple → 档位 Agent")

    # Jev 判低置信 → 声明顺序第一个
    router6.jev = JevRouter(cfg6, transport=fake_jev({"kind": {}, "complexity": {
        "choice": "complex", "probabilities": {"complex": 0.2}}}))
    picked = await router6._resolve_agent("s:u6", "任意消息")
    check(picked.name == "lite", "复杂度第二问低置信：取声明顺序第一个")

    # 候选部分未配档位 → 不进 Jev，直接声明顺序
    cfg7 = make_cfg(jev_key="k", default_agent="lite", complexity_route=True)
    reg7 = AgentRegistry()
    reg7.register(FakeAgent("lite", kind="chat", complexity="simple"), active=True)
    reg7.register(FakeAgent("noev", kind="chat"), active=True)  # 未配档位
    router7 = MessageRouter(cfg7, reg7)
    calls: list[dict] = []

    def spy(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.read()))
        return httpx.Response(200, json={"answers": {}})
    router7.jev = JevRouter(cfg7, transport=httpx.MockTransport(spy))
    picked = await router7._resolve_agent("s:u7", "任意消息")
    check(picked.name == "lite", "候选未全配档位：不进复杂度决策取第一个")
    check(all("complexity" not in p["questions"] for p in calls),
          "候选未全配档位：未发出档位决策请求")

    # ===== 六、DEFAULT_AGENT 兜底与空活跃 =====
    # DEFAULT_AGENT 指向已关闭 Agent → 取活跃第一个
    cfg8 = make_cfg(default_agent="closed")
    reg8 = AgentRegistry()
    reg8.register(FakeAgent("closed", kind="chat"), active=False)
    reg8.register(FakeAgent("on1", kind="office"), active=True)
    router8 = MessageRouter(cfg8, reg8)
    picked = await router8._resolve_agent("s:u8", "消息")
    check(picked.name == "on1", "DEFAULT_AGENT 不在活跃：取活跃声明顺序第一个")

    # 全部关闭 → None
    reg9 = AgentRegistry()
    reg9.register(FakeAgent("x", kind="chat"), active=False)
    router9 = MessageRouter(make_cfg(), reg9)
    picked = await router9._resolve_agent("s:u9", "消息")
    check(picked is None, "无活跃 Agent 返回 None")

    # auto_route=0：直接 DEFAULT_AGENT（不经两级）
    cfg10 = make_cfg(default_agent="pro", auto_route=False)
    reg10 = AgentRegistry()
    reg10.register(FakeAgent("lite", kind="chat"), active=True)
    reg10.register(FakeAgent("pro", kind="coding"), active=True)
    router10 = MessageRouter(cfg10, reg10)
    picked = await router10._resolve_agent("s:u10", "帮我修 bug")
    check(picked.name == "pro", "auto_route=0：直接走 DEFAULT_AGENT")

    # 关闭资源
    for r in (router, router2, router3, router4, router5,
              router6, router7, router8, router9, router10):
        await r.close()
    for rg in (registry, reg2, reg3, reg4, reg5, reg6, reg7, reg8, reg9, reg10):
        await rg.close()

    print("\n全部通过")


asyncio.run(main())
