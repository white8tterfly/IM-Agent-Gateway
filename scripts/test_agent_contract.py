# 契约测试：Agent 接入要求——第三方只提供「BaseAgent 实现 + 配置声明」即可参与路由，核心代码零改动。
# 本文件定义的各 Agent 位于核心包之外，模拟第三方接入方。
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.agents.base import AgentResult, BaseAgent, CommandSpec
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import load_config
from im_agent_gateway.gateway.base import IMChannel, InboundMessage
from im_agent_gateway.i18n import set_language
from im_agent_gateway.plugins import load_agent
from im_agent_gateway.router.dispatcher import MessageRouter


class LocalChannel(IMChannel):
    """配套最小通道：只实现契约接口，记录回传内容。"""

    name = "local"

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[tuple[bool, str]] = []
        self.texts: list[str] = []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

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


class TranslatorAgent(BaseAgent):
    """第三方 Agent：带 routing_hint 与特有指令，验证元数据与指令契约。"""

    name = "translator"
    label = "Translator（契约示例）"
    kind = "chat"
    routing_hint = "翻译任务：中英互译、润色文本"

    def __init__(self, cfg=None) -> None:
        super().__init__()
        self.submits: list[str] = []

    async def submit(self, chat_key, query, cwd="", on_output=None, model=""):
        self.submits.append(query)

        async def _run() -> AgentResult:
            if on_output is not None:
                await on_output(f"译：{query}")
            return AgentResult(ok=True, text=f"译：{query}")
        return _run()

    async def stop(self, chat_key) -> bool:
        return True

    async def reset(self, chat_key) -> None:
        pass

    def busy(self, chat_key) -> bool:
        return False

    def commands(self):
        return super().commands() + [CommandSpec("/lang <语言>", "切换目标语言")]

    async def handle_command(self, chat_key, cmd, arg, send) -> bool:
        if cmd == "/lang":
            await send(f"目标语言：{arg or 'en'}")
            return True
        return await super().handle_command(chat_key, cmd, arg, send)


class CoderAgent(TranslatorAgent):
    """kind="coding" 且不写 routing_hint：验证默认路由描述生成。"""

    name = "coder"
    label = "Coder（契约示例）"
    kind = "coding"
    routing_hint = ""


class LockedAgent(TranslatorAgent):
    """available=False：前置条件不满足时注册中心拒绝开启。"""

    name = "locked"
    label = "Locked（契约示例）"
    routing_hint = ""

    @property
    def available(self) -> bool:
        return False


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


async def main() -> None:
    cfg = load_config()
    set_language("zh")  # 断言中文文案：固定语言，避免受本机 .env 的 LANGUAGE 影响
    cfg.allowed_users = set()
    cfg.jev_route = False
    cfg.auto_route = False
    cfg.default_agent = "translator"

    # ===== 一、配置声明加载（plugins.load_agent，两种写法） =====
    for spec, cls_name, agent_name in [
        ("siliconflow", "SiliconFlowAgent", "siliconflow"),
        ("qoder", "QoderRunner", "qoder"),
        ("workbuddy", "WorkBuddyAgent", "workbuddy"),
        ("im_agent_gateway.agents.qoder:QoderRunner", "QoderRunner", "qoder"),
        ("__main__:TranslatorAgent", "TranslatorAgent", "translator"),  # 模拟第三方包路径声明
    ]:
        agent = load_agent(spec, cfg)
        check(type(agent).__name__ == cls_name and agent.name == agent_name,
              f"声明 {spec} 加载出 {cls_name}（构造签名 Agent(cfg)）")

    for spec, why in [
        ("no_such_agent", "不存在的声明"),
        ("im_agent_gateway.config:Config", "类未实现 BaseAgent"),
        ("im_agent_gateway.plugins:load_agent", "声明解析到非类"),
    ]:
        try:
            load_agent(spec, cfg)
            check(False, f"{why}应报错")
        except RuntimeError:
            check(True, f"{why}启动即报错（RuntimeError）")

    # ===== 二、注册数据（元数据契约） =====
    registry = AgentRegistry()
    translator = TranslatorAgent()
    coder = CoderAgent()
    locked = LockedAgent()
    registry.register(translator, active=True)
    registry.register(coder, active=True)
    registry.register(locked, active=locked.available)  # 同 main.py 的注册公式
    check(registry.names == ["translator", "coder", "locked"], "注册名按声明顺序保存")
    check(registry.active_names == ["translator", "coder"], "available=False 的 Agent 注册即停用")

    check(translator.route_description == "翻译任务：中英互译、润色文本",
          "routing_hint 优先作为决策路由候选描述")
    check(coder.route_description.startswith("编码任务"), "kind=coding 且无 hint 用默认编码描述")
    check(locked.route_description == "日常聊天与通用问答", "kind=chat 且无 hint 用默认聊天描述")

    err = registry.set_active("locked", True)
    check(err and "未满足启用条件" in err, "available=False 拒绝开启并返回原因")

    # ===== 三、任务接口契约 =====
    future = await translator.submit("s:u1", "hello", on_output=None)
    result = await future
    check(isinstance(result, AgentResult) and result.ok and result.text == "译：hello",
          "submit 返回任务结束时 resolve 的 Future（AgentResult）")

    # ===== 四、端到端：只靠契约数据参与路由与指令分发 =====
    router = MessageRouter(cfg, registry)
    ch = LocalChannel()
    ch.on_message = router.handle_msg

    await ch.on_message(ch.make_msg("hello"))
    check(translator.submits and translator.submits[-1] == "hello", "普通消息路由到默认 Agent")
    check(any("译：hello" in c for _, c in ch.frames), "任务输出经通道流式回传")

    ch.texts.clear()
    await ch.on_message(ch.make_msg("/help"))
    check(any("【Translator（契约示例）】" in t and "/lang <语言>" in t for t in ch.texts),
          "/help 按 label 分组并展示特有指令")

    ch.texts.clear()
    await ch.on_message(ch.make_msg("/lang 中文"))
    check(any("目标语言：中文" == t for t in ch.texts), "特有指令由 Agent 的 handle_command 处理")

    ch.texts.clear()
    await ch.on_message(ch.make_msg("/stop"))
    check(any("已终止当前任务" in t for t in ch.texts), "通用指令 /stop 由基类统一处理")

    ch.texts.clear()
    await ch.on_message(ch.make_msg("/agent on locked"))
    check(any("未满足启用条件" in t for t in ch.texts), "/agent on 对 available=False 的 Agent 回复拒绝原因")

    ch.texts.clear()
    await ch.on_message(ch.make_msg("/agent coder"))
    check(any("本会话 Agent 已切换为 Coder（契约示例）" in t for t in ch.texts), "/agent <名称> 固定到第三方 Agent")
    await ch.on_message(ch.make_msg("写段代码"))
    check(coder.submits and coder.submits[-1] == "写段代码", "固定路由后消息到达该 Agent")

    await router.close()
    await registry.close()

    print("\n全部通过")


asyncio.run(main())
