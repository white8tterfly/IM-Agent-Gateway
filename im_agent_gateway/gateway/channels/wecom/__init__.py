"""企业微信通道：智能机器人长连接模式。"""

from .channel import WeComChannel

# 接入约定：模块暴露 Channel 供 plugins 按配置声明加载
Channel = WeComChannel

__all__ = ["WeComChannel", "Channel"]
