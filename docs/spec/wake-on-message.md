# 消息唤醒与看门狗（Wake on Message）规格

## 1. 背景与目标

服务常驻时，桥接进程与 IM 长连接始终占用，机器人及其 Agent 能力在无人使用时也保持在线。

目标：提供一套平台通用的「消息唤醒」能力——开机只启动一个轻量看门狗，不启动服务；当有 IM 消息到达时，看门狗唤醒完整服务处理该消息；服务空闲一段时间后自动退出，看门狗重新接管，形成「睡-醒」闭环。

约束：该能力必须对所有 IM 通道走同一套平台机制，不在平台核心里按 IM 分支。

## 2. 术语

| 词 | 含义 |
| --- | --- |
| 看门狗（watcher） | 开机自启的轻量进程，只维持 IM 长连接与唤醒逻辑，不加载 Agent、不跑路由 |
| 服务（service） | 现有 `main.py` 启动的完整桥接服务 |
| 消息交棒（handoff） | 看门狗把唤醒消息交给服务重新处理的机制 |
| 会话级回复 | 依据 `chat_id`/`chat_type` 回传，不依赖连接期的 `reply_token` |

## 3. 需求

### 3.1 功能需求

- R1：看门狗进程（`watcher.py`）独立于服务，可常驻。
- R2：看门狗收到任意入站消息时，先向发送方回「正在唤醒」占位，再把该消息交给服务处理，最后由服务回传处理结果。
- R3：唤醒消息不被丢弃，服务就绪后原样处理并回传结果（两段式：占位 + 结果）。
- R4：服务空闲超过阈值后优雅退出，看门狗重新接管监听（睡-醒闭环）。
- R5：整个唤醒机制对 IM 通道一致——平台核心不出现 `if im == "feishu"` / `if im == "wecom"` 分支。

### 3.2 开关

| 开关 | 位置 | 关闭 | 开启 |
| --- | --- | --- | --- |
| 看门狗开关 | `watcher.py` | 进程启动即退出，不监听、不拉起服务 | 进入监听-唤醒循环 |
| 空闲自动退开关 | `main.py` | 服务常驻，行为与现状完全一致 | 空闲超时优雅退出 |

两个开关相互独立，分别控制两个进程的行为，互不影响。

## 4. 平台通用机制设计

平台核心跨三个组件：看门狗（新建）、服务入口（改 `main.py`）、路由层（改 `dispatcher.py`）。核心只依赖 `IMChannel` 与 `InboundMessage` 契约，不感知具体 IM。

### 4.1 会话级回复契约（关键）

问题：服务被唤醒、重建连接后，原消息的 `reply_token` 对企微已失效（`send_text` 用 req_id 回传），导致「结果回传」不能统一走 `send_text`。

方案：在 `IMChannel` 增加一个可选方法，语义为「按会话身份主动回传，交棒后仍可用」：

```python
async def send_push(self, msg: InboundMessage, content: str) -> None:
    """按会话身份（chat_id/chat_type）主动回传，不依赖连接期 reply_token。

    用于看门狗唤醒服务后的结果回传（服务已重建连接，旧 reply_token 可能失效）。
    默认退化为 send_text：本就按 chat_id 回复的通道（如飞书）无需覆写。
    """
    await self.send_text(msg, content)
```

各通道通过「覆写 `send_push`」实现跨连接回传，属于通道自身的协议差异，不属于平台分支：

- 飞书：`send_text` 已按 `chat_id` 回传，默认实现即可用，不覆写。
- 企微：覆写 `send_push`，调用协议层已有的 `aibot_send_msg`（复用 `WeComBotClient.send_push_markdown`）。

### 4.2 消息交棒

看门狗与服务是两个进程，通过一个交接文件传递唤醒消息。交棒信息使用统一序列化后的 `InboundMessage` 字段，与 IM 无关。

交接文件内容（JSON）：

```json
{
  "version": 1,
  "im": "feishu",
  "msg_id": "om_xxx",
  "user_id": "ou_xxx",
  "chat_id": "oc_xxx",
  "chat_type": "single",
  "msg_type": "text",
  "content": "帮我写个脚本",
  "reply_token": "om_xxx"
}
```

约定：

- 文件路径由配置 `WAKE_HANDOFF_FILE` 决定，默认项目根下 `.wake_pending.json`。
- 看门狗先写临时文件再原子重命名，避免服务读到半截内容。
- 服务启动时若存在该文件，读取后立即删除，重建 `InboundMessage` 并交给路由处理；读取失败或 `im` 对应通道未注册则丢弃并记录日志。

序列化/反序列化封装为 `im_agent_gateway/gateway/handoff.py`，提供 `dump(msg)` 与 `load(cfg)`（返回 `InboundMessage` 或 None）。

### 4.3 看门狗生命周期（`watcher.py`）

```
启动
  ├─ 读配置；WATCH_ENABLED=0 → 记录日志并退出
  ├─ 用 plugins.load_channel 加载 CHANNELS 声明的通道（与 main.py 同一入口，无 Agent）
  └─ 循环：
        ├─ 连接通道（gateway.run 在监听态）
        ├─ 收到首条消息：
        │     ├─ send_text(msg, WAKE_REPLY) 回「正在唤醒」占位（连接尚在，必达）
        │     ├─ handoff.dump(msg) 写入交接文件
        │     ├─ gateway.close() 释放长连接
        │     └─ subprocess 拉起 SERVICE_CMD（默认 python main.py）并 wait()
        └─ 服务退出后回到连接步骤（重新监听）
```

要点：

