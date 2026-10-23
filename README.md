# IM-Agent Gateway

**Unified IM-Agent Access Platform · IM Agent 统一接入平台**

把即时通讯（IM）统一接入多个 Agent：在企业微信里通过「智能机器人」驱动三个 Agent 系统——企微发消息 → IM-Agent Gateway 用 Jev 决策模型（System One）自动路由 → SiliconFlow（聊天）/ `qoderclicn`（编码）/ WorkBuddy（办公）→ 结果流式回传企微。

## 架构

```
企业微信 App（单聊 / 群 @机器人）
        │  WebSocket 长连接（wss://openws.work.weixin.qq.com）
        ▼
IM-Agent Gateway 桥接服务（Python asyncio，本机运行）
        │  gateway/ 网关：多 IM 通道接入（新 IM 实现 IMChannel 并在 CHANNELS 声明即接入）
        │
        │  router/ 路由：框架命令（切换/开关 Agent）+ 消息分发（Jev 决策模型路由，失败降级关键词正则）
        ├──▶ agents/siliconflow/ 聊天（SiliconFlow，OpenAI 兼容 API，SSE 流式）
        ├──▶ agents/qoder/       编码（Qoder CN CLI，accept_edits 模式，在配置的仓库目录中执行）
        └── agents/workbuddy/   办公（腾讯 WorkBuddy CLI：文档/表格/日常自动化）
```

企业微信官方长连接模式：无需公网 IP 和回调 URL，也不用做消息加解密。

## 快速开始

### 1. 安装 Qoder CN CLI 并登录

```powershell
npm install -g @qodercn-ai/qoderclicn
qoderclicn login        # 浏览器登录你的 Qoder CN 账号（与 IDE 同账号）
```

> 也可用 Personal Access Token：在 https://qoder.com.cn/account/integrations 生成，
> 填入 `.env` 的 `QODERCN_PERSONAL_ACCESS_TOKEN`（与交互式登录二选一）。

### 2. 安装 WorkBuddy CLI 并登录（可选）

WorkBuddy 办公 Agent（文档/表格/日常自动化）需要单独安装 CLI：

```powershell
npm install -g @tencent-ai/codebuddy-code
codebuddy login        # 扫码/账号登录你的 WorkBuddy 账号
```

> 未安装时 WorkBuddy Agent 会注册为停用状态，不影响聊天与编码 Agent。

### 3. 配置 `.env`

> **配置分层**：从 [`.env.example`](./.env.example) 复制出本项目 `.env`；保密凭证（`BOT_ID`/`SECRET`/`API_KEY` 等）放在**上一级目录的 `.env`**，不随项目文件提交，本项目 `.env` 仅维护个性化配置。
> 同名配置优先级：系统环境变量 > 上一级 `.env` > 项目 `.env` > 代码默认值。

| 变量 | 说明 |
| --- | --- |
| `CHANNELS` / `AGENTS` | 平台接入声明：加载哪些 IM 通道与 Agent（短名或 `模块路径:类名`，详见「平台接入契约」） |
| `BOT_ID` / `SECRET` | 企微智能机器人长连接凭证（位于上一级目录 `.env`） |
| `QODER_CWD` | 编码任务的工作目录（Git 仓库），留空默认项目根目录 |
| `QODER_MODEL` | 模型名，留空用 CLI 默认（如 `qwen3.8-flash`、`kimi-k3`） |
| `API_KEY` | SiliconFlow API Key（位于上一级目录 `.env`，在 cloud.siliconflow.cn 控制台获取） |
| `SF_MODEL` | SiliconFlow 默认模型，如 `deepseek-ai/DeepSeek-V4.1-Flash` |
| `SF_BASE_URL` / `SF_MAX_TOKENS` / `SF_TIMEOUT_SEC` | SiliconFlow 对话参数（一般无需修改） |
| `WORKBUDDY_BIN` | WorkBuddy CLI 命令名，默认 `codebuddy` |
| `WORKBUDDY_MODEL` | WorkBuddy 模型名，留空用 CLI 默认 |
| `WORKBUDDY_CWD` | 办公任务的工作目录，默认项目根目录 |
| `WORKBUDDY_PERMISSION_MODE` | 权限模式（`acceptEdits`/`auto`/`dontAsk`/`plan`），默认 `acceptEdits` |
| `WORKBUDDY_TIMEOUT_SEC` | 办公任务单次超时（秒），默认 1800 |
| `DEFAULT_AGENT` | 默认 Agent：`siliconflow`=聊天 / `qoder`=编码 / `workbuddy`=办公 |
| `ENABLED_AGENTS` | Agent 启用开关（注册名逗号分隔，如 `qoder,siliconflow,workbuddy`；留空=全部启用） |
| `AUTO_ROUTE` | 自动路由开关（`1`=开）：由 Jev 决策模型选择 Agent，不可用时降级为编码关键词正则 |
| `MAKERS_MODELS_KEY` | Makers 网关 API Key，用于调用 Jev 决策模型（位于上一级目录 `.env`，EdgeOne 控制台获取） |
| `JEV_BASE_URL` / `JEV_MODEL` | Jev 网关地址与模型 ID（默认 `https://ai-gateway.edgeone.link/v1` + `@makers/jev`） |
| `JEV_ROUTE` / `JEV_TIMEOUT_SEC` / `JEV_MIN_CONFIDENCE` | Jev 路由开关 / 单次决策超时（默认 6 秒）/ 置信度阈值（默认 0.5，低于则走默认 Agent） |
| `ALLOWED_USERS` | 白名单（企微 userid，逗号分隔），留空不限制 |
| `QODER_TIMEOUT_SEC` | 单任务超时（秒），默认 1800 |
| `STREAM_INTERVAL_SEC` | 流式消息推送间隔，默认 1 秒（企微限 30 条/分钟） |

