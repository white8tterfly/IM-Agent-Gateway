"""DeepSeek 聊天 Agent（OpenAI 兼容 chat/completions）。"""

from .agent import DeepSeekAgent

# 接入约定：模块暴露 Agent 供 plugins 按配置声明加载
Agent = DeepSeekAgent

__all__ = ["DeepSeekAgent", "Agent"]
