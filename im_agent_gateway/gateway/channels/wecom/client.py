"""企微智能机器人 WebSocket 协议客户端。

仅负责协议层：连接、订阅、心跳、收发帧、断线重连。
协议参考: https://developer.work.weixin.qq.com/document/path/101463
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Awaitable, Callable

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from ....i18n import t

logger = logging.getLogger(__name__)

# 连接断开（如：同一机器人被新连接踢下线）时的 WebSocket 关闭码
KICKED_CLOSE_CODE = 4001
NORMAL_CLOSE_CODES = {1000, 1001, KICKED_CLOSE_CODE}


def new_req_id() -> str:
    return uuid.uuid4().hex


class WeComBotClient:
    """维护一条到企业微信智能机器人服务的 WebSocket 长连接。"""

    def __init__(
        self,
        bot_id: str,
        secret: str,
        ws_url: str = "wss://openws.work.weixin.qq.com",
        heartbeat_interval: float = 30.0,
        on_msg: Callable[[dict], Awaitable[None]] | None = None,
        on_event: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self.bot_id = bot_id
        self.secret = secret
        self.ws_url = ws_url
        self.heartbeat_interval = heartbeat_interval
        self.on_msg = on_msg
        self.on_event = on_event

        self._ws: ClientConnection | None = None
        self._stop = asyncio.Event()
        # req_id -> Future，用于订阅等请求/响应对应
        self._pending: dict[str, asyncio.Future[dict]] = {}
        self.connected = asyncio.Event()

    # ---------- 生命周期 ----------

    async def run_forever(self) -> None:
        """连接、订阅、维持心跳；断线后指数退避重连。"""
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._session()
                backoff = 1.0  # 正常断开（被踢/服务端关闭）后快速重连
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(t("长连接异常，%.0f 秒后重连",
                                   "Long connection error; reconnecting in %.0f seconds"), backoff)
            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                backoff = min(backoff * 2, 60)

    async def _session(self) -> None:
        logger.info(t("连接 %s ...", "Connecting to %s ..."), self.ws_url)
        async with websockets.connect(
            self.ws_url,
            open_timeout=15,
            # 禁用协议层 ping/pong：企微网关不回 Pong（35 秒后判死断连），
            # 且对协议层 ping 帧可能报 invalid opcode/incorrect masking，
            # 心跳改用应用层 JSON ping（见 _heartbeat_loop）
            ping_interval=None,
            close_timeout=5,
            max_size=4 * 1024 * 1024,
            # 企微网关不支持 permessage-deflate：大消息触发压缩帧（RSV1 位）
            # 会被服务端以 1002 "reserved bits must be 0" 踢掉，必须禁用压缩
            compression=None,
        ) as ws:
            self._ws = ws
            # 必须先启动收包循环再发订阅，否则订阅响应无人接收
            recv_task = asyncio.create_task(self._recv_loop(ws))
            try:
                await self._subscribe(ws)
                self.connected.set()
                logger.info(t("长连接建立，已订阅机器人 %s",
                              "Long connection established; subscribed to bot %s"), self.bot_id)

                beat_task = asyncio.create_task(self._heartbeat_loop(ws))
                try:
                    done, pending = await asyncio.wait(
                        {recv_task, beat_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        task.result()  # 抛出异常（如有）
                finally:
                    self.connected.clear()
            finally:
                self._ws = None
                for fut in self._pending.values():
                    if not fut.done():
                        fut.cancel()
                self._pending.clear()
                if not recv_task.done():
                    recv_task.cancel()
                    try:
                        await recv_task
                    except (asyncio.CancelledError, ConnectionClosed):
                        pass

    async def _subscribe(self, ws: ClientConnection) -> None:
        req_id = new_req_id()
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await ws.send(json.dumps({
            "cmd": "aibot_subscribe",
            "headers": {"req_id": req_id},
            "body": {"bot_id": self.bot_id, "secret": self.secret},
        }, ensure_ascii=False))
        try:
            resp = await asyncio.wait_for(fut, timeout=15)
        except asyncio.TimeoutError as exc:
            raise ConnectionError("订阅超时：请检查 BOT_ID/SECRET 与网络") from exc
        if resp.get("errcode") != 0:
            raise ConnectionError(f"订阅失败: {resp.get('errmsg')} (errcode={resp.get('errcode')})")

    async def _recv_loop(self, ws: ClientConnection) -> None:
        try:
            async for raw in ws:
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(t("收到非 JSON 报文: %.200s", "Received a non-JSON frame: %.200s"), raw)
                    continue
                await self._handle_frame(frame)
        except ConnectionClosed as exc:
            # 被踢下线时服务端会先发 disconnected_event 再断开，这里安静退出等待重连
            if exc.code in NORMAL_CLOSE_CODES:
                logger.info(t("连接关闭 code=%s reason=%s", "Connection closed code=%s reason=%s"),
                            exc.code, exc.reason)
            else:
                logger.warning(t("连接异常关闭 code=%s reason=%s",
                                 "Connection closed unexpectedly code=%s reason=%s"),
                               exc.code, exc.reason)
            raise
        finally:
            logger.info(t("收包循环结束", "Receive loop ended"))

    async def _handle_frame(self, frame: dict) -> None:
        cmd = frame.get("cmd")
        headers = frame.get("headers") or {}
        req_id = headers.get("req_id") or ""

        # 订阅等请求的响应：企微订阅响应帧无 cmd 字段，仅按 req_id 关联
        if req_id in self._pending:
            fut = self._pending.pop(req_id)
            if not fut.done():
                fut.set_result(frame)
            return

        if cmd == "aibot_msg_callback":
            body = frame.get("body") or {}
            logger.info(
                t("收到消息 msgid=%s chattype=%s from=%s msgtype=%s",
                  "Received message msgid=%s chattype=%s from=%s msgtype=%s"),
                body.get("msgid"), body.get("chattype"),
                (body.get("from") or {}).get("userid"), body.get("msgtype"),
            )
            logger.info(t("消息帧 headers=%s", "Message frame headers=%s"),
                        json.dumps(headers, ensure_ascii=False))
            if self.on_msg:
                await self._safe_call(self.on_msg, self._wrap(body, req_id))
        elif cmd == "aibot_event_callback":
            body = frame.get("body") or {}
            event = body.get("event") or {}
            logger.info(t("收到事件 eventtype=%s", "Received event eventtype=%s"), event.get("eventtype"))
            if self.on_event:
                await self._safe_call(self.on_event, self._wrap(body, req_id))
        elif cmd == "pong":
            pass
        else:
            # 响应帧（如回复/推送后的 errcode）没有 cmd 字段，打印便于排查
            logger.info(t("其他帧: %.400s", "Other frame: %.400s"),
                        json.dumps(frame, ensure_ascii=False))

    @staticmethod
    def _wrap(body: dict, req_id: str) -> dict:
        body = dict(body)
        body["_req_id"] = req_id  # 回复时必须透传
        return body

    async def _safe_call(self, handler, msg: dict) -> None:
        """业务回调异常只记录日志，不影响长连接。"""
        try:
            await handler(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(t("处理消息时发生异常: %.200s", "Error while handling message: %.200s"),
                             str(msg)[:200])

    async def _heartbeat_loop(self, ws: ClientConnection) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                await ws.send(json.dumps({
                    "cmd": "ping",
                    "headers": {"req_id": new_req_id()},
                }))
                logger.debug(t("心跳已发送", "Heartbeat sent"))
            except ConnectionClosed:
                logger.info(t("心跳发送失败，连接已断开", "Heartbeat send failed; connection closed"))
                return

    # ---------- 发送 ----------

    async def _send(self, payload: dict) -> None:
        ws = self._ws
        if ws is None or not self.connected.is_set():
            raise ConnectionError("长连接未就绪")
        logger.debug(t("发送 %s", "Sending %s"), payload.get("cmd"))
        await ws.send(json.dumps(payload, ensure_ascii=False))

    async def send_text_reply(self, req_id: str, content: str) -> None:
        """文本回复：企微不支持 msgtype=text，改用 finish=true 的流式消息承载。"""
        await self._send({
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": req_id},
            "body": {
                "msgtype": "stream",
                "stream": {"id": new_req_id(), "finish": True, "content": content},
            },
        })

    async def send_stream(self, req_id: str, stream_id: str, content: str, finish: bool) -> None:
        """发送/刷新流式消息；finish=True 结束该流。"""
        await self._send({
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": req_id},
            "body": {
                "msgtype": "stream",
                "stream": {"id": stream_id, "finish": finish, "content": content},
            },
        })

    async def send_welcome(self, req_id: str, content: str) -> None:
        """回复进入会话事件的欢迎语（须在事件后 5 秒内）。"""
        await self._send({
            "cmd": "aibot_respond_welcome_msg",
            "headers": {"req_id": req_id},
            "body": {"msgtype": "text", "text": {"content": content}},
        })

    async def send_push_markdown(self, chatid: str, chat_type: int, content: str) -> None:
        """主动推送 markdown 消息（chat_type: 1=单聊 2=群聊）。"""
        await self._send({
            "cmd": "aibot_send_msg",
            "headers": {"req_id": new_req_id()},
            "body": {
                "chatid": chatid,
                "chat_type": chat_type,
                "msgtype": "markdown",
                "markdown": {"content": content},
            },
        })

    async def close(self) -> None:
        self._stop.set()
        if self._ws is not None:
            await self._ws.close()
