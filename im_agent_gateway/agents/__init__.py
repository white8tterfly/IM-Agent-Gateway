"""Agent 系统集合。

各 Agent 在 agents/<name>/ 下实现 BaseAgent 并暴露 Agent 类，经 .env 的 AGENTS 声明即可接入；
开关由 AgentRegistry 统一维护：启动时读 ENABLED_AGENTS，运行中用 /agent on|off 调整。
"""
