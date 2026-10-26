"""webui 通道实现：把浏览器当作第三个 IM 接入平台。

承载方式是本机 HTTP 服务（FastAPI + uvicorn）：
- 聊天走 /ws/chat WebSocket，报文在这里归一化为 InboundMessage 经 on_message 上抛，
  之后路由、白名单、排队、指令、流式回复与其他 IM 完全一致；
- 管理端点经可选契约 mount_admin 由 main.py 统一注入；
- 静态页面（管理台 / 聊天台）由同一 app 服务。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from ipaddress import ip_address
from typing import ClassVar
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ...base import IMChannel, InboundMessage
from ....config import Config

logger = logging.getLogger(__name__)

# 会话令牌头 / 查询参数名：前端 api.js、ws.js 与 manage.py 使用同一约定
TOKEN_HEADER = "x-webui-token"
TOKEN_QUERY = "token"



class WebUIChannel(IMChannel):
    """本机 HTTP 通道：浏览器即 IM，复用平台全部消息机制。"""

    name: ClassVar[str] = "webui"
    # HTTP 被动型通道由服务自身承载，看门狗无法（也不需要）独立监听它来唤醒服务
    wake_listen: ClassVar[bool] = False

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.host = cfg.webui_host
        self.port = cfg.webui_port
        self.user_id = cfg.webui_user_id
        self.static_dir = cfg.webui_static_resolved
        # reply_token -> WebSocket 连接：send_* 按 token 定位连接回推
        self._conns: dict[str, WebSocket] = {}
        self._server: uvicorn.Server | None = None
        self._admin_mounted = False   # 管理端点幂等挂载标志
        self._static_mounted = False  # 静态目录幂等挂载标志
        self._exit = asyncio.Event()  # HTTP 启动失败时的挂起等待（close 解除）
        # 本次进程的会话令牌：页面经同源 /api/session 获取，API 与 WS 均需携带，
        # 防止任意网页跨站调用本机管理端点（服务重启后旧令牌自动失效）
        self._token = secrets.token_urlsafe(32)

        self.app = FastAPI(title="IM-Agent Gateway 控制台")

        @self.app.get("/api/session")
        def session() -> JSONResponse:
            """下发会话令牌：跨站页面受同源策略限制，无法读取本响应。"""
            resp = JSONResponse({"token": self._token})
            resp.headers["Cache-Control"] = "no-store"
            return resp

        @self.app.middleware("http")
        async def guard_http(request, call_next):
            """管理端点守卫：校验 Host 与令牌，仅 /api/session 免令牌。"""
            path = request.url.path
            if path.startswith("/api/"):
                if not self._host_allowed(request.headers.get("host", "")):
                    return JSONResponse({"ok": False, "error": "禁止的访问来源"}, status_code=403)
                if path != "/api/session" and not self._token_ok(request.headers.get(TOKEN_HEADER)):
                    return JSONResponse({"ok": False, "error": "会话令牌缺失或失效"}, status_code=403)
            return await call_next(request)

        @self.app.websocket("/ws/chat")
        async def ws_chat(ws: WebSocket) -> None:
            """聊天端点：接收浏览器报文，归一化后上抛路由；回复经 send_* 推回。"""
            if not self._ws_allowed(ws):
                await ws.close(code=1008)  # 未通过令牌 / Origin 校验：拒绝握手
                return
            await ws.accept()
            token = uuid.uuid4().hex
            self._conns[token] = ws
            try:
                while True:
                    data = await ws.receive_json()
                    # 只处理有效文本帧，其余类型留给前端扩展（如 ping）或直接忽略
                    if not isinstance(data, dict) or data.get("type") != "text":
                        continue
                    content = str(data.get("content", "")).strip()
                    if not content:
                        continue
                    if self.on_message is None:
                        continue
                    msg = InboundMessage(
                        channel=self, im=self.name,
                        msg_id=uuid.uuid4().hex,
                        user_id=self.user_id,
                        chat_id="webui",
                        chat_type="single",
                        msg_type="text",
                        content=content,
                        reply_token=token,
                    )
                    # 串行等待路由处理（与 IM 通道同口径：同会话排队）
                    await self.on_message(msg)
            except (WebSocketDisconnect, RuntimeError):
                pass  # 客户端断开或重复断开，走下面统一摘除
            finally:
                self._conns.pop(token, None)

    # ---------- 安全防护 ----------

    @staticmethod
    def _host_only(hostport: str) -> str:
        """从 Host / Origin 中取出主机名（兼容 hostname、IPv4 与带方括号的 IPv6）。"""
        value = hostport.strip().lower()
        if value.startswith("["):
            end = value.find("]")
            return value[1:end] if end != -1 else value
        if value.count(":") > 1:
            return value
        return value.rsplit(":", 1)[0] if ":" in value else value

    def _host_allowed(self, hostport: str) -> bool:
        """只接受回环地址、显式绑定的主机，或通配绑定下的局域网 IP。

        通配绑定（0.0.0.0 / ::）时拒绝任意域名，避免 DNS rebinding 把攻击者域名
        解析到本机后读取管理端点。
        """
        host = self._host_only(hostport)
        if not host:
            return False
        if host in ("127.0.0.1", "localhost", "::1"):
            return True
        configured = self.host.strip().lower()
        if configured and configured not in ("0.0.0.0", "::"):
            return host == configured
        try:
            addr = ip_address(host)
        except ValueError:
            return False
        return addr.is_loopback or addr.is_private

    def _origin_allowed(self, origin: str) -> bool:
        """浏览器 Origin 必须是 http(s) 且主机在允许范围内。"""
        parsed = urlsplit(origin)
        return parsed.scheme in ("http", "https") and self._host_allowed(parsed.hostname or "")

    def _token_ok(self, token: str | None) -> bool:
        return bool(token) and secrets.compare_digest(token, self._token)

    def _ws_allowed(self, ws: WebSocket) -> bool:
        """WS 握手校验：必须携带有效令牌；浏览器来源还需 Origin 合法。

        令牌用于挡住跨站 WebSocket 劫持（CSWSH）：攻击者页面既拿不到令牌，
        Origin 也不是本机地址。
        """
        if not self._token_ok(ws.query_params.get(TOKEN_QUERY)):
            return False
        origin = ws.headers.get("origin", "")
        return not origin or self._origin_allowed(origin)

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        """启动 HTTP 服务并长期运行；由 Gateway 以 task 方式承载。"""
        # 静态挂载放在最后：Starlette 按注册顺序匹配，根路径 mount 会吞掉后注册的
        # 显式路由，因此必须等管理端点等显式路由全部就位后再挂
        self.mount_static()
        config = uvicorn.Config(self.app, host=self.host, port=self.port,
                                log_level="warning")
        self._server = uvicorn.Server(config)
        await self._server.serve()
        if not self._server.started:
            # 端口被占用等启动失败：只放弃本通道，不拖垮其他 IM 通道
            logger.error("webui HTTP 启动失败（端口 %s 可能被占用），Web 控制台不可用", self.port)
            await self._exit.wait()

    async def close(self) -> None:
        """优雅退出：解除挂起等待，停 HTTP 服务并断开全部 WebSocket。"""
        self._exit.set()
        if self._server is not None:
            self._server.should_exit = True
        for ws in list(self._conns.values()):
            try:
                await ws.close()
            except Exception:
                pass  # 连接已被客户端抢先断开等场景，忽略
        self._conns.clear()

    # ---------- 可选契约 ----------

    def mount_admin(self, api_router) -> None:
        """挂载管理端点（可选契约）：main.py 在注册通道后统一检查注入。幂等。"""
        if self._admin_mounted:
            return
        self._admin_mounted = True
        self.app.include_router(api_router)

    def mount_static(self) -> None:
        """挂载静态页面目录。幂等；须在管理端点等显式路由注册之后调用。"""
        if self._static_mounted:
            return
        self._static_mounted = True
        if self.static_dir.is_dir():
            # html=True：访问根路径时自动返回 index.html
            self.app.mount("/", StaticFiles(directory=self.static_dir, html=True),
                           name="static")
        else:
            logger.warning("webui 静态目录不存在：%s，页面不可用", self.static_dir)

    # ---------- 回复能力 ----------

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        await self._push(msg.reply_token, {"type": "text", "content": content})

    async def send_stream(self, msg: InboundMessage, stream_id: str,
                          content: str, finish: bool) -> None:
        await self._push(msg.reply_token, {
            "type": "stream", "id": stream_id,
            "content": content, "finish": finish,
        })

    async def _push(self, token: str, payload: dict) -> None:
        """按 reply_token 定位连接回推；连接已失效时静默丢弃。"""
        ws = self._conns.get(token)
        if ws is None:
            logger.info("webui 连接已关闭，丢弃回复（token=%s…）", token[:8])
            return
        try:
            await ws.send_json(payload)
        except Exception:
            # 连接失效（浏览器已关页等）：摘除，避免后续继续往死连接推
            self._conns.pop(token, None)
            logger.info("webui 推送失败，已摘除连接（token=%s…）", token[:8])

    # ---------- 观测 ----------

    @property
    def open_connections(self) -> int:
        """当前在线的聊天连接数（管理台 / 测试观测用）。"""
        return len(self._conns)
