"""管理命令层：Agent 开关与配置写回的单一实现。

IM 框架指令（/agent on|off）、Web 管理端点（POST /api/agents/{name}/active、
POST /api/config）、本地 CLI（manage.py agent on/off、config set）三个入口
都调用 AgentOps / ConfigOps，保证行为、文案、持久化完全一致。
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


class ConfigOps:
    """配置写回操作：校验 + 持久化，CLI config set 与 Web 管理端点共用。"""

    @staticmethod
    def set(cfg: Config, key: str, value: str) -> str | None:
        """把键值写回项目 .env（键名规范化为大写），重启服务后生效。

        成功返回 None；失败返回可直接提示给用户的错误文案。
        - 只允许修改元数据表登记过的键（有说明的键才给改，防误写）；
        - 敏感项空值拒绝：页面留空表示「不修改」而不是「清空」，
          如确需清空请直接编辑 .env 文件。
        """
        from .config_meta import CONFIG_META_BY_KEY
        item = CONFIG_META_BY_KEY.get(key.strip().upper())
        if item is None:
            return f"未知的配置项：{key}（该配置暂不支持在这里修改，请直接编辑 .env 文件）"
        if item.sensitive and not value.strip():
            return f"{item.label}是敏感配置，留空表示不修改；如需清空请直接编辑 .env 文件"
        try:
            persist.set_env_value(cfg.env_file, item.key, value)
        except OSError as exc:
            return f"写入 .env 失败：{exc}"
        return None
