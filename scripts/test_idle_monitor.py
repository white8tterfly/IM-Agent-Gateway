# 测试空闲判定（is_idle）与唤醒消息的整条回传（replay → send_push，无网络依赖）
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.agents.base import AgentResult, BaseAgent
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import load_config
from im_agent_gateway.gateway.base import IMChannel, InboundMessage
from im_agent_gateway.i18n import set_language
from im_agent_gateway.router.dispatcher import MessageRouter


class FakeChannel(IMChannel):
    name = "fake"

    def __init__(self) -> None:
        super().__init__()
        self.streamed: list[str] = []   # send_stream 收到的内容
        self.pushed: list[str] = []     # send_push 收到的内容
        self._stop = asyncio.Event()

    async def start(self) -> None:
        await self._stop.wait()

    async def close(self) -> None:
        self._stop.set()

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        self.streamed.append(content)

    async def send_stream(self, msg: InboundMessage, stream_id: str, content: str, finish: bool) -> None:
        self.streamed.append(content)

    async def send_push(self, msg: InboundMessage, content: str) -> None:
        self.pushed.append(content)

    def make_msg(self, content: str) -> InboundMessage:
        return InboundMessage(
            channel=self, im=self.name, msg_id="m1", user_id="u1", chat_id="c1",
            chat_type="single", msg_type="text", content=content, reply_token="r1",
        )


class FakeAgent(BaseAgent):
    name = "fake_chat"
    label = "聊天 Fake"
    kind = "chat"

    @property
    def default_model(self) -> str:
        return "fake-model"

    async def submit(self, chat_key: str, query: str, cwd: str = "", on_output=None, model: str = ""):
        async def _done() -> AgentResult:
            if on_output is not None:
                await on_output(f"[fake:{query}]")
            return AgentResult(ok=True, text="done")

        return _done()

    async def stop(self, chat_key: str) -> bool:
        return False

    async def reset(self, chat_key: str) -> None:
        pass

    def busy(self, chat_key: str) -> bool:
        return False


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


async def main() -> None:
    cfg = load_config()
    set_language("zh")  # 断言中文文案：固定语言，避免受本机 .env 的 LANGUAGE 影响
    cfg.auto_route = False
    cfg.default_agent = "fake_chat"
    cfg.allowed_users = set()

    registry = AgentRegistry()
    agent = FakeAgent()
    registry.register(agent, active=True)
    router = MessageRouter(cfg, registry)
    ch = FakeChannel()
    ch.on_message = router.handle_msg

    # 一、空闲判定：无任务 + 超时阈值
    check(router.is_idle(0), "初始无任务且超时 0 秒视为空闲")
    check(not router.is_idle(3600), "未超过阈值时不空闲")
    router._inflight = 1
    check(not router.is_idle(0), "有任务在执行时不空闲")
    router._inflight = 0

    # 二、普通消息走流式（send_stream），不触发 send_push
    await ch.on_message(ch.make_msg("普通任务"))
    check(ch.streamed and not ch.pushed, "普通消息走 send_stream，不进 send_push")

    # 三、唤醒交棒消息（replay=True）走整条 send_push，不走 send_stream
    ch.streamed.clear()
    ch.pushed.clear()
    replay = ch.make_msg("唤醒任务")
    replay.replay = True
    await ch.on_message(replay)
    check(len(ch.pushed) == 1 and not ch.streamed, "replay 消息结果整条走 send_push，不流式")
    check("[fake:唤醒任务]" in ch.pushed[0], "send_push 内容含处理结果")
    check("· 聊天 Fake处理中" in ch.pushed[0], "send_push 内容含开始提示")

    await router.close()
    await registry.close()
    print("\n全部通过")


asyncio.run(main())