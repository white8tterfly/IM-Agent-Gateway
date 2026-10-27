"""看门狗睡眠态 HTTP 托管：服务睡眠期间占住 webui 端口的轻量页面服务。

只提供三件事（标准库 http.server，不依赖 FastAPI）：
- 服务与 webui 通道同一份静态目录（管理台 / 聊天台页面可打开）；
- GET /api/watch-state：告诉前端「服务正在睡眠」；
- POST /api/wake：唤醒按钮入口，回调触发后由看门狗停托管、拉起服务。
"""

from __future__ import annotations

import json
import logging
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from ..i18n import t

logger = logging.getLogger(__name__)


def _make_handler(directory: Path, on_wake: Callable[[], None]):
    """构建请求处理器：API 路径优先，其余落到静态目录。"""

    class Handler(SimpleHTTPRequestHandler):
        # 静态根目录（SimpleHTTPRequestHandler 3.7+ 支持构造注入）
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/api/watch-state":
                # 睡眠态标志：前端探测到它即切换「服务正在休息」界面
                self._json({"state": "sleeping"})
                return
            super().do_GET()  # 静态文件（含根路径 index.html；缺失路径 404）

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/api/wake":
                try:
                    on_wake()
                except Exception:
                    logger.exception(t("唤醒回调执行失败", "Wake callback failed"))
                self._json({"ok": True})
                return
            self.send_error(404)

        def _json(self, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            pass  # 托管期间静默访问日志，避免刷屏

    return partial(Handler, directory=str(directory))


class WatchHttpServer:
    """睡眠态托管服务：独立线程承载，start / stop 幂等，端口用完即还。"""

    def __init__(self, static_dir: Path, host: str, port: int,
                 on_wake: Callable[[], None]) -> None:
        self._handler = _make_handler(Path(static_dir), on_wake)
        self._host = host
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动托管线程；幂等，端口被占用时抛 OSError（由调用方决定降级）。"""
        if self._thread is not None:
            return
        self._httpd = ThreadingHTTPServer((self._host, self._port), self._handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="watch-http", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止托管并释放端口；幂等，可重复调用。"""
        if self._thread is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None
