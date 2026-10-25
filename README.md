# IM-Agent Gateway

把即时通讯（IM）统一接入多个 AI Agent 的网关平台：任意 IM 通道与任意 Agent 都通过接入契约挂到平台上，由平台统一完成消息归一化、智能路由、指令分发与流式回传。接入新的 IM 或新的 Agent，不需要改动平台任何现有代码。

仓库内置企业微信、飞书、Web 控制台三种通道，以及 SiliconFlow（对话）、Qoder（编码）、WorkBuddy（办公）、Trae（编码）四种 Agent 实现。它们可直接使用，也可作为接入新通道、新 Agent 的参考实现。

## 特性

| 能力 | 说明 |
| --- | --- |
| 多 IM 接入 | 实现 `IMChannel` 并声明即可接入新 IM；消息统一归一化为 `InboundMessage`，路由层不感知具体协议 |
| 多 Agent 接入 | 实现 `BaseAgent` 并声明即可接入新 Agent；统一 `submit / stop / reset / busy` 接口，会话级并发控制 |
| 两级智能路由 | 先判定消息用途（对话 / 编码 / 办公），再在同类 Agent 中按问题复杂度选择；决策模型不可用时退化为关键词计分 |
| 框架命令 | `/agent`（切换与开关）、`/help`、`/status` 由平台提供；Agent 特有指令由各 Agent 声明 |
| 流式回复 | 平台定义统一流式接口（`send_stream`）；不支持流式的 IM 可退化为整条发送 |
| 会话与队列 | 每个 IM 会话分别维护各 Agent 的会话记忆；同一会话内任务串行，期间新消息自动排队 |
| 消息唤醒 | 可选：登录后只运行轻量待命进程监听消息，收到消息再启动完整服务，空闲后自动退回 |
| Web 控制台 | 可选：管理台（状态 / Agent 开关 / 任务 / 日志 / 配置）与聊天台（浏览器直连 Agent） |
| 多入口管理 | IM 指令、Web 管理台、本地 CLI（`manage.py`）共用同一管理命令层，改动一致写回 `.env` |
| 权限控制 | 可按用户 ID 限制使用者；是否自动接受 Agent 的编辑操作由各自权限模式决定 |

## 环境要求

- Python 3.11 及以上。
- 依赖见 [`requirements.txt`](./requirements.txt)，安装：`pip install -r requirements.txt`。建议先创建虚拟环境：`python -m venv .venv`。
- Windows、macOS、Linux 均可运行；开机自启脚本仅支持 Windows。
- Qoder、WorkBuddy、Trae 三种 Agent 依赖各自的外部命令行工具，未安装时自动注册为不可用，不影响平台启动。

## 快速开始

### 1. 安装依赖

```powershell
pip install -r requirements.txt
```

### 2. 配置 `.env`

复制 [`.env.example`](./.env.example) 为项目根目录的 `.env`，至少声明要接入的通道与 Agent：

```env
CHANNELS = "wecom"
AGENTS   = "siliconflow,qoder,workbuddy,trae"
```

配置优先级：系统环境变量 > 上一级目录 `.env` > 项目 `.env` > 代码默认值。项目 `.env` 已被 `.gitignore` 忽略，可安全存放本机凭证；如希望把凭证与本机路径统一放在仓库之外，也可以写到上一级目录的 `.env`。

各内置通道与 Agent 所需凭证见 `.env.example` 注释。前置条件不满足的实现会自动注册为不可用，不影响其他部分运行。

### 3. 启动

```powershell
python main.py
```

也可以双击 `run.bat`。日志同时输出到控制台和 `logs/im_agent_gateway.log`。

### 4. 验证

- 给机器人发一条单聊消息，应收到 Agent 回复。
- 开启 `WEBUI_ENABLED=1` 后访问 `http://127.0.0.1:8790` 查看管理台。
- 另开一个终端执行 `python manage.py status`，确认服务在线。

## IM 使用方式

| 操作 | 说明 |
| --- | --- |
| 单聊直接发消息 | 自动路由：按用途与问题复杂度两级选择 Agent，决策不可用时按关键词降级 |
| 群里 @机器人 | 发送 `@机器人名 任务描述`，@ 前缀由通道层剥离 |
| `/agent <名称>` / `/agent auto` | 固定到某个 Agent / 恢复自动路由 |
| `/agent on\|off <名称>` | 运行时开启或关闭 Agent，无需重启 |
| `/help` / `/status` | 查看帮助（含各 Agent 指令分组）/ 查看服务状态 |
| Agent 特有指令 | 由各 Agent 提供（如 `/cd`、`/ws`、`/model`、`/stop`、`/reset`），消息路由到谁就由谁处理 |

