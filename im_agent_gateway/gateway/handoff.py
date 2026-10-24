"""消息交棒：看门狗与服务进程之间的唤醒消息序列化传递。

看门狗收到唤醒消息后，把归一化的 InboundMessage 字段写入交接文件，服务
启动时读取并重建消息继续处理。序列化字段与具体 IM 协议无关，只出现在
平台侧的文件路径里。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from .base import InboundMessage

logger = logging.getLogger(__name__)

VERSION = 1


def dump(msg: InboundMessage, path: Path) -> None:
    """把入站消息写入交接文件（原子写：先落临时文件再重命名）。"""
    payload = {
        "version": VERSION,
        "im": msg.im,
        "msg_id": msg.msg_id,
        "user_id": msg.user_id,
        "chat_id": msg.chat_id,
        "chat_type": msg.chat_type,
        "msg_type": msg.msg_type,
        "content": msg.content,
        "reply_token": msg.reply_token,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(path: Path) -> dict | None:
    """读取并删除交接文件；返回字段字典，无文件或格式不对时返回 None。"""
    if not path.is_file():
        return None
    raw: dict | None = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            raw = data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("交接文件读取失败，已忽略：%s", exc)
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    if not raw or raw.get("version") != VERSION:
        logger.warning("交接文件格式不识别，已忽略")
        return None
    return raw