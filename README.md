# IM-Agent Gateway

**Unified IM-Agent Access Platform · IM Agent 统一接入平台**

一个把即时通讯（IM）统一接入多个 Agent 的网关平台：任意 IM 通道与任意 Agent 系统都通过接入契约挂到平台上，由平台统一完成消息归一化、智能路由、指令分发与流式回传。接入一个新的 IM 或新的 Agent，不需要改动平台任何现有代码。

> 本仓库内置的 IM 通道与若干 Agent 实现仅用于验证接入契约和连通性，是实验性示例代码；**平台本体（网关 / 路由 / 接入契约）才是本项目的重点**。示例实现随时可替换为真实的通道与 Agent。

## 架构

```
任意 IM App（单聊 / 群 @机器人）
        │  gateway/ 网关：多 IM 通道接入（实现 IMChannel 并在 CHANNELS 声明即接入）
        ▼
IM-Agent Gateway 桥接服务（Python asyncio，本机运行）
        │  router/ 路由：框架命令（切换/开关 Agent）+ 消息分发（决策模型路由，失败降级关键词正则）
        ├──▶ Agent 聊天类（通用问答）
        ├──▶ Agent 编码类（写代码 / 修 bug / 跑命令 / git 操作）
        └──▶ Agent 办公类（文档 / 表格 / 日常自动化）
```

平台只对接入方提两个要求：**实现对应基类**、**在 `.env` 里声明**。注册中心、路由与决策模型对具体实现零感知。

## 平台核心能力

| 能力 | 说明 |
| --- | --- |
| 多 IM 接入 | 实现 `IMChannel` 即可接入新 IM；消息统一归一化为 `InboundMessage`，路由层不感知具体协议 |
| 多 Agent 接入 | 实现 `BaseAgent` 即可接入新 Agent；统一 `submit/stop/reset/busy` 接口，会话级并发控制 |
| 智能路由 | 默认由决策模型根据消息内容在已开启的 Agent 间自动选择（含置信度），未配置或失败/低置信度时自动退化为关键字意图解析（按各 Agent 声明的意图关键词计分选路） |
| 框架命令 | `/agent`（切换/开关 Agent）、`/help`、`/status` 由平台提供；Agent 特有指令由各 Agent 自行声明并参与帮助展示 |
| 流式回复 | 平台定义统一流式发送接口（`send_stream`）；不支持流式的 IM 可退化为整条发送 |
| 会话管理 | 每个 IM 会话（单聊 / 群聊）分别维护各 Agent 的独立会话与记忆，`/reset` 一键清空 |
| 任务队列 | 同一会话内任务串行执行，期间新消息自动排队；编码类 Agent 全局默认单任务，避免并发改动同一目录 |
| 用量统计 | 任务结束自动附上执行的 Agent、模型与用量（token / 费用），有数据才展示 |
| 白名单安全 | 支持按用户 ID 限制可使用范围；编码 Agent 编辑自动接受需显式配置 |
| 消息唤醒 | 可选：开机只跑轻量看门狗监听 IM，收到消息再唤醒完整服务，空闲自动退回（睡-醒闭环），对 IM 通道一致 |
| Web 控制台 | 可选：内嵌管理台（状态 / Agent 开关 / 任务 / 日志 / 配置）与聊天台（浏览器直连 Agent，走完整路由），与 IM 消息同一套机制 |
| 多入口管理 | Agent 开关与配置在 IM 指令、Web 管理台、本地 CLI（`manage.py`）三处操作同一管理命令层，行为一致且写回 `.env` |

## 快速开始

### 1. 安装依赖

```powershell
pip install -r requirements.txt
```

### 2. 配置 `.env`

从 [`.env.example`](./.env.example) 复制出本项目 `.env`，至少声明接入哪些 IM 通道与 Agent：

```env
CHANNELS = "wecom"          # 内置示例 IM 通道；第三方通道用 "模块路径:类名"
AGENTS   = "siliconflow,qoder,workbuddy,trae"   # 内置示例 Agent
```

> **配置分层**：保密凭证（Token / API Key 等）放在**上一级目录的 `.env`**，不随项目文件提交；同名配置优先级：系统环境变量 > 上一级 `.env` > 项目 `.env` > 代码默认值。
>
> 内置示例实现所需的额外 CLI 与凭证，见各自目录及 `.env.example` 内注释；不满足前置条件的示例实现会自动注册为停用状态，不影响平台运行。

### 3. 启动

```powershell
python main.py
```

看到类似 `服务已启动：1 个 IM 通道…Agent：…` 即启动成功。也可以双击 `run.bat`。

## 使用方式

| 操作 | 说明 |
| --- | --- |
| 单聊直接发消息 | 自动路由：由决策模型路由到最合适的 Agent（不可用时按编码关键词降级） |
| 群里 @机器人 | 内容为 `@机器人名 任务描述`，@ 前缀由通道层剥离 |
| `/agent <名称>\|auto` | 框架命令：固定到某个 Agent / 恢复自动路由 |
| `/agent on\|off <名称>` | 框架命令：运行时开启/关闭 Agent（无需重启） |
| `/help` / `/status` | 框架命令：帮助（含各 Agent 指令分组）/ 服务状态 |
| Agent 特有指令 | 由各 Agent 系统提供（如 `/cd`、`/ws`、`/model`、`/stop`、`/reset`），路由到哪个 Agent 就由哪个 Agent 处理 |

指令分层：切换/开关 Agent 的命令由框架提供；其余指令由各 Agent 系统提供，不支持的指令会提示未知命令。

## 消息唤醒（可选）

默认关闭，不影响现有运行方式。开启后由两个**相互独立**的开关分别控制两个进程：

| 开关 | 生效位置 | 说明 |
| --- | --- | --- |
| `WATCH_ENABLED` | `watcher.py` | 看门狗总开关：`1`=监听并唤醒，`0`=启动即退出 |
| `IDLE_EXIT` | `main.py` | 服务空闲自动退开关：`1`=空闲超时退出，`0`=常驻 |

工作方式：

1. 开机自启的是**看门狗** `watcher.py`（只加载 CHANNELS 通道、不加载 Agent、不跑路由，保持轻量），它**不启动完整服务**。
2. 收到任意 IM 消息时，看门狗先回一条占位文案（`WAKE_REPLY`，默认「正在唤醒…」），把消息写入交接文件，释放长连接，再按 `SERVICE_CMD`（默认 `python main.py`）拉起完整服务。
3. 服务启动后读到交接文件，重新处理该消息并把结果经 `send_push`（按 `chat_id`/`chat_type` 会话级回传，不依赖旧的 reply_token）回给发送方。
4. 若开启 `IDLE_EXIT`，服务空闲超过 `IDLE_EXIT_MIN` 分钟后自动退出，看门狗重新接管监听——形成「睡-醒」闭环。

> 平台通用性：整套机制只依赖 `IMChannel`/`InboundMessage` 契约，看门狗、交接、空闲判定均在平台核心里按同一套逻辑工作，不出现 `im == "feishu"` / `im == "wecom"` 分支；唯一通道差异落在可选方法 `send_push` 的覆写里（飞书默认即可、企微覆写为按 chatid 主动推送），与现有 `send_welcome`/`send_stream` 的逐通道退化模式一致。

