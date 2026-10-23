"""Qoder CN 编码执行器（qoderclicn 无头调用）。"""

from .runner import QoderRunner

# 接入约定：模块暴露 Agent 供 plugins 按配置声明加载
Agent = QoderRunner

__all__ = ["QoderRunner", "Agent"]
