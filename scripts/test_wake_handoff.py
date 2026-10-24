# 测试消息交棒：dump/load 往返、原子写、目录创建与损坏/缺失容错（无网络依赖）
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.gateway.base import IMChannel, InboundMessage
from im_agent_gateway.gateway.handoff import dump, load


class FakeChannel(IMChannel):
    name = "fake"

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def send_text(self, msg: InboundMessage, content: str) -> None:
        pass

    async def send_stream(self, msg: InboundMessage, stream_id: str, content: str, finish: bool) -> None:
        pass


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


def main() -> None:
    ch = FakeChannel()
    tmp = Path(tempfile.mkdtemp()) / ".wake_pending.json"
    msg = InboundMessage(
        channel=ch, im="fake", msg_id="m1", user_id="u1", chat_id="c1",
        chat_type="single", msg_type="text", content="你好", reply_token="r1",
    )

    dump(msg, tmp)
    check(tmp.is_file(), "dump 写入交接文件")

    raw = load(tmp)
    check(raw is not None and raw["content"] == "你好" and raw["im"] == "fake", "load 往返还原字段")
    check("replay" not in raw, "交接文件不落 replay 标记（重放时由服务置 True）")
    check(not tmp.is_file(), "load 读取后删除文件")

    check(load(tmp) is None, "无文件时 load 返回 None")

    bad = Path(tempfile.mkdtemp()) / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    check(load(bad) is None, "损坏文件 load 返回 None")
    check(not bad.is_file(), "损坏文件读取后被清理")

    nest = Path(tempfile.mkdtemp()) / "a" / "b" / ".wake.json"
    dump(msg, nest)
    check(nest.is_file(), "dump 自动创建父目录")

    print("\n全部通过")


main()