切换与开关 Agent 的命令由平台提供，其余指令由各 Agent 自行声明；不支持的指令会提示未知命令。

## 命令行用户手册

`manage.py` 是本地管理入口，与 IM 指令、Web 管理台共用同一套管理命令层与校验规则。命令分两类：离线命令直接读写项目 `.env`，不需要服务在运行；在线命令访问服务的本地管理端点，需要服务已启动且 `WEBUI_ENABLED=1`。

### 命令总览

| 命令 | 是否依赖服务 | 说明 |
| --- | --- | --- |
| `python manage.py config show` | 否 | 展示当前生效配置，按常用 / 高级分组，每项带通俗名称与说明，敏感值隐藏 |
| `python manage.py config check` | 否 | 检查通道必填项、白名单冲突与 Agent 前置条件 |
| `python manage.py config set <键> <值>` | 否 | 修改配置并写回项目 `.env` |
| `python manage.py status` | 是 | 服务状态、通道连接情况、Agent 数量、空闲倒计时 |
| `python manage.py agents` | 是 | Agent 列表，含用途、复杂度档位、开关与可用状态 |
| `python manage.py agent on\|off <名称>` | 是 | 开启或关闭 Agent，写回 `.env`，重启后保持 |
| `python manage.py --help` | 否 | 查看命令与子命令帮助 |

### 配置查看与校验

```powershell
python manage.py config show
python manage.py config check
```

`config show` 输出三部分：常用配置、高级设置、未登记（只读）。每项格式为 `通俗名称（键名）= 当前值`，下一行附一句说明；密钥、令牌等敏感项显示为 `******`。

`config check` 面向排查：通道必填项是否齐全、白名单是否漏掉 Web 控制台身份、Agent 是否满足前置条件，最后给出「一切正常」或问题清单。它是检查工具，发现问题时退出码仍为 0，不会中断脚本。

### 修改配置

```powershell
python manage.py config set DEFAULT_AGENT workbuddy
python manage.py config set IDLE_EXIT_MIN 30
python manage.py config set WATCH_AUTOSTART 0
```

规则：

- 键名大小写不敏感，内部统一转大写后匹配。
- 只有配置元数据中登记过的键才允许写入，未登记的键会被拒绝，避免写错配置名。
- 敏感项（密钥、令牌）留空表示保持原值，不会清空；要更换时填入新值。
- 保存位置是项目 `.env`，注释与原顺序保留；缺失的键追加到文件末尾。
- 支持热生效的配置会提示“运行中的服务会即时采用新值”，其余配置提示“重启服务后生效”。

### 查看运行状态

```powershell
python manage.py status
python manage.py agents
python manage.py agent off qoder
python manage.py agent on qoder
```

`status` 显示服务是否在线、各通道连接状态、Agent 总数与开启数量；开启空闲自动休息时还会显示剩余时间。`agents` 列出每个 Agent 的名称、用途（对话 / 编码 / 办公）、档位（简单 / 中等 / 复杂）以及是否可用。开关操作与在 IM 里发 `/agent on|off`、在管理台点击开关完全等价。

在线命令访问 `http://<WEBUI_HOST>:<WEBUI_PORT>`（默认 `127.0.0.1:8790`）并设置 3 秒超时。服务睡眠或未启动时，统一提示先唤醒服务或执行 `python main.py`。

### 退出码

- `0`：命令执行完成（包括 `config check` 发现问题的情况）。
- `1`：配置加载失败，例如通道必填凭证缺失；此时除 `config check` 外的命令都会退出。

### 典型场景

| 场景 | 做法 |
| --- | --- |
| 首次接入后确认配置 | `python manage.py config check`，按提示补齐通道凭证与白名单 |
| 不打开网页快速改配置 | `python manage.py config set <键> <值>` |
| 临时停用某个 Agent | `python manage.py agent off <名称>`，或在 IM 发 `/agent off <名称>` |
| 取消开机自启但保留计划任务 | `python manage.py config set WATCH_AUTOSTART 0`，下次开机生效 |
| 服务是否还活着 | `python manage.py status`；睡眠中会提示先唤醒 |

## 接入 IM 通道

### 接入步骤

