"""管理端点：挂在 webui 通道 HTTP 服务上的 REST 路由。

create_router(service) 返回 FastAPI APIRouter，由 webui 通道经
mount_admin 注入（main.py 装配），不感知任何具体通道。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..agents.registry import AgentRegistry
from ..config import Config
from .commands import AgentOps
from .service import ManageService


class ActiveBody(BaseModel):
    """Agent 开关请求体。"""

    active: bool


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
        """当前生效配置（敏感值脱敏）。"""
        return service.config_view()

    return api