- 看门狗只加载 IM 通道，不加载 Agent、不建路由，保持轻量。
- 唤醒只触发一次：断开连接后不再有消息流入，天然避免重复唤醒。
- 服务退出（空闲自动退或异常退出）后，看门狗重新连上监听。

### 4.4 服务空闲自动退（`main.py` 改）

- `main.py` 启动时，若存在交接文件，先重放：重建 `InboundMessage` 并交给路由处理，结果由通道的 `send_push`（会话级回复，见 4.1）回传。
- `IDLE_EXIT=0`：行为与现状完全一致（`await gateway.run()`），不新增监视任务。
- `IDLE_EXIT=1`：额外启动一个空闲监视任务，与 `gateway.run()` 并发（`asyncio.wait` FIRST_COMPLETED），监视触发后关闭网关优雅退出。

空闲判定（在路由层维护，平台通用）：

- 路由层 `MessageRouter` 增加两个状态：
  - `last_activity`：时间戳，每次 `handle_msg` / `handle_event` / 任务结束时刷新。
  - `inflight`：计数器，`_start_task` 开始时 +1、结束时 -1（标识是否有任务在执行）。
- 判定：`now - last_activity > IDLE_EXIT_MIN` 且 `inflight == 0`。

「无任务在执行」用路由层计数器而非遍历各 Agent 的 `busy(chat)`（后者按会话维度，无全局口径）。

## 5. 配置项

新增到 `Config` 与 `.env.example`：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `WATCH_ENABLED` | `0` | 看门狗总开关；1=监听并唤醒，0=启动即退出 |
| `IDLE_EXIT` | `0` | 服务空闲自动退开关；1=空闲超时退出，0=常驻 |
| `IDLE_EXIT_MIN` | `10` | 空闲判定分钟数（仅 `IDLE_EXIT=1` 生效） |
| `WAKE_REPLY` | `正在唤醒…` | 看门狗唤醒前的占位文案 |
| `SERVICE_CMD` | `python main.py` | 看门狗拉起服务所用的命令 |
| `WAKE_HANDOFF_FILE` | `.wake_pending.json` | 唤醒消息交接文件路径（相对项目根） |

默认值全部为关闭态：升级后不影响任何现有行为。

## 6. 接口契约变更

仅一处新增，向后兼容：

- `IMChannel.send_push(msg, content)`：可选方法，默认退化为 `send_text`（见 4.1）。

不新增 `InboundMessage` 字段；交棒序列化复用现有字段。

## 7. 平台通用性论证

| 环节 | 平台统一做法 | 通道差异是否存在 |
| --- | --- | --- |
| 发现消息 | 统一 `IMChannel.on_message` | 否 |
| 占位回复 | 统一 `send_text`（连接期，各通道均可用） | 否 |
| 消息交棒 | 统一 `handoff.dump/load`（字段一致） | 否 |
| 结果回传 | 统一 `send_push`（会话级） | 仅通道内部实现（覆写 `send_push`），平台核心不分支 |
| 空闲判定 | 统一路由层 `last_activity + inflight` | 否 |

平台核心零 `im` 名分支；唯一的 IM 差异落在 `send_push` 覆写里，与现有 `send_welcome`/`send_stream` 的逐通道退化模式一致。

## 8. 运行约束与边界

- 长连接独占：看门狗与服务共享同一机器人，任一时刻只有一个进程持连接。看门狗必须在拉起服务前 `close()` 释放连接；期间有短暂空窗，空窗期消息以 IM 端行为为准。
- 与手动启动的关系：用户在看门狗监听期间手动 `run.bat` 会与看门狗抢连接。看门狗拉起服务前先检测是否已有 `main.py` 实例在运行，有则本轮直接重新监听（不重复拉起），避免互踢。
- 结果回传保真度：交棒后的结果统一走 `send_push`（整条回传），不追求跨交棒的增量流式（企微推送流不支持增量编辑）。占位「正在唤醒」仍即时返回。

## 9. 验收标准

- A1：`WATCH_ENABLED=0` 时 `watcher.py` 启动即退出，无连接、无拉起。
- A2：`WATCH_ENABLED=1` 时，用 FakeChannel 发一条消息，断言：写入交接文件 → 回「正在唤醒」占位 → 按 `SERVICE_CMD` 拉起子进程 → 断开连接。
- A3：服务启动读到交接文件时，重建该消息并走路由；`send_push` 被调用且结果内容正确。
- A4：`IDLE_EXIT=0` 时服务空闲不再退出；`IDLE_EXIT=1` 且空闲超阈值、无 `inflight` 时退出；有任务执行中不退出。
- A5：`send_push` 默认实现与 `send_text` 等价；企微通道覆写后按 chatid 主动推送（协议层调用 `aibot_send_msg`）。
- A6：平台核心（watcher/handoff/idle）不含任何 `im == "feishu"` / `"wecom"` 名称分支。
- A7：交接文件残留、损坏、通道未注册三种异常下，服务不崩溃并记录日志。

## 10. 测试计划（TDD）

| 用例 | 验证点 |
| --- | --- |
| `scripts/test_wake_handoff.py` | `dump`/`load` 往返一致；原子写；损坏/残留文件的容错 |
| `scripts/test_channel_push.py` | `send_push` 默认退化；FakeChannel 契约 |
| `scripts/test_idle_monitor.py` | 空闲判定阈值、`inflight` 阻止退出、开关关闭时不退 |
| `scripts/test_watch_flow.py` | R2/R3 全流程（monkeypatch 拉起命令与连接），复用 `test_registry.py` 的 FakeChannel |

实现顺序：先写以上用例（红），再逐项实现契约、handoff、空闲监视、watcher、通道 `send_push` 覆写。