"""管理命令层：Agent 运行时开关的单一实现。

IM 框架指令（/agent on|off）、Web 管理端点（POST /api/agents/{name}/active）、
本地 CLI（manage.py agent on/off）三入口都调用 AgentOps，保证行为、文案、
持久化完全一致。
"""

from __future__ import annotations

from ..agents.registry import AgentRegistry
from ..config import Config
from . import persist


class AgentOps:
    """Agent 开关操作：执行开关 + 校验错误文案 + 持久化写回。"""

    @staticmethod
    def set_active(cfg: Config, registry: AgentRegistry, name: str, active: bool) -> str | None:
        """开启/关闭 Agent 并把当前活跃列表写回 .env，重启后保持。

        成功返回 None；失败返回可直接回复给用户的错误文案。
        写回键为 ENABLED_AGENTS（当前全部已开启 Agent 名，逗号分隔）。
        注：全部关闭时写回空值——按配置「留空=全部启用」的历史语义，重启后
        会恢复为全部开启；正常场景（部分开/关）均能精确还原。
        """
        err = registry.set_active(name, active)
        if err is not None:
            return err
        names = [n.lower() for n in registry.active_names]
        # 内存配置与磁盘写回保持一致，后续 status / config 查询不出现两套口径
        cfg.enabled_agents = set(names)
        try:
            persist.set_env_value(cfg.env_file, "ENABLED_AGENTS", ",".join(names))
        except OSError as exc:
            # 开关已在内存生效但没写进文件：如实告知，重启后回旧值
            return f"开关已更新，但写回 .env 失败：{exc}"
        return None
