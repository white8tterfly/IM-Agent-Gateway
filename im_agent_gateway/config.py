"""配置加载：分层 .env 读取（凭证在上一级，个性化配置在项目目录）。"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
# 保密凭证文件（项目目录上一级的 .env），不入项目仓库
PARENT_ENV_FILE = PROJECT_ROOT.parent / ".env"

# Agent 用途合法值（AGENT_KINDS 的 value 白名单）
VALID_AGENT_KINDS = ("chat", "coding", "office")
# 复杂度档位合法值（AGENT_COMPLEXITY 的 value 白名单，固定三档）
VALID_COMPLEXITY = ("simple", "mid", "complex")


def _lan_ip() -> str:
    """探测本机局域网 IPv4：UDP 连接只查路由不发包，无网络时返回空串。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
    except OSError:
        return ""
    return "" if ip.startswith("127.") else ip


@dataclass
class Config:
    # ---- 企微机器人 ----
    bot_id: str
    secret: str
    wecom_ws_url: str = "wss://openws.work.weixin.qq.com"

    # ---- 飞书机器人（长连接模式，App ID / App Secret） ----
    feishu_app_id: str = ""
    feishu_app_secret: str = ""

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

    # ---- Agent 用途与复杂度路由（两级路由：用途 → 档位） ----
    # AGENT_KINDS：`名称=用途` 覆写映射，空 = 用各 Agent 类默认 kind
    agent_kinds: dict[str, str] = field(default_factory=dict)
    # AGENT_COMPLEXITY：`名称=档位` 映射（simple/mid/complex），空 = 不参与档位选择
    agent_complexity: dict[str, str] = field(default_factory=dict)
    # COMPLEXITY_ROUTE：同用途多 Agent 时是否由 Jev 按问题复杂度选择
    complexity_route: bool = False

    # ---- 工作区（/ws 切换） ----
    workspaces: dict[str, Path] = field(default_factory=dict)

    # ---- 安全 ----
    allowed_users: set[str] = field(default_factory=set)

    # ---- 使用引导 ----
    # 首次使用引导：用户第一次发消息时回一条简短指令说明（同一用户只发一次）
    first_use_guide: bool = True

    # ---- 消息与心跳 ----
    stream_interval_sec: float = 1.0
    stream_chunk_size: int = 200
    heartbeat_interval_sec: float = 30.0

    # ---- 日志 ----
    log_level: str = "INFO"

    # ---- 看门狗 / 消息唤醒 ----
    watch_enabled: bool = False      # 看门狗总开关：服务不自动起，由看门狗监听并唤醒
    # 开机自动启动看门狗：0=登录后计划任务拉起的看门狗读配置即退出，1=正常监听。
    # 只影响「下次开机是否拉起」，不改变本次已在运行的看门狗。
    watch_autostart: bool = True
    idle_exit: bool = False          # 服务空闲自动退开关：1=空闲超时退出，0=常驻
    idle_exit_min: float = 10.0      # 空闲判定分钟数（仅 idle_exit=True 生效）
    wake_reply: str = "正在唤醒…"    # 看门狗唤醒前的占位回复文案
    service_cmd: str = "python main.py"  # 看门狗拉起服务所用命令
    wake_handoff_file: str = ".wake_pending.json"  # 唤醒消息交接文件（相对项目根）
    watch_http_enabled: bool = False  # 睡眠态托管开关：睡眠期间占住 webui 端口提供页面与唤醒按钮

    # ---- Web 控制台（webui 通道）----
    webui_enabled: bool = True     # webui 通道接入开关：开启时自动并入 CHANNELS 声明（默认开启）
    webui_host: str = "127.0.0.1"  # HTTP 绑定地址（默认仅本机可达）
    webui_port: int = 8790         # webui / 睡眠态托管共用端口
    webui_user_id: str = "webui"   # 浏览器会话的 user_id（参与 ALLOWED_USERS 白名单校验）
    webui_static: str = ""         # Web 静态目录；空 = 项目根 webui/（管理台与聊天台页面）

    # ---- 配置文件位置 ----
    # 运行时开关写回目标：只指向项目目录的 .env，绝不指向上一级凭证文件
    env_file: Path = ENV_FILE

    @property
    def qoder_cwd_resolved(self) -> Path:
        return Path(self.qoder_cwd).expanduser().resolve()

    @property
    def webui_static_resolved(self) -> Path:
        """Web 静态目录：显式配置优先，默认用项目根 webui/（页面与通道代码分离，改前端不用进包里翻）。"""
        if self.webui_static:
            return Path(self.webui_static).expanduser()
        return PROJECT_ROOT / "webui"

    @property
    def webui_urls(self) -> tuple[str, str]:
        """(电脑端地址, 手机端地址)：手机端用局域网 IP，探测不到时为空串。"""
        port = self.webui_port
        host = (self.webui_host or "").strip()
        if host in ("", "0.0.0.0", "::"):
            host = "127.0.0.1"
        elif host not in ("127.0.0.1", "localhost", "::1"):
            # 已显式绑定某个可访问地址：电脑端与手机端都用它
            return f"http://{host}:{port}", f"http://{host}:{port}"
        ip = _lan_ip()
        return f"http://{host}:{port}", (f"http://{ip}:{port}" if ip else "")

    @property
    def webui_local_only(self) -> bool:
        """是否只监听回环地址：手机或其他电脑无法直接访问。"""
        return (self.webui_host or "").strip() in ("127.0.0.1", "localhost", "::1")

    def apply_webui_switch(self) -> None:
        """把 WEBUI_ENABLED 开关收敛到 CHANNELS 声明：开 = 自动并入，关 = 从声明剔除。

        避免只改开关或只改声明时出现行为歧义：最终接入与否只看这一处。
        """
        if self.webui_enabled:
            if "webui" not in self.channels:
                self.channels.append("webui")
        else:
            self.channels = [c for c in self.channels if c != "webui"]

    @property
    def first_use_state_file(self) -> Path:
        """首次引导已发送记录：记在项目根，重启后不会重复打扰同一用户。"""
        return PROJECT_ROOT / ".first_use.json"

    @property
    def wake_handoff_file_resolved(self) -> Path:
        p = Path(self.wake_handoff_file).expanduser()
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    def agent_enabled(self, name: str) -> bool:
        """ENABLED_AGENTS 留空视为全部启用。"""
        return not self.enabled_agents or name in self.enabled_agents


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _parse_agent_map(raw: str, allowed: tuple[str, ...], field_label: str) -> dict[str, str]:
    """解析 `名称=值` 逗号分隔映射（AGENT_KINDS / AGENT_COMPLEXITY 共用）。

    值大小写不敏感（统一小写）；非法值与空项启动时告警并忽略，不影响其余项。
    """
    out: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, value = item.partition("=")
        name, value = name.strip(), value.strip().lower()
        if not name or not value:
            logger.warning("%s 含空项 %r，已忽略", field_label, item)
            continue
        if value not in allowed:
            logger.warning("%s 中 %s=%s 非法（允许值：%s），已忽略",
                           field_label, name, value, "/".join(allowed))
            continue
        out[name] = value
    return out


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


