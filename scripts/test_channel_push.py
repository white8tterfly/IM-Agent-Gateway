# 测试会话级回复契约 send_push：默认退化为 send_text，覆写后走主动推送（无网络依赖）
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.gateway.base import IMChannel, InboundMessage


class BaseChannel(IMChannel):
    name = "base"

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        self.sent.append(content)

    async def send_stream(self, msg: InboundMessage, stream_id: str, content: str, finish: bool) -> None:
        self.sent.append(content)


class PushChannel(BaseChannel):
    """覆写 send_push 的通道：结果走独立的主动推送，不走 send_text。"""

    def __init__(self) -> None:
        super().__init__()
        self.pushed: list[str] = []

    async def send_push(self, msg: InboundMessage, content: str) -> None:
        self.pushed.append(content)


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


async def main() -> None:
    msg = InboundMessage(
        channel=None, im="base", msg_id="", user_id="", chat_id="",
        chat_type="single", msg_type="text", content="", reply_token="",
    )

    base = BaseChannel()
    await base.send_push(msg, "hi")
    check(base.sent == ["hi"], "send_push 默认退化为 send_text")

    push = PushChannel()
    await push.send_push(msg, "yo")
    check(push.pushed == ["yo"] and not push.sent, "覆写 send_push 后只走主动推送")

    print("\n全部通过")


asyncio.run(main())