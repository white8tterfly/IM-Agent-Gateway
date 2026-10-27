# 测试网关与 Agent 注册、运行时开关和消息路由（不依赖网络，用 FakeChannel/FakeAgent 模拟）
import asyncio
import atexit
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.agents.base import AgentResult, BaseAgent, CommandSpec
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import load_config
from im_agent_gateway.gateway.base import IMChannel, InboundEvent, InboundMessage
from im_agent_gateway.gateway.registry import Gateway
from im_agent_gateway.i18n import set_language
from im_agent_gateway.router.dispatcher import MessageRouter


class FakeChannel(IMChannel):
    """模拟 IM 通道：记录发送内容与生命周期调用，不建立真实连接。"""

    name = "fake"

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []          # 收到的文本回复
        self.welcomes: list[str] = []      # 收到的欢迎语
        self.started = False
        self.closed = False
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self.started = True
        await self._stop.wait()  # 长期运行直到被 close

    async def close(self) -> None:
        self.closed = True
        self._stop.set()

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        self.sent.append(content)

    async def send_stream(self, msg: InboundMessage, stream_id: str, content: str, finish: bool) -> None:
        self.sent.append(f"[stream:{finish}]{content}")

    async def send_welcome(self, evt: InboundEvent, content: str) -> None:
        self.welcomes.append(content)

    def make_msg(self, content: str, chat_type: str = "single") -> InboundMessage:
        return InboundMessage(
            channel=self, im=self.name, msg_id="m1",
            user_id="user1", chat_id="c1", chat_type=chat_type,
            msg_type="text", content=content, reply_token="r1",
        )


class FakeAgent(BaseAgent):
    """模拟 Agent：记录调用并返回固定结果，不做真实任务。"""

    def __init__(self, name: str, label: str, kind: str = "chat", available: bool = True) -> None:
        super().__init__()
        self.name = name
        self.label = label
        self.kind = kind
        self._available = available
        self.submits: list[tuple[str, str]] = []
        self.stops: list[str] = []
        self.resets: list[str] = []
        self.closed = False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def default_model(self) -> str:
        return "fake-model"

    async def submit(self, chat_key: str, query: str, cwd: str = "", on_output=None, model: str = ""):
        self.submits.append((chat_key, query))

        async def _done() -> AgentResult:
            if on_output is not None:
                await on_output(f"[fake:{query}]")
            return AgentResult(ok=True, text="done")

        return _done()

    async def stop(self, chat_key: str) -> bool:
        self.stops.append(chat_key)
        return True

    async def reset(self, chat_key: str) -> None:
        self.resets.append(chat_key)

    def busy(self, chat_key: str) -> bool:
        return False

    async def list_models(self) -> list[str]:
        return ["fake-model", "other-model"]

    async def close(self) -> None:
        self.closed = True


class FakeCoder(FakeAgent):
    """带特有指令 /ping 的编码 Fake：验证指令由 Agent 系统提供。"""

    def commands(self) -> list[CommandSpec]:
        return super().commands() + [CommandSpec("/ping", "测试指令：回复 pong")]

    async def handle_command(self, chat_key: str, cmd: str, arg: str, send) -> bool:
        if cmd == "/ping":
            await send("pong")
            return True
        return await super().handle_command(chat_key, cmd, arg, send)


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


