# Web 控制台与多入口管理（Web Console）规格

## 1. 背景与目标

平台目前的状态（Agent 开关、会话路由、任务队列、空闲判定）全部在 `main.py` 进程内存里，没有任何对外查看与操作渠道；运行时操作只能通过 IM 消息发框架命令（`/agent on|off`、`/status`），静态配置只能手工编辑 `.env`。

目标：提供一套内嵌于服务进程的 Web 控制台（管理台 + 聊天台），并与 IM 框架命令、本地 CLI 共享同一个「管理命令层」，适配不同用户习惯；睡眠态由看门狗托管页面并支持一键唤醒。

约束：

- 平台通用：聊天台作为普通 IM 通道接入（实现 `IMChannel` + `CHANNELS` 声明），路由 / 白名单 / 会话队列 / 流式回复全部复用现有机制，平台核心不出现 UI 专属分支。
- 轻量：不引入 Node 构建链，前端为原生 ES modules 静态文件；新增依赖仅 fastapi + uvicorn。
- 向后兼容：所有新能力默认关闭，`WEBUI_ENABLED=0` 时行为与现状完全一致。

## 2. 术语

| 词 | 含义 |
| --- | --- |
| 管理命令层（manage） | 平台核心新组件：统一封装状态查询与开关操作的逻辑，IM / UI / CLI 三个入口共用 |
| webui 通道 | 实现 `IMChannel` 的本地 HTTP 通道：浏览器即第三个 IM |
| 管理端点（/api/*） | 挂在 webui 通道 HTTP 服务上的 REST 路由，直读进程内 registry/router 状态 |
| 睡眠态托管 | 看门狗在服务睡眠期间用轻量 HTTP 服务托管同一份静态页，提供状态与唤醒按钮 |
| 持久化写回 | 运行时开关变更时同步写回项目 `.env`，重启不丢 |

## 3. 需求

### 3.1 功能需求

- R1：管理台页面——服务状态（运行中 / 睡眠 / 离线、通道连接、空闲倒计时）、Agent 列表与开关、当前与最近任务（含用量）、日志尾部。
- R2：聊天台页面——浏览器直连 Agent，走完整路由（自动路由 / 指令 / 会话记忆 / 任务排队 / 流式回复），与 IM 消息同一套白名单校验。
- R3：Agent 开关三入口一致——IM `/agent on|off`、UI 管理端点、CLI 命令最终调用同一管理命令层；开关变更写回 `.env`，重启后保持。
- R4：CLI 配置——本地命令行查看 / 校验 / 修改配置（离线可用），在线命令（状态、开关）走管理端点。
- R5：睡眠态托管——`WATCH_ENABLED` 模式下服务睡眠期间页面仍可打开，显示睡眠状态并提供「唤醒服务」按钮；唤醒后端口无缝交回服务。
- R6：平台通用——webui 是普通通道；看门狗对通道的监听筛选基于通道自声明的能力属性，不按通道名分支。

### 3.2 开关

| 开关 | 默认 | 关闭 | 开启 |
| --- | --- | --- | --- |
| `WEBUI_ENABLED` | `0` | webui 通道不注册（与其他通道一致：不声明即不接入） | 加入 `CHANNELS` 后提供管理台 + 聊天台 |
| `WATCH_HTTP_ENABLED` | `0` | 看门狗不做睡眠态托管（睡眠时页面不可达，现状行为） | 睡眠期间托管静态页 + 状态 + 唤醒按钮 |

## 4. 设计

### 4.1 管理命令层（`im_agent_gateway/manage/`）

```
manage/
  commands.py   # AgentOps：开关 / 校验 / 错误文案（函数式，dispatcher 与 API、CLI 共用）
  persist.py    # .env 写回：改值保注释、缺键追加、原子替换
  api.py        # FastAPI APIRouter：管理端点（依赖注入 ManageService）
```

- `AgentOps.set_active(cfg, registry, name, active)`：执行开关 + 持久化写回，返回错误文案或 None。现有 `dispatcher.py` 的 `/agent on|off` 处理重构为调用它（IM 入口与 UI/CLI 行为天然一致，含写回）。
- `ManageService`：聚合状态（持 cfg/registry/router 引用），提供 `status()` / `agents()` / `tasks()` / `logs_tail()` / `config_view()`（脱敏：隐藏含 key/secret/token 的值）。
- 任务事件：`MessageRouter` 增加可选回调 `on_task_event`（默认 None），任务结束时上报 `(agent, model, usage, chat_key, 开始/耗时)`，`ManageService` 维护内存环形缓冲（最近 50 条）供管理台展示。

### 4.2 持久化写回（`persist.py`）

- 只写**项目目录的 `.env`**，绝不触碰上一级凭证文件（与配置分层原则一致）。
- 逐行处理：定位 `KEY=` 行原位替换；不存在则在文件末尾追加 `KEY=值`；注释、空行、其余行原样保留。
- 原子写：写临时文件后 `os.replace`，避免写一半被读。
- 一期写回键：`ENABLED_AGENTS`（Agent 开关时同步）。其余静态键的编辑由 CLI `config set` 与二期 UI 配置页覆盖。

### 4.3 webui 通道（`im_agent_gateway/gateway/channels/webui/`）

实现现有 `IMChannel` 契约，`CHANNELS` 声明短名 `webui` 即接入：

- `start()`：在服务事件循环内以 uvicorn Server task 运行 FastAPI app，绑定 `WEBUI_HOST:WEBUI_PORT`（默认 `127.0.0.1:8790`，仅本机可达）。
- `close()`：停 server task，断开所有 WebSocket。
- 聊天链路（`/ws/chat`，WebSocket）：
  - 浏览器发 `{"type":"text","content":"…"}`；通道构造 `InboundMessage(im="webui", user_id=WEBUI_USER_ID, chat_id="webui", chat_type="single", msg_id=uuid, reply_token=连接标识)`，经 `on_message` 上抛——之后路由、白名单、排队、指令与 IM 消息完全一致。
  - `send_text` / `send_stream`：按 `reply_token` 定位连接，回推 `{"type":"text"}` / `{"type":"stream","id":…,"content":…,"finish":…}`；流式节奏复用平台 `StreamSender`。
- 管理端点挂载（可选契约）：通道提供可选方法 `mount_admin(api_router)`；`main.py` 在注册通道后统一检查并注入 `manage.api.router`。不实现该方法的通道不受影响（与 `send_push` 的可选覆写模式一致）。
- `wake_listen = False`（见 4.5）。
- 静态文件：`channels/webui/static/` 由同一 app 以静态目录方式服务（管理页 `index.html`、聊天页 `chat.html`、`js/`、`css/`）。

### 4.4 管理端点（REST，挂在 webui 通道上）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/status` | 运行状态、通道连接状态、空闲倒计时（`IDLE_EXIT` 开启时） |
| GET | `/api/agents` | Agent 列表：name / label / kind / active / available / routing_hint |
| POST | `/api/agents/{name}/active` | body `{"active": bool}`；走 `AgentOps.set_active`（含写回） |
| GET | `/api/tasks` | 当前 inflight + 最近任务环形缓冲（agent / model / 用量 / 耗时） |
| GET | `/api/logs?lines=200` | `logs/im_agent_gateway.log` 尾部 |
| GET | `/api/config` | 当前生效配置（脱敏展示） |

### 4.5 睡眠态托管（`watcher.py`）

- 通道筛选：`IMChannel` 增加可选类属性 `wake_listen: ClassVar[bool] = True`（「看门狗可独立监听以触发唤醒」）。webui 覆写为 `False`（HTTP 被动型通道由服务自身承载）。看门狗加载通道时跳过 `wake_listen=False` 者——基于通道自声明能力，非通道名分支。
- 托管（`WATCH_HTTP_ENABLED=1` 时）：看门狗监听会话内用标准库 `http.server`（独立线程）占用 `WEBUI_PORT`，服务**同一份**静态目录（配置 `WEBUI_STATIC`，默认指向 webui 通道的 static/），并提供：
  - `GET /api/watch-state` → `{"state":"sleeping"}`（前端据此切换睡眠态 UI）。
  - `POST /api/wake` → 与 IM 消息唤醒同一流程：先停 HTTP 托管，再按 `SERVICE_CMD` 拉起服务，端口随服务启动接管。
- 时序：看门狗每轮 `_session` 开始时起托管、触发唤醒（IM 消息或 wake 按钮）前先停托管；服务退出后下一轮重新托管。端口任一时刻只有一个进程占用，交接无冲突。
- 前端探测：页面加载先 `GET /api/status`——可达即在线态；不可达再 `GET /api/watch-state`——可达即睡眠态（显示「服务睡眠中」+ 唤醒按钮，按钮打 `/api/wake` 后轮询等待在线）；都不可达为离线态（提示检查服务进程）。

### 4.6 CLI 入口（`manage.py`，项目根）

| 命令 | 依赖 | 说明 |
| --- | --- | --- |
| `python manage.py config show` | 离线 | 展示当前生效配置（脱敏，同 `/api/config` 口径） |
| `python manage.py config check` | 离线 | 校验声明 / 必填项 / 前置条件，给出可读建议 |
| `python manage.py config set KEY VALUE` | 离线 | 写回 `.env`（走 persist），提示需重启生效 |
| `python manage.py status` / `agents` | 在线 | 打管理端点（`http://127.0.0.1:WEBUI_PORT`），不可达时提示「服务睡眠中或未启动」 |
| `python manage.py agent on/off <名称>` | 在线 | 走 `POST /api/agents/{name}/active`，与 IM / UI 行为一致 |

在线命令复用依赖里已有的 httpx；离线命令直接复用 `config.load_config` 与 `persist`。

### 4.7 前端（原生 ES modules，零构建）

- 文件：`static/index.html`（管理台）、`static/chat.html`（聊天台）、`static/js/api.js`（REST 封装与状态探测）、`static/js/ws.js`（WebSocket 封装与自动重连）、`static/css/main.css`。
- 动效：CSS 关键帧实现 Q 弹反馈（按钮按压回弹、消息气泡入场、开关滑块的弹性过渡），全部 `transform`/`opacity` 驱动。
- 文案：面向普通用户的大白话（「服务正在休息」「点击唤醒」），标题与标签纯文字，不用表情符号。
- 无任何构建产物入库：提交的就是可直接运行的静态文件。

## 5. 配置项

新增到 `Config` 与 `.env.example`：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `WEBUI_ENABLED` | `0` | webui 通道接入开关（配合 `CHANNELS` 声明） |
| `WEBUI_HOST` | `127.0.0.1` | HTTP 绑定地址（默认仅本机） |
| `WEBUI_PORT` | `8790` | webui / 托管共用端口 |
| `WEBUI_USER_ID` | `webui` | 浏览器会话的 user_id（参与 `ALLOWED_USERS` 白名单校验） |
| `WATCH_HTTP_ENABLED` | `0` | 看门狗睡眠态托管开关 |
| `WEBUI_STATIC` | 包内 webui static 路径 | 睡眠态托管服务的静态目录 |

默认值全部为关闭态：升级后不影响任何现有行为。

## 6. 接口契约变更

均为可选、向后兼容：

- `IMChannel` 新增可选类属性 `wake_listen`（默认 True）与可选方法 `mount_admin(api_router)`（默认不实现）。
- `MessageRouter` 新增可选回调 `on_task_event`（默认 None，任务结束上报）。
- `InboundMessage` 字段不变；webui 消息复用现有字段。

## 7. 平台通用性论证

| 环节 | 平台统一做法 | 是否引入 UI 分支 |
| --- | --- | --- |
| 聊天接入 | webui 实现 `IMChannel`，经 `CHANNELS` 声明注册 | 否 |
| 消息路由 / 白名单 / 队列 / 流式 | 复用现有 dispatcher 逻辑 | 否 |
| Agent 开关 | `AgentOps.set_active` 单一入口，三入口共用 | 否 |
| 看门狗通道筛选 | 通道自声明 `wake_listen` 能力属性 | 否（无通道名分支） |
| 睡眠态托管 | 看门狗通用 HTTP 能力（任何通道都受益） | 否 |

## 8. 运行约束与边界

- 安全：HTTP 仅绑 `127.0.0.1`；聊天走 `ALLOWED_USERS` 白名单（需包含 `webui`，为空则全放行，与 IM 同口径）；不做登录鉴权（本机单用户场景）。
- 端口独占：`WEBUI_PORT` 在服务与看门狗托管之间交接；用户手动同时跑服务与看门狗时的端口冲突表现与现有长连接互踢一致（以日志提示为准）。
- 交棒边界：webui 消息不参与看门狗交棒重放（睡眠态页面只提供唤醒，不提供聊天输入）。
- 配置写回并发：开关操作为单进程串行事件；`os.replace` 原子写保证 CLI 与服务同时写 `.env` 时不会产生半截文件（最后写入者胜出）。
- `WEBUI_ENABLED=0` 或 `WATCH_HTTP_ENABLED=0` 时不新增任何监听端口。

## 9. 验收标准

- A1：全部默认关闭时，启动行为、日志、端口占用与现状完全一致。
- A2：`CHANNELS` 声明 `webui` 后，浏览器聊天全链路可用：WS 消息 → 归一化 → 路由（自动路由与指令均生效）→ 流式回传渲染。
- A3：Agent 开关在 IM `/agent on|off`、UI、CLI 三入口行为一致，且写回 `.env`，重启后保持。
- A4：`/api/config` 与 CLI `config show` 对 key/secret/token 类值脱敏。
- A5：`persist` 写回保留原有注释与结构、原子替换、不触碰上一级凭证文件。
- A6：睡眠态（`WATCH_ENABLED=1` 且 `WATCH_HTTP_ENABLED=1`）：页面可打开并显示睡眠状态；唤醒按钮拉起服务，端口交接无冲突；IM 消息唤醒路径不回归。
- A7：看门狗不加载 `wake_listen=False` 的通道；平台核心（watcher / manage / dispatcher）无 `im == "webui"` 式通道名分支。
- A8：CLI 离线命令在服务未运行时可用；在线命令不可达时给出明确提示。
- A9：前端为纯静态 ES modules，仓库无 Node 构建产物、无 node_modules。
- A10：管理台可查看通道连接状态、Agent 开关状态、当前/最近任务与日志尾部。

## 10. 测试计划（TDD）

| 用例 | 验证点 |
| --- | --- |
| `scripts/test_manage_commands.py` | 开关成功 / 未知 Agent / 不可用 Agent 错误文案；写回联动（FakeRegistry，无网络） |
| `scripts/test_env_persist.py` | 改值保注释、缺键追加、原子替换、只动项目 `.env`（无网络） |
| `scripts/test_admin_api.py` | 管理 API 各端点返回结构与脱敏（FastAPI TestClient + Fake，无网络） |
| `scripts/test_webui_channel.py` | 通道契约：归一化上抛、`send_text`/`send_stream` 推送、`mount_admin` 注入、`wake_listen=False`（TestClient WS + FakeAgent） |
| `scripts/test_watcher_http.py` | 托管静态页、`watch-state`、`wake` 触发停托管拉服务流程；跳过 `wake_listen=False`（monkeypatch，无网络） |
| `scripts/test_cli.py` | 离线 `config show/check/set`；在线命令经 httpx MockTransport 打假 API |

实现顺序：先写以上用例（红），再按提交拆分逐项实现。

## 11. 提交拆分

1. 管理命令层 + `.env` 持久化（dispatcher `/agent on|off` 重构对齐）
2. 管理 API + `main.py` 装配（ManageService、任务事件上报）
3. webui 通道（HTTP 承载、WS 聊天、静态服务、`wake_listen`）
4. 前端页面（管理台 + 聊天台 + 状态探测 + 动效）
5. 看门狗睡眠态托管 + 唤醒按钮
6. CLI 入口 `manage.py`
7. 文档（README 能力表 / 使用方式 / 配置说明、`.env.example`）
