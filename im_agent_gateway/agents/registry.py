"""Agent 注册中心：按名注册、查询与运行时开关。"""

from __future__ import annotations

import logging

from .base import BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Agent 注册中心：按名称注册、查询与开关。"""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}
        self._active: dict[str, bool] = {}

    def register(self, agent: BaseAgent, active: bool = True) -> None:
        """注册 Agent，active 决定初始开关状态。"""
        if not agent.name:
            raise ValueError("Agent 未定义 name")
        if agent.name in self._agents:
            raise ValueError(f"Agent {agent.name} 已注册，不能重复注册")
        self._agents[agent.name] = agent
        self._active[agent.name] = active
        logger.info("已注册 Agent：%s（%s）", agent.label, "启用" if active else "停用")

    def get(self, name: str) -> BaseAgent | None:
        return self._agents.get(name)

    @property
    def agents(self) -> list[BaseAgent]:
        """全部已注册 Agent（含停用）。"""
        return list(self._agents.values())

    @property
    def active_agents(self) -> list[BaseAgent]:
        """仅已开启的 Agent。"""
        return [a for a in self._agents.values() if self._active[a.name]]

    @property
    def names(self) -> list[str]:
        return list(self._agents)

    @property
    def active_names(self) -> list[str]:
        return [n for n, on in self._active.items() if on]

    def is_active(self, name: str) -> bool:
        return bool(self._active.get(name))

    def set_active(self, name: str, active: bool) -> str | None:
        """开启/关闭 Agent；成功返回 None，失败返回错误文案。"""
        agent = self._agents.get(name)
        if agent is None:
            return f"未注册的 Agent：{name}（可用：{', '.join(self.names) or '无'}）"
        if active and not agent.available:
            return f"{agent.label} 未满足启用条件（如未配置 API_KEY），无法开启"
        if self._active[name] == active:
            return f"{agent.label} 已{'开启' if active else '关闭'}，无需重复操作"
        self._active[name] = active
        logger.info("Agent %s 已%s", agent.label, "开启" if active else "关闭")
        return None

    async def close(self) -> None:
        """关闭所有已注册 Agent（幂等；单个失败不影响其余）。"""
        for name, agent in self._agents.items():
            try:
                await agent.close()
            except Exception:
                logger.exception("关闭 Agent %s 时发生异常", name)
