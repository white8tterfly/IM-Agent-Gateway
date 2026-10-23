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
| 智能路由 | 默认由决策模型根据消息内容在已开启的 Agent 间自动选择（含置信度），失败或低置信度自动降级为关键词路由 |
| 框架命令 | `/agent`（切换/开关 Agent）、`/help`、`/status` 由平台提供；Agent 特有指令由各 Agent 自行声明并参与帮助展示 |
| 流式回复 | 平台定义统一流式发送接口（`send_stream`）；不支持流式的 IM 可退化为整条发送 |
| 会话管理 | 每个 IM 会话（单聊 / 群聊）分别维护各 Agent 的独立会话与记忆，`/reset` 一键清空 |
| 任务队列 | 同一会话内任务串行执行，期间新消息自动排队；编码类 Agent 全局默认单任务，避免并发改动同一目录 |
| 用量统计 | 任务结束自动附上执行的 Agent、模型与用量（token / 费用），有数据才展示 |
| 白名单安全 | 支持按用户 ID 限制可使用范围；编码 Agent 编辑自动接受需显式配置 |

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
CHANNELS = "wecom,echo"                     # 放进 channels/ 目录用短名
CHANNELS = "wecom,mycorp.myim.echo:Channel" # 第三方包用 "模块路径:类名"
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
| `available` | 否 | 前置条件（如未配 API Key）；不满足时注册中心拒绝开启 |
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
AGENTS = "siliconflow,qoder,workbuddy,trae,myagent"
# 或
AGENTS = "siliconflow,qoder,mycorp.myagent:Agent"
```

### 声明与加载规则

- `CHANNELS` / `AGENTS` 逗号分隔，每项支持短名（内置目录约定 `channels/<名>`、`agents/<名>`）或 `模块路径:类名`（任意第三方包）。
- 构造签名统一 `Channel(cfg)` / `Agent(cfg)`；加载时校验是否实现对应基类，未实现的声明启动即报错。
- `ENABLED_AGENTS` 只控制初始开关，与是否加载无关；运行中用 `/agent on|off <名称>` 调整。

## 行为说明

- **消息路由**：默认自动路由——先由决策模型根据消息内容在已开启的 Agent 中选择最优者（含概率与置信度）；决策模型未配置、调用失败或置信度低于阈值时降级为编码关键词正则 → 编码类 Agent，其余走默认 Agent；`/agent` 可把某会话固定到任一 Agent。
- **Agent 开关**：启动时按 `.env` 的 `ENABLED_AGENTS` 决定初始开关；运行中可发 `/agent on|off <名称>` 动态启停，全部关闭时消息会提示先开启。
- **会话记忆**：每个 IM 会话（单聊 userid / 群聊 chatid）分别维护各 Agent 的会话状态；`/reset` 清空当前路由 Agent 的会话记忆。
- **任务队列**：同一会话内任务串行执行，执行期间新消息自动排队；编码/办公类 Agent 全局默认同时只跑一个任务，避免多会话并发改同一目录。
- **流式回复**：支持流式的 Agent 结果实时推送到 IM；超长任务会自动轮换消息（受 IM 流式消息窗口限制）。
- **用量统计**：每条任务结束的回复末尾会附上本次执行的 Agent、模型与用量（token / 费用）；无用量数据时仅显示 Agent 与模型。
- **安全**：是否自动接受编辑由各 Agent 权限模式决定；可通过 `ALLOWED_USERS` 白名单限制使用者。

## 目录结构

```
main.py              服务入口（按 .env 声明组装网关/路由/Agent，接入新 IM/Agent 无需改这里）
im_agent_gateway/
  config.py          配置加载（分层 .env；CHANNELS/AGENTS 接入声明）
  plugins.py         插件加载：把 CHANNELS/AGENTS 声明解析成通道与 Agent 实例（短名 / 模块路径两种写法）
  gateway/           网关层：多 IM 通道接入（平台核心）
    base.py          IMChannel 抽象基类 + 归一化消息模型（InboundMessage/InboundEvent）
    registry.py      Gateway 注册中心（register / run / close）
    channels/        各 IM 通道目录（每个 IM 一个子目录，暴露 Channel 类即可接入）
      wecom/         示例通道：企业微信智能机器人（长连接，纯协议层 + 报文归一化）
      feishu/        示例通道：飞书企业自建应用（lark-oapi 长连接，归一化 + 流式回复）
  router/            路由层：归一化消息分发（IM 无关）
    dispatcher.py    消息路由：框架命令（/agent、/help、/status）、白名单、Agent 分发与指令转发、流式回复
    jev_router.py    决策路由：调用决策模型网关，失败/低置信度自动降级
  agents/            Agent 系统（注册与运行时开关）
    base.py         BaseAgent 抽象基类 + AgentResult（统一接口）
    registry.py     AgentRegistry 注册中心（按名注册、查询与开关）
    siliconflow/    示例 Agent：聊天（OpenAI 兼容 API，SSE 流式，多轮历史）
    qoder/          示例 Agent：编码（CLI 子进程执行、会话管理、输出解析）
    workbuddy/      示例 Agent：办公（CLI 子进程执行、会话续接、流式解析）
    trae/           示例 Agent：编码（开源 CLI，任务进度/结果提取）
scripts/
  test_registry.py        网关与路由注册测试（FakeChannel，无网络依赖）
  test_channel_contract.py  IM 接入契约测试：第三方通道仅靠实现+声明接入（无网络依赖）
  test_agent_contract.py    Agent 接入契约测试：第三方 Agent 仅靠实现+声明接入（无网络依赖）
  test_jev_route.py      决策路由连通性测试
  test_list_cmds.py      Agent 指令列表测试
  其余 test_*.py         各示例实现（通道/Agent）的连通性测试
```

> `channels/wecom/` 与 `agents/` 下各目录均为验证接入契约的**实验性示例**：实现思路可参考，真实使用请替换为生产级通道与 Agent。

## 已验证连通性（仅供参考）

以下为代码中内置、均曾实际跑通「连接 → 收发消息 → Agent 回复」链路的通道与 Agent（部分在其它机器验证）；仅供参考，实际可用性取决于你的凭证与平台侧配置。

| 类型 | 名称 | 用途 |
| --- | --- | --- |
| IM 通道 | 飞书 `feishu` | 企业自建应用长连接收发消息 |
| IM 通道 | 企业微信 `wecom` | 智能机器人长连接收发消息 |
| Agent | SiliconFlow `siliconflow` | 聊天（SSE 流式、多轮历史） |
| Agent | Qoder `qoder` | 编码 |
| Agent | WorkBuddy `workbuddy` | 办公 |
| Agent | Trae `trae` | 编码 |

## 常见问题

- **连接反复重连**：某些 IM 只允许一条长连接，确认没有其他程序也在订阅同一账号。
- **回复乱码/超时**：检查 `logs/im_agent_gateway.log`；任务超时调大对应 Agent 的超时配置。
- **聊天走不通**：确认上一级目录 `.env` 的 API Key 有效（各示例实现带连通性测试脚本可验证）。
- **某 Agent 报不可用**：该 Agent 前置条件未满足（未安装 CLI / 未登录 / 未配凭证）；满足后发 `/agent on <名称>` 开启。

## 开源协议

[MIT](./LICENSE)
