"""网关通道抽象：IMChannel 接口与归一化消息模型。

新 IM 实现 IMChannel 后 register 即接入，路由层不感知具体协议。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable, ClassVar


@dataclass
class InboundMessage:
    """归一化入站消息。"""

    channel: IMChannel  # 回发通道
    im: str             # 通道名（如 "wecom"）
    msg_id: str
    user_id: str
    chat_id: str
    chat_type: str      # "single" | "group"
    msg_type: str       # "text" / "image" / ...
    content: str        # 纯文本（群聊 @ 前缀已在通道层剥离）
    reply_token: str    # 回复所需的协议 token
    replay: bool = False  # 看门狗唤醒后交棒的待处理消息；结果走 send_push 整条回传


@dataclass
class InboundEvent:
    """归一化入站事件（如进会话）。"""

    channel: IMChannel
    im: str
    event_type: str     # "enter_chat" 等
    user_id: str
    chat_id: str
    reply_token: str


class IMChannel(ABC):
    """IM 通道接口：通道负责协议连接与报文归一化，回复走 send_* 系列方法。"""

    name: ClassVar[str] = ""

    def __init__(self) -> None:
        self.on_message: Callable[[InboundMessage], Awaitable[None]] | None = None
        self.on_event: Callable[[InboundEvent], Awaitable[None]] | None = None

    @property
    def connected(self) -> bool:
        """通道长连接是否就绪；默认视为就绪（如用 HTTP 发送的通道）。"""
        return True

    # ---------- 生命周期 ----------

    @abstractmethod
    async def start(self) -> None:
        """建立连接并长期运行，直到被关闭或内部退出。"""

    @abstractmethod
    async def close(self) -> None:
        """优雅退出：断开连接、停止重连。"""

    # ---------- 回复能力 ----------

    @abstractmethod
    async def send_text(self, msg: InboundMessage, content: str) -> None:
        """以文本回复某条入站消息。"""

    @abstractmethod
    async def send_stream(self, msg: InboundMessage, stream_id: str, content: str, finish: bool) -> None:
        """发送/刷新流式消息；finish=True 结束该流。"""

    async def send_push(self, msg: InboundMessage, content: str) -> None:
        """按会话身份（chat_id/chat_type）主动回传，不依赖连接期 reply_token。

        用于看门狗唤醒服务后的结果回传：服务已重建连接，旧 reply_token 可能失效。
        默认退化为 send_text——本就按 chat_id 回复的通道无需覆写；需按 chatid
        主动推送的通道（如企微）覆写本方法即可。
        """
        await self.send_text(msg, content)

    async def send_welcome(self, evt: InboundEvent, content: str) -> None:
        """回复进入会话事件；默认退化为 send_text，企微需覆写用专用欢迎语指令。"""
        msg = InboundMessage(
            channel=self, im=self.name,
            msg_id="", user_id=evt.user_id, chat_id=evt.chat_id,
            chat_type="single", msg_type="text",
            content="", reply_token=evt.reply_token,
        )
        await self.send_text(msg, content)
