# 测试看门狗唤醒流程：占位回复 → 交接文件 → 拉起服务 → 释放连接（monkeypatch，无网络依赖）
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.config import load_config
from im_agent_gateway.gateway.base import IMChannel, InboundMessage
from im_agent_gateway.gateway.handoff import load as load_handoff

import watcher


class FakeChannel(IMChannel):
    name = "fake"

    def __init__(self) -> None:
        super().__init__()
        self.replies: list[str] = []
        self.closed = False
        self._stop = asyncio.Event()

    async def start(self) -> None:
        await self._stop.wait()

    async def close(self) -> None:
        self.closed = True
        self._stop.set()

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        self.replies.append(content)

    async def send_stream(self, msg: InboundMessage, stream_id: str, content: str, finish: bool) -> None:
        self.replies.append(content)

    def make_msg(self, content: str) -> InboundMessage:
        return InboundMessage(
            channel=self, im=self.name, msg_id="m1", user_id="u1", chat_id="c1",
            chat_type="single", msg_type="text", content=content, reply_token="r1",
        )


class FakeProc:
    async def wait(self) -> int:
        return 0


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


async def _run_session(handoff_path, service_running: bool, spawned: list[str]) -> tuple[FakeChannel, Path]:
    ch = FakeChannel()
    cfg = load_config()
    cfg.watch_enabled = True
    cfg.channels = ["fake"]
    cfg.wake_reply = "正在唤醒…"
    cfg.service_cmd = "python main.py"
    cfg.wake_handoff_file = str(handoff_path)

    async def fake_spawn(cmd: str, cwd=None) -> FakeProc:
        spawned.append(cmd)
        return FakeProc()

    watcher.load_channel = lambda spec, c: ch
    watcher._service_running = lambda: service_running
    watcher.asyncio.create_subprocess_shell = fake_spawn

    task = asyncio.create_task(watcher._session(cfg))
    await asyncio.sleep(0.1)                      # 等 run_task 启动并挂上 on_message
    await ch.on_message(ch.make_msg("你好"))
    await asyncio.wait_for(task, timeout=5)
    return ch, Path(handoff_path)


async def main() -> None:
    # 一、正常唤醒：占位、交接、拉起、释放
    handoff = Path(tempfile.mkdtemp()) / ".wake_pending.json"
    spawned: list[str] = []
    ch, path = await _run_session(handoff, service_running=False, spawned=spawned)
    check(any(r == "正在唤醒…" for r in ch.replies), "唤醒前回占位文案")
    raw = load_handoff(path)
    check(raw is not None and raw["content"] == "你好" and raw["im"] == "fake", "交接文件含唤醒消息与通道名")
    check(spawned == ["python main.py"], "按 service_cmd 拉起服务")
    check(ch.closed, "拉起服务前释放长连接")

    # 二、已有服务在运行：不占位、不交接、不拉起
    handoff2 = Path(tempfile.mkdtemp()) / ".wake_pending.json"
    spawned2: list[str] = []
    ch2, path2 = await _run_session(handoff2, service_running=True, spawned=spawned2)
    check(not ch2.replies, "已有服务运行时不再回占位")
    check(not path2.is_file(), "已有服务运行时不写交接文件")
    check(not spawned2, "已有服务运行时不重复拉起")
    check(ch2.closed, "已有服务运行时仍释放本轮连接")

    print("\n全部通过")


asyncio.run(main())