Windows 开机自启（注册「登录时运行 watcher.py」的计划任务）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_watcher.ps1          # 安装
powershell -ExecutionPolicy Bypass -File scripts\install_watcher.ps1 -Uninstall   # 卸载
```

相关配置见 [`.env.example`](./.env.example) 的「消息唤醒 / 看门狗」段。注意：`WATCH_ENABLED=1` 时应由看门狗常驻监听，不要再手动长期运行 `main.py`，二者会抢同一机器人的长连接。

## Web 控制台（可选）

默认关闭，开启后浏览器即可作为第三个 IM 使用，与机器人消息走同一套路由、白名单与队列机制。相关配置见 [`.env.example`](./.env.example) 的「Web 控制台」段。

| 开关 | 默认 | 说明 |
| --- | --- | --- |
| `WEBUI_ENABLED` | `0` | `1` = 接入 webui 通道（自动并入 `CHANNELS`），提供管理台与聊天台 |
| `WATCH_HTTP_ENABLED` | `0` | `1` = 服务睡眠期间由看门狗占住同一端口托管页面，显示「服务正在休息」并提供一键唤醒按钮（需 `WATCH_ENABLED=1`） |

开启 `WEBUI_ENABLED=1` 后，在本机浏览器访问：

| 页面 | 地址 | 用途 |
| --- | --- | --- |
| 管理台 | `http://127.0.0.1:8790` | 运行状态、通道连接、空闲倒计时、Agent 开关、进行中与最近任务、日志尾部、脱敏配置 |
| 聊天台 | `http://127.0.0.1:8790/chat.html` | 浏览器直连 Agent：自动路由、指令、会话记忆、任务排队、流式回复与 IM 消息完全一致 |

端口（`WEBUI_PORT`）由 webui 通道与睡眠态托管共用：服务运行时由服务占用，服务睡眠时（开启 `WATCH_HTTP_ENABLED=1`）由看门狗轻量页面接管，唤醒按钮按下后先停托管再拉起服务，端口交接无冲突。HTTP 仅绑定 `127.0.0.1`（本机访问）；聊天台身份为 `WEBUI_USER_ID`（默认 `webui`），若设置了 `ALLOWED_USERS` 白名单须包含它。

## 多入口管理（CLI）

`manage.py` 与 IM 指令、Web 管理台操作同一管理命令层：Agent 开关在三处行为一致且写回 `.env`，重启后保持。

| 命令 | 依赖服务 | 说明 |
| --- | --- | --- |
| `python manage.py config show` | 否 | 展示当前生效配置（敏感值已隐藏） |
| `python manage.py config check` | 否 | 检查通道必填项、白名单与 Agent 前置条件，给出可读建议 |
| `python manage.py config set KEY VALUE` | 否 | 修改配置并写回项目 `.env`（重启生效） |
| `python manage.py status` / `agents` | 是 | 服务状态与 Agent 列表（打管理端点；不可达时提示服务睡眠中或未启动） |
| `python manage.py agent on\|off <名称>` | 是 | 开关 Agent，与 `/agent on\|off`、Web 管理台行为一致 |

## 平台接入契约

### 接入 IM 通道

实现 `IMChannel` 基类，模块暴露 `Channel` 类（构造签名 `Channel(cfg)`）。注册需要提供的数据：

| 数据 | 提供方式 | 说明 |
| --- | --- | --- |
| `name` | 类属性 | 通道唯一标识，注册与消息 `im` 字段使用 |
| 凭证、连接参数 | `.env` → `Config` | 各 IM 自行定义所需变量，构造时从 cfg 读取 |
| 归一化消息 | `InboundMessage` / `InboundEvent` | 把协议报文转成统一模型，经 `on_message` / `on_event` 回调上抛（由网关注入） |

需要实现的接口：

| 方法 | 必须 | 说明 |
| --- | --- | --- |
| `start()` / `close()` | 是 | 建立连接长期运行 / 优雅退出 |
| `send_text(msg, content)` | 是 | 回复文本 |
| `send_stream(msg, stream_id, content, finish)` | 是 | 流式回复；不支持流式的 IM 可退化为整条发送 |
| `send_welcome(evt, content)` | 否 | 进入会话的欢迎语，默认退化为 `send_text` |
| `send_push(msg, content)` | 否 | 按会话身份（`chat_id`/`chat_type`）主动回传，不依赖 reply_token；用于消息唤醒后的结果回传，默认退化为 `send_text` |

> `send_push` 是消息唤醒能力的可选回传契约：本就按 chat_id 回复的通道（如飞书）无需覆写；按 req_id 连接期回复的通道（如企微）覆写为按 chatid 主动推送即可。