### 4. 安装依赖并启动

```powershell
pip install -r requirements.txt
python main.py
```

看到 `长连接建立，已订阅机器人 xxx` 即启动成功。也可以双击 `run.bat`。

## 使用方式

| 操作 | 说明 |
| --- | --- |
| 单聊直接发消息 | 自动路由：由 Jev 决策模型路由到最合适的 Agent（不可用时按编码关键词降级） |
| 群里 @机器人 | 内容为 `@机器人名 任务描述`，自动剥离 @ 前缀 |
| `/agent qoder\|sf\|wb\|auto` | 框架命令：切换路由 Agent（固定编码 / 固定聊天 / 固定办公 / 自动路由） |
| `/agent on\|off <名称>` | 框架命令：运行时开启/关闭 Agent 服务（无需重启） |
| `/help` / `/status` | 框架命令：帮助（含各 Agent 指令分组）/ 服务状态 |
| `/cd <目录>` / `/ws` | 编码 Agent 指令：切换工作目录 / 工作区（仅路由到编码 Agent 时可用） |
| `/model <模型>` / `/models` | Agent 指令：切换 / 查看当前会话 Agent 的模型 |
| `/stop` / `/reset` | Agent 指令：终止任务 / 清空会话记忆（作用于当前路由的 Agent） |

## 平台接入契约

网关对接入方只提两个要求：**实现对应基类**、**在 `.env` 里声明**。注册中心、路由与决策模型对具体实现零感知——新增一个 IM 或一个 Agent 不需要改动任何现有代码，未满足契约的声明会在启动时报错退出。

### 接入 IM 通道

实现 `IMChannel` 基类，模块暴露 `Channel` 类（构造签名 `Channel(cfg)`）。注册需要提供的数据：

| 数据 | 提供方式 | 说明 |
| --- | --- | --- |
| `name` | 类属性 | 通道唯一标识（如 `wecom`），注册与消息 `im` 字段使用 |
| 凭证、连接参数 | `.env` → `Config` | 各 IM 自行定义所需变量，构造时从 cfg 读取 |
| 归一化消息 | `InboundMessage` / `InboundEvent` | 把协议报文转成统一模型，经 `on_message` / `on_event` 回调上抛（由网关注入） |

需要实现的接口：

| 方法 | 必须 | 说明 |
| --- | --- | --- |
| `start()` / `close()` | 是 | 建立连接长期运行 / 优雅退出 |
| `send_text(msg, content)` | 是 | 回复文本 |
| `send_stream(msg, stream_id, content, finish)` | 是 | 流式回复；不支持流式的 IM 可退化为整条发送 |
| `send_welcome(evt, content)` | 否 | 进入会话的欢迎语，默认退化为 `send_text` |

最小示例（可运行的完整版本见 `scripts/test_channel_contract.py`）：

