"""DeepSeek 聊天 Agent：DeepSeek 官方平台，OpenAI 兼容接口。

复用 SiliconFlow 的会话队列与 SSE 流式循环，仅替换平台凭证、地址与默认模型。
"""

from __future__ import annotations

from ..siliconflow.agent import SiliconFlowAgent


class DeepSeekAgent(SiliconFlowAgent):
    """DeepSeek 官方平台聊天 Agent。"""

    name = "deepseek"
    label = "DeepSeek（聊天）"
    label_en = "DeepSeek (Chat)"
    kind = "chat"
    routing_hint = "日常聊天与通用问答：闲聊、知识问答、非编码话题"
    routing_hint_en = "General chat and Q&A: casual talk, knowledge questions, non-coding topics"
    provider = "DeepSeek"

    # DeepSeek 对话接口的默认上限（与 SiliconFlow 侧配置解耦）
    max_tokens = 4096
    timeout_sec = 300.0

    @property
    def _api_key(self) -> str:
        return self.cfg.deepseek_api_key

    @property
    def _base_url(self) -> str:
        return self.cfg.deepseek_base_url

    @property
    def _timeout_sec(self) -> float:
        return self.timeout_sec

    @property
    def _model_name(self) -> str:
        return self.cfg.deepseek_model

    @property
    def _max_tokens(self) -> int:
        return self.max_tokens

    @property
    def _include_reasoning(self) -> bool:
        return False