完整接入示例见 [examples/im_channel_example.py](./examples/im_channel_example.py)：一个虚构 IM「DemoIM」的通道实现，每个方法都有「平台何时调用我 / 参数怎么填」的教程式注释，可直接运行观察「报文进来 → 归一化 → 路由 → 回复发出」全链路。契约的可运行校验另见 `scripts/test_channel_contract.py`。

在 `.env` 的 `CHANNELS` 里声明即完成接入：放进 `im_agent_gateway/gateway/channels/<名>/` 目录用短名；第三方包用「模块路径:类名」，如 `wecom,mycorp.myim:Channel`。

### 接入 Agent

实现 `BaseAgent` 基类，模块暴露 `Agent` 类（构造签名 `Agent(cfg)`）。注册需要提供的数据：

| 数据 | 提供方式 | 说明 |
| --- | --- | --- |
| `name` / `label` | 类属性 | 注册名（路由与 `/agent` 命令用）/ 展示名（帮助与状态里出现） |
| `kind` | 类属性 | `chat` / `coding`，关键词兜底路由用 |
| `routing_hint` | 类属性，可选 | 自动路由的候选描述，留空按 kind 生成默认 |
| 凭证等配置 | `.env` → `Config` | 构造时从 cfg 读取，并反映到 `available` |

需要实现的接口：

| 方法 | 必须 | 说明 |
| --- | --- | --- |
| `submit(chat_key, query, cwd, on_output, model)` | 是 | 提交任务；返回任务结束时 resolve 的 Future（结果 `AgentResult`）。一切用户可见输出（含最终结果）经 `on_output` 流式上抛，平台不会自动下发 `result.text` |
| `stop(chat_key)` | 是 | 终止该会话当前任务 |
| `reset(chat_key)` | 是 | 清空该会话记忆 |
| `busy(chat_key)` | 是 | 该会话是否有任务在执行或排队 |
| `available` | 否 | 前置条件（如未配 API Key）；不满足时注册中心拒绝开启 |
| `commands()` / `handle_command()` | 否 | Agent 特有指令（框架负责分发与帮助展示） |
| `list_models()` / `default_model` / `cwd_for()` / `close()` | 否 | 模型列表 / 默认模型 / 工作目录 / 资源释放 |

完整接入示例见 [examples/agent_example.py](./examples/agent_example.py)：一个虚构「回声 Agent」的实现，教程式注释覆盖会话队列串行、流式输出、特有指令、`/stop` 终止、会话记忆等平台约定的 Agent 侧实现方式，可直接运行观察全链路。契约的可运行校验另见 `scripts/test_agent_contract.py`。

在 `.env` 的 `AGENTS` 里声明即完成接入：放进 `im_agent_gateway/agents/<名>/` 目录用短名；第三方包用「模块路径:类名」，如 `siliconflow,mycorp.myagent:Agent`。

### 声明与加载规则

- `CHANNELS` / `AGENTS` 逗号分隔，每项支持短名（内置目录约定 `channels/<名>`、`agents/<名>`）或 `模块路径:类名`（任意第三方包）。
- 构造签名统一 `Channel(cfg)` / `Agent(cfg)`；加载时校验是否实现对应基类，未实现的声明启动即报错。
- `ENABLED_AGENTS` 只控制初始开关，与是否加载无关；运行中用 `/agent on|off <名称>` 调整。

## 行为说明

- **消息路由**：默认自动路由——先由决策模型根据消息内容在已开启的 Agent 中选择最优者（含概率与置信度）；决策模型未配置、调用失败或置信度低于阈值时，退化为关键字意图解析——按各 Agent 声明的意图关键词（`routing_keywords`）对消息计分，得分最高的 Agent 胜出，全部未命中则走默认 Agent；`/agent` 可把某会话固定到任一 Agent。
- **Agent 开关**：启动时按 `.env` 的 `ENABLED_AGENTS` 决定初始开关；运行中可发 `/agent on|off <名称>` 动态启停，全部关闭时消息会提示先开启。
- **会话记忆**：每个 IM 会话（单聊 userid / 群聊 chatid）分别维护各 Agent 的会话状态；`/reset` 清空当前路由 Agent 的会话记忆。
- **任务队列**：同一会话内任务串行执行，执行期间新消息自动排队；编码/办公类 Agent 全局默认同时只跑一个任务，避免多会话并发改同一目录。
- **流式回复**：支持流式的 Agent 结果实时推送到 IM；超长任务会自动轮换消息（受 IM 流式消息窗口限制）。
- **用量统计**：每条任务结束的回复末尾会附上本次执行的 Agent、模型与用量（token / 费用）；无用量数据时仅显示 Agent 与模型。
- **安全**：是否自动接受编辑由各 Agent 权限模式决定；可通过 `ALLOWED_USERS` 白名单限制使用者。