1. **实现通道类**：新建类继承 `IMChannel`，实现 `start`、`close`、`send_text`、`send_stream`，并在收到报文时把内容归一化为 `InboundMessage`，通过 `self.on_message(...)` 上抛。
2. **放置代码**：内置通道放到 `im_agent_gateway/gateway/channels/<名称>/` 并暴露 `Channel` 类；第三方通道放在自己的包里，同样暴露 `Channel` 类。
3. **声明接入**：在 `.env` 的 `CHANNELS` 中写入短名或 `模块路径:类名`，例如 `CHANNELS = "wecom,mycorp.myim:Channel"`。
4. **提供凭证**：构造签名固定为 `Channel(cfg)`，需要的凭证从 `cfg` 或环境变量读取，并在 `.env.example` 中补充说明。
5. **验证**：运行 `python scripts/test_channel_contract.py` 校验契约；对照 [`examples/im_channel_example.py`](./examples/im_channel_example.py) 观察“报文进入 → 归一化 → 路由 → 回复发出”的全链路。

### 必须与可选接口

| 成员 | 必须 | 说明 |
| --- | --- | --- |
| `name` | 是 | 通道唯一标识，注册与消息的 `im` 字段使用 |
| `start()` / `close()` | 是 | 建立并维持长连接 / 优雅断开 |
| `send_text(msg, content)` | 是 | 回复文本 |
| `send_stream(msg, stream_id, content, finish)` | 是 | 发送或刷新流式消息；不支持时可整条发送 |
| `send_welcome(evt, content)` | 否 | 进入会话的欢迎语，默认退化为 `send_text` |
| `send_push(msg, content)` | 否 | 按 `chat_id` / `chat_type` 主动回传，用于消息唤醒后的结果回传；默认退化为 `send_text` |
| `wake_listen` | 否 | 是否允许待命进程独立监听该通道；HTTP 被动型通道（如 webui）设为 `False` |

### 接入检查清单

- [ ] 类继承 `IMChannel`，模块暴露 `Channel`。
- [ ] 群聊 @ 前缀已在通道层剥离，`content` 是纯文本。
- [ ] 单聊与群聊分别填入正确的 `chat_id`、`chat_type`。
- [ ] 长连接断开后能自行重连，`close()` 后不再重连。
- [ ] 消息回调只经 `self.on_message` 上抛，不直接调用路由。
- [ ] 已在 `CHANNELS` 声明，且 `python manage.py config check` 无通道级问题。

## 接入 Agent

### 接入步骤

1. **实现 Agent 类**：继承 `BaseAgent`，声明 `name`、`label`、`kind`，实现 `submit`、`stop`、`reset`、`busy`。
2. **放置代码**：内置 Agent 放到 `im_agent_gateway/agents/<名称>/` 并暴露 `Agent` 类；第三方 Agent 放在自己的包中。
3. **声明接入**：在 `.env` 的 `AGENTS` 中写入短名或 `模块路径:类名`，例如 `AGENTS = "siliconflow,mycorp.myagent:Agent"`。
4. **配置用途与档位**：需要时用 `AGENT_KINDS` 指定用途，用 `AGENT_COMPLEXITY` 指定复杂度档位。
5. **验证**：运行 `python scripts/test_agent_contract.py` 校验契约；对照 [`examples/agent_example.py`](./examples/agent_example.py) 查看会话队列、流式输出与指令处理的完整写法。

### 必须与可选成员

| 成员 | 必须 | 说明 |
| --- | --- | --- |
| `name` / `label` | 是 | 注册名（路由与 `/agent` 使用）/ 展示名（帮助与状态展示） |
| `kind` | 是 | 用途：`chat` 对话 / `coding` 编码 / `office` 办公，两级路由第一级依据 |
| `submit(chat_key, query, cwd, on_output, model)` | 是 | 提交任务，返回结束时 resolve 的 Future；用户在 IM 里看到的每一段内容都必须经 `on_output` 上抛，平台不会自动发送 `result.text` |
| `stop(chat_key)` / `reset(chat_key)` | 是 | 终止当前任务 / 清空会话记忆 |
| `busy(chat_key)` | 是 | 该会话是否有任务在执行或排队 |
| `available` | 否 | 前置条件是否满足；不满足时注册中心拒绝开启 |
| `complexity` | 否 | 复杂度档位：`simple` / `mid` / `complex`，同用途多 Agent 时参与选择 |
| `routing_hint` | 否 | 自动路由的候选描述，留空按 `kind` 生成 |
| `commands()` / `handle_command()` | 否 | Agent 特有指令，平台负责分发与帮助展示 |
| `list_models()` / `default_model` / `cwd_for()` / `close()` | 否 | 模型列表 / 默认模型 / 工作目录 / 资源释放 |

### 接入检查清单