# 可热生效配置的解析器：.env 键 -> (Config 字段名, 解析函数)
# 写回 .env 后立即把解析值写回 cfg 对象，使用方每次读取 cfg 即生效。
# 未在此表中的配置项需重启服务（涉及对象创建 / 长连接 / 进程级资源）。
HOT_PARSERS: dict[str, tuple[str, "object"]] = {
    # 路由与用途
    "AUTO_ROUTE": ("auto_route", lambda v: v.lower() not in ("0", "false", "no")),
    "JEV_ROUTE": ("jev_route", lambda v: v.lower() not in ("0", "false", "no")),
    "MAKERS_MODELS_KEY": ("jev_api_key", lambda v: v),
    "JEV_MODEL": ("jev_model", lambda v: v or "@makers/jev"),
    "JEV_MIN_CONFIDENCE": ("jev_min_confidence", lambda v: float(v or 0.5)),
    "COMPLEXITY_ROUTE": ("complexity_route", lambda v: v.lower() in ("1", "true", "yes")),
    "DEFAULT_AGENT": ("default_agent", lambda v: v.lower() or "siliconflow"),
    "AGENT_KINDS": ("agent_kinds", lambda v: _parse_agent_map(v, VALID_AGENT_KINDS, "AGENT_KINDS")),
    "AGENT_COMPLEXITY": ("agent_complexity", lambda v: _parse_agent_map(v, VALID_COMPLEXITY, "AGENT_COMPLEXITY")),
    # 安全与休眠
    "ALLOWED_USERS": ("allowed_users", lambda v: {u.strip() for u in v.split(",") if u.strip()}),
    "IDLE_EXIT_MIN": ("idle_exit_min", lambda v: float(v or 10)),
    "FIRST_USE_GUIDE": ("first_use_guide", lambda v: v.lower() not in ("0", "false", "no")),
    # WAKE_REPLY 由看门狗独立进程读取，服务进程改 cfg 不影响，故不热生效
    # WorkBuddy（cwd / model / permission / timeout 每次任务读 cfg）
    "WORKBUDDY_CWD": ("workbuddy_cwd", lambda v: v or str(PROJECT_ROOT)),
    "WORKBUDDY_MODEL": ("workbuddy_model", lambda v: v),
    "WORKBUDDY_PERMISSION_MODE": ("workbuddy_permission_mode", lambda v: v or "acceptEdits"),
    "WORKBUDDY_TIMEOUT_SEC": ("workbuddy_timeout_sec", lambda v: float(v or 1800)),
    # Qoder
    "QODER_CWD": ("qoder_cwd", lambda v: v or str(PROJECT_ROOT)),
    "QODER_MODEL": ("qoder_model", lambda v: v),
    "QODER_PERMISSION_MODE": ("qoder_permission_mode", lambda v: v or "accept_edits"),
    "QODER_TIMEOUT_SEC": ("qoder_timeout_sec", lambda v: float(v or 1800)),
    "QODER_MAX_OUTPUT_TOKENS": ("qoder_max_output_tokens", lambda v: int(v or 8000)),
    "QODERCN_PERSONAL_ACCESS_TOKEN": ("qodercn_pat", lambda v: v),
    # SiliconFlow（api_key 每次请求读；base_url / timeout 需重建 client，由 apply_config 处理）
    "API_KEY": ("sf_api_key", lambda v: v),
    "SF_MODEL": ("sf_model", lambda v: v or "deepseek-ai/DeepSeek-V4.1-Flash"),
    "SF_MAX_TOKENS": ("sf_max_tokens", lambda v: int(v or 4096)),
    "SF_INCLUDE_REASONING": ("sf_include_reasoning", lambda v: v.lower() in ("1", "true", "yes")),
    # Jev 网关（base_url / timeout 需重建 client，由 JevRouter.reconfigure 处理）
    "JEV_BASE_URL": ("jev_base_url", lambda v: (v or "https://ai-gateway.edgeone.link/v1").rstrip("/")),
    "JEV_TIMEOUT_SEC": ("jev_timeout_sec", lambda v: float(v or 6)),
    # SiliconFlow 网关（需重建 client）
    "SF_BASE_URL": ("sf_base_url", lambda v: (v or "https://api.siliconflow.cn/v1").rstrip("/")),
    "SF_TIMEOUT_SEC": ("sf_timeout_sec", lambda v: float(v or 300)),
    # CLI 路径（available 每次读，改后新任务自动用新路径）
    "QODER_BIN": ("qoder_bin", lambda v: v or "qoderclicn"),
    "WORKBUDDY_BIN": ("workbuddy_bin", lambda v: v or "codebuddy"),
    # 流式与日志
    "STREAM_INTERVAL_SEC": ("stream_interval_sec", lambda v: float(v or 1.0)),
    "STREAM_CHUNK_SIZE": ("stream_chunk_size", lambda v: int(v or 200)),
    "LOG_LEVEL": ("log_level", lambda v: (v or "INFO").upper()),
    # 工作区
    "WORKSPACES": ("workspaces", lambda v: _parse_workspaces(v)),
}