## 目录结构

```
main.py              服务入口（按 .env 声明组装网关/路由/Agent，接入新 IM/Agent 无需改这里）
watcher.py           看门狗入口（消息唤醒：只监听 IM，收到消息拉起服务，见「消息唤醒」节）
manage.py            管理 CLI 入口（config show/check/set 离线可用；status/agents/agent on|off 在线，见「多入口管理」节）
im_agent_gateway/
  config.py          配置加载（分层 .env；CHANNELS/AGENTS 接入声明）
  plugins.py         插件加载：把 CHANNELS/AGENTS 声明解析成通道与 Agent 实例（短名 / 模块路径两种写法）
  manage/            管理命令层（IM 指令 / Web 管理台 / CLI 三入口共用）
    commands.py      AgentOps：Agent 开关 + .env 写回（返回错误文案或 None）
    persist.py       .env 持久化写回：原位替换保注释、缺键追加、原子落盘
    api.py           管理端点（FastAPI 路由：status/agents/tasks/logs/config）
    service.py       ManageService：状态聚合 + 最近任务环形缓冲 + 配置脱敏视图
  gateway/           网关层：多 IM 通道接入（平台核心）
    base.py          IMChannel 抽象基类 + 归一化消息模型（InboundMessage/InboundEvent）
    registry.py      Gateway 注册中心（register / run / close）
    handoff.py       消息交棒：看门狗与服务间的唤醒消息序列化传递（dump/load）
    watch_http.py    睡眠态 HTTP 托管：看门狗占住 webui 端口提供页面与唤醒按钮
    channels/        各 IM 通道目录（每个 IM 一个子目录，暴露 Channel 类即可接入）
      wecom/         示例通道：企业微信智能机器人（长连接，纯协议层 + 报文归一化）
      feishu/        示例通道：飞书企业自建应用（lark-oapi 长连接，归一化 + 流式回复）
      webui/         示例通道：本机 Web 控制台（HTTP + WebSocket；页面在项目根 webui/，见「Web 控制台」节）
  router/            路由层：归一化消息分发（IM 无关）
    dispatcher.py    消息路由：框架命令（/agent、/help、/status）、白名单、Agent 分发与指令转发、流式回复、空闲判定
    jev_router.py    决策路由：调用决策模型网关，失败/低置信度自动降级
  agents/            Agent 系统（注册与运行时开关）
    base.py         BaseAgent 抽象基类 + AgentResult（统一接口）
    registry.py     AgentRegistry 注册中心（按名注册、查询与开关）
    siliconflow/    示例 Agent：聊天（OpenAI 兼容 API，SSE 流式，多轮历史）
    qoder/          示例 Agent：编码（CLI 子进程执行、会话管理、输出解析）
    workbuddy/      示例 Agent：办公（CLI 子进程执行、会话续接、流式解析）
    trae/           示例 Agent：编码（开源 CLI，任务进度/结果提取）
webui/               Web 控制台页面（管理台 index.html + 聊天台 chat.html + css/js，由 webui 通道与睡眠态托管共同服务）
examples/
  im_channel_example.py    IM 通道接入示例（教程式注释 + 全链路演示，可直接运行）
  agent_example.py         Agent 接入示例（教程式注释 + 全链路演示，可直接运行）
scripts/
  install_watcher.ps1      看门狗开机自启计划任务的安装/卸载脚本（Windows）
  test_registry.py        网关与路由注册测试（FakeChannel，无网络依赖）
  test_channel_contract.py  IM 接入契约测试：第三方通道仅靠实现+声明接入（无网络依赖）
  test_agent_contract.py    Agent 接入契约测试：第三方 Agent 仅靠实现+声明接入（无网络依赖）
  test_wake_handoff.py     消息交棒测试：dump/load 往返与损坏容错（无网络依赖）
  test_channel_push.py     send_push 契约测试：默认退化与覆写（无网络依赖）
  test_idle_monitor.py     空闲判定测试：阈值、inflight、replay 整条回传（无网络依赖）
  test_watch_flow.py       唤醒全流程测试：占位/交接/拉起/释放（monkeypatch，无网络依赖）
  test_jev_route.py      决策路由连通性测试
  test_keyword_route.py    关键字意图路由测试：Jev 未配置/失败时的退化选路（无网络依赖）
  test_list_cmds.py      Agent 指令列表测试
  test_env_persist.py    .env 写回测试：保注释 / 缺键追加 / 原子替换（无网络依赖）
  test_manage_commands.py 管理命令层测试：开关三入口一致性 + 写回联动（无网络依赖）
  test_admin_api.py       管理端点测试：状态 / 开关 / 任务事件 / 日志 / 配置脱敏（无网络依赖）
  test_webui_channel.py   webui 通道测试：WS 聊天归一化 / 流式推送 / 静态托管（无网络依赖）
  test_watcher_http.py   睡眠态托管测试：状态接口 / 唤醒按钮 / 通道跳过（无网络依赖）
  test_cli.py             管理 CLI 测试：离线命令 + 在线命令（MockTransport，无网络依赖）
  其余 test_*.py         各示例实现（通道/Agent）的连通性测试
```

