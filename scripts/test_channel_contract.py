# 契约测试：IM 通道接入要求——第三方只提供「IMChannel 实现 + 配置声明」即可接入，核心代码零改动。
# EchoChannel 定义在本文件（核心包之外），模拟第三方接入方。
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.agents.base import AgentResult, BaseAgent
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import load_config
from im_agent_gateway.gateway.base import IMChannel, InboundMessage
from im_agent_gateway.gateway.registry import Gateway
from im_agent_gateway.plugins import load_channel
from im_agent_gateway.router.dispatcher import MessageRouter


class EchoChannel(IMChannel):
    """第三方通道：契约要求数据的最小集（name + 生命周期 + 回复接口）。"""

    name = "echo"

    def __init__(self, cfg) -> None:
        super().__init__()
        self.frames: list[tuple[bool, str]] = []  # (finish, content)
        self.texts: list[str] = []
        self.started = False
        self.closed = False
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self.started = True
        await self._stop.wait()

    async def close(self) -> None:
        self.closed = True
        self._stop.set()

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        self.texts.append(content)

    async def send_stream(self, msg: InboundMessage, stream_id: str, content: str, finish: bool) -> None:
        self.frames.append((finish, content))

    def make_msg(self, content: str) -> InboundMessage:
        return InboundMessage(
            channel=self, im=self.name, msg_id="m1", user_id="u1",
            chat_id="c1", chat_type="single", msg_type="text",
            content=content, reply_token="r1",
        )


class MiniAgent(BaseAgent):
    """配套第三方 Agent：契约要求数据的最小集。"""

    name = "mini"
    label = "Mini（契约示例）"
    kind = "chat"

    async def submit(self, chat_key, query, cwd="", on_output=None, model=""):
        async def _run() -> AgentResult:
            if on_output is not None:
                await on_output(f"echo:{query}")
            return AgentResult(ok=True, text=f"echo:{query}")
        return _run()

    async def stop(self, chat_key) -> bool:
        return False

    async def reset(self, chat_key) -> None:
        pass

    def busy(self, chat_key) -> bool:
        return False


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


async def main() -> None:
    cfg = load_config()
    cfg.allowed_users = set()
    cfg.jev_route = False

    # ===== 一、配置声明加载（plugins.load_channel，两种写法） =====
    wecom = load_channel("wecom", cfg)
    check(wecom.name == "wecom" and isinstance(wecom, IMChannel), "短名声明加载内置通道 wecom")
    full = load_channel("im_agent_gateway.gateway.channels.wecom:WeComChannel", cfg)
    check(type(full) is type(wecom), "「模块路径:类名」声明加载（第三方包同理）")

    for spec, why in [
        ("no_such_im", "不存在的声明"),
        ("im_agent_gateway.config:Config", "类未实现 IMChannel"),
        ("im_agent_gateway.plugins:load_channel", "声明解析到非类"),
    ]:
        try:
            load_channel(spec, cfg)
            check(False, f"{why}应报错")
        except RuntimeError:
            check(True, f"{why}启动即报错（RuntimeError）")

    # ===== 二、注册数据校验（Gateway.register） =====
    gateway = Gateway()
    echo = EchoChannel(cfg)
    gateway.register(echo)
    check(gateway.get("echo") is echo, "第三方通道 register 接入（未改任何核心代码）")

    class NoName(EchoChannel):
        name = ""

    try:
        gateway.register(NoName(cfg))
        check(False, "缺 name 应拒绝")
    except ValueError:
        check(True, "缺 name 拒绝注册（ValueError）")

    try:
        gateway.register(EchoChannel(cfg))
        check(False, "重名应拒绝")
    except ValueError:
        check(True, "重名拒绝注册")

    # ===== 三、端到端：只靠契约数据完成收发 =====
    cfg.auto_route = False
    cfg.default_agent = "mini"
    registry = AgentRegistry()
    registry.register(MiniAgent(), active=True)
    router = MessageRouter(cfg, registry)
    echo.on_message = router.handle_msg

    await echo.on_message(echo.make_msg("你好"))
    check(echo.frames and echo.frames[0][1].startswith("Mini（契约示例） 处理中"),
          "任务开始提示经通道 send_stream 下发")
    check(any("echo:你好" in c for _, c in echo.frames), "过程输出经 on_output 流式回传到通道")
    check(echo.frames[-1][0] and "—— Mini（契约示例）" in echo.frames[-1][1],
          "任务结束帧 finish=True 且附 Agent/模型摘要")

    await echo.on_message(echo.make_msg("/help"))
    check(any("IM-Agent Gateway 指令帮助" in t for t in echo.texts), "指令回复经通道 send_text 下发")
    check(any("【Mini（契约示例）】" in t for t in echo.texts), "/help 按注册数据（label）分组展示")

    # ===== 四、main.py 同款组装循环：配置声明驱动，零代码改动 =====
    gw2 = Gateway()
    for spec in ["wecom", "__main__:EchoChannel"]:  # 第二项模拟第三方包「模块路径:类名」声明
        ch = load_channel(spec, cfg)
        gw2.register(ch)
        ch.on_message = router.handle_msg
        ch.on_event = router.handle_event
    check(gw2.channel_names == ["wecom", "echo"], "组装循环按声明加载内置与第三方通道")

    # ===== 五、生命周期（第三方通道同样受网关调度） =====
    gw3 = Gateway()
    echo2 = EchoChannel(cfg)
    gw3.register(echo2)
    run_task = asyncio.create_task(gw3.run())
    await asyncio.sleep(0.1)
    check(echo2.started, "gateway.run 启动第三方通道")
    await gw3.close()
    await asyncio.wait_for(run_task, timeout=5)
    check(echo2.closed, "gateway.close 关闭第三方通道")

    await router.close()
    await registry.close()

    print("\n全部通过")


asyncio.run(main())
