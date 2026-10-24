"""消息路由：命令处理 + 按规则分发到 Agent，流式回传来源通道。"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from ..agents.base import BaseAgent
from ..agents.registry import AgentRegistry
from ..config import Config
from ..gateway.base import IMChannel, InboundEvent, InboundMessage
from .jev_router import JevRouter
from .keyword_router import KeywordRouter

logger = logging.getLogger(__name__)

# 流式消息单条最大展示字符数（企微单条消息约 20480 字节）
STREAM_CAP = 6000
# 企微要求流式消息 10 分钟内 finish，8 分钟时轮换新流留余量
STREAM_MAX_AGE_SEC = 8 * 60

class StreamSender:
    """流式消息推送：按节流节奏推送，10 分钟窗口快到时自动轮换新流。"""

    def __init__(self, channel: IMChannel, msg: InboundMessage, interval: float, replay: bool = False) -> None:
        self.channel = channel
        self.msg = msg
        self.interval = interval
        self.replay = replay
        self.stream_id = uuid.uuid4().hex
        self.buf = ""
        self.started_at = time.monotonic()
        self.last_flush = 0.0
        self.sent_once = False
        self.closed = False

    async def feed(self, text: str) -> None:
        """追加增量文本，按间隔刷出；忽略流已关闭后的写入。"""
        if self.closed:
            return
        self.buf += text
        if self.replay:
            # 唤醒交棒后的结果：不流式推进，攒到最后一次 flush 整条回传
            return
        now = time.monotonic()
        if not self.sent_once or now - self.last_flush >= self.interval:
            await self.flush()

    async def flush(self, finish: bool = False) -> None:
        if self.closed:
            return
        content = self.buf[-STREAM_CAP:]
        if finish:
            if self.replay:
                await self.channel.send_push(self.msg, content or "（无内容）")
            else:
                await self.channel.send_stream(self.msg, self.stream_id, content or "（无内容）", finish=True)
            self.closed = True
            return
        if self.replay:
            return
        # 快满 10 分钟窗口：结束旧流、开新流继续
        if time.monotonic() - self.started_at > STREAM_MAX_AGE_SEC:
            await self.channel.send_stream(self.msg, self.stream_id, content, finish=True)
            self.stream_id = uuid.uuid4().hex
            self.buf = ""
            self.started_at = time.monotonic()
            self.sent_once = False
            return
        await self.channel.send_stream(self.msg, self.stream_id, content, finish=False)
        self.sent_once = True
        self.last_flush = time.monotonic()


class MessageRouter:
    """消息路由：接收通道回调，分发到 Agent，流式回传结果。"""

    def __init__(self, cfg: Config, registry: AgentRegistry) -> None:
        self.cfg = cfg
        self.registry = registry
        self.chat_agents: dict[str, str] = {}  # chat_key -> 固定 Agent 名（/agent 切换）
        self.jev = JevRouter(cfg)
        # 关键字意图路由：Jev 未配置/失败/低置信时的退化方案
        self.keyword = KeywordRouter()
        self._last_activity = time.monotonic()  # 最近一次消息/任务结束时刻（空闲判定）
        self._inflight = 0                      # 正在执行的任务数

    # ---------- 帮助文本 ----------

    def _help_text(self) -> str:
        """动态生成帮助文本：框架命令 + 各 Agent 指令分组。"""
        names = "|".join(self.registry.names) or "…"
        lines = [
            "IM-Agent Gateway 指令帮助",
            "直接发送消息：由 Jev 决策模型自动路由到最合适的 Agent（不可用时按关键词降级）",
            "",
            f"/agent [{names}|auto]  切换路由 Agent（固定/自动）",
            "/agent on|off <名称>   开启/关闭 Agent 服务",
            "/status        查看当前状态",
            "/help          显示本帮助",
        ]
        for agent in self.registry.agents:
            cmds = agent.commands()
            if not cmds:
                continue
            lines.append("")
            lines.append(f"【{agent.label}】")
            lines.extend(f"{c.usage:<20}{c.brief}" for c in cmds)
        return "\n".join(lines)

    # ---------- 通道回调 ----------

    async def handle_msg(self, msg: InboundMessage) -> None:
        self._last_activity = time.monotonic()
        if not self._allowed(msg.user_id):
            logger.info("拒绝非白名单用户 %s", msg.user_id)
            return

        if msg.msg_type != "text":
            await msg.channel.send_text(msg, "暂不支持该类型消息，请发送文本（图片/文件可在群内发文本描述）")
            return

        if msg.chat_type == "group" and not msg.content:
            await msg.channel.send_text(msg, "请 @我 并附上任务描述，发送 /help 查看用法")
            return

        chat_key = f"g:{msg.chat_id}" if msg.chat_type == "group" and msg.chat_id else f"s:{msg.user_id}"

        if msg.content.startswith("/"):
            await self._handle_command(chat_key, msg, msg.content)
        else:
            agent = await self._resolve_agent(chat_key, msg.content)
            if agent is None:
                await msg.channel.send_text(msg, "当前没有可用的 Agent 服务，请先 /agent on <名称> 开启")
                return
            logger.info("路由消息 → %s（chat=%s，IM=%s）", agent.name, chat_key, msg.im)
            await self._start_task(chat_key, msg.content, msg, agent)

    async def handle_event(self, evt: InboundEvent) -> None:
        self._last_activity = time.monotonic()
        if evt.event_type == "enter_chat":
            # 企微要求 5 秒内回复欢迎语（由通道 send_welcome 保证协议正确性）
            await evt.channel.send_welcome(evt, self._help_text())
        # template_card_event / feedback_event / disconnected_event 暂不处理

    # ---------- 命令处理 ----------

    async def _handle_command(self, chat_key: str, msg: InboundMessage, content: str) -> None:
        parts = content.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/help":
            await msg.channel.send_text(msg, self._help_text())

        elif cmd == "/status":
            await msg.channel.send_text(msg, await self._status_text(chat_key))

        elif cmd == "/agent":
            await self._handle_agent(chat_key, arg, msg)

        else:
            # 非框架命令：转发给当前路由的 Agent 处理
            agent = await self._resolve_agent(chat_key, "")
            if agent is None:
                await msg.channel.send_text(msg, "当前没有可用的 Agent 服务，请先 /agent on <名称> 开启")
                return

            async def send(text: str) -> None:
                await msg.channel.send_text(msg, text)

            handled = await agent.handle_command(chat_key, cmd, arg, send)
            if not handled:
                await msg.channel.send_text(msg, f"未知命令 {cmd}，发送 /help 查看用法")

    async def _handle_agent(self, chat_key: str, arg: str, msg: InboundMessage) -> None:
        """/agent 命令：查看状态、切换路由、开关 Agent。"""
        # 别名：注册名 + 短别名 + auto
        aliases = {"auto": "auto", "q": "qoder", "sf": "siliconflow", "wb": "workbuddy"}
        aliases.update({a.name: a.name for a in self.registry.agents})

        # /agent on|off <名称>：运行时开关
        parts = arg.split(maxsplit=1)
        if parts and parts[0].lower() in ("on", "off") and len(parts) == 2:
            err = self.registry.set_active(parts[1].strip(), parts[0].lower() == "on")
            await msg.channel.send_text(msg, err or "已更新 Agent 开关状态")
            return

        if not arg:
            current = self.chat_agents.get(chat_key) or ("auto" if self.cfg.auto_route else self.cfg.default_agent)
            lines = ["Agent 状态", f"• 路由偏好：{self._agent_label(current)}"]
            for agent in self.registry.agents:
                on = self.registry.is_active(agent.name)
                note = "" if on or agent.available else "（不可用：如未配置 API_KEY）"
                lines.append(f"• {agent.name}: {agent.label}{'已开启' if on else '已关闭'}{note}")
            lines.append("\n/agent <名称>|auto  固定路由偏好")
            lines.append("/agent on|off <名称>  开启/关闭 Agent")
            await msg.channel.send_text(msg, "\n".join(lines))
            return

        key = aliases.get(arg.lower(), "")
        if not key:
            names = "|".join(self.registry.names) or "…"
            await msg.channel.send_text(msg, f"用法：/agent [{names}|auto]，或 /agent on|off <名称>")
            return
        if key == "auto":
            self.chat_agents.pop(chat_key, None)
        elif self.registry.get(key) is None:
            await msg.channel.send_text(msg, f"未注册的 Agent：{key}")
            return
        else:
            self.chat_agents[chat_key] = key
        await msg.channel.send_text(msg, f"本会话 Agent 已切换为 {self._agent_label(key)}")

    # ---------- 任务执行 ----------

    async def _start_task(self, chat_key: str, query: str, msg: InboundMessage, agent: BaseAgent) -> None:
        self._inflight += 1
        try:
            await self._run_task(chat_key, query, msg, agent)
        finally:
            self._inflight -= 1
            # 任务结束即视为一次活动，空闲计时从此刻重新起算
            self._last_activity = time.monotonic()

    async def _run_task(self, chat_key: str, query: str, msg: InboundMessage, agent: BaseAgent) -> None:
        cwd = agent.cwd_for(chat_key)
        model = agent.model_for(chat_key)

        if agent.busy(chat_key):
            # 有任务在执行：先给反馈，再排队
            queued = StreamSender(msg.channel, msg, self.cfg.stream_interval_sec, replay=msg.replay)
            await queued.feed("上一条任务正在执行，本条消息已排队")
            await queued.flush(finish=True)

        sender = StreamSender(msg.channel, msg, self.cfg.stream_interval_sec, replay=msg.replay)
        await sender.feed(agent.start_hint(cwd=cwd, model=model))
        try:
            future = await agent.submit(
                chat_key, query,
                cwd=cwd,
                on_output=sender.feed,
                model=model,
            )
            result = await future
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("任务调度异常（%s）", agent.name)
            await sender.feed(f"\n服务异常：{exc}")
            await sender.flush(finish=True)
            return

        if not result.ok:
            await sender.feed(f"\n{result.error or '任务执行失败'}")
        # 结尾附上本次任务信息：Agent / 模型 / 用量
        model_name = model or agent.default_model or "默认"
        usage = getattr(result, "usage", None)
        summary = f"\n—— {agent.label} · 模型 {model_name}"
        if usage:
            summary += f" · {usage}"
        await sender.feed(summary)
        await sender.flush(finish=True)

    # ---------- 工具 ----------

    async def _resolve_agent(self, chat_key: str, query: str) -> BaseAgent | None:
        """路由决策：会话固定 → Jev 决策 → 关键词兜底 → 默认 Agent。"""
        active = {a.name: a for a in self.registry.active_agents}
        if not active:
            return None
        override = self.chat_agents.get(chat_key)
        if override in active:
            return active[override]
        if self.cfg.auto_route and query:
            # 自动路由：Jev 决策优先，未配置/失败/低置信退化到关键字意图解析
            if self.jev.available:
                name = await self.jev.route(query, list(active.values()))
                if name in active:
                    return active[name]
            name = self.keyword.route(query, list(active.values()))
            if name in active:
                return active[name]
        if self.cfg.default_agent in active:
            return active[self.cfg.default_agent]
        return self.registry.active_agents[0]

    def _agent_label(self, key: str) -> str:
        agent = self.registry.get(key)
        return agent.label if agent else ("自动路由" if key == "auto" else key)

    def _allowed(self, userid: str) -> bool:
        return not self.cfg.allowed_users or userid in self.cfg.allowed_users

    def is_idle(self, min_seconds: float) -> bool:
        """空闲判定：无任务在执行，且距最近活动达到 min_seconds。"""
        return self._inflight == 0 and (time.monotonic() - self._last_activity) >= min_seconds

    async def close(self) -> None:
        """释放路由资源。"""
        await self.jev.close()

    async def _status_text(self, chat_key: str) -> str:
        agent = await self._resolve_agent(chat_key, "")
        cwd = agent.cwd_for(chat_key) if agent else str(self.cfg.qoder_cwd_resolved)
        busy = "执行中" if any(a.busy(chat_key) for a in self.registry.active_agents) else "空闲"
        lines = [
            "IM-Agent Gateway 服务状态",
            f"• 路由 Agent：{agent.label if agent else '无（请 /agent on 开启）'}",
            f"• 工作目录：{cwd}",
        ]
        for a in self.registry.agents:
            on = self.registry.is_active(a.name)
            model = a.model_for(chat_key) or a.default_model or "默认"
            note = "" if on or a.available else "（前置条件未满足）"
            lines.append(f"• {a.label}：{'已开启' if on else '已关闭'}{note}，模型 {model}")
        lines.append(f"• 会话状态：{busy}")
        lines.append(f"• 权限模式：{self.cfg.qoder_permission_mode}")
        return "\n".join(lines)