> `channels/wecom/` 与 `agents/` 下各目录均为验证接入契约的**实验性示例**：实现思路可参考，真实使用请替换为生产级通道与 Agent。

## 已验证连通性（仅供参考）

以下为代码中内置、均实际跑通过「连接 → 收发消息 → Agent 回复」链路的通道与 Agent，仅供参考——实际可用性取决于各自的凭证与平台侧配置。

| 类型 | 名称 | 用途 |
| --- | --- | --- |
| IM 通道 | 飞书 `feishu` | 企业自建应用长连接收发消息 |
| IM 通道 | 企业微信 `wecom` | 智能机器人长连接收发消息 |
| Agent | SiliconFlow `siliconflow` | 聊天（SSE 流式、多轮历史） |
| Agent | Qoder `qoder` | 编码 |
| Agent | WorkBuddy `workbuddy` | 办公 |
| Agent | Trae `trae` | 编码 |

## 常见问题

- **连接反复重连**：某些 IM 只允许一条长连接，确认没有其他程序也在订阅同一账号（开启 `WATCH_ENABLED=1` 后，看门狗与服务任一时刻只有一个持连接，勿再手动长期跑 `main.py`）。
- **开启看门狗但服务没被唤醒**：确认 `.env` 里 `WATCH_ENABLED = "1"`，且开机自启计划任务已安装（`scripts\install_watcher.ps1`）；再看 `logs/im_agent_gateway.log`。
- **回复乱码/超时**：检查 `logs/im_agent_gateway.log`；任务超时调大对应 Agent 的超时配置。
- **聊天走不通**：确认上一级目录 `.env` 的 API Key 有效（各示例实现带连通性测试脚本可验证）。
- **某 Agent 报不可用**：该 Agent 前置条件未满足（未安装 CLI / 未登录 / 未配凭证）；满足后发 `/agent on <名称>` 开启。
- **Web 控制台页面打不开**：确认 `.env` 里 `WEBUI_ENABLED = "1"`、服务已启动（`python main.py`）、端口 `WEBUI_PORT`（默认 8790）未被占用；若希望服务睡眠期间页面也能打开并提供唤醒按钮，需同时设置 `WATCH_ENABLED = "1"`、`WATCH_HTTP_ENABLED = "1"` 并由 `watcher.py` 常驻。

## 开源协议

[MIT](./LICENSE)
