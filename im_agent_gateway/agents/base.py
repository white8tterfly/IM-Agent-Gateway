"""Agent 抽象基类：定义任务提交、会话与指令接口。

子类实现后在 main.py 中经 AgentRegistry.register 注册，即可参与消息路由。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable, ClassVar

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Agent 任务统一结果。"""

    ok: bool
    text: str
    error: str | None = None
    usage: str | None = None  # 用量摘要行（Agent 自行格式化，如 "tokens 输入 1234 / 输出 567 · 费用 $0.01"）


@dataclass
class CommandSpec:
    """Agent 指令元数据（框架 /help 按 Agent 分组展示）。"""

    usage: str  # 用法示例，如 "/cd <目录>"
    brief: str  # 一句话说明


class BaseAgent(ABC):
    """Agent 基类：实现后 register 即接入路由。"""

    name: ClassVar[str] = ""      # 注册名，如 "qoder" / "siliconflow"
    label: ClassVar[str] = ""     # 展示名，如 "Qoder CN（编码）"
    # 用途三值（两级路由第一级）："chat"=对话；"coding"=编码；"office"=办公。
    # 类默认可被 .env 的 AGENT_KINDS 按实例覆写，故不用 ClassVar 限定。
    kind: str = "chat"
    routing_hint: ClassVar[str] = ""  # 决策路由（Jev）的候选描述；空串则按 kind 生成默认
    # 意图关键词（关键字路由用）：Jev 未配置/失败/低置信时，按这些词对消息做简单
    # 命中计分选路。不声明的 coding Agent 默认继承内置编码词表，其余默认为空。
    routing_keywords: ClassVar[tuple[str, ...]] = ()

    def __init__(self) -> None:
        self._models: dict[str, str] = {}  # chat_key -> 会话模型覆盖（/model 切换）
        # 复杂度档位（两级路由第二级）：simple / mid / complex，空 = 未声明。
        # 由 .env 的 AGENT_COMPLEXITY 覆写；仅同用途多 Agent 时参与选择。
        self.complexity: str = ""

    # ---------- 元信息 ----------

    @property
    def available(self) -> bool:
        """Agent 自身前置条件（如 API 凭证）；不满足时注册中心拒绝开启。"""
        return True

    @property
    def route_keywords(self) -> tuple[str, ...]:
        """关键字路由实际使用的词表（合成规则，子类一般无需覆写）。

        编码类（kind="coding"）默认继承内置编码词表、办公类（kind="office"）
        继承内置办公词表，其余（如 chat）默认为空；routing_keywords
        声明的是追加词，并入默认词表后生效
        （如编码 Agent 追加名字直达词、办公 Agent 追加办公词）。
        """
        try:
            # 延迟导入：keyword_router 反向依赖本模块，顶部导入会循环
            from ..router.keyword_router import CODE_KEYWORDS, OFFICE_KEYWORDS
            if self.kind == "coding":
                base = CODE_KEYWORDS
            elif self.kind == "office":
                base = OFFICE_KEYWORDS
            else:
                base = ()
        except ImportError:
            base = ()
        return base + tuple(self.routing_keywords)

    @property
    def route_description(self) -> str:
        """供 Jev 决策路由使用的候选描述。"""
        if self.routing_hint:
            return self.routing_hint
        if self.kind == "coding":
            return "编码任务：写代码、修复 bug、运行命令、git 操作、仓库文件读写"
        if self.kind == "office":
            return "办公任务：文档、表格、PPT 等办公文件处理与办公自动化"
        return "日常聊天与通用问答"

    @property
    def default_model(self) -> str:
        """默认模型名（/model 留空时生效）。"""
        return ""

    def model_for(self, chat_key: str) -> str:
        """当前会话的模型覆盖（/model 切换）；空串表示用默认模型。"""
        return self._models.get(chat_key, "")

    def cwd_for(self, chat_key: str) -> str:
        """当前会话的工作目录（编码类 Agent 覆写；聊天类返回空串）。"""
        return ""

    # ---------- 任务能力 ----------

    @abstractmethod
    async def submit(
        self,
        chat_key: str,
        query: str,
        cwd: str = "",
        on_output: Callable[[str], object] | None = None,
        model: str = "",
    ) -> Awaitable[AgentResult]:
        """提交任务到会话队列；返回在任务结束时 resolve 的 Future（结果为 ok/text/error）。"""

    @abstractmethod
    async def stop(self, chat_key: str) -> bool:
        """终止该会话当前正在执行的任务，返回是否确有任务被终止。"""

    @abstractmethod
    async def reset(self, chat_key: str) -> None:
        """清空该会话的记忆/历史，下次任务开启全新会话。"""

    @abstractmethod
    def busy(self, chat_key: str) -> bool:
        """该会话是否有任务正在执行或排队。"""

    async def list_models(self) -> list[str]:
        """列出该 Agent 可用的模型名（默认不支持）。"""
        return []

    async def close(self) -> None:
        """释放资源（默认无操作）。"""

    # ---------- 指令（Agent 系统自带的命令，框架只负责分发） ----------

    def commands(self) -> list[CommandSpec]:
        """该 Agent 提供的指令集（子类可追加特有指令）。"""
        return [
            CommandSpec("/model <模型>", "切换本会话模型（/models 查看可用列表）"),
            CommandSpec("/models", "列出可用模型"),
            CommandSpec("/stop", "终止当前正在执行的任务"),
            CommandSpec("/reset", "清空会话记忆，开启新会话"),
        ]

    async def handle_command(
        self, chat_key: str, cmd: str, arg: str, send: Callable[[str], object]
    ) -> bool:
        """处理 Agent 指令；返回是否已处理。子类先处理特有指令，剩余交回 super()。"""
        if cmd == "/model":
            if not arg:
                await send("用法：/model <模型名>，作用于当前会话的 Agent（/models 查看可用列表）")
                return True
            self._models[chat_key] = arg
            await send(f"{self.label} 模型已切换为 {arg}（下次任务生效）")
            return True
        if cmd == "/models":
            try:
                models = await self.list_models()
            except asyncio.TimeoutError:
                await send("获取模型列表超时，请稍后重试")
                return True
            except Exception as exc:
                logger.exception("获取模型列表失败（%s）", self.name)
                await send(f"获取模型列表失败：{exc}")
                return True
            if not models:
                await send("未获取到模型列表，请确认该 Agent 已正确配置并登录")
                return True
            current = self.model_for(chat_key) or self.default_model or "默认"
            lines = [f"{self.label} 可用模型（前 30 个，共 {len(models)}）："]
            for name in models[:30]:
                mark = " ← 当前会话" if name.lower() == current.lower() else ""
                lines.append(f"• {name}{mark}")
            lines.append(f"\n当前使用: {current}（/model <模型名> 切换）")
            await send("\n".join(lines))
            return True
        if cmd == "/stop":
            stopped = await self.stop(chat_key)
            await send("已终止当前任务" if stopped else "当前没有正在执行的任务")
            return True
        if cmd == "/reset":
            await self.reset(chat_key)
            self._models.pop(chat_key, None)
            await send("会话已重置，下一条消息将开启新会话")
            return True
        return False

    # ---------- 展示 ----------

    def start_hint(self, cwd: str = "", model: str = "") -> str:
        """任务开始时的提示语（路由层在 submit 前推送给用户）。"""
        return f"{self.label} 处理中…\n"
