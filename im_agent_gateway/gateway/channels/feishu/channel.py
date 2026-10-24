"""飞书通道：IMChannel 实现，把飞书消息归一化为网关注入站消息并回复。

协议层（长连接鉴权、心跳、断线重连、消息归一化、发送）统一交给飞书
官方 SDK 的高层通道 ``lark_oapi.channel.FeishuChannel``，本类只负责把它的
入站消息桥接回网关路由层，并把网关的回复调用转成 SDK 的发送接口。

关键点：SDK 的 FeishuChannel 在独立的后台 event loop + 线程里运行，而网关
路由在主 asyncio loop 里运行，因此入站事件用 ``run_coroutine_threadsafe``
投递回主 loop，避免跨 loop 执行协程。
"""

from __future__ import annotations

import asyncio
import logging
import re

from lark_oapi.channel import FeishuChannel as LarkChannel

from ...base import IMChannel, InboundMessage
from ....config import Config

logger = logging.getLogger(__name__)

# 群聊里 @机器人（多人连续 @）的前缀，飞书侧 @ 已由 SDK 解析为「@名字」或
# 保留原始占位符（@_user_N），这里用与企微一致的正则把开头连续 @ 剥掉。
_AT_PREFIX_RE = re.compile(r"^(\s*@[^\s@，,]{1,64})+")


def _reset_lark_ws_loop() -> None:
    """清理 lark-oapi 模块级 ws 事件循环，防止旧连接的自动重连协程复活。

    lark-oapi 的 WS 客户端全部跑在 lark_oapi.ws.client 模块级全局 loop 上，
    且客户端没有公开的 stop 方法：断开连接后 auto_reconnect 重连协程仍挂
    在旧 loop 上。同一进程内若再次建立飞书连接（看门狗睡-醒循环就是这种
    场景），run_until_complete 会把旧 loop 一并跑起来，旧协程随即重连成一
    条挂着旧回调的僵尸长连接，与新连接抢飞书下发的消息。因此每次关闭通道
    时：先在旧 loop 上把残留任务全部取消并关闭该 loop，再给模块换上全新
    loop，旧协程从此没有运行环境，彻底废弃。
    """
    try:
        from lark_oapi.ws import client as ws_module

        old = getattr(ws_module, "loop", None)
        if old is None:
            return
        if not old.is_running() and not old.is_closed():
            # 此刻旧 loop 已被 SDK disconnect 停下（没有别的线程在跑），
            # 当前线程可以安全接手跑一小会儿，把残留任务取消干净

            async def _drain() -> None:
                # 取消全部残留任务（自动重连、心跳、缓存清理等），排除自身
                for t in asyncio.all_tasks(old):
                    if t is not asyncio.current_task():
                        t.cancel()
                # 留一点运行时间，让被取消的任务走完取消流程
                await asyncio.sleep(0.1)

            try:
                old.run_until_complete(_drain())
            except Exception:
                logger.debug("清理飞书 SDK 残留任务异常（忽略）", exc_info=True)
            try:
                old.close()
            except Exception:
                logger.debug("关闭飞书 SDK 旧事件循环异常（忽略）", exc_info=True)
        # 换上全新 loop：后续同进程再建连接时绑定新 loop，旧任务无处复活
        ws_module.loop = asyncio.new_event_loop()
    except Exception:
        logger.exception("重置飞书 SDK 事件循环失败")


