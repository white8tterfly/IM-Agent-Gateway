"""网关注册中心：管理 IM 通道的注册、启动与关闭。"""

from __future__ import annotations

import asyncio
import logging

from .base import IMChannel

logger = logging.getLogger(__name__)


class Gateway:
    """网关：IM 通道注册中心。"""

    def __init__(self) -> None:
        self._channels: dict[str, IMChannel] = {}

    def register(self, channel: IMChannel) -> None:
        """注册一个 IM 通道，按通道名唯一标识。"""
        if not channel.name:
            raise ValueError("通道未定义 name")
        if channel.name in self._channels:
            raise ValueError(f"通道 {channel.name} 已注册，不能重复注册")
        self._channels[channel.name] = channel
        logger.info("已注册 IM 通道：%s", channel.name)

    def get(self, name: str) -> IMChannel | None:
        return self._channels.get(name)

    @property
    def channel_names(self) -> list[str]:
        return list(self._channels)

    def __len__(self) -> int:
        return len(self._channels)

    async def run(self) -> None:
        """启动所有已注册通道并长期运行；任一通道异常退出则取消其余通道并传播异常。"""
        if not self._channels:
            raise RuntimeError("网关未注册任何 IM 通道")
        tasks = {
            asyncio.create_task(ch.start(), name=f"channel:{name}"): name
            for name, ch in self._channels.items()
        }
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                task.result()  # 抛出异常（如有）
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

    async def close(self) -> None:
        """关闭所有已注册通道（幂等，可重复调用）。"""
        for name, ch in self._channels.items():
            try:
                await ch.close()
            except Exception:
                logger.exception("关闭通道 %s 时发生异常", name)
