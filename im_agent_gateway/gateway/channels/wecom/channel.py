"""企微通道：IMChannel 实现，负责报文归一化与回复。"""

from __future__ import annotations

import logging
import re

from ...base import IMChannel, InboundEvent, InboundMessage
from ....config import Config
from .client import WeComBotClient

logger = logging.getLogger(__name__)

# 群聊里 @机器人 的前缀，如 "@Qoder 帮我..."，可能连续出现多个 @
_AT_PREFIX_RE = re.compile(r"^(\s*@[^\s@，,]{1,64})+")


class WeComChannel(IMChannel):
    """企微智能机器人通道。"""

    name = "wecom"

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self._client = WeComBotClient(
            bot_id=cfg.bot_id,
            secret=cfg.secret,
            ws_url=cfg.wecom_ws_url,
            heartbeat_interval=cfg.heartbeat_interval_sec,
        )
        # 协议客户端回调 → 归一化后转交路由层
        self._client.on_msg = self._on_raw_msg
        self._client.on_event = self._on_raw_event

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        await self._client.run_forever()

    async def close(self) -> None:
        await self._client.close()

    @property
    def connected(self) -> bool:
        # 服务被看门狗唤醒后重放消息前，用它确认长连接已就绪
        return self._client.connected.is_set()

    # ---------- 报文归一化 ----------

    async def _on_raw_msg(self, body: dict) -> None:
        if self.on_message is None:
            return
        await self.on_message(self._to_message(body))

    def _to_message(self, body: dict) -> InboundMessage:
        chattype = body.get("chattype", "single")
        content = (body.get("text") or {}).get("content", "")
        if chattype == "group":
            # 群聊需 @机器人，剥离 @ 前缀（可能连续多个 @）
            content = _AT_PREFIX_RE.sub("", content).strip()
        return InboundMessage(
            channel=self,
            im=self.name,
            msg_id=str(body.get("msgid", "")),
            user_id=(body.get("from") or {}).get("userid", ""),
            chat_id=str(body.get("chatid", "")),
            chat_type=chattype,
            msg_type=body.get("msgtype", "text"),
            content=content,
            reply_token=str(body.get("_req_id", "")),
        )

    async def _on_raw_event(self, body: dict) -> None:
        event = body.get("event") or {}
        eventtype = event.get("eventtype", "")
        if eventtype != "enter_chat":
            # template_card_event / feedback_event / disconnected_event 暂不处理
            return
        if self.on_event is None:
            return
        evt = InboundEvent(
            channel=self,
            im=self.name,
            event_type=eventtype,
            user_id=(body.get("from") or {}).get("userid", ""),
            chat_id=str(body.get("chatid", "")),
            reply_token=str(body.get("_req_id", "")),
        )
        await self.on_event(evt)

    # ---------- 回复 ----------

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        await self._client.send_text_reply(msg.reply_token, content)

    async def send_stream(self, msg: InboundMessage, stream_id: str, content: str, finish: bool) -> None:
        await self._client.send_stream(msg.reply_token, stream_id, content, finish)

    async def send_push(self, msg: InboundMessage, content: str) -> None:
        # 交棒后 reply_token 失效：按 chatid 主动推送，不依赖入站消息的连接
        chat_type = 2 if msg.chat_type == "group" else 1
        await self._client.send_push_markdown(msg.chat_id, chat_type, content)

    async def send_welcome(self, evt: InboundEvent, content: str) -> None:
        # 企微要求进入会话事件后 5 秒内用专用欢迎语指令回复
        await self._client.send_welcome(evt.reply_token, content)
