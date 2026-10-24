"""webui 通道：本机 HTTP 承载的 Web 控制台（管理台 + 聊天台）。"""

from .channel import WebUIChannel

# 接入约定：模块暴露 Channel 供 plugins 按配置声明加载
Channel = WebUIChannel

__all__ = ["WebUIChannel", "Channel"]