async def main() -> None:
    # ===== 一、网关注册 =====
    gateway = Gateway()
    ch1 = FakeChannel()
    gateway.register(ch1)
    check(len(gateway) == 1 and gateway.channel_names == ["fake"], "注册后通道数为 1")
    check(gateway.get("fake") is ch1, "按名取通道")

    try:
        gateway.register(FakeChannel())
        check(False, "重名注册应报错")
    except ValueError:
        check(True, "重名注册抛 ValueError")

    class NoName(FakeChannel):
        name = ""

    try:
        gateway.register(NoName())
        check(False, "空 name 注册应报错")
    except ValueError:
        check(True, "空 name 注册抛 ValueError")

    # ===== 二、AgentRegistry 注册与开关 =====
    registry = AgentRegistry()
    chat_agent = FakeAgent("fake_chat", "聊天 Fake", kind="chat")
    code_agent = FakeAgent("fake_code", "编码 Fake", kind="coding")
    broken_agent = FakeAgent("fake_broken", "坏 Agent", available=False)
    registry.register(chat_agent, active=True)
    registry.register(code_agent, active=False)      # 启动时关闭
    registry.register(broken_agent, active=False)    # 前置条件不满足
    check(registry.names == ["fake_chat", "fake_code", "fake_broken"], "注册名按序保存")
    check(registry.active_names == ["fake_chat"], "active_agents 只含已开启")
    check(registry.get("fake_chat") is chat_agent and registry.get("nope") is None, "get 按名取 / 未注册返回 None")

    try:
        registry.register(FakeAgent("fake_chat", "重复"), active=True)
        check(False, "Agent 重名注册应报错")
    except ValueError:
        check(True, "Agent 重名注册抛 ValueError")

    err = registry.set_active("fake_broken", True)
    check(err and "未满足启用条件" in err, "available=False 拒绝开启并返回原因")
    check(not registry.is_active("fake_broken"), "拒绝开启后仍保持关闭")

    err = registry.set_active("nope", True)
    check(err and "未注册的 Agent" in err, "未注册名开启返回错误文案")

    # ===== 三、路由：registry 分发 / fallback / 运行时开关命令 =====
    cfg = load_config()
    set_language("zh")  # 断言中文文案：固定语言，避免受本机 .env 的 LANGUAGE 影响
    # /agent on|off 会写回 .env：测试期间指向临时文件，避免污染真实配置
    fd, tmp_env = tempfile.mkstemp(suffix=".env")
    os.close(fd)
    cfg.env_file = Path(tmp_env)
    atexit.register(lambda: Path(tmp_env).unlink(missing_ok=True))
    cfg.auto_route = True          # 固定路由行为，避免受 .env 影响
    cfg.allowed_users = set()      # 测试环境放开白名单
    cfg.jev_route = False          # 本测试不依赖网络：关闭 Jev 决策，走关键词降级
    router = MessageRouter(cfg, registry)
    ch1.on_message = router.handle_msg
    ch1.on_event = router.handle_event

    msg = ch1.make_msg("/help")
    await ch1.on_message(msg)
    check(any("IM-Agent Gateway 指令帮助" in s for s in ch1.sent), "/help 回发帮助文本")
    check(any("fake_chat|fake_code|fake_broken" in s for s in ch1.sent), "/help 动态文案含全部注册名")

    non_text = ch1.make_msg("x")
    non_text.msg_type = "image"
    await ch1.on_message(non_text)
    check(any("暂不支持该类型消息" in s for s in ch1.sent), "非文本消息回发提示")

    empty = ch1.make_msg("", chat_type="group")
    await ch1.on_message(empty)
    check(any("请 @我" in s for s in ch1.sent), "群聊空内容回发提示")

    # 编码意图：coding Agent 未开启 → fallback 到已开启的 fake_chat
    await ch1.on_message(ch1.make_msg("帮我修复这个 bug"))
    check(chat_agent.submits and not code_agent.submits, "关闭的 coding Agent 不路由，fallback 到开启的 Agent")

    # 任务结尾摘要：Agent / 模型（无 usage 数据时不附带用量）
    last_finish = [s for s in ch1.sent if s.startswith("[stream:True]")][-1]
    check("· 模型 fake-model" in last_finish, "任务结尾附上模型摘要")

    # 运行时开启 fake_code 后，编码意图命中它
    check(registry.set_active("fake_code", True) is None, "set_active 开启成功返回 None")
    await ch1.on_message(ch1.make_msg("这个报错怎么解决"))
    check(code_agent.submits, "开启后编码意图路由到 coding Agent")

    # 全部关闭 → 提示无可用 Agent
    check(registry.set_active("fake_chat", False) is None, "关闭 fake_chat")
    check(registry.set_active("fake_code", False) is None, "关闭 fake_code")
    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("你好"))
    check(any("当前没有可用的 Agent 服务" in s for s in ch1.sent), "全部关闭时回发无可用提示")

    # /agent on|off 命令
    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/agent on fake_chat"))
    check(registry.is_active("fake_chat"), "/agent on 开启 Agent")
    check(any("已更新 Agent 开关状态" in s for s in ch1.sent), "/agent on 回发成功文案")

    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/agent off fake_chat"))
    check(not registry.is_active("fake_chat"), "/agent off 关闭 Agent")

    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/agent on nope"))
    check(any("未注册的 Agent" in s for s in ch1.sent), "/agent on 未注册名回发错误文案")

    # /agent 状态列表
    check(registry.set_active("fake_chat", True) is None, "恢复 fake_chat 开启")
    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/agent"))
    status = ch1.sent[-1]
    check("编码 Fake" in status and "已开启" in status and "已关闭" in status,
          "/agent 列出各 Agent 开关状态")

    # /status 含各 Agent label
    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/status"))
    check(any("聊天 Fake" in s for s in ch1.sent), "/status 含 Agent label 与开关")

    # /model 与 /models（统一走 agent.list_models()）
    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/model other-model"))
    check(any("聊天 Fake 模型已切换为 other-model" in s for s in ch1.sent), "/model 切换会话 Agent 模型")
    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/models"))
    check(any("可用模型" in s and "fake-model" in s for s in ch1.sent), "/models 统一走 agent.list_models()")

    # /reset、/stop 由当前路由 Agent 提供（仅作用于该 Agent 的会话）
    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/reset"))
    check(chat_agent.resets and not code_agent.resets and not broken_agent.resets,
          "/reset 仅作用于当前路由的 Agent")
    check(chat_agent.model_for("s:user1") == "", "/reset 同时清空该 Agent 的会话模型覆盖")

    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/stop"))
    check(chat_agent.stops and not code_agent.stops and not broken_agent.stops,
          "/stop 仅作用于当前路由的 Agent")
    check(any("已终止当前任务" in s for s in ch1.sent), "/stop 回发终止文案")

    # 固定路由偏好：/agent <名称> 后普通消息也走该 Agent
    check(registry.set_active("fake_code", True) is None, "恢复 fake_code 开启")
    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/agent fake_code"))
    check(any("本会话 Agent 已切换为 编码 Fake" in s for s in ch1.sent), "/agent <名称> 固定路由回发 label")
    n_before = len(code_agent.submits)
    await ch1.on_message(ch1.make_msg("随便聊聊"))
    check(len(code_agent.submits) == n_before + 1, "固定路由后普通消息走指定 Agent")
    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/agent auto"))
    check(any("自动路由" in s for s in ch1.sent), "/agent auto 恢复自动路由")

    # Agent 特有指令：/ping 由 FakeCoder 提供；框架不识别则回未知命令
    coder = FakeCoder("fake_coder", "编码 Fake2", kind="coding")
    registry.register(coder, active=False)
    check(registry.set_active("fake_coder", True) is None, "注册并开启 FakeCoder")
    await ch1.on_message(ch1.make_msg("/agent fake_coder"))

    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/ping"))
    check(any(s == "pong" for s in ch1.sent), "Agent 特有指令 /ping 由 Agent 系统处理")

    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/cd e:/tmp"))
    check(any("未知命令 /cd" in s for s in ch1.sent), "当前 Agent 不提供的指令回未知命令")

    ch1.sent.clear()
    await ch1.on_message(ch1.make_msg("/help"))
    help_text = "\n".join(ch1.sent)
    check("【编码 Fake2】" in help_text and "/ping" in help_text, "/help 按 Agent 分组展示特有指令")
    check("【聊天 Fake】" in help_text and "/model <模型>" in help_text, "/help 分组含通用指令")
    check("切换路由 Agent" in help_text, "/help 框架段保留切换 Agent 命令说明")

    # ===== 四、事件与生命周期 =====
    evt = InboundEvent(channel=ch1, im="fake", event_type="enter_chat",
                       user_id="user1", chat_id="c1", reply_token="r1")
    await ch1.on_event(evt)
    check(any("IM-Agent Gateway 指令帮助" in s for s in ch1.welcomes), "enter_chat 回发欢迎语")

    run_task = asyncio.create_task(gateway.run())
    await asyncio.sleep(0.1)
    check(ch1.started, "gateway.run 启动了通道")
    await gateway.close()
    await asyncio.wait_for(run_task, timeout=5)
    check(ch1.closed, "gateway.close 关闭了通道")

    await gateway.close()
    check(ch1.closed, "close 可重复调用")

    await router.close()
    await registry.close()
    check(chat_agent.closed and code_agent.closed and broken_agent.closed, "registry.close 遍历关闭所有 Agent")

    print("\n全部通过")


asyncio.run(main())
