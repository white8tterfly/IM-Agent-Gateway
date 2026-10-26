"""管理服务：聚合平台运行状态，供 Web 管理端点与 CLI 查询。

只读聚合，不持有任何连接：状态来自 cfg / registry / router / gateway 的
当前内存值；最近任务来自路由层的 on_task_event 上报（内存环形缓冲，
最近 50 条，重启即清空——管理台展示的只是"本次运行期间"的任务）。
"""

from __future__ import annotations

import asyncio
import re
from collections import deque
from dataclasses import fields
from pathlib import Path
from typing import Callable

from ..agents.registry import AgentRegistry
from ..config import PROJECT_ROOT, Config
from ..gateway.registry import Gateway
from ..router.dispatcher import MessageRouter

# 最近任务环形缓冲条数
RECENT_TASKS = 50

# 敏感配置字段名特征：命中即脱敏（值非空显示 ******，空值保持空串）
_SENSITIVE_NAME_RE = re.compile(r"key|secret|token|pat|password", re.IGNORECASE)

# 默认日志文件：main.py 的 setup_logging 落盘位置
DEFAULT_LOG_FILE = PROJECT_ROOT / "logs" / "im_agent_gateway.log"


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
            "key": it.key, "field": it.field, "label": it.label, "desc": it.desc,
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
            "desc": "（暂无说明）", "sensitive": sensitive, "type": "text",
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
                "label": a.label,
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