class FeishuChannel(IMChannel):
    """飞书通道（企业自建应用，长连接接收事件模式）。"""

    name = "feishu"

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self._main_loop: asyncio.AbstractEventLoop | None = None
        # SDK 高层通道：内部维护后台 loop/线程，处理长连接与 HTTP 发送
        self._sdk = LarkChannel(
            app_id=cfg.feishu_app_id,
            app_secret=cfg.feishu_app_secret,
        )
        # 收到消息时（在 SDK 后台 loop 触发），桥接回主 loop 再交给路由层
        self._sdk.on("message", self._on_sdk_message)
        # 流式回复映射：目录 stream_id -> 飞书已发送消息的 message_id
        self._stream_messages: dict[str, str] = {}

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        # 记录主 loop，供后台线程把消息投递回来
        self._main_loop = asyncio.get_running_loop()
        logger.info("正在建立飞书长连接（App %s）", self._app_id_masked())
        # 阻塞式连接：SDK 在后台 loop 维持长连接，connect() 直到连接断开才返回，
        # 这样才能让 gateway.run 的 FIRST_COMPLETED 保持长期运行（与企微 run_forever 一致）。
        await self._sdk.connect()
        logger.info("飞书长连接已断开（App %s）", self._app_id_masked())

    async def close(self) -> None:
        # 优雅关闭：停止后台 loop 与 WS 连接，并废弃 SDK 残留在模块级
        # ws loop 上的自动重连协程（详见 _reset_lark_ws_loop 注释）
        await self._sdk.disconnect()
        _reset_lark_ws_loop()

    def _app_id_masked(self) -> str:
        # 日志里不完整暴露凭证，仅展示前缀便于排查
        app_id = getattr(self._sdk, "config", None)
        app_id = getattr(app_id, "app_id", "") if app_id else ""
        return app_id[:8] + "…" if app_id else "?"

    # ---------- 入站：SDK 事件 → 网关消息 ----------

    def _on_sdk_message(self, sdk_msg) -> None:
        """SDK 后台 loop 触发的同步回调：把消息投递回主 loop 处理。"""
        loop = self._main_loop
        if loop is None or self.on_message is None:
            return
        msg = self._to_message(sdk_msg)
        # 跨线程投递：在主 loop 里执行 on_message（否则路由层会在后台 loop 跑）
        asyncio.run_coroutine_threadsafe(self.on_message(msg), loop)

    def _to_message(self, m) -> InboundMessage:
        """把 SDK 归一化后的入站消息转成网关统一模型。"""
        # 飞书 chat_type: p2p=单聊，group/topic=群聊，其余按单聊处理
        chat_type = "group" if m.chat_type in ("group", "topic") else "single"
        content = m.content_text or ""
        if chat_type == "group":
            # 群聊消息以 @机器人 开头，剥离开头的 @ 提及
            content = _AT_PREFIX_RE.sub("", content).strip()
        # 优先使用 open_id 作为用户标识；没有则回退 user_id
        user_id = m.sender.open_id or m.sender.user_id or ""

        return InboundMessage(
            channel=self,
            im=self.name,
            msg_id=m.message_id or "",
            user_id=user_id,
            chat_id=m.chat_id or "",
            chat_type=chat_type,
            # 目前只处理文本消息；其他类型（图片/文件等）按文本兜底
            msg_type="text",
            content=content,
            # 回复时用到 message_id（reply_token 复用为协议 token）
            reply_token=m.message_id or "",
        )

    # ---------- 回复 ----------

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        """以文本回复到消息所在会话（chat_id）。"""
        await self._sdk.send(msg.chat_id, {"text": content})

    async def send_stream(self, msg: InboundMessage, stream_id: str, content: str, finish: bool) -> None:
        """流式回复：首帧发一条新消息，后续用编辑消息更新内容。

        飞书没有企微那种原生流式消息，这里用「发送一条文本消息 → 持续编辑」
        的方式近似实现打字机效果，对网关路由层保持 send_stream 契约一致。
        """
        message_id = self._stream_messages.get(stream_id)
        try:
            if message_id is None:
                # 首帧：发送新消息并记录 message_id 供后续编辑
                result = await self._sdk.send(msg.chat_id, {"text": content})
                if not result.success or not result.message_id:
                    logger.warning("飞书流式首帧发送失败: %s", getattr(result, "error", None))
                    return
                if not finish:
                    self._stream_messages[stream_id] = result.message_id
            else:
                # 后续帧：编辑已有消息
                await self._sdk.edit_message(message_id, {"text": content})
        finally:
            if finish:
                self._stream_messages.pop(stream_id, None)