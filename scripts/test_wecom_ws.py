# 测试企微智能机器人长连接（只验证 BOT_ID/SECRET 和协议，不依赖 qoder 登录）
import asyncio
import json
import uuid

from dotenv import load_dotenv
import os
from pathlib import Path

# 分层配置：上一级目录 .env 存放保密凭证，项目 .env 存放个性化配置
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT.parent / ".env")
load_dotenv(PROJECT_ROOT / ".env")

BOT_ID = os.environ["BOT_ID"].strip('"')
SECRET = os.environ["SECRET"].strip('"')


async def main():
    import websockets
    async with websockets.connect("wss://openws.work.weixin.qq.com") as ws:
        req_id = uuid.uuid4().hex
        await ws.send(json.dumps({
            "cmd": "aibot_subscribe",
            "headers": {"req_id": req_id},
            "body": {"bot_id": BOT_ID, "secret": SECRET},
        }))
        print("subscribed, waiting frames...")
        for i in range(3):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
            except asyncio.TimeoutError:
                print("(timeout, no frame)")
                continue
            frame = json.loads(raw)
            print(json.dumps(frame, ensure_ascii=False)[:500])

asyncio.run(main())
