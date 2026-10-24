"""Agent 接入示例：把一个虚构的「回声 Agent」接入平台。

这是一个可以直接运行的教程，在项目根目录执行：

    python examples/agent_example.py

它会本地模拟「用户发消息 → 路由到本 Agent → 流式执行 → 回复发回 IM」的
完整链路，顺带演示排队、/stop 终止、特有指令、会话记忆这些平台约定的
Agent 侧实现方式。照着本文件的注释实现任意真实 Agent 即可接入。

接入任何 Agent 只需要三件事，平台现有代码零改动：

    1. 像下面的 Agent 类一样，实现 BaseAgent 的四个必须接口
       （submit / stop / reset / busy）；
    2. 让模块暴露名为 Agent 的类，构造签名固定为 Agent(cfg)；
    3. 在 .env 的 AGENTS 里声明本模块，两种写法任选其一：

           AGENTS = "siliconflow,examples.agent_example:Agent"  # 模块路径:类名
           # 若把实现放进 im_agent_gateway/agents/echo/，也可用短名：
           AGENTS = "siliconflow,echo"

声明之后 main.py 启动时会自动完成，Agent 代码无需关心：

    · 启动时按 available 决定初始开关（前置条件不满足则注册为停用，不报错）；
    · 路由层把消息投给 submit，并 await 返回的 Future 拿最终结果；
    · /help 自动按 label 分组展示 commands()，特有指令自动转发给 handle_command；
    · 同一会话内任务串行、新消息排队提示由路由层与 Agent 的队列配合完成。
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# 保证在任意目录执行时都能 import 到项目包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.agents.base import AgentResult, BaseAgent, CommandSpec
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import Config, load_config
from im_agent_gateway.gateway.base import IMChannel, InboundMessage
from im_agent_gateway.plugins import load_agent
from im_agent_gateway.router.dispatcher import MessageRouter


@dataclass
class _Job:
    """一个排队中的任务：用户消息、流式回调、终止事件与结果 Future。"""

    query: str
    on_output: Callable[[str], object] | None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Future = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


@dataclass
class _ChatState:
    """会话状态：任务队列 + 当前正在执行的任务。"""

    queue: asyncio.Queue[_Job] = field(default_factory=asyncio.Queue)
    current: _Job | None = None
    worker: asyncio.Task | None = None


class Agent(BaseAgent):
    """回声 Agent 实现示例。注释按「平台何时调用我 / 契约要求什么」逐一说明，可照抄。"""

    # ---------- 注册元信息（路由与展示都依赖这几个类属性） ----------

    name = "echo"      # 注册名：/agent echo、AGENTS 声明、日志里都用它
    label = "Echo（示例 Agent）"  # 展示名：出现在 /help、/status 与任务摘要里
    kind = "chat"      # "chat"=聊天 / "coding"=编码：关键词兜底路由用
    routing_hint = "示例回声：把消息回显并带会话轮次"  # 决策路由的候选描述；留空按 kind 生成

    def __init__(self, cfg: Config | None = None) -> None:
        # 构造签名固定为 Agent(cfg)；真实 Agent 在这里从 cfg 读凭证/CLI 路径，
        # 并让 available 反映「是否满足前置条件」
        super().__init__()
        self._memories: dict[str, list[str]] = {}  # chat_key -> 会话历史（演示会话记忆）
        self._upper: set[str] = set()              # 开启了 /upper 的会话（演示特有指令）
        self._states: dict[str, _ChatState] = {}   # chat_key -> 任务队列

    @property
    def available(self) -> bool:
        """前置条件（可选覆写）：如「API Key 已配置 / CLI 已登录 / 已登录账号」。

        返回 False 时注册中心拒绝开启（/agent on 会回拒绝原因），
        平台其余功能不受影响。演示实现恒为可用。
        """
        return True

    @property
    def default_model(self) -> str:
        """默认模型名（可选覆写）：任务摘要与 /model 留空时使用。"""
        return "echo-mini"

    async def list_models(self) -> list[str]:
        """可用模型列表（可选覆写）：/models 与 /model 指令用。默认返回空表示不支持。"""
        return ["echo-mini", "echo-pro"]

    # ---------- 任务接口（接入的四个必须方法） ----------

    async def submit(
        self,
        chat_key: str,
        query: str,
        cwd: str = "",
        on_output: Callable[[str], object] | None = None,
        model: str = "",
    ) -> asyncio.Future:
        """【必须】把任务放进本会话队列，立刻返回 Future。

        平台的约定语义：
        · submit 立即返回，任务在后台执行（路由层随后 await Future 拿结果）；
        · 执行期间 busy(chat_key) 必须为 True（路由层据此提示「已排队」）；
        · 一切用户可见输出（包括最终结果）经 on_output 流式上抛，
          平台不会自动下发 AgentResult.text；
        · 任务结束（含失败/被终止）必须给 Future set_result，不能悬空。
        参数 cwd / model 主要给编码类 Agent 用，聊天类忽略即可。
        """
        state = self._states.setdefault(chat_key, _ChatState())
        job = _Job(query=query, on_output=on_output)
        state.queue.put_nowait(job)
        if state.worker is None or state.worker.done():
            # 本会话还没有活跃 worker：拉起一个串行消费队列
            state.worker = asyncio.create_task(self._worker(chat_key, state))
        return job.done

    async def stop(self, chat_key: str) -> bool:
        """【必须】终止该会话正在执行的任务，返回是否确有任务被终止。"""
        job = self._states.get(chat_key, _ChatState()).current
        if job is None:
            return False
        # 演示实现用事件让执行段自行中止；真实实现通常是杀子进程/断开请求，
        # 参考 agents/qoder/runner.py 的 _kill_tree
        job.stop.set()
        return True

    async def reset(self, chat_key: str) -> None:
        """【必须】清空该会话的记忆，下次任务开启全新会话（/reset 指令触发）。"""
        self._memories.pop(chat_key, None)
        self._upper.discard(chat_key)

    def busy(self, chat_key: str) -> bool:
        """【必须】该会话是否有任务正在执行或排队（路由层据此做排队提示）。"""
        state = self._states.get(chat_key)
        return state is not None and (state.current is not None or not state.queue.empty())

    # ---------- 执行：队列 worker（每会话串行的实现方式） ----------

    async def _worker(self, chat_key: str, state: _ChatState) -> None:
        """每会话一个 worker：从队列逐个取任务串行执行。

        平台「同一会话内任务串行、新消息自动排队」正是由 Agent 侧的这个
        队列保证的：路由层发现 busy 时只提示「已排队」，仍照常 submit。
        队列空闲即退出（下次 submit 再拉起），避免常驻协程。
        """
        try:
            while True:
                job = await state.queue.get()
                state.current = job
                try:
                    result = await self._execute(chat_key, job)
                    if not job.done.done():
                        job.done.set_result(result)
                except Exception as exc:
                    # 任何失败（含被 /stop 中止）都给等待方一个失败收尾，
                    # Future 不能悬空，否则路由层会一直等
                    if not job.done.done():
                        job.done.set_result(AgentResult(ok=False, text="", error=str(exc)))
                finally:
                    state.current = None
                if state.queue.empty():
                    return
        finally:
            state.worker = None
            # 竞态兜底：worker 退出瞬间又有新任务入队时，重新拉起 worker
            if not state.queue.empty():
                state.worker = asyncio.create_task(self._worker(chat_key, state))

    async def _execute(self, chat_key: str, job: _Job) -> AgentResult:
        """真正的任务在这里执行：驱动 CLI 子进程 / 调 HTTP API / 本地推理。

        演示实现：分段 sleep 模拟真实耗时，过程中经 on_output 流式上抛，
        并检查 stop 事件以便 /stop 及时打断。
        """
        history = self._memories.setdefault(chat_key, [])
        history.append(job.query)
        text = job.query.upper() if chat_key in self._upper else job.query
        for chunk in ("正在理解消息…", "组织回复中…"):
            if job.stop.is_set():
                raise RuntimeError("任务被用户 /stop 终止")
            await asyncio.sleep(0.4)  # 模拟耗时（真实实现是子进程输出 / API 往返）
            if job.on_output is not None:
                await job.on_output(f"[echo] {chunk}")
        if job.stop.is_set():
            raise RuntimeError("任务被用户 /stop 终止")
        final = f"第 {len(history)} 轮，收到：{text}"
        if job.on_output is not None:
            # 平台语义：用户可见输出（包括最终结果）一律经 on_output 上抛，
            # 平台不会自动下发 AgentResult.text（内置 Agent 都遵循这一点）
            await job.on_output(final)
        return AgentResult(
            ok=True,
            text=final,  # 同一结果的程序化快照，供测试与非流式调用方使用
            usage=f"演示用量 · 本会话累计 {len(history)} 条",  # 任务摘要行会拼上它
        )

    # ---------- 特有指令（框架负责分发，Agent 只管处理） ----------

    def commands(self) -> list[CommandSpec]:
        """本 Agent 的特有指令：自动出现在 /help 的本 Agent 分组里。"""
        return super().commands() + [
            CommandSpec("/upper on|off", "回显改用全大写 / 恢复原文"),
        ]

    async def handle_command(
        self, chat_key: str, cmd: str, arg: str, send: Callable[[str], object]
    ) -> bool:
        """处理特有指令；返回 False 的指令由框架提示「未知命令」。

        处理完自己的指令后务必交回 super()，基类统一实现了
        /model /models /stop /reset 这些通用指令。
        """
        if cmd == "/upper":
            if arg == "on":
                self._upper.add(chat_key)
                await send("已切换：本会话回显将使用全大写")
            elif arg == "off":
                self._upper.discard(chat_key)
                await send("已恢复原文回显")
            else:
                await send("用法：/upper on|off")
            return True
        return await super().handle_command(chat_key, cmd, arg, send)


class _DemoChannel(IMChannel):
    """演示通道：只在内存记录平台回传的内容并打印。

    真实通道的接入见 examples/im_channel_example.py。
    """

    name = "demo"

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[tuple[bool, str]] = []  # 流式帧 (finish, content)
        self.texts: list[str] = []                # 整条回复

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        print(f"[DemoIM→用户] {content}")
        self.texts.append(content)

    async def send_stream(self, msg: InboundMessage, stream_id: str, content: str, finish: bool) -> None:
        # 流式帧会拼接成同一条消息不断刷新；这里按帧打印以便观察
        print(f"[DemoIM→用户]{'（流式刷新）' if not finish else '（流式收尾）'} {content}")
        self.frames.append((finish, content))

    def make_msg(self, content: str, user: str = "u_alice") -> InboundMessage:
        return InboundMessage(
            channel=self, im=self.name, msg_id="", user_id=user,
            chat_id=user, chat_type="single", msg_type="text",
            content=content, reply_token="",
        )


def rule(title: str) -> None:
    print("\n" + "─" * 62)
    print(title)


async def main() -> None:
    print("=" * 62)
    print("Agent 接入示例：Echo Agent 完整链路演示")
    print("=" * 62)

    # ── 步骤 0：验证「.env 声明接入」这条路真的走得通 ─────────────────
    # load_agent 就是 main.py 组装时用的加载器：声明写什么、它就实例化什么
    cfg = load_config()
    cfg.allowed_users = set()  # 演示关闭白名单
    cfg.jev_route = False       # 演示关闭决策路由
    cfg.auto_route = False
    cfg.default_agent = "echo"
    agent = load_agent("examples.agent_example:Agent", cfg)
    print(f"\n[步骤 0] 声明加载通过：AGENTS 里写 "
          f"\"examples.agent_example:Agent\" 即接入，类型 = {type(agent).__name__}")

    # ── 步骤 1：注册并接上路由（main.py 对每个声明的 Agent 自动做同样的事） ──
    registry = AgentRegistry()
    # main.py 的注册公式：available=False 的 Agent 注册为停用，而不是报错
    registry.register(agent, active=agent.available)
    router = MessageRouter(cfg, registry)
    demo = _DemoChannel()
    demo.on_message = router.handle_msg  # 通道消息进来即路由

    # ── 步骤 2：普通消息 → 观察流式回复与多轮记忆 ─────────────────────
    rule("[步骤 2] 发第一条消息（观察：开始提示 → 过程输出 → 结果+摘要）：")
    await demo.on_message(demo.make_msg("你好"))
    rule("[步骤 2.1] 再发一条（回显里轮次 +1，说明会话记忆按 chat_key 保留）：")
    await demo.on_message(demo.make_msg("还在吗"))

    # ── 步骤 3：任务执行中再发消息 → 自动排队 ─────────────────────────
    rule("[步骤 3] 上一条任务还在跑时再发一条（观察「已排队」提示与串行执行）：")
    t1 = asyncio.create_task(demo.on_message(demo.make_msg("慢任务甲")))
    await asyncio.sleep(0.2)  # 确保第一条已进入执行段，busy=True
    t2 = asyncio.create_task(demo.on_message(demo.make_msg("慢任务乙")))
    await asyncio.gather(t1, t2)

    # ── 步骤 4：特有指令与通用指令 ────────────────────────────────────
    rule("[步骤 4] 特有指令 /upper on（由本 Agent 的 handle_command 处理）：")
    await demo.on_message(demo.make_msg("/upper on"))
    rule("[步骤 4.1] 消息回显已变为全大写：")
    await demo.on_message(demo.make_msg("hello world"))
    rule("[步骤 4.2] 通用指令 /models（基类实现直通 list_models）：")
    await demo.on_message(demo.make_msg("/models"))

    # ── 步骤 5：/stop 终止执行中的任务 ────────────────────────────────
    rule("[步骤 5] 执行中 /stop（观察任务被打断并回失败原因）：")
    t = asyncio.create_task(demo.on_message(demo.make_msg("这条会被中途打断")))
    await asyncio.sleep(0.5)   # 让任务进入执行段
    await demo.on_message(demo.make_msg("/stop"))
    await t

    # ── 步骤 6：/reset 清空会话记忆 ──────────────────────────────────
    rule("[步骤 6] /reset 后轮次归零（说明记忆是会话级、可清空的）：")
    await demo.on_message(demo.make_msg("/reset"))
    await demo.on_message(demo.make_msg("新的一轮"))

    await router.close()
    await registry.close()

    print("\n" + "=" * 62)
    print("演示结束。接入真实 Agent 时，把 _execute 换成真实的执行方式")
    print("（CLI 子进程 / HTTP API / 本地推理），available 反映真实前置条件即可。")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
