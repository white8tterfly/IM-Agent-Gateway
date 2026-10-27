"""管理服务：聚合平台运行状态，供 Web 管理端点与 CLI 查询。

只读聚合，不持有任何连接：状态来自 cfg / registry / router / gateway 的
当前内存值；最近任务来自路由层的 on_task_event 上报（内存环形缓冲，
最近 50 条，重启即清空——管理台展示的只是"本次运行期间"的任务）。
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from collections import deque
from dataclasses import fields
from pathlib import Path
from typing import Callable

from ..agents.registry import AgentRegistry
from ..config import PROJECT_ROOT, Config
from ..gateway.registry import Gateway
from ..i18n import t
from ..router.dispatcher import MessageRouter

# 最近任务环形缓冲条数
RECENT_TASKS = 50

# 敏感配置字段名特征：命中即脱敏（值非空显示 ******，空值保持空串）
_SENSITIVE_NAME_RE = re.compile(r"key|secret|token|pat|password", re.IGNORECASE)

# 默认日志文件：main.py 的 setup_logging 落盘位置
DEFAULT_LOG_FILE = PROJECT_ROOT / "logs" / "im_agent_gateway.log"

# 重启用的独立进程脚本：等旧实例让出端口后再拉起 main.py。
# 不放在服务进程里重新执行，是因为新实例需要一个干净的 asyncio 事件循环与进程级资源。
_RESTART_WORKER = r'''
import os, socket, subprocess, sys, time

root, port, exe = sys.argv[1], int(sys.argv[2]), sys.argv[3]

def serving() -> bool:
    """旧实例是否仍占着 Web 端口。"""
    if port <= 0:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False

# 等旧实例释放端口（最多 30 秒）：否则新实例会因端口被占用启动失败
deadline = time.time() + 30
while serving() and time.time() < deadline:
    time.sleep(0.2)

os.makedirs(os.path.join(root, "logs"), exist_ok=True)
out = open(os.path.join(root, "logs", "restart_service.log"), "ab")
subprocess.Popen(
    [exe, os.path.join(root, "main.py")],
    cwd=root,
    stdin=subprocess.DEVNULL,
    stdout=out,
    stderr=out,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0),
)
out.close()
'''


def spawn_replacement(port: int = 0) -> None:
    """拉起独立进程，在旧实例退出后启动新实例（托盘与 Web 管理台的重启共用）。"""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():  # 和服务启动方式一致：无窗口运行
        exe = pythonw
    flags = 0
    if os.name == "nt":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    subprocess.Popen(
        [sys.executable, "-c", _RESTART_WORKER, str(PROJECT_ROOT), str(port), str(exe)],
        cwd=str(PROJECT_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        start_new_session=(os.name != "nt"),
        close_fds=True,
    )


def _format_config_value(field: str, val: object) -> str:
    """把 Config 字段值转成适合展示与写回 .env 的字符串。

    布尔转 1/0、集合排序转逗号串、映射转「键=值」串
    （WORKSPACES 沿用历史分号分隔口径），保持与 .env 文件格式一致。
    """
    if val is None or val == "":
        return ""
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, set):
        return ",".join(sorted(val))
    if isinstance(val, (list, tuple)):
        return ",".join(str(v) for v in val)
    if isinstance(val, dict):
        sep = ";" if field == "workspaces" else ","
        return sep.join(f"{k}={v}" for k, v in val.items())
    return str(val)


def config_grouped_view(cfg: Config, meta: list | None = None) -> dict:
    """分组配置视图：/api/config 端点与 CLI config show 共用同一口径。

    按元数据表分成常用 / 高级两组，每项带通俗名称与说明、当前运行值
    （敏感项非空显示 ******）、是否支持热生效（hot）；漏登记的 Config
    字段自动落 unknown 组只读展示（desc 为暂无说明），避免新增配置凭空消失。
    meta 参数供测试传入截断的登记表验证 unknown 归类。
    """
    from ..config import HOT_PARSERS
    from .config_meta import CONFIG_META
    items = CONFIG_META if meta is None else meta

    declared: set[str] = set()
    common: list[dict] = []
    advanced: list[dict] = []
    for it in items:
        declared.add(it.field)
        val = getattr(cfg, it.field, None)
        shown = "******" if (it.sensitive and val) else _format_config_value(it.field, val)
        # 是否支持即时生效：键在 HOT_PARSERS 中 = 改完写回内存立即生效；否则需重启
        hot = it.key in HOT_PARSERS
        row = {
            "key": it.key, "field": it.field,
            "label": t(it.label, it.label_en or it.label),
            "desc": t(it.desc, it.desc_en or it.desc),
            "sensitive": it.sensitive, "type": it.type, "value": shown,
            "hot": hot,
        }
        (common if not it.advanced else advanced).append(row)

    unknown: list[dict] = []
    for f in fields(Config):
        if f.name == "env_file" or f.name in declared:
            continue
        val = getattr(cfg, f.name)
        sensitive = bool(_SENSITIVE_NAME_RE.search(f.name))
        shown = "******" if (sensitive and val) else _format_config_value(f.name, val)
        unknown.append({
            "key": f.name.upper(), "field": f.name, "label": f.name,
            "desc": t("（暂无说明）", "(no description)"), "sensitive": sensitive, "type": "text",
            "value": shown,
            "hot": f.name.upper() in HOT_PARSERS,
        })
    return {"common": common, "advanced": advanced, "unknown": unknown}


class ManageService:
    """平台状态聚合：状态 / Agent / 任务 / 日志 / 配置（脱敏）。"""

    def __init__(
        self,
        cfg: Config,
        registry: AgentRegistry,
        router: MessageRouter,
        gateway: Gateway | None = None,
        log_file: Path | None = None,
        shutdown_event: asyncio.Event | None = None,
    ) -> None:
        self.cfg = cfg
        self.registry = registry
        self.router = router
        self.gateway = gateway
        self.log_file = Path(log_file) if log_file is not None else DEFAULT_LOG_FILE
        # 退出信号：main 创建并等待，管理台 / 托盘可请求优雅关闭
        self._shutdown_event = shutdown_event
        # 记录事件循环：管理端点在 FastAPI 线程池里执行，跨线程触发必须走 loop 线程安全接口
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # 最近任务环形缓冲：路由层任务结束时经 on_task_event 上报
        self._recent: deque[dict] = deque(maxlen=RECENT_TASKS)

    def request_shutdown(self, delay: float = 0.5) -> bool:
        """请求优雅退出；delay 留出时间把 HTTP 响应发回客户端。"""
        if self._shutdown_event is None:
            return False
        if self._loop is None or not self._loop.is_running():
            self._shutdown_event.set()
        else:
            self._loop.call_soon_threadsafe(
                self._loop.call_later, delay, self._shutdown_event.set
            )
        return True

    def request_restart(self, delay: float = 0.5) -> bool:
        """重启服务：先挂好「退出后拉起新实例」的等待进程，再请求优雅退出。

        Web 管理台与托盘共用：调用方无需关心由谁启动，退出后新实例会自动接管。
        """
        if self._shutdown_event is None:
            return False
        # Web 控制台未开启时无需等端口，交给新实例的单实例锁重试兜底
        spawn_replacement(self.cfg.webui_port if self.cfg.webui_enabled else 0)
        return self.request_shutdown(delay)

    # ---------- 任务事件 ----------

    def on_task_event(self, event: dict) -> None:
        """接收路由层上报的任务结束事件，压入环形缓冲。"""
        self._recent.append(event)

    # ---------- 查询 ----------

    def status(self) -> dict:
        """运行状态：本端点可达即在线；含通道连接与空闲信息。"""
        channels = []
        if self.gateway is not None:
            channels = [
                {"name": name, "connected": bool(self.gateway.get(name).connected)}
                for name in self.gateway.channel_names
            ]
        return {
            "state": "running",
            # 配置里持久化的语言（不是本次请求的临时语言）：前端首次访问据此初始化
            "language": self.cfg.language,
            "channels": channels,
            "agents": {
                "total": len(self.registry.agents),
                "active": len(self.registry.active_agents),
            },
            "idle": {
                "exit_enabled": self.cfg.idle_exit,
                "idle_min": self.cfg.idle_exit_min,
                "idle_sec": round(self.router.idle_seconds(), 1),
            },
        }

    def agents(self) -> list[dict]:
        """Agent 列表：注册名 / 展示名 / 用途 / 档位 / 开关 / 可用性 / 路由描述。"""
        return [
            {
                "name": a.name,
                "label": a.display_label,
                "kind": a.kind,
                "complexity": a.complexity,
                "active": self.registry.is_active(a.name),
                "available": a.available,
                "routing_hint": a.route_description,
            }
            for a in self.registry.agents
        ]

    def tasks(self) -> dict:
        """当前执行中的任务数 + 最近任务环形缓冲。"""
        return {
            "inflight": self.router.inflight,
            "recent": list(self._recent),
        }

    def logs_tail(self, lines: int = 200) -> list[str]:
        """日志文件尾部 N 行（缺失返回空列表，行数收敛到 1..1000）。"""
        lines = max(1, min(int(lines), 1000))
        if not self.log_file.is_file():
            return []
        text = self.log_file.read_text(encoding="utf-8", errors="replace")
        return text.splitlines()[-lines:]

    def config_grouped(self) -> dict:
        """分组配置视图：常用 / 高级 / 未登记三组，每项带说明与当前值。"""
        return config_grouped_view(self.cfg)
