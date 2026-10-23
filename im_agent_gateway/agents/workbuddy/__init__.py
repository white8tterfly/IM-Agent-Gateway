"""WorkBuddy 办公 Agent 包。"""

from .agent import WorkBuddyAgent

# 接入约定：模块暴露 Agent 供 plugins 按配置声明加载
Agent = WorkBuddyAgent

__all__ = ["WorkBuddyAgent", "Agent"]
