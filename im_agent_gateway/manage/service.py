"""管理服务：聚合平台运行状态，供 Web 管理端点与 CLI 查询。

只读聚合，不持有任何连接：状态来自 cfg / registry / router / gateway 的
当前内存值；最近任务来自路由层的 on_task_event 上报（内存环形缓冲，
最近 50 条，重启即清空——管理台展示的只是"本次运行期间"的任务）。
"""

from __future__ import annotations

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


def config_view(cfg: Config) -> dict:
    """配置脱敏视图：/api/config 端点与 CLI config show 共用同一口径。

    敏感字段（命中 key/secret/token/pat/password 特征）非空显示 ******，
    空值保持空串；set 排序、Path 转字符串、dict 值转字符串，便于展示。
    """
    out: dict = {}
    for f in fields(Config):
        val = getattr(cfg, f.name)
        if _SENSITIVE_NAME_RE.search(f.name):
            # 非空一律显示 ******（不区分长度，避免泄漏任何字符）
            out[f.name] = "******" if val else ""
            continue
        if isinstance(val, set):
            val = sorted(val)
        elif isinstance(val, Path):
            val = str(val)
        elif isinstance(val, dict):
            val = {k: str(v) for k, v in val.items()}
        out[f.name] = val
    return out


class ManageService:
    """平台状态聚合：状态 / Agent / 任务 / 日志 / 配置（脱敏）。"""

    def __init__(
        self,
        cfg: Config,
        registry: AgentRegistry,
        router: MessageRouter,
        gateway: Gateway | None = None,
        log_file: Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.registry = registry
        self.router = router
        self.gateway = gateway
        self.log_file = Path(log_file) if log_file is not None else DEFAULT_LOG_FILE
        # 最近任务环形缓冲：路由层任务结束时经 on_task_event 上报
        self._recent: deque[dict] = deque(maxlen=RECENT_TASKS)

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
        """Agent 列表：注册名 / 展示名 / 类型 / 开关 / 可用性 / 路由描述。"""
        return [
            {
                "name": a.name,
                "label": a.label,
                "kind": a.kind,
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

    def config_view(self) -> dict:
        """当前生效配置（供展示）：敏感字段脱敏，其余原样。"""
        return config_view(self.cfg)
