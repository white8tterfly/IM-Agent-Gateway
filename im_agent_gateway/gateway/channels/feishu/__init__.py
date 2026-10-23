"""飞书通道：企业自建应用长连接模式。"""

from .channel import FeishuChannel

# 接入约定：模块暴露 Channel 供 plugins 按配置声明加载
Channel = FeishuChannel

__all__ = ["FeishuChannel", "Channel"]