```python
# myim/echo.py —— 一个把文本原样回显的通道
from im_agent_gateway.config import Config
from im_agent_gateway.gateway.base import IMChannel, InboundMessage


class Channel(IMChannel):
    name = "echo"

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        # 所需凭证从 cfg 读取，对应变量在 .env 里声明

    async def start(self) -> None:
        ...  # 连接 IM；每收到一条消息组装 InboundMessage 后：
        #     await self.on_message(InboundMessage(channel=self, im=self.name, ...))

    async def close(self) -> None:
        ...

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        ...  # 按 msg.reply_token 调用 IM 的回复接口

    async def send_stream(self, msg: InboundMessage, stream_id: str,
                          content: str, finish: bool) -> None:
        await self.send_text(msg, content)  # 不支持流式就整条发
```

在 `.env` 声明即完成接入：

```env
# 放进 channels/ 目录用短名；pip 安装的第三方包用 "模块路径:类名"
CHANNELS = "wecom,echo"
# 或
CHANNELS = "wecom,mycorp.myim.echo:Channel"
```

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
| `submit(chat_key, query, cwd, on_output, model)` | 是 | 提交任务；返回任务结束时 resolve 的 Future（结果 `AgentResult`），过程输出经 `on_output` 流式上抛 |
| `stop(chat_key)` | 是 | 终止该会话当前任务 |
| `reset(chat_key)` | 是 | 清空该会话记忆 |
| `busy(chat_key)` | 是 | 该会话是否有任务在执行或排队 |
| `available` | 否 | 前置条件（如未配 API_KEY）；不满足时注册中心拒绝开启 |
| `commands()` / `handle_command()` | 否 | Agent 特有指令（框架负责分发与帮助展示） |
| `list_models()` / `default_model` / `cwd_for()` / `close()` | 否 | 模型列表 / 默认模型 / 工作目录 / 资源释放 |

最小示例（可运行的完整版本见 `scripts/test_agent_contract.py`）：

```python
# myagent/__init__.py
from im_agent_gateway.agents.base import AgentResult, BaseAgent
from im_agent_gateway.config import Config


class Agent(BaseAgent):
    name = "myagent"
    label = "我的 Agent（示例）"
    kind = "chat"
    routing_hint = "示例：把消息原样返回"

    def __init__(self, cfg: Config) -> None:
        super().__init__()

    async def submit(self, chat_key, query, cwd="", on_output=None, model=""):
        async def _run() -> AgentResult:
            return AgentResult(ok=True, text=query)
        return _run()

    async def stop(self, chat_key: str) -> bool:
        return False

    async def reset(self, chat_key: str) -> None:
        pass

    def busy(self, chat_key: str) -> bool:
        return False
```

```env
AGENTS = "siliconflow,qoder,workbuddy,myagent"
# 或
AGENTS = "siliconflow,qoder,mycorp.myagent:Agent"
```

### 声明与加载规则

- `CHANNELS` / `AGENTS` 逗号分隔，每项支持短名（内置目录约定 `channels/<名>`、`agents/<名>`）或 `模块路径:类名`（任意第三方包）。
- 构造签名统一 `Channel(cfg)` / `Agent(cfg)`；加载时校验是否实现对应基类，未实现的声明启动即报错。
- `ENABLED_AGENTS` 只控制初始开关，与是否加载无关；运行中用 `/agent on|off <名称>` 调整。

## 行为说明

- **消息路由**：默认自动路由——先用 Jev（System One 决策模型，经 Makers 网关）根据消息内容在已开启的 Agent 中选择最优者（含概率与置信度）；Jev 未配置、调用失败或置信度低于 `JEV_MIN_CONFIDENCE` 时降级为编码关键词正则（修复/bug/报错/代码/部署/文件/目录/git…）→ Qoder CN，其余走默认 Agent；`/agent` 可把某会话固定到任一 Agent。
- **Agent 开关**：启动时按 `.env` 的 `ENABLED_AGENTS` 决定初始开关；运行中可发 `/agent on|off <名称>` 动态启停，全部关闭时消息会提示先开启。
- **指令分层**：切换/开关 Agent 的命令由框架提供；其余指令由各 Agent 系统提供（`/cd`、`/ws` 是编码 Agent 特有），路由到哪个 Agent 就由哪个 Agent 处理，不支持的指令会提示未知命令。
- **会话记忆**：每个企微会话（单聊 userid / 群聊 chatid）分别维护 Qoder 会话（UUID）、WorkBuddy 会话（UUID）与 SiliconFlow 多轮对话历史；`/reset` 清空当前路由 Agent 的会话记忆。
- **任务队列**：同一会话内任务串行执行，执行期间新消息自动排队；Qoder/WorkBuddy 全局默认同时只跑一个任务，避免多会话并发改同一目录。
- **流式回复**：SiliconFlow 走 SSE 流式输出；qoder 的工具调用（读取/执行/编辑）以进度形式实时推送到企微；超长任务会自动轮换消息（企微流式消息 10 分钟窗口）。
- **用量统计**：每条任务结束的回复末尾会附上本次执行的 Agent、模型与用量（SiliconFlow 取流式 usage 的 token 数；Qoder/WorkBuddy 取 CLI result 事件的 usage 与 total_cost_usd，即 token 与费用）；无用量数据时仅显示 Agent 与模型。
- **安全**：编辑自动接受（`accept_edits`）；可通过 `ALLOWED_USERS` 白名单限制使用者。