def apply_hot_config(cfg: Config, key: str, value: str) -> bool:
    """把单个 .env 键值热更新到 cfg 对象，返回是否支持热生效。

    仅更新内存配置对象，不写磁盘（写盘由调用方负责）。解析口径与
    load_config 保持一致；解析失败（如数字非数字）保留原值并返回 False。
    """
    entry = HOT_PARSERS.get(key.strip().upper())
    if entry is None:
        return False
    field, parser = entry
    try:
        setattr(cfg, field, parser(value))
    except (ValueError, TypeError):
        logger.warning("配置 %s 热更新解析失败，保留原值：%r", key, value)
        return False
    return True


def load_config(env_file: Path | None = None) -> Config:
    # 分层加载：先上一级凭证，再项目配置（不覆盖已有值）
    if PARENT_ENV_FILE.is_file():
        load_dotenv(PARENT_ENV_FILE, override=False)
    load_dotenv(ENV_FILE if env_file is None else env_file, override=False)

    cfg = Config(
        bot_id=_env("BOT_ID"),
        secret=_env("SECRET"),
        # 写回目标与读取来源保持同一份文件（默认项目 .env）
        env_file=ENV_FILE if env_file is None else Path(env_file),
        wecom_ws_url=_env("WECOM_WS_URL") or "wss://openws.work.weixin.qq.com",
        feishu_app_id=_env("FEISHU_APP_ID"),
        feishu_app_secret=_env("FEISHU_APP_SECRET"),
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
        agent_kinds=_parse_agent_map(_env("AGENT_KINDS"), VALID_AGENT_KINDS, "AGENT_KINDS"),
        agent_complexity=_parse_agent_map(_env("AGENT_COMPLEXITY"), VALID_COMPLEXITY, "AGENT_COMPLEXITY"),
        complexity_route=_env("COMPLEXITY_ROUTE").lower() in ("1", "true", "yes"),
        workspaces=_parse_workspaces(_env("WORKSPACES")),
        allowed_users={u.strip() for u in _env("ALLOWED_USERS").split(",") if u.strip()},
        # 留空按开启处理：老 .env 没有这一项时也提供首次使用提示
        first_use_guide=_env("FIRST_USE_GUIDE", "1").lower() not in ("0", "false", "no"),
        stream_interval_sec=float(_env("STREAM_INTERVAL_SEC") or 1.0),
        stream_chunk_size=int(_env("STREAM_CHUNK_SIZE") or 200),
        heartbeat_interval_sec=float(_env("HEARTBEAT_INTERVAL_SEC") or 30.0),
        log_level=(_env("LOG_LEVEL") or "INFO").upper(),
        watch_enabled=_env("WATCH_ENABLED").lower() in ("1", "true", "yes"),
        # 留空按开启处理：老 .env 没有这一项时保持「登录即启动」的原有行为
        watch_autostart=_env("WATCH_AUTOSTART").lower() not in ("0", "false", "no"),
        idle_exit=_env("IDLE_EXIT").lower() in ("1", "true", "yes"),
        idle_exit_min=float(_env("IDLE_EXIT_MIN") or 10),
        wake_reply=_env("WAKE_REPLY") or "正在唤醒…",
        service_cmd=_env("SERVICE_CMD") or "python main.py",
        wake_handoff_file=_env("WAKE_HANDOFF_FILE") or ".wake_pending.json",
        watch_http_enabled=_env("WATCH_HTTP_ENABLED").lower() in ("1", "true", "yes"),
        # 留空按开启处理：老 .env 没有这一项时默认提供 Web 控制台
        webui_enabled=_env("WEBUI_ENABLED", "1").lower() not in ("0", "false", "no"),
        webui_host=_env("WEBUI_HOST") or "127.0.0.1",
        webui_port=int(_env("WEBUI_PORT") or 8790),
        webui_user_id=_env("WEBUI_USER_ID") or "webui",
        webui_static=_env("WEBUI_STATIC"),
    )

    # webui 接入开关收敛到 CHANNELS 声明（放在必填校验前：关闭态不因残留声明误接入）
    cfg.apply_webui_switch()

    # 默认 Agent 须在 AGENTS 声明中（注册制校验），不在则回退到声明列表第一个
    if cfg.default_agent not in cfg.agents:
        cfg.default_agent = cfg.agents[0] if cfg.agents else ""

    # 必填校验按平台接入声明（CHANNELS）进行：只校验实际接入的通道所需凭证
    missing = []
    if "wecom" in cfg.channels:
        if not cfg.bot_id:
            missing.append("BOT_ID")
        if not cfg.secret:
            missing.append("SECRET")
    if "feishu" in cfg.channels:
        if not cfg.feishu_app_id:
            missing.append("FEISHU_APP_ID")
        if not cfg.feishu_app_secret:
            missing.append("FEISHU_APP_SECRET")
    if missing:
        raise RuntimeError(
            f"缺少必填配置: {', '.join(missing)}，"
            f"请检查上一级目录 {PARENT_ENV_FILE} 或项目 {ENV_FILE}"
        )
    return cfg