- [ ] 类继承 `BaseAgent`，模块暴露 `Agent`。
- [ ] 所有用户可见输出（含最终结果）都经 `on_output` 上抛。
- [ ] `submit` 返回 Future，任务结束以 `AgentResult` 收尾。
- [ ] 同一 `chat_key` 的并发由 Agent 自身串行化。
- [ ] `available` 能反映 CLI 未安装、凭证未配置等前置条件。
- [ ] 已在 `AGENTS` 声明，`python manage.py agents` 中能看到并正常开关。

## 声明与加载规则

- `CHANNELS` / `AGENTS` 为逗号分隔，每项支持短名（内置目录约定）或 `模块路径:类名`（任意第三方包）。
- 构造签名统一为 `Channel(cfg)` / `Agent(cfg)`；加载时会校验是否实现对应基类，未实现则启动即报错。
- `ENABLED_AGENTS` 只决定初始开关，不影响是否加载；运行中用 `/agent on|off` 调整。

## 消息唤醒（可选）

默认关闭，不改变现有运行方式。开启后由以下三个相互独立的开关控制：

| 开关 | 作用位置 | 说明 |
| --- | --- | --- |
| `WATCH_ENABLED` | `watcher.py` | 总开关：`1` = 待命进程监听并唤醒服务，`0` = 启动即退出 |
| `WATCH_AUTOSTART` | `watcher.py` | 开机自启：`1` = 登录后静默启动（默认），`0` = 不随开机启动（下次开机生效） |
| `IDLE_EXIT` | `main.py` | 空闲自动休息：`1` = 超过 `IDLE_EXIT_MIN` 分钟无消息则退出，`0` = 常驻 |

工作流程：

1. 登录后启动的是轻量待命进程 `watcher.py`，只加载 `CHANNELS` 中的通道，不加载 Agent、不跑路由。
2. 收到任意 IM 消息时，先回一条占位文案（`WAKE_REPLY`，默认“正在唤醒…”），把消息写入交接文件，释放长连接，再按 `SERVICE_CMD`（默认 `python main.py`）启动完整服务。
3. 服务启动后读取交接文件，重新处理该消息，并通过 `send_push` 按会话身份回传结果，不依赖已失效的 reply_token。
4. 服务空闲超过阈值后退出，待命进程重新接管监听，形成“睡—醒”闭环。

Windows 开机自启：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_watcher.ps1             # 安装
powershell -ExecutionPolicy Bypass -File scripts\install_watcher.ps1 -Uninstall  # 卸载
```

计划任务使用 `pythonw.exe` 启动待命进程，拉起服务与探测进程时也带 `CREATE_NO_WINDOW`，登录后不会弹出命令行窗口。若希望保留计划任务但本次不自动启动，把 `WATCH_AUTOSTART` 设为 `0`。

开启 `WATCH_ENABLED=1` 后，应由待命进程常驻监听，不要再手动长期运行 `main.py`，两者会争抢同一条机器人长连接。相关配置见 [`.env.example`](./.env.example) 的消息唤醒段落。

## Web 控制台（可选）

默认关闭。开启后浏览器相当于第三个 IM，与机器人消息共享同一套路由、白名单与队列机制。

| 开关 | 默认 | 说明 |
| --- | --- | --- |
| `WEBUI_ENABLED` | `0` | `1` = 接入 webui 通道，提供管理台与聊天台 |
| `WATCH_HTTP_ENABLED` | `0` | `1` = 服务睡眠期间由待命进程占住端口托管页面，并提供一键唤醒（需 `WATCH_ENABLED=1`） |

| 页面 | 地址 | 用途 |
| --- | --- | --- |
| 管理台 | `http://127.0.0.1:8790` | 运行状态、通道连接、空闲倒计时、Agent 开关、任务、日志、配置查看与修改 |
| 聊天台 | `http://127.0.0.1:8790/chat.html` | 浏览器直连 Agent，支持自动路由、指令、会话记忆、排队与流式回复 |

端口由服务的 webui 通道与睡眠态托管共用，任一时刻只有一个进程占用。HTTP 仅绑定 `127.0.0.1`。聊天台身份为 `WEBUI_USER_ID`（默认 `webui`），设置 `ALLOWED_USERS` 白名单时需包含它。

## 行为说明

