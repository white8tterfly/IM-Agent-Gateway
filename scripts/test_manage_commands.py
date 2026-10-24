# 测试管理命令层 AgentOps：开关成功 / 未知 Agent / 不可用 Agent 错误文案与写回联动（不依赖网络）
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.agents.base import AgentResult, BaseAgent
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import Config
from im_agent_gateway.gateway.base import IMChannel, InboundMessage
from im_agent_gateway.manage.commands import AgentOps
from im_agent_gateway.router.dispatcher import MessageRouter


class FakeAgent(BaseAgent):
    """模拟 Agent：只关心注册名与可用性，不做真实任务。"""

    def __init__(self, name: str, label: str, available: bool = True) -> None:
        super().__init__()
        self.name = name
        self.label = label
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    async def submit(self, chat_key: str, query: str, cwd: str = "", on_output=None, model: str = ""):
        async def _done() -> AgentResult:
            return AgentResult(ok=True, text="done")
        return _done()

    async def stop(self, chat_key: str) -> bool:
        return False

    async def reset(self, chat_key: str) -> None:
        pass

    def busy(self, chat_key: str) -> bool:
        return False


class FakeChannel(IMChannel):
    """模拟通道：只记录回复文本，不建立连接。"""

    name = "fake"

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        self.sent.append(content)

    async def send_stream(self, msg: InboundMessage, stream_id: str, content: str, finish: bool) -> None:
        self.sent.append(content)

    def make_msg(self, content: str) -> InboundMessage:
        return InboundMessage(
            channel=self, im=self.name, msg_id="m1", user_id="u1",
            chat_id="c1", chat_type="single", msg_type="text",
            content=content, reply_token="r1",
        )


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


async def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        env = Path(td) / ".env"
        cfg = Config(bot_id="", secret="")
        cfg.env_file = env          # 写回目标指向临时文件，测试不污染真实 .env
        cfg.jev_route = False       # 本测试不依赖网络决策

        registry = AgentRegistry()
        registry.register(FakeAgent("alpha", "甲", available=True), active=True)
        registry.register(FakeAgent("beta", "乙", available=False), active=False)

        # 未知 Agent：错误文案，不写回
        err = AgentOps.set_active(cfg, registry, "nope", True)
        check(err is not None and "未注册的 Agent" in err, "未知 Agent 返回错误文案")
        check(not env.exists(), "失败时不写回 .env")

        # 不可用 Agent：拒绝开启，不写回
        err = AgentOps.set_active(cfg, registry, "beta", True)
        check(err is not None and "未满足启用条件" in err, "不可用 Agent 拒绝开启")
        check(not env.exists(), "拒绝开启时不写回")

        # 关闭成功：写回空活跃列表（配置「留空=全部启用」语义，见 commands 注释）
        err = AgentOps.set_active(cfg, registry, "alpha", False)
        check(err is None, "关闭成功返回 None")
        check(env.read_text(encoding="utf-8") == "ENABLED_AGENTS=\n", "全部关闭时写回空列表")
        check(cfg.enabled_agents == set(), "内存配置同步")

        # 开启成功：写回活跃名列表
        err = AgentOps.set_active(cfg, registry, "alpha", True)
        check(err is None, "开启成功返回 None")
        check(env.read_text(encoding="utf-8") == "ENABLED_AGENTS=alpha\n", "开启后写回活跃名列表")
        check(cfg.enabled_agents == {"alpha"}, "内存配置同步为活跃集")

        # 重复操作：透传 registry 文案且不写回
        before = env.read_text(encoding="utf-8")
        err = AgentOps.set_active(cfg, registry, "alpha", True)
        check(err is not None and "无需重复操作" in err, "重复开关透传错误文案")
        check(env.read_text(encoding="utf-8") == before, "重复操作不写回")

        # dispatcher /agent on|off 已接入 AgentOps（IM 入口与 UI/CLI 行为一致）
        router = MessageRouter(cfg, registry)
        ch = FakeChannel()
        ch.on_message = router.handle_msg
        registry.set_active("alpha", False)
        ch.sent.clear()
        await ch.on_message(ch.make_msg("/agent on alpha"))
        check(registry.is_active("alpha"), "/agent on 经 AgentOps 开启")
        check(any("已更新 Agent 开关状态" in s for s in ch.sent), "/agent on 成功文案不变")
        check(env.read_text(encoding="utf-8") == "ENABLED_AGENTS=alpha\n", "IM 入口开关写回 .env")

        ch.sent.clear()
        await ch.on_message(ch.make_msg("/agent on nope"))
        check(any("未注册的 Agent" in s for s in ch.sent), "/agent on 未注册名错误文案不变")

        await router.close()

    print("\n全部通过")


asyncio.run(main())
