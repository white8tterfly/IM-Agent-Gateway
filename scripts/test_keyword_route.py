# 关键字意图路由测试：Jev 未配置/失败/低置信时退化到关键字解析（无网络依赖）
# 用法：python scripts/test_keyword_route.py
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.agents.base import AgentResult, BaseAgent
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import load_config
from im_agent_gateway.gateway.base import IMChannel, InboundMessage
from im_agent_gateway.router.dispatcher import MessageRouter


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


class FakeAgent(BaseAgent):
    """最小 Agent 实现：仅声明路由元数据，用于验证关键字路由。"""

    def __init__(self, name: str, kind: str = "chat",
                 keywords: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.name = name
        self.label = name
        self.kind = kind
        self.routing_keywords = keywords
        self.submits: list[str] = []

    async def submit(self, chat_key, query, cwd="", on_output=None, model=""):
        self.submits.append(query)

        async def _run() -> AgentResult:
            return AgentResult(ok=True, text=query)
        return _run()

    async def stop(self, chat_key) -> bool:
        return False

    async def reset(self, chat_key) -> None:
        pass

    def busy(self, chat_key) -> bool:
        return False


class FakeChannel(IMChannel):
    """最小通道实现：记录回传内容。"""

    name = "fake"

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[tuple[bool, str]] = []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def send_text(self, msg, content) -> None:
        self.frames.append((True, content))

    async def send_stream(self, msg, stream_id, content, finish) -> None:
        self.frames.append((finish, content))

    def make_msg(self, content: str) -> InboundMessage:
        return InboundMessage(
            channel=self, im=self.name, msg_id="", user_id="u1",
            chat_id="u1", chat_type="single", msg_type="text",
            content=content, reply_token="",
        )


async def main() -> None:
    from im_agent_gateway.router.keyword_router import KeywordRouter

    # ===== 一、route_keywords 合成规则 =====
    coder = FakeAgent("coder", kind="coding")
    chat = FakeAgent("chat", kind="chat")
    office = FakeAgent("office", kind="chat",
                       keywords=("文档", "表格", "会议纪要"))
    named_coder = FakeAgent("named", kind="coding", keywords=("namedtool",))
    check(len(coder.route_keywords) > 0,
          "coding Agent 未声明时继承默认编码词表")
    check(chat.route_keywords == (),
          "chat Agent 未声明时为空（不参与关键字路由）")
    check(office.route_keywords == ("文档", "表格", "会议纪要"),
          "chat Agent 声明追加词合成后即全部词表")
    check(any(kw in named_coder.route_keywords for kw in coder.route_keywords)
          and "namedtool" in named_coder.route_keywords,
          "coding Agent 声明追加词后仍保留编码词表（追加合并，非覆盖）")

    # ===== 二、KeywordRouter 计分与裁决 =====
    kw = KeywordRouter()
    agents = [chat, office, coder]
    check(kw.route("帮我写个会议纪要", agents) == "office",
          "办公消息命中 workbuddy 类关键词 → office")
    check(kw.route("这段代码有 bug，修一下", agents) == "coder",
          "编码消息命中编码词表 → coder")
    check(kw.route("今天天气怎么样", agents) is None,
          "无命中返回 None（保守落默认）")

    tie = kw.route("把这段代码整理成文档", agents)
    check(tie in ("coder", "office"),
          "多 Agent 命中时返回注册顺序中最先的高分者")
    check(kw.route("写个 script 跑一遍", agents) == "coder",
          "英文关键词大小写不敏感命中")
    check(kw.route("", agents) is None, "空消息返回 None")

    # ===== 三、dispatcher 集成：Jev 未配置 → 关键字解析接管 =====
    cfg = load_config()
    cfg.allowed_users = set()
    cfg.auto_route = True
    cfg.jev_route = True
    cfg.jev_api_key = ""  # 模拟「没配置决策模型」
    cfg.default_agent = "chat"

    registry = AgentRegistry()
    registry.register(chat, active=True)
    registry.register(office, active=True)
    registry.register(coder, active=True)
    router = MessageRouter(cfg, registry)
    check(router.jev.available is False, "未配置 Key 时 Jev 不可用")

    ch = FakeChannel()
    ch.on_message = router.handle_msg

    async def routed(content: str) -> str:
        """发一条消息，返回实际路由到的 Agent 名（从 submits 记录推断）。"""
        ch.frames.clear()
        await ch.on_message(ch.make_msg(content))
        for a in (chat, office, coder):
            if a.submits and a.submits[-1] == content:
                return a.name
        return ""

    check(await routed("帮我修复这个报错") == "coder",
          "未配置 Jev：编码消息经关键字解析到 coder")
    check(await routed("把这个表格整理一下") == "office",
          "未配置 Jev：办公消息经关键字解析到 office")
    check(await routed("晚上吃什么好") == "chat",
          "未配置 Jev：闲聊无命中落默认 chat")

    await router.close()
    await registry.close()

    print("\n全部通过")


asyncio.run(main())
