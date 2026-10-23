"""配置加载：分层 .env 读取（凭证在上一级，个性化配置在项目目录）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
# 保密凭证文件（项目目录上一级的 .env），不入项目仓库
PARENT_ENV_FILE = PROJECT_ROOT.parent / ".env"


@dataclass
class Config:
    # ---- 企微机器人 ----
    bot_id: str
    secret: str
    wecom_ws_url: str = "wss://openws.work.weixin.qq.com"

    # ---- Qoder CN 编码 Agent ----
    qoder_cwd: str = ""
    qoder_model: str = ""
    qoder_permission_mode: str = "accept_edits"
    qoder_timeout_sec: float = 1800
    qoder_max_output_tokens: int = 8000
    qoder_bin: str = "qoderclicn"
    qodercn_pat: str = ""

    # ---- SiliconFlow 聊天 Agent ----
    sf_api_key: str = ""
    sf_base_url: str = "https://api.siliconflow.cn/v1"
    sf_model: str = "deepseek-ai/DeepSeek-V4.1-Flash"
    sf_max_tokens: int = 4096
    sf_timeout_sec: float = 300.0
    sf_include_reasoning: bool = False

    # ---- WorkBuddy 办公 Agent ----
    workbuddy_bin: str = "codebuddy"
    workbuddy_model: str = ""
    workbuddy_cwd: str = ""
    workbuddy_permission_mode: str = "acceptEdits"
    workbuddy_timeout_sec: float = 1800

    # ---- Jev 决策路由（System One） ----
    jev_api_key: str = ""
    jev_base_url: str = "https://ai-gateway.edgeone.link/v1"
    jev_model: str = "@makers/jev"
    jev_route: bool = True
    jev_timeout_sec: float = 6.0
    jev_min_confidence: float = 0.5

    # ---- 平台接入声明（CHANNELS / AGENTS，逗号分隔；详见 README 接入契约） ----
    channels: list[str] = field(default_factory=lambda: ["wecom"])
    agents: list[str] = field(default_factory=lambda: ["siliconflow", "qoder", "workbuddy"])

    # ---- 消息路由 ----
    default_agent: str = "siliconflow"
    auto_route: bool = True
    enabled_agents: set[str] = field(default_factory=set)

    # ---- 工作区（/ws 切换） ----
    workspaces: dict[str, Path] = field(default_factory=dict)

    # ---- 安全 ----
    allowed_users: set[str] = field(default_factory=set)

    # ---- 消息与心跳 ----
    stream_interval_sec: float = 1.0
    stream_chunk_size: int = 200
    heartbeat_interval_sec: float = 30.0

    # ---- 日志 ----
    log_level: str = "INFO"

    @property
    def qoder_cwd_resolved(self) -> Path:
        return Path(self.qoder_cwd).expanduser().resolve()

    def agent_enabled(self, name: str) -> bool:
        """ENABLED_AGENTS 留空视为全部启用。"""
        return not self.enabled_agents or name in self.enabled_agents


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _parse_workspaces(raw: str) -> dict[str, Path]:
    """解析 WORKSPACES 配置：`名称=路径;名称2=路径2`。"""
    out: dict[str, Path] = {}
    for item in raw.split(";"):
        item = item.strip().strip('"')
        if not item:
            continue
        if "=" in item:
            name, _, path = item.partition("=")
            name, path = name.strip(), path.strip().strip('"')
        else:
            path = item
            name = Path(path).name or path
        if name and path:
            out[name] = Path(path).expanduser()
    return out


def load_config(env_file: Path | None = None) -> Config:
    # 分层加载：先上一级凭证，再项目配置（不覆盖已有值）
    if PARENT_ENV_FILE.is_file():
        load_dotenv(PARENT_ENV_FILE, override=False)
    load_dotenv(ENV_FILE if env_file is None else env_file, override=False)

    cfg = Config(
        bot_id=_env("BOT_ID"),
        secret=_env("SECRET"),
        wecom_ws_url=_env("WECOM_WS_URL") or "wss://openws.work.weixin.qq.com",
        qoder_cwd=_env("QODER_CWD") or str(PROJECT_ROOT),
        qoder_model=_env("QODER_MODEL"),
        qoder_permission_mode=_env("QODER_PERMISSION_MODE") or "accept_edits",
        qoder_timeout_sec=float(_env("QODER_TIMEOUT_SEC") or 1800),
        qoder_max_output_tokens=int(_env("QODER_MAX_OUTPUT_TOKENS") or 8000),
        qoder_bin=_env("QODER_BIN") or "qoderclicn",
        qodercn_pat=_env("QODERCN_PERSONAL_ACCESS_TOKEN"),
        sf_api_key=_env("API_KEY") or _env("SILICONFLOW_API_KEY"),
        sf_base_url=(_env("SF_BASE_URL") or "https://api.siliconflow.cn/v1").rstrip("/"),
        sf_model=_env("SF_MODEL") or "deepseek-ai/DeepSeek-V4.1-Flash",
        sf_max_tokens=int(_env("SF_MAX_TOKENS") or 4096),
        sf_timeout_sec=float(_env("SF_TIMEOUT_SEC") or 300),
        sf_include_reasoning=_env("SF_INCLUDE_REASONING").lower() in ("1", "true", "yes"),
        workbuddy_bin=_env("WORKBUDDY_BIN") or "codebuddy",
        workbuddy_model=_env("WORKBUDDY_MODEL"),
        workbuddy_cwd=_env("WORKBUDDY_CWD") or str(PROJECT_ROOT),
        workbuddy_permission_mode=_env("WORKBUDDY_PERMISSION_MODE") or "acceptEdits",
        workbuddy_timeout_sec=float(_env("WORKBUDDY_TIMEOUT_SEC") or 1800),
        jev_api_key=_env("MAKERS_MODELS_KEY") or _env("JEV_API_KEY"),
        jev_base_url=(_env("JEV_BASE_URL") or "https://ai-gateway.edgeone.link/v1").rstrip("/"),
        jev_model=_env("JEV_MODEL") or "@makers/jev",
        jev_route=_env("JEV_ROUTE").lower() not in ("0", "false", "no"),
        jev_timeout_sec=float(_env("JEV_TIMEOUT_SEC") or 6),
        jev_min_confidence=float(_env("JEV_MIN_CONFIDENCE") or 0.5),
        channels=[c.strip() for c in _env("CHANNELS").split(",") if c.strip()] or ["wecom"],
        agents=[a.strip() for a in _env("AGENTS").split(",") if a.strip()]
        or ["siliconflow", "qoder", "workbuddy"],
        default_agent=(_env("DEFAULT_AGENT").lower() or "siliconflow"),
        auto_route=_env("AUTO_ROUTE").lower() not in ("0", "false", "no"),
        enabled_agents={a.strip().lower() for a in _env("ENABLED_AGENTS").split(",") if a.strip()},
        workspaces=_parse_workspaces(_env("WORKSPACES")),
        allowed_users={u.strip() for u in _env("ALLOWED_USERS").split(",") if u.strip()},
        stream_interval_sec=float(_env("STREAM_INTERVAL_SEC") or 1.0),
        stream_chunk_size=int(_env("STREAM_CHUNK_SIZE") or 200),
        heartbeat_interval_sec=float(_env("HEARTBEAT_INTERVAL_SEC") or 30.0),
        log_level=(_env("LOG_LEVEL") or "INFO").upper(),
    )

    if cfg.default_agent not in ("siliconflow", "qoder"):
        cfg.default_agent = "siliconflow"

    missing = []
    if not cfg.bot_id:
        missing.append("BOT_ID")
    if not cfg.secret:
        missing.append("SECRET")
    if missing:
        raise RuntimeError(
            f"缺少必填配置: {', '.join(missing)}，"
            f"请检查上一级目录 {PARENT_ENV_FILE} 或项目 {ENV_FILE}"
        )
    return cfg
