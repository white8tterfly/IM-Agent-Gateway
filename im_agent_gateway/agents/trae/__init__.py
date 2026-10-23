"""Trae 编码 Agent（traecli exec 无头调用）。"""

from .agent import TraeAgent

# 接入约定：模块暴露 Agent 供 plugins 按配置声明加载
Agent = TraeAgent

__all__ = ["TraeAgent", "Agent"]
