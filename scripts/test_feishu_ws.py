# 测试飞书长连接连通性（只验证 FEISHU_APP_ID/SECRET 与长连接，不依赖任何 Agent CLI）
# 用法：python scripts/test_feishu_ws.py
# 启动后打印「飞书长连接已建立」，随后在飞书给机器人发消息，脚本会打印收到的消息并退出。
import asyncio
import sys
from pathlib import Path

# 保证能 import 到项目包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.config import load_config
from im_agent_gateway.gateway.channels.feishu import FeishuChannel

# 收到第一条消息即视为连通；超时默认 90 秒（可用环境变量 FEISHU_TEST_TIMEOUT 覆盖）
TIMEOUT_SEC = float(__import__("os").environ.get("FEISHU_TEST_TIMEOUT", "90"))


async def main() -> None:
    # 分层加载：上一级目录 .env 提供 FEISHU_APP_ID/SECRET，项目 .env 提供 CHANNELS
    cfg = load_config()
    channel = FeishuChannel(cfg)

    received = asyncio.Event()

    async def on_msg(msg) -> None:
        print(
            f"[收到消息] im={msg.im} chat={msg.chat_id} chat_type={msg.chat_type} "
            f"user={msg.user_id} content={msg.content!r}"
        )
        received.set()

    channel.on_message = on_msg

    await channel.start()
    print("飞书长连接已建立，请在飞书给机器人发一条消息（单聊或群@机器人）...")

    try:
        await asyncio.wait_for(received.wait(), timeout=TIMEOUT_SEC)
        print("\n连通性验证通过：已成功接收并归一化飞书消息。")
    except asyncio.TimeoutError:
        print(
            "\n{} 秒内未收到消息。连接本身可能正常，请确认：\n"
            "  1) 飞书后台「订阅方式」已选择「使用长连接接收事件」；\n"
            "  2) 已给机器人发送消息（群聊需 @机器人）；\n"
            "  3) 应用已发布版本且机器人已上线。".format(int(TIMEOUT_SEC))
        )
    finally:
        await channel.close()
        print("已关闭飞书长连接。")


if __name__ == "__main__":
    asyncio.run(main())