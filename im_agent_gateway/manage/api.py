"""管理端点：挂在 webui 通道 HTTP 服务上的 REST 路由。

create_router(service) 返回 FastAPI APIRouter，由 webui 通道经
mount_admin 注入（main.py 装配），不感知任何具体通道。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..agents.registry import AgentRegistry
from ..config import Config
from .commands import AgentOps, ConfigOps
from .service import ManageService


class ActiveBody(BaseModel):
    """Agent 开关请求体。"""

    active: bool


class ConfigBody(BaseModel):
    """配置修改请求体。"""

    key: str
    value: str = ""


def create_router(service: ManageService) -> APIRouter:
    """按注入的 ManageService 构建管理端点路由。"""
    api = APIRouter()

    @api.get("/api/status")
    def status() -> dict:
        """运行状态、通道连接、空闲信息。"""
        return service.status()

    @api.get("/api/agents")
    def agents() -> list[dict]:
        """Agent 列表与开关/可用状态。"""
        return service.agents()

    @api.post("/api/agents/{name}/active")
    def set_agent_active(name: str, body: ActiveBody) -> dict:
        """开关 Agent：与 IM 指令 / CLI 走同一管理命令层（含 .env 写回）。"""
        err = AgentOps.set_active(service.cfg, service.registry, name, body.active)
        return {"ok": err is None, "error": err}

    @api.get("/api/tasks")
    def tasks() -> dict:
        """当前执行中的任务与最近任务记录。"""
        return service.tasks()

    @api.get("/api/logs")
    def logs(lines: int = 200) -> dict:
        """日志文件尾部 N 行。"""
        return {"lines": service.logs_tail(lines)}

    @api.get("/api/config")
    def config() -> dict:
        """配置分组视图：常用 / 高级 / 未登记，每项带说明与当前值（敏感脱敏）。"""
        return service.config_grouped()

    @api.post("/api/config")
    async def set_config(body: ConfigBody) -> dict:
        """修改配置写回 .env：与 CLI config set 同一命令层，尽量热生效。

        返回 hot 字段：true 表示已写入内存并立即生效；false 表示仅写盘、
        需重启服务生效。涉及长连接重建（Jev / SiliconFlow client）或
        Agent 元数据覆写的，在写盘后异步重建。
        """
        err, hot = ConfigOps.set(service.cfg, body.key, body.value)
        if err is not None:
            return {"ok": False, "error": err, "hot": False}
        # 热生效副作用：写盘成功后重建依赖 cfg 构造期参数的资源
        key = body.key.strip().upper()
        if key in ("AGENT_KINDS", "AGENT_COMPLEXITY"):
            service.registry.apply_meta_overrides(
                service.cfg.agent_kinds, service.cfg.agent_complexity
            )
        if key in ("JEV_BASE_URL", "JEV_TIMEOUT_SEC"):
            await service.router.jev.reconfigure()
        if key in ("SF_BASE_URL", "SF_TIMEOUT_SEC", "API_KEY"):
            for agent in service.registry.agents:
                await agent.apply_config()
        if key == "LOG_LEVEL":
            import logging
            logging.getLogger().setLevel(service.cfg.log_level)
        return {"ok": True, "error": None, "hot": hot}

    return api
