# 测试 webui 通道契约：WS 聊天归一化上抛、send_text / send_stream 推送、
# 管理端点注入、静态托管、wake_listen 声明与接入开关收敛（TestClient + FakeAgent，无网络）
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from im_agent_gateway.agents.base import AgentResult, BaseAgent
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import Config
from im_agent_gateway.gateway.base import IMChannel, InboundMessage
from im_agent_gateway.gateway.channels.webui import WebUIChannel
from im_agent_gateway.manage.api import create_router
from im_agent_gateway.manage.service import ManageService
from im_agent_gateway.router.dispatcher import MessageRouter


class FakeAgent(BaseAgent):
    """模拟 Agent：流式上抛一条固定输出，不做真实任务。"""

    def __init__(self, name: str, label: str) -> None:
        super().__init__()
        self.name = name
        self.label = label

    @property
    def default_model(self) -> str:
        return "fake-model"

    async def submit(self, chat_key: str, query: str, cwd: str = "", on_output=None, model: str = ""):
        async def _done() -> AgentResult:
            if on_output is not None:
                await on_output(f"[{query}]")
            return AgentResult(ok=True, text="done", usage="tokens 输入 10 / 输出 20")
        return _done()

    async def stop(self, chat_key: str) -> bool:
        return False

    async def reset(self, chat_key: str) -> None:
        pass

    def busy(self, chat_key: str) -> bool:
        return False


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


async def wait_until(pred, timeout: float = 5.0) -> None:
    """等待跨线程条件成立（TestClient 的 app 跑在独立线程）。"""
    for _ in range(int(timeout / 0.05)):
        if pred():
            return
        await asyncio.sleep(0.05)


async def main() -> None:
    # ---------- 契约声明 ----------
    check(issubclass(WebUIChannel, IMChannel), "webui 实现 IMChannel 契约")
    check(WebUIChannel.name == "webui", "通道名为 webui")
    check(IMChannel.wake_listen is True, "IMChannel 默认声明可唤醒监听")
    check(WebUIChannel.wake_listen is False, "webui 覆写 wake_listen=False（HTTP 被动型通道）")

    # ---------- 装配 ----------
    with tempfile.TemporaryDirectory() as td:
        cfg = Config(bot_id="", secret="")
        cfg.env_file = Path(td) / ".env"
        cfg.jev_route = False  # 跳过外部决策路由，走默认 Agent

        registry = AgentRegistry()
        registry.register(FakeAgent("alpha", "模拟助手"), active=True)
        router = MessageRouter(cfg, registry)
        service = ManageService(cfg, registry, router, gateway=None,
                                log_file=Path(td) / "run.log")

        channel = WebUIChannel(cfg)
        channel.on_message = router.handle_msg

        # 管理端点注入 → 静态挂载（与 main.py 真实时序一致：显式路由先于根路径 mount）
        channel.mount_admin(create_router(service))
        channel.mount_static()
        client = TestClient(channel.app)

        # ---------- 管理端点注入与静态托管 ----------
        r = client.get("/api/status")
        check(r.status_code == 200 and r.json()["state"] == "running", "管理端点注入后可访问")
        channel.mount_admin(create_router(service))  # 幂等：重复注入无副作用
        r = client.get("/api/status")
        check(r.status_code == 200, "mount_admin 幂等")

        r = client.get("/")
        check(r.status_code == 200 and "IM-Agent Gateway" in r.text, "静态根路径托管")
        r = client.get("/chat.html")
        check(r.status_code == 200 and "聊天台" in r.text, "聊天页静态托管")
        r = client.get("/css/main.css")
        check(r.status_code == 200 and "--spring" in r.text, "样式资源托管")

        # ---------- WS 聊天：归一化上抛 ----------
        received: list[InboundMessage] = []

        async def collect(msg: InboundMessage) -> None:
            received.append(msg)

        channel.on_message = collect
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"type": "text", "content": "你好"})
            # 无效帧被忽略，不影响后续消息
            ws.send_json({"type": "ping"})
            ws.send_json({"type": "text", "content": "   "})
            ws.send_json({"type": "text", "content": "第二条"})
            await wait_until(lambda: len(received) >= 1)
        check(len(received) == 2, "仅有效文本消息上抛（无效帧被忽略）")
        m = received[0]
        check(m.im == "webui" and m.user_id == "webui" and m.chat_id == "webui", "消息字段归一化")
        check(m.chat_type == "single" and m.msg_type == "text", "会话与消息类型归一化")
        check(m.content == "你好" and m.msg_id and m.reply_token, "内容与标识完整")
        check(channel.open_connections == 0, "连接关闭后自动摘除")

        # ---------- WS 聊天：全链路（路由 → 流式回传） ----------
        channel.on_message = router.handle_msg
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"type": "text", "content": "写个冒泡排序"})
            first = ws.receive_json()
            check(first["type"] == "stream" and first["finish"] is False, "首帧为流式帧")
            frames = 1
            while True:
                frame = ws.receive_json()
                frames += 1
                if frame.get("finish"):
                    break
                if frames > 10:
                    check(False, "流式帧数异常")
                    break
            check("[写个冒泡排序]" in frame["content"], "流式内容含 Agent 输出")
            check("fake-model" in frame["content"], "流式内容含模型与用量摘要")

        # ---------- WS 聊天：send_text 推送（指令回复） ----------
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"type": "text", "content": "/help"})
            frame = ws.receive_json()
        check(frame["type"] == "text" and "指令帮助" in frame["content"], "send_text 回推指令帮助")

        # ---------- close：断开全部连接且幂等 ----------
        ws1 = client.websocket_connect("/ws/chat")
        ws1.__enter__()
        ws2 = client.websocket_connect("/ws/chat")
        ws2.__enter__()
        await wait_until(lambda: channel.open_connections == 2)
        check(channel.open_connections == 2, "两个连接登记在册")
        await channel.close()
        check(channel.open_connections == 0, "close 断开全部连接")
        await channel.close()
        check(True, "close 幂等可重复调用")
        for ws in (ws1, ws2):
            try:
                ws.__exit__(None, None, None)
            except Exception:
                pass

        await router.close()

    # ---------- 接入开关收敛 ----------
    cfg2 = Config(bot_id="", secret="", webui_enabled=True)
    cfg2.channels = ["feishu"]
    cfg2.apply_webui_switch()
    check(cfg2.channels == ["feishu", "webui"], "开启开关自动并入 webui 声明")

    cfg3 = Config(bot_id="", secret="", webui_enabled=False)
    cfg3.channels = ["webui", "feishu"]
    cfg3.apply_webui_switch()
    check(cfg3.channels == ["feishu"], "关闭开关从声明中剔除 webui")

    print("\n全部通过")


asyncio.run(main())
