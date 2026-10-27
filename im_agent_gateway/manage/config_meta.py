"""配置元数据表：管理台「配置」区的单一事实源。

每项声明 .env 键名、对应 Config 字段、通俗名称与说明（中英两版）、是否敏感、
控件类型（text/bool）与常用/高级分组。页面展示、修改写回、CLI show
共用这一份登记表；以后新增 Config 字段必须在这里登记，
漏登记的字段会在配置视图中落到 unknown 组（只读展示），测试会兜底提醒。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigItem:
    """单个配置项的元数据。"""

    key: str            # .env 键名（大写，写回用）
    field: str          # Config 字段名（取当前运行值用）
    label: str          # 通俗名称（中文界面展示标题）
    desc: str           # 含义与影响的通俗说明（中文，一句话）
    label_en: str = ""  # 英文界面展示标题
    desc_en: str = ""   # 英文界面说明
    sensitive: bool = False   # 敏感项：展示脱敏为 ******，留空保存=不修改
    type: str = "text"        # text=文本输入 / bool=开关（点击即保存）
    advanced: bool = True     # True 收进「高级设置」折叠区；False 常用区前置


# 常用配置在前（advanced=False），高级配置在后；顺序即页面展示顺序
CONFIG_META: list[ConfigItem] = [
    # ---------- 常用配置 ----------
    ConfigItem(
        "AGENTS", "agents", "接入哪些智能体",
        "服务启动时加载哪些智能体，用英文逗号隔开；改完重启生效",
        "Agents to load", "Which agents to load at startup, comma separated; restart required",
        advanced=False),
    ConfigItem(
        "ENABLED_AGENTS", "enabled_agents", "默认开启的智能体",
        "哪些智能体一开始就开启，用英文逗号隔开，留空表示全部开启；管理台的开关会实时改写这一项",
        "Agents enabled by default",
        "Which agents start enabled, comma separated, empty means all; console toggles rewrite this",
        advanced=False),
    ConfigItem(
        "DEFAULT_AGENT", "default_agent", "默认交给哪个智能体",
        "消息不知道该给谁处理时，交给这个智能体兜底",
        "Default agent", "Fallback agent when a message cannot be routed",
        advanced=False),
    ConfigItem(
        "AUTO_ROUTE", "auto_route", "自动选路开关",
        "开启后每条消息自动挑最合适的智能体处理；关闭后所有消息都交给默认智能体",
        "Auto routing", "Pick the best agent for each message; off means everything goes to the default agent",
        type="bool", advanced=False),
    ConfigItem(
        "JEV_ROUTE", "jev_route", "智能选路开关",
        "用 Jev 决策模型做自动选路；关闭或调用失败时退回关键词规则选路",
        "Smart routing", "Use the Jev decision model for routing; falls back to keyword rules on failure",
        type="bool", advanced=False),
    ConfigItem(
        "AGENT_KINDS", "agent_kinds", "智能体用途分配",
        "给智能体指定用途，自动选路按用途匹配。格式：名称=用途（office 办公 / chat 对话 / coding 编码），逗号分隔",
        "Agent purposes",
        "Assign a purpose per agent for routing. Format: name=purpose (office / chat / coding), comma separated",
        advanced=False),
    ConfigItem(
        "AGENT_COMPLEXITY", "agent_complexity", "智能体难度档位",
        "同一用途配了多个智能体时，按问题难度分档。格式：名称=档位（simple 简单 / mid 中等 / complex 复杂），逗号分隔",
        "Agent complexity tiers",
        "Tier agents of the same purpose by question difficulty. Format: name=tier (simple / mid / complex), comma separated",
        advanced=False),
    ConfigItem(
        "COMPLEXITY_ROUTE", "complexity_route", "按难度自动选智能体",
        "开启后同一用途的多个智能体由 Jev 按问题难度自动挑一个（需要配置 Jev）；关闭或不可用时取排在最前面的那个",
        "Route by difficulty",
        "Let Jev pick among agents of the same purpose by difficulty (Jev required); otherwise the first one wins",
        type="bool", advanced=False),
    ConfigItem(
        "ALLOWED_USERS", "allowed_users", "谁可以使用",
        "允许使用机器人的用户 ID，用英文逗号隔开，留空表示不限制；用了网页控制台要确保 webui 在名单里",
        "Allowed users",
        "User IDs allowed to use the bot, comma separated, empty means unrestricted; include webui if using the console",
        advanced=False),
    ConfigItem(
        "IDLE_EXIT", "idle_exit", "空闲自动休息开关",
        "开启后，一段时间没人说话服务就自动休息，等下次有新消息再自动启动",
        "Idle auto-sleep",
        "Let the service sleep after a period of inactivity and wake on the next message",
        type="bool", advanced=False),
    ConfigItem(
        "IDLE_EXIT_MIN", "idle_exit_min", "多久没人说话自动休息",
        "单位分钟，配合空闲自动休息开关使用",
        "Idle timeout (minutes)", "Minutes of inactivity before sleeping, used with idle auto-sleep",
        advanced=False),
    ConfigItem(
        "WAKE_REPLY", "wake_reply", "唤醒时先回的话",
        "服务刚被叫醒时先回复的占位内容，正式结果出来后会再发一条；留空按界面语言自动选择",
        "Wake-up placeholder",
        "Placeholder reply while the service wakes up, followed by the real answer; empty picks it by language",
        advanced=False),
    ConfigItem(
        "WATCH_AUTOSTART", "watch_autostart", "开机自动在后台待命",
        "开启后每次登录电脑都会在后台自动待命（不会弹窗口）；关闭后不再自动启动，"
        "本次已在运行的不受影响，下次开机生效。",
        "Start watchdog at login",
        "Run the watchdog in the background after login (no window); turning it off applies from the next login",
        type="bool", advanced=False),
    ConfigItem(
        "WORKBUDDY_CWD", "workbuddy_cwd", "办公智能体工作目录",
        "WorkBuddy 处理任务的工作目录（建议用没有空格的路径），文档、表格等文件相对这里读写",
        "Office agent workspace",
        "Working directory for WorkBuddy tasks (avoid spaces); documents are read and written here",
        advanced=False),
    ConfigItem(
        "WEBUI_ENABLED", "webui_enabled", "网页控制台开关",
        "开启后可以在浏览器里使用管理台与聊天台（默认开启）",
        "Web console", "Serve the admin and chat pages in a browser (on by default)",
        type="bool", advanced=False),
    ConfigItem(
        "FIRST_USE_GUIDE", "first_use_guide", "首次使用提示开关",
        "开启后每个用户第一次发消息时会收到一条简短指令说明（同一用户只发一次）",
        "First-use guide",
        "Send a short command guide the first time each user messages (once per user)",
        type="bool", advanced=False),
    ConfigItem(
        "LANGUAGE", "language", "界面语言",
        "中文或 English，页面文案与机器人回复随之切换；网页可在右上角随时切换",
        "Language",
        "Chinese or English for pages and bot replies; switchable from the page header",
        advanced=False),

    # ---------- 高级设置：通道凭证与细节 ----------
    ConfigItem(
        "CHANNELS", "channels", "接入哪些消息通道",
        "服务启动加载哪些 IM 通道，用英文逗号隔开：wecom 企业微信、feishu 飞书",
        "IM channels",
        "Which IM channels to load at startup, comma separated: wecom, feishu",
        advanced=False),
    ConfigItem(
        "BOT_ID", "bot_id", "企微机器人 ID",
        "企业微信智能机器人的 Bot ID，企微通道登录用；不接企微可留空",
        "WeCom bot ID", "Bot ID of the WeCom bot used to sign in; leave empty if unused"),
    ConfigItem(
        "SECRET", "secret", "企微机器人密钥",
        "企业微信智能机器人的 Secret，与机器人 ID 配套",
        "WeCom bot secret", "Secret paired with the WeCom bot ID",
        sensitive=True),
    ConfigItem(
        "WECOM_WS_URL", "wecom_ws_url", "企微长连接地址",
        "一般无需修改",
        "WeCom WebSocket URL", "Usually no change needed"),
    ConfigItem(
        "FEISHU_APP_ID", "feishu_app_id", "飞书应用 ID",
        "飞书开放平台自建应用的 App ID，飞书通道登录用；不接飞书可留空",
        "Feishu app ID", "App ID of the Feishu custom app used to sign in; leave empty if unused"),
    ConfigItem(
        "FEISHU_APP_SECRET", "feishu_app_secret", "飞书应用密钥",
        "飞书自建应用的 App Secret，与应用 ID 配套",
        "Feishu app secret", "App secret paired with the Feishu app ID",
        sensitive=True),

    # ---------- 高级设置：Jev 决策细节 ----------
    ConfigItem(
        "MAKERS_MODELS_KEY", "jev_api_key", "Jev 网关密钥",
        "调用 Makers 网关（Jev 决策模型）的密钥；不配置时智能选路退回关键词规则",
        "Jev gateway key", "Key for the Makers gateway (Jev); without it routing falls back to keyword rules",
        sensitive=True),
    ConfigItem(
        "JEV_BASE_URL", "jev_base_url", "Jev 网关地址",
        "Makers 网关接口地址，一般无需修改",
        "Jev gateway URL", "Makers gateway endpoint, usually no change needed"),
    ConfigItem(
        "JEV_MODEL", "jev_model", "Jev 模型 ID",
        "Makers 网关固定为 @makers/jev",
        "Jev model ID", "Fixed to @makers/jev on the Makers gateway"),
    ConfigItem(
        "JEV_TIMEOUT_SEC", "jev_timeout_sec", "Jev 决策超时",
        "单次选路请求的秒数上限，超时自动退回关键词规则",
        "Jev timeout (seconds)", "Upper bound per routing request; timeouts fall back to keyword rules"),
    ConfigItem(
        "JEV_MIN_CONFIDENCE", "jev_min_confidence", "Jev 置信度门槛",
        "低于该值时不用 Jev 的结果（0 到 1 之间），调小更容易采纳它的选择",
        "Jev confidence threshold",
        "Ignore Jev results below this value (0 to 1); lower means more willing to accept"),

    # ---------- 高级设置：Qoder 编码智能体 ----------
    ConfigItem(
        "QODER_CWD", "qoder_cwd", "Qoder 工作目录",
        "Qoder 编码任务的工作目录（Git 仓库），留空用项目根目录",
        "Qoder workspace", "Working directory (git repo) for Qoder tasks; empty uses the project root"),
    ConfigItem(
        "QODER_MODEL", "qoder_model", "Qoder 模型",
        "留空用 CLI 默认模型，可用 /models 查看列表",
        "Qoder model", "Empty uses the CLI default; send /models to list available models"),
    ConfigItem(
        "QODER_PERMISSION_MODE", "qoder_permission_mode", "Qoder 权限模式",
        "accept_edits 自动接受文件编辑（无人值守必备）；default 每次编辑需确认",
        "Qoder permission mode",
        "accept_edits auto-accepts file edits (needed for unattended use); default asks every time"),
    ConfigItem(
        "QODER_TIMEOUT_SEC", "qoder_timeout_sec", "Qoder 任务超时",
        "单次任务最长运行秒数，超时终止进程",
        "Qoder task timeout (seconds)", "Longest runtime per task; the process is killed on timeout"),
    ConfigItem(
        "QODER_MAX_OUTPUT_TOKENS", "qoder_max_output_tokens", "Qoder 输出上限",
        "单次回复最大输出 token 数（不是凭证，明文展示）",
        "Qoder output limit", "Max output tokens per reply (not a credential, shown in clear)"),
    ConfigItem(
        "QODER_BIN", "qoder_bin", "Qoder 命令行路径",
        "qoderclicn 可执行文件，默认走系统 PATH",
        "Qoder CLI path", "qoderclicn executable; defaults to the system PATH"),
    ConfigItem(
        "QODERCN_PERSONAL_ACCESS_TOKEN", "qodercn_pat", "Qoder 访问令牌",
        "Personal Access Token，与交互式登录二选一",
        "Qoder access token", "Personal access token, an alternative to interactive login",
        sensitive=True),

    # ---------- 高级设置：SiliconFlow 对话智能体 ----------
    ConfigItem(
        "API_KEY", "sf_api_key", "SiliconFlow 密钥",
        "对话智能体调用 SiliconFlow 的 API 密钥；不接对话智能体可留空",
        "SiliconFlow API key", "API key used by the chat agent; leave empty if unused",
        sensitive=True),
    ConfigItem(
        "SF_BASE_URL", "sf_base_url", "SiliconFlow 接口地址",
        "OpenAI 兼容接口地址，一般无需修改",
        "SiliconFlow base URL", "OpenAI-compatible endpoint, usually no change needed"),
    ConfigItem(
        "SF_MODEL", "sf_model", "SiliconFlow 模型",
        "默认对话模型，完整列表可发 /models 查看",
        "SiliconFlow model", "Default chat model; send /models for the full list"),
    ConfigItem(
        "SF_MAX_TOKENS", "sf_max_tokens", "SiliconFlow 输出上限",
        "单次回复最大输出 token 数（不是凭证，明文展示）",
        "SiliconFlow output limit", "Max output tokens per reply (not a credential, shown in clear)"),
    ConfigItem(
        "SF_TIMEOUT_SEC", "sf_timeout_sec", "SiliconFlow 请求超时",
        "单次请求的秒数上限",
        "SiliconFlow timeout (seconds)", "Upper bound per request"),
    ConfigItem(
        "SF_INCLUDE_REASONING", "sf_include_reasoning", "推送思维链内容",
        "开启后把模型的思维过程也推送出来",
        "Stream reasoning",
        "Also stream the model's reasoning process",
        type="bool"),

    # ---------- 高级设置：WorkBuddy 细节 ----------
    ConfigItem(
        "WORKBUDDY_BIN", "workbuddy_bin", "WorkBuddy 命令行路径",
        "codebuddy 可执行文件，默认走系统 PATH",
        "WorkBuddy CLI path", "codebuddy executable; defaults to the system PATH"),
    ConfigItem(
        "WORKBUDDY_MODEL", "workbuddy_model", "WorkBuddy 模型",
        "留空用 CLI 默认模型",
        "WorkBuddy model", "Empty uses the CLI default"),
    ConfigItem(
        "WORKBUDDY_PERMISSION_MODE", "workbuddy_permission_mode", "WorkBuddy 权限模式",
        "acceptEdits 自动接受编辑；auto / dontAsk 跳过确认；plan 只读计划",
        "WorkBuddy permission mode",
        "acceptEdits auto-accepts edits; auto / dontAsk skip confirmation; plan is read-only"),
    ConfigItem(
        "WORKBUDDY_TIMEOUT_SEC", "workbuddy_timeout_sec", "WorkBuddy 任务超时",
        "单次任务最长运行秒数，超时终止进程",
        "WorkBuddy task timeout (seconds)", "Longest runtime per task; the process is killed on timeout"),

    # ---------- 高级设置：消息流与杂项 ----------
    ConfigItem(
        "WORKSPACES", "workspaces", "命名工作区列表",
        "聊天里用 /ws 命令列出与切换。格式：名称=路径;名称2=路径2",
        "Named workspaces", "List and switch with /ws in chat. Format: name=path;name2=path2"),
    ConfigItem(
        "STREAM_INTERVAL_SEC", "stream_interval_sec", "流式推送间隔",
        "流式消息两次推送的最小间隔秒数，企微限流 30 条每分钟",
        "Stream interval (seconds)", "Minimum seconds between stream pushes; WeCom allows 30 per minute"),
    ConfigItem(
        "STREAM_CHUNK_SIZE", "stream_chunk_size", "流式推送块大小",
        "每次推送的最大字符数",
        "Stream chunk size", "Max characters per push"),
    ConfigItem(
        "HEARTBEAT_INTERVAL_SEC", "heartbeat_interval_sec", "心跳间隔",
        "企微长连接心跳秒数，官方建议 30",
        "Heartbeat interval (seconds)", "WeCom WebSocket heartbeat, 30 is recommended"),
    ConfigItem(
        "LOG_LEVEL", "log_level", "日志级别",
        "DEBUG / INFO / WARNING / ERROR，排查问题时可临时调成 DEBUG",
        "Log level", "DEBUG / INFO / WARNING / ERROR; use DEBUG temporarily for troubleshooting"),

    # ---------- 高级设置：消息唤醒细节 ----------
    ConfigItem(
        "WATCH_ENABLED", "watch_enabled", "消息唤醒开关",
        "开启后由后台待命程序监听消息、收到后自动启动完整服务；是否随开机启动由「开机自动在后台待命」单独控制。"
        "开启后不要再手动长期运行主服务",
        "Message wake-up",
        "A background watchdog listens and starts the full service on demand; autostart is configured separately. "
        "Do not keep the main service running manually when enabled",
        type="bool"),
    ConfigItem(
        "SERVICE_CMD", "service_cmd", "唤醒服务时执行的命令",
        "收到消息后自动启动完整服务所用的命令，在项目根目录执行",
        "Service command", "Command used to start the full service on demand, run in the project root"),
    ConfigItem(
        "WAKE_HANDOFF_FILE", "wake_handoff_file", "唤醒消息暂存文件",
        "待命程序与完整服务之间传递唤醒消息的文件，一般无需修改",
        "Wake handoff file", "File used to pass the wake-up message to the full service, usually no change needed"),
    ConfigItem(
        "WATCH_HTTP_ENABLED", "watch_http_enabled", "休息时托管控制台开关",
        "服务休息时由后台待命程序占住控制台端口，显示正在休息页并提供一键唤醒；需与消息唤醒开关搭配",
        "Serve console while sleeping",
        "While asleep the watchdog keeps the console port and shows a wake-up page; pair with message wake-up",
        type="bool"),

    # ---------- 高级设置：网页控制台细节 ----------
    ConfigItem(
        "WEBUI_HOST", "webui_host", "控制台监听地址",
        "默认仅本机可访问；不要填 0.0.0.0 暴露到公网",
        "Console host", "Loopback only by default; avoid 0.0.0.0 unless you intend public exposure"),
    ConfigItem(
        "WEBUI_PORT", "webui_port", "控制台端口",
        "管理台与聊天台页面端口，也是睡眠托管占用的端口",
        "Console port", "Port for the admin and chat pages, also used while sleeping"),
    ConfigItem(
        "WEBUI_USER_ID", "webui_user_id", "控制台用户身份",
        "浏览器会话在白名单里的身份 ID；设了白名单就须包含 webui",
        "Console user ID", "Identity used by browser sessions; include webui when the allow-list is set"),
    ConfigItem(
        "WEBUI_STATIC", "webui_static", "控制台静态目录",
        "留空用项目根 webui 目录，一般无需修改",
        "Console static directory", "Empty uses the project webui folder, usually no change needed"),
]

# 按键名速查（写回校验用）
CONFIG_META_BY_KEY: dict[str, ConfigItem] = {it.key: it for it in CONFIG_META}
