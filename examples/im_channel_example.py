"""IM 通道接入示例：把一个虚构的 IM「DemoIM」接入平台。

这是一个可以直接运行的教程，在项目根目录执行：

    python examples/im_channel_example.py

它会本地模拟「IM 平台推来一条报文 → 通道归一化 → 路由到 Agent → 回复发回 IM」
的完整链路，每一步都在控制台打印说明。照着本文件的注释实现任意真实 IM 即可接入。

接入任何 IM 只需要三件事，平台现有代码零改动：

    1. 像下面的 Channel 类一样，实现 IMChannel 的四个必须接口
       （start / close / send_text / send_stream）；
    2. 让模块暴露名为 Channel 的类，构造签名固定为 Channel(cfg)；
    3. 在 .env 的 CHANNELS 里声明本模块，两种写法任选其一：

           CHANNELS = "wecom,examples.im_channel_example:Channel"  # 模块路径:类名（任意包）
           # 若把实现放进 im_agent_gateway/gateway/channels/demoim/，也可用短名：
           CHANNELS = "wecom,demoim"

声明之后 main.py 启动时会自动完成，通道代码无需关心：

    · 实例化并注册进网关（Gateway.register）；
    · 把路由层的 handle_msg / handle_event 接到 on_message / on_event；
    · 服务退出时调用 close()。

通道只需要做好三件事：协议连接、报文归一化、把回复发回 IM。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 保证在任意目录执行时都能 import 到项目包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.agents.base import AgentResult, BaseAgent
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import Config, load_config
from im_agent_gateway.gateway.base import IMChannel, InboundMessage
from im_agent_gateway.plugins import load_channel
from im_agent_gateway.router.dispatcher import MessageRouter


class Channel(IMChannel):
    """DemoIM 通道实现。注释按「平台何时调用我 / 参数怎么填」逐一说明，可照抄。"""

    # 通道唯一标识：注册名、消息的 im 字段、日志与状态展示都用它
    name = "demoim"

    def __init__(self, cfg: Config) -> None:
        # 构造签名必须是 Channel(cfg)；所需凭证、连接参数一律从 cfg 读取，
        # 对应变量声明在 .env 并在 config.py 增加解析（参考 FEISHU_* 的做法）
        super().__init__()
        self.cfg = cfg
        self._connected = False
        # 模拟 IM 平台的收件箱：存放「长连接收到的原始协议报文」。
        # 真实实现里不需要这个队列，报文是在长连接的收包回调里直接处理的
        self._inbox: asyncio.Queue[dict | None] = asyncio.Queue()
        # 记录本通道发出的回复，供本文件 __main__ 演示时核对
        self.sent: list[str] = []

    @property
    def connected(self) -> bool:
        # 可选覆写：向网关汇报长连接是否就绪（用于启动日志与消息唤醒的交接判断）。
        # 纯 HTTP 调用、无长连接的通道不用覆写，基类默认视为就绪
        return self._connected

    # ---------- 生命周期（由网关调度：启动时 start，退出时 close） ----------

    async def start(self) -> None:
        """【必须】建立连接并长期运行，直到被 close() 打断。

        真实 IM 在这里建立 websocket / 长轮询连接，然后进入收包循环；
        DemoIM 用一个内存队列模拟「IM 平台不断推来报文」。
        """
        self._connected = True
        print("[DemoIM] 长连接已建立（start）")
        try:
            while True:
                raw = await self._inbox.get()
                if raw is None:  # close() 投入哨兵让循环退出；真实实现里是连接断开事件
                    break
                await self._on_raw(raw)
        finally:
            self._connected = False

    async def close(self) -> None:
        """【必须】优雅退出：断开连接、让 start() 返回。"""
        print("[DemoIM] 断开长连接（close）")
        await self._inbox.put(None)

    # ---------- 报文归一化：协议报文 → InboundMessage（接入工作的核心） ----------

    async def _on_raw(self, raw: dict, replay: bool = False) -> None:
        """把 DemoIM 的原始报文转成平台统一的 InboundMessage 再上抛。

        平台只认 InboundMessage，路由层对具体协议零感知，
        因此「字段映射是否填对」直接决定接入质量。

        replay 不是 DemoIM 的协议字段，而是平台的交棒标记：真实链路里它由
        main.py 的 replay_pending 在看门狗唤醒后置 True（见 gateway/handoff.py），
        通道归一化时永远填 False 即可；这里仅为完整演示留了参数。
        """
        chat_id = raw["chat"]
        # 单聊的 chat_id 就是用户 ID；群聊是群 ID（各 IM 的取法不同，按其协议来）
        chat_type = "group" if chat_id.startswith("g_") else "single"
        text = raw["text"]
        if chat_type == "group":
            # 群聊 @ 前缀必须在通道层剥离，路由层拿到的就是纯任务内容
            text = text.removeprefix(f"@{raw['bot']}").strip()
        msg = InboundMessage(
            channel=self,             # 平台通过它找回本通道来发回复
            im=self.name,             # 通道名（日志与展示用）
            msg_id=raw["msg_id"],     # 协议消息 ID（平台用它去重）
            user_id=raw["from"],      # 发送者 ID（白名单、会话归属用）
            chat_id=chat_id,          # 会话 ID：单聊=用户，群聊=群
            chat_type=chat_type,      # 只能是 "single" 或 "group"
            msg_type="text",          # 目前平台只路由文本
            content=text,             # 纯文本内容（@ 已剥离）
            reply_token=raw["token"], # 回复所需的协议 token，send_* 时要用
            replay=replay,            # 交棒标记，真实通道恒为 False（见方法注释）
        )
        # on_message 由网关注入（指向路由层 handle_msg）：
        # 通道只管归一化后上抛，不要自行判断消息该给哪个 Agent
        await self.on_message(msg)

    # ---------- 回复能力（平台把用户的回复通过 send_* 送回来） ----------

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        """【必须】整条回复文本：用 msg.reply_token 调 IM 的回复接口。"""
        print(f"[DemoIM→用户] {content}")
        self.sent.append(content)

    async def send_stream(
        self, msg: InboundMessage, stream_id: str, content: str, finish: bool
    ) -> None:
        """【必须】流式回复：同一条消息按 content 逐段刷新，finish=True 表示本条结束。

        DemoIM 不支持流式——最常见的退化写法：整条发送，
        效果等同于只在任务结束时发最终结果（企微内置通道就是这么做的）。
        真实支持流式的 IM（如飞书）则调用平台的流式消息接口。
        """
        await self.send_text(msg, content)

    async def send_push(self, msg: InboundMessage, content: str) -> None:
        """【可选】按会话身份（chat_id/chat_type）主动回传，不依赖旧连接的 reply_token。

        只有一种情况需要覆写：回复强依赖「连接期 token」（如企微）的 IM。
        覆写为「按 chat_id 调主动推送接口」即可；本就按 chat_id 回复的 IM（如飞书）
        不用覆写，用基类默认退化（发 send_text）即可。DemoIM 两种方式都支持，
        这里覆写仅为展示接入点，__main__ 的第 4 步会演示它的触发场景。
        """
        print(f"[DemoIM→会话 {msg.chat_id}]（主动推送）{content}")
        self.sent.append(content)


class _MiniAgent(BaseAgent):
    """演示用的极简 Agent：收到什么就回什么。

    只为了让通道演示能跑通，真实 Agent 接入请看 examples/agent_example.py。
    """

    name = "mini"
    label = "Mini（演示 Agent）"
    kind = "chat"

    async def submit(self, chat_key, query, cwd="", on_output=None, model=""):
        async def _run() -> AgentResult:
            if on_output is not None:
                # 注意平台语义：平台不会自动下发 AgentResult.text——
                # 一切用户可见输出（包括最终结果）都要经 on_output 上抛，
                # 这也是所有内置 Agent 的统一做法
                await on_output(f"回显：{query}")
            # AgentResult.text 是同一结果的程序化快照，供测试与非流式调用方使用
            return AgentResult(ok=True, text=f"回显：{query}")
        return _run()

    async def stop(self, chat_key: str) -> bool:
        return False

    async def reset(self, chat_key: str) -> None:
        pass

    def busy(self, chat_key: str) -> bool:
        return False


async def main() -> None:
    print("=" * 62)
    print("IM 通道接入示例：DemoIM 完整链路演示")
    print("=" * 62)

    # ── 步骤 0：验证「.env 声明接入」这条路真的走得通 ─────────────────
    # load_channel 就是 main.py 组装时用的加载器：声明写什么、它就实例化什么。
    # 本文件在 examples/ 包里，所以第三方写法是「模块路径:类名」
    cfg = load_config()
    cfg.allowed_users = set()  # 演示关闭白名单，任何人都能用
    cfg.jev_route = False      # 演示关闭决策路由，走固定默认 Agent
    cfg.auto_route = False
    cfg.default_agent = "mini"
    ch = load_channel("examples.im_channel_example:Channel", cfg)
    print(f"\n[步骤 0] 声明加载通过：CHANNELS 里写 "
          f"\"examples.im_channel_example:Channel\" 即接入，类型 = {type(ch).__name__}")

    # ── 步骤 1：组装最小链路（main.py 对每个声明的通道自动做同样的事） ──
    registry = AgentRegistry()
    registry.register(_MiniAgent(), active=True)
    router = MessageRouter(cfg, registry)
    ch.on_message = router.handle_msg  # 网关注入路由回调，通道只管上抛

    # ── 步骤 2：单聊消息：报文进来 → 归一化 → Agent 回复 ─────────────
    print("\n" + "─" * 62)
    print("[步骤 2] 模拟 DemoIM 推来一条单聊报文：")
    await ch._on_raw({
        "msg_id": "m-001", "from": "u_alice", "chat": "u_alice",
        "text": "帮我看看今天的天气", "token": "reply-token-001",
    })
    # 平台行为（可从上面输出对照）：
    #   1) 任务开始提示经 send_stream 下发（DemoIM 退化为整条）；
    #   2) Agent 过程输出经 send_stream 流式回传；
    #   3) 结束帧 finish=True，并附上「Agent / 模型」摘要行。

    # ── 步骤 3：群聊消息：验证 @ 前缀剥离 ────────────────────────────
    print("\n" + "─" * 62)
    print("[步骤 3] 模拟 DemoIM 推来一条群聊报文（注意 @机器人 已在通道层剥离）：")
    await ch._on_raw({
        "msg_id": "m-002", "from": "u_bob", "chat": "g_devs",
        "text": "@demoim-bot 你好", "bot": "demoim-bot", "token": "reply-token-002",
    })
    # MiniAgent 回显的内容是「你好」而不是「@demoim-bot 你好」，说明归一化生效

    # ── 步骤 4：replay 消息（看门狗唤醒后的交棒场景）────────────────
    print("\n" + "─" * 62)
    print("[步骤 4] 模拟「服务被消息唤醒后重放」的消息（replay=True）：")
    await ch._on_raw({
        "msg_id": "m-003", "from": "u_alice", "chat": "u_alice",
        "text": "服务刚醒，重放我这条", "token": "reply-token-003",
    }, replay=True)
    # 与步骤 2 的区别：回复不再走 send_text / 流式，而是整条经 send_push
    # 按 chat_id 主动推送——因为服务重启后旧 reply_token 可能已失效，
    # 这正是 send_push 契约存在的意义（详见 README「消息唤醒」一节）

    await router.close()
    await registry.close()

    print("\n" + "=" * 62)
    print("演示结束。接入真实 IM 时，把上面 _on_raw 的字典换成你的协议报文，")
    print("把 send_* 的 print 换成真实的 IM API 调用即可。")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