- **路由**：默认两级决策。第一级判定用途，消息只涉及一种用途的已开启 Agent 时跳过；第二级在该用途内选择：仅一个 Agent 直接使用，多个且 `COMPLEXITY_ROUTE=1` 且候选都配置了档位时按复杂度选择，其余情况取声明顺序第一个。决策模型未配置、失败或置信度不足时，第一级退化为关键词计分；`/agent` 可把会话固定到指定 Agent，不参与两级决策。
- **Agent 开关**：启动时按 `ENABLED_AGENTS` 初始化，运行中可动态启停；全部关闭时消息会提示先开启。
- **会话记忆**：单聊按用户 ID、群聊按群 ID 分别维护各 Agent 的会话；`/reset` 清空当前 Agent 的记忆。
- **任务队列**：同一会话串行执行，执行期间新消息排队；编码与办公类 Agent 全局默认同时只跑一个任务，避免并发修改同一目录。
- **流式回复**：任务输出实时推送；超长内容会自动轮换消息，以适配各 IM 的流式消息窗口限制。
- **用量统计**：任务结束时附上 Agent、模型与用量（token / 费用），没有用量数据时只显示 Agent 与模型。
- **安全**：是否自动接受文件编辑由各 Agent 的权限模式决定；可用 `ALLOWED_USERS` 限制使用者。

## 目录结构

```
main.py                 服务入口：按 .env 声明组装网关、路由与 Agent
watcher.py              待命进程入口：消息唤醒与睡眠态托管
manage.py               管理 CLI 入口
im_agent_gateway/
  config.py             配置加载（分层 .env、用途与档位覆写、热生效解析）
  plugins.py            接入声明解析：短名与“模块路径:类名”两种写法
  gateway/              网关层：IMChannel 基类、通道注册中心、消息交接、睡眠态托管
    channels/           内置通道：wecom、feishu、webui
  router/               路由层：两级决策、关键词降级、指令分发、空闲判定
  agents/               Agent 系统：BaseAgent、注册中心与各内置实现
  manage/               管理命令层：CLI / Web 管理台 / IM 指令共用
webui/                  Web 控制台页面（管理台、聊天台、样式与脚本）
examples/               通道与 Agent 的接入示例，均可直接运行
scripts/                开机自启脚本与各模块测试脚本
docs/spec/              功能规格文档
```

测试脚本位于 `scripts/`，每个 `test_*.py` 可独立运行，例如：

```powershell
python scripts/test_channel_contract.py
python scripts/test_agent_contract.py
```

## 内置实现

| 类型 | 名称 | 说明 |
| --- | --- | --- |
| IM 通道 | 飞书 `feishu` | 企业自建应用长连接收发消息 |
| IM 通道 | 企业微信 `wecom` | 智能机器人长连接收发消息 |
| IM 通道 | Web 控制台 `webui` | 本机 HTTP + WebSocket，管理台与聊天台 |
| Agent | SiliconFlow `siliconflow` | 对话：OpenAI 兼容接口、SSE 流式、多轮历史 |
| Agent | Qoder `qoder` | 编码：CLI 子进程、会话管理、输出解析 |
| Agent | WorkBuddy `workbuddy` | 办公：CLI 子进程、会话续接、流式解析 |
| Agent | Trae `trae` | 编码：开源 CLI，任务进度与结果提取 |

能否使用取决于各自的凭证与外部 CLI 安装情况；条件不满足时显示为不可用，不影响其他实现运行。

## 常见问题

- **连接反复重连**：部分 IM 只允许一条长连接。确认没有其他程序订阅同一账号；开启消息唤醒后，待命进程与服务任一时刻只能有一个持连接。
- **开启消息唤醒后服务没被启动**：确认 `WATCH_ENABLED=1`、`WATCH_AUTOSTART=1`（默认），且已执行 `scripts\install_watcher.ps1` 安装计划任务；再查看 `logs/im_agent_gateway.log`。
- **回复乱码或超时**：查看 `logs/im_agent_gateway.log`；超时可调大对应 Agent 的超时配置。
- **聊天不可用**：确认对话 Agent 的 API Key 有效，且 `python manage.py config check` 没有提示缺项。
- **Agent 显示不可用**：前置条件未满足（未安装 CLI、未登录或未配凭证）；满足后执行 `python manage.py agent on <名称>`。
- **Web 控制台打不开**：确认 `WEBUI_ENABLED=1`、服务已启动、端口未被占用；若希望服务休息时页面仍可访问并支持一键唤醒，需同时开启 `WATCH_ENABLED=1` 与 `WATCH_HTTP_ENABLED=1`。

## 参与贡献

欢迎提交 Issue 与 Pull Request。提交前请运行相关测试脚本，并确保不把 `.env`、日志、本机路径或凭证带入仓库。

## 许可证

[MIT](./LICENSE)
