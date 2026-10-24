# 测试看门狗睡眠态托管：静态页托管、watch-state 状态接口、wake 按钮唤醒流程、
# wake_listen=False 通道跳过（monkeypatch + 本机临时端口，无外部网络）
import asyncio
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

import watcher
from im_agent_gateway.config import Config
from im_agent_gateway.gateway.base import IMChannel, InboundMessage
from im_agent_gateway.gateway.watch_http import WatchHttpServer

# 真实静态目录（webui 通道自带页面），托管须服务同一份
STATIC_DIR = Path(__file__).resolve().parent.parent / "im_agent_gateway" / "gateway" / "channels" / "webui" / "static"


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


def free_port() -> int:
    """找一个本机空闲端口，避免测试与服务端口冲突。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeWakeChannel(IMChannel):
    """可被看门狗监听的模拟通道：start 挂起直至 close。"""

    name = "fakewake"

    def __init__(self) -> None:
        super().__init__()
        self.started = False
        self.sent: list[str] = []
        self._exit = asyncio.Event()

    async def start(self) -> None:
        self.started = True
        await self._exit.wait()

    async def close(self) -> None:
        self._exit.set()

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        self.sent.append(content)

    async def send_stream(self, msg: InboundMessage, stream_id: str,
                          content: str, finish: bool) -> None:
        self.sent.append(content)


class FakeHttpChannel(FakeWakeChannel):
    """HTTP 被动型模拟通道：自声明无需看门狗独立监听。"""

    name = "fakehttp"
    wake_listen = False


# 模拟通道工厂：让 _session 不加载真实 IM 通道（无网络）
_FAKES = {"fakewake": FakeWakeChannel, "fakehttp": FakeHttpChannel}
_made: dict[str, FakeWakeChannel] = {}


def fake_loader(spec: str, cfg) -> IMChannel:
    ch = _FAKES[spec]()
    _made[spec] = ch
    return ch


def make_msg(ch: IMChannel, content: str) -> InboundMessage:
    return InboundMessage(
        channel=ch, im=ch.name, msg_id="m1", user_id="u1",
        chat_id="c1", chat_type="single", msg_type="text",
        content=content, reply_token="r1",
    )


async def test_server_unit() -> None:
    """WatchHttpServer 单元级：状态接口、静态托管、唤醒回调、404、启停。"""
    port = free_port()
    calls: list[int] = []
    srv = WatchHttpServer(static_dir=STATIC_DIR, host="127.0.0.1",
                          port=port, on_wake=lambda: calls.append(1))
    srv.start()
    srv.start()  # 幂等：重复启动不产生双服务
    base = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{base}/api/watch-state")
        check(r.status_code == 200 and r.json() == {"state": "sleeping"},
              "状态接口返回睡眠态")
        r = await c.get(base + "/")
        check(r.status_code == 200 and "IM-Agent Gateway" in r.text,
              "托管静态根页面")
        r = await c.get(f"{base}/chat.html")
        check(r.status_code == 200 and "聊天台" in r.text, "托管静态子页面")
        r = await c.get(f"{base}/not-exist.txt")
        check(r.status_code == 404, "静态缺失路径返回 404")
        r = await c.get(f"{base}/api/status")
        check(r.status_code == 404, "在线态接口在睡眠托管下不可达（前端据此区分三态）")
        r = await c.post(f"{base}/api/wake")
        check(r.status_code == 200 and r.json() == {"ok": True}, "唤醒接口返回成功")
        check(calls == [1], "唤醒回调已触发")
    srv.stop()
    srv.stop()  # 幂等：重复停止不报错
    try:
        async with httpx.AsyncClient() as c:
            await c.get(f"{base}/api/watch-state", timeout=1)
        check(False, "停止后端口已释放")
    except httpx.HTTPError:
        check(True, "停止后端口已释放")


async def test_session_skip_and_wake() -> None:
    """_session 集成：跳过 wake_listen=False 通道；消息唤醒且服务已在跑时不拉服务。"""
    cfg = Config(bot_id="", secret="")
    cfg.channels = ["fakewake", "fakehttp"]
    watcher.load_channel = fake_loader
    watcher._service_running = lambda: True  # 已运行短路：不占位、不交接、不拉服务

    _made.clear()
    session_task = asyncio.create_task(watcher._session(cfg))
    await asyncio.sleep(0.3)  # 等注册与通道启动

    wk = _made["fakewake"]
    hp = _made["fakehttp"]
    check(wk.on_message is not None, "监听通道已注册消息回调")
    check(wk.started, "监听通道已启动")
    check(hp.on_message is None, "HTTP 型通道被跳过（未注册回调）")

    await wk.on_message(make_msg(wk, "你好"))
    await asyncio.wait_for(session_task, 10)
    check(hp.started is False, "HTTP 型通道全程未启动")
    check(wk.sent == [], "服务已运行时不发占位回复")
    check(not cfg.wake_handoff_file_resolved.exists(), "服务已运行时不写交接文件")


async def test_session_http_wake() -> None:
    """_session 集成：HTTP 托管在线、wake 按钮唤醒、结束后端口交还。"""
    port = free_port()
    cfg = Config(bot_id="", secret="")
    cfg.channels = ["fakewake", "fakehttp"]
    cfg.watch_http_enabled = True
    cfg.webui_port = port
    watcher.load_channel = fake_loader
    watcher._service_running = lambda: True  # 短路：按钮唤醒后不真拉服务

    _made.clear()
    session_task = asyncio.create_task(watcher._session(cfg))
    await asyncio.sleep(0.3)

    base = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{base}/api/watch-state")
        check(r.status_code == 200 and r.json()["state"] == "sleeping",
              "会话期间托管在线")
        r = await c.post(f"{base}/api/wake")
        check(r.status_code == 200 and r.json()["ok"] is True, "按钮唤醒接口成功")
    await asyncio.wait_for(session_task, 10)
    check(watcher is not None and session_task.done(), "唤醒后会话收尾退出")
    try:
        async with httpx.AsyncClient() as c:
            await c.get(f"{base}/api/watch-state", timeout=1)
        check(False, "会话结束后托管端口已交还")
    except httpx.HTTPError:
        check(True, "会话结束后托管端口已交还")


async def main() -> None:
    await test_server_unit()
    await test_session_skip_and_wake()
    await test_session_http_wake()
    print("\n全部通过")


asyncio.run(main())