## 目录结构

```
main.py              服务入口（按 .env 声明组装网关/路由/Agent，接入新 IM/Agent 无需改这里）
im_agent_gateway/
  config.py          配置加载（分层 .env；CHANNELS/AGENTS 接入声明）
  plugins.py         插件加载：把 CHANNELS/AGENTS 声明解析成通道与 Agent 实例（短名 / 模块路径两种写法）
  gateway/           网关层：多 IM 通道接入
    base.py          IMChannel 抽象基类 + 归一化消息模型（InboundMessage/InboundEvent）
    registry.py      Gateway 注册中心（register / run / close）
    channels/        各 IM 通道目录（每个 IM 一个子目录，暴露 Channel 类即可接入）
      wecom/         企业微信通道
        client.py    智能机器人长连接客户端（订阅/心跳/收发/重连，纯协议层）
        channel.py   WeComChannel：报文归一化 + 回复能力（实现 IMChannel 接口）
  router/            路由层：归一化消息分发（IM 无关）
    dispatcher.py    消息路由：框架命令（/agent、/help、/status）、白名单、Agent 分发与指令转发、流式回复
    jev_router.py    Jev（System One）决策路由：调用 Makers 网关 /v1/systemone，失败/低置信度自动降级
  agents/            Agent 系统（注册与运行时开关）
    base.py         BaseAgent 抽象基类 + AgentResult（统一接口）
    registry.py     AgentRegistry 注册中心（按名注册、查询与开关）
    siliconflow/     SiliconFlow 聊天（暴露 Agent 类）
      agent.py       多轮历史、SSE 流式、模型列表（指令：/model、/models、/stop、/reset）
    qoder/           Qoder CN 编码（暴露 Agent 类）
      runner.py      qoderclicn 子进程执行、会话管理、指令（/cd、/ws、/model…）与输出解析
    workbuddy/       WorkBuddy 办公（暴露 Agent 类）
      agent.py       codebuddy 子进程执行、会话管理（--resume 续接）、流式解析
scripts/
  test_registry.py        网关与路由注册测试（FakeChannel，无网络依赖）
  test_channel_contract.py  IM 接入契约测试：第三方通道仅靠实现+声明接入（无网络依赖）
  test_agent_contract.py    Agent 接入契约测试：第三方 Agent 仅靠实现+声明接入（无网络依赖）
  test_jev_route.py      Jev 决策路由连通性测试（验证 MAKERS_MODELS_KEY）
  test_wecom_ws.py       企微连接连通性测试
  test_qoder_runner.py   本地编码任务链路测试
  test_sf_agent.py       SiliconFlow 模型列表与流式聊天连通性测试
  test_wb_agent.py       WorkBuddy 链路与流式解析测试
```

## 常见问题

- **连接反复重连**：同一机器人只允许一条长连接，确认没有其他程序也在订阅该机器人。
- **回复乱码/超时**：检查 `logs/im_agent_gateway.log`；任务超时调大 `QODER_TIMEOUT_SEC`。
- **群聊收不到**：机器人需在群里，且 @机器人 时带上任务内容。
- **聊天走不通**：确认上一级目录 `.env` 的 `API_KEY` 有效（`python scripts/test_sf_agent.py` 可验证）；若只想用编码 Agent，发 `/agent qoder`。
- **WorkBuddy 报找不到命令/未登录**：先 `npm install -g @tencent-ai/codebuddy-code`，再 `codebuddy login` 完成登录后发 `/agent on workbuddy` 开启。

## 开源协议

[MIT](./LICENSE)
