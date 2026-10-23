"""SiliconFlow 聊天 Agent：OpenAI 兼容接口，SSE 流式输出。

每个企微会话独立维护多轮历史，会话内任务串行。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Callable

import httpx

from ...config import Config
from ..base import BaseAgent

logger = logging.getLogger(__name__)

# 单会话保留的最大历史消息条数（user/assistant 各计 1 条），防止上下文无限增长
MAX_HISTORY = 40


@dataclass
class SFResult:
    ok: bool
    text: str
    error: str | None = None
    usage: str | None = None


@dataclass
class _Task:
    query: str
    model: str = ""
    on_output: Callable[[str], object] | None = None
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


@dataclass
class _ChatState:
    history: list[dict] = field(default_factory=list)
    queue: asyncio.Queue["_Task"] = field(default_factory=asyncio.Queue)
    current: asyncio.Task | None = None  # 正在执行的 HTTP 请求任务，供 /stop 取消
    stop_requested: bool = False


class SiliconFlowAgent(BaseAgent):
    """SiliconFlow 聊天 Agent。"""

    name = "siliconflow"
    label = "SiliconFlow（聊天）"
    kind = "chat"
    routing_hint = "日常聊天与通用问答：闲聊、知识问答、非编码话题"

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.cfg = config
        self._chats: dict[str, _ChatState] = {}
        self._active: set[str] = set()
        self._workers: set[asyncio.Task] = set()
        self._client = httpx.AsyncClient(
            base_url=self.cfg.sf_base_url,
            timeout=httpx.Timeout(connect=15.0, read=self.cfg.sf_timeout_sec, write=30.0, pool=15.0),
        )

    @property
    def available(self) -> bool:
        """是否已配置 API_KEY。"""
        return bool(self.cfg.sf_api_key)

    @property
    def default_model(self) -> str:
        return self.cfg.sf_model

    def start_hint(self, cwd: str = "", model: str = "") -> str:
        return f"SiliconFlow 回复中（模型：{model or self.cfg.sf_model}）…\n"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.sf_api_key}",
            "Content-Type": "application/json",
        }

    # ---------- 入口 ----------

    async def submit(
        self,
        chat_key: str,
        query: str,
        cwd: str = "",
        on_output: Callable[[str], object] | None = None,
        model: str = "",
    ) -> asyncio.Future:
        """提交聊天请求到会话队列。"""
        state = self._chats.setdefault(chat_key, _ChatState())
        state.stop_requested = False
        task = _Task(query=query, model=model, on_output=on_output)
        state.queue.put_nowait(task)
        if chat_key not in self._active:
            self._active.add(chat_key)
            worker = asyncio.create_task(self._worker(chat_key, state))
            self._workers.add(worker)
            worker.add_done_callback(lambda t: self._workers.discard(t))
        logger.info("会话 %s 已排队 SF 请求: %.120s", chat_key, query)
        return task.done

    async def stop(self, chat_key: str) -> bool:
        """取消该会话正在进行的 HTTP 请求。"""
        state = self._chats.get(chat_key)
        if state is None or state.current is None:
            return False
        state.stop_requested = True
        state.current.cancel()
        return True

    async def reset(self, chat_key: str) -> None:
        """清空该会话的多轮对话历史。"""
        state = self._chats.get(chat_key)
        if state is not None:
            state.history.clear()
        logger.info("会话 %s SF 历史已清空", chat_key)

    def busy(self, chat_key: str) -> bool:
        state = self._chats.get(chat_key)
        if state is None:
            return False
        return chat_key in self._active or not state.queue.empty()

    async def list_models(self) -> list[str]:
        """获取账号可用的模型列表（GET /models）。"""
        resp = await self._client.get("/models", headers=self._headers())
        if resp.status_code != 200:
            raise RuntimeError(self._extract_error(resp.text, resp.status_code))
        data = resp.json()
        models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        return sorted(models)

    async def close(self) -> None:
        """关闭 HTTP 客户端并取消未完成的任务。"""
        for state in self._chats.values():
            if state.current is not None:
                state.current.cancel()
        for worker in list(self._workers):
            worker.cancel()
        await self._client.aclose()

    # ---------- 会话工作循环 ----------

    async def _worker(self, chat_key: str, state: _ChatState) -> None:
        try:
            while True:
                task = await state.queue.get()
                try:
                    await self._run_task(chat_key, state, task)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # 兜底：队列不能因异常停摆
                    logger.exception("会话 %s SF 请求异常", chat_key)
                    if not task.done.done():
                        task.done.set_result(SFResult(ok=False, text="", error=str(exc)))
                finally:
                    state.queue.task_done()
                if state.queue.empty():
                    return
        finally:
            self._active.discard(chat_key)
            # 竞态兜底：worker 退出瞬间又排入了新任务，则拉起新 worker
            if not state.queue.empty():
                self._active.add(chat_key)
                worker = asyncio.create_task(self._worker(chat_key, state))
                self._workers.add(worker)
                worker.add_done_callback(lambda t: self._workers.discard(t))

    async def _run_task(self, chat_key: str, state: _ChatState, task: _Task) -> None:
        req_task = asyncio.create_task(
            self._chat_once(chat_key, task.query, task.model, task.on_output)
        )
        state.current = req_task
        try:
            result = await req_task
        except asyncio.CancelledError:
            if state.stop_requested:
                result = SFResult(ok=False, text="", error="已手动终止")
            else:
                raise
        state.current = None
        logger.info(
            "会话 %s SF 回复完成 ok=%s 输出 %d 字%s",
            chat_key, result.ok, len(result.text),
            f"（{result.error}）" if result.error else "",
        )
        if not task.done.done():
            task.done.set_result(result)

    # ---------- HTTP 请求 ----------

    async def _chat_once(
        self,
        chat_key: str,
        query: str,
        model: str,
        on_output: Callable[[str], object] | None = None,
    ) -> SFResult:
        state = self._chats.setdefault(chat_key, _ChatState())
        state.history.append({"role": "user", "content": query})
        self._trim_history(state)

        payload = {
            "model": model or self.cfg.sf_model,
            "messages": list(state.history),
            "stream": True,
            "max_tokens": self.cfg.sf_max_tokens,
            # 让 OpenAI 兼容接口在最后一个 chunk 回传 usage（token 用量）
            "stream_options": {"include_usage": True},
        }
        collected: list[str] = []
        usage_raw: dict | None = None
        try:
            async with self._client.stream(
                "POST", "/chat/completions", headers=self._headers(), json=payload
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    return SFResult(
                        ok=False, text="",
                        error=self._extract_error(body, resp.status_code),
                    )
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    usage_chunk = chunk.get("usage")
                    if isinstance(usage_chunk, dict):
                        usage_raw = usage_chunk
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if self.cfg.sf_include_reasoning:
                            reason = delta.get("reasoning_content") or ""
                            if reason and on_output:
                                await on_output(f"\n[思考] {reason}\n")
                        text = delta.get("content") or ""
                        if text:
                            collected.append(text)
                            if on_output:
                                await on_output(text)
        except httpx.HTTPError as exc:
            return SFResult(ok=False, text="", error=f"SiliconFlow 请求失败: {exc}")

        text = "".join(collected).strip()
        if not text:
            return SFResult(ok=False, text="", error="SiliconFlow 返回空内容")
        state.history.append({"role": "assistant", "content": text})
        self._trim_history(state)
        return SFResult(ok=True, text=text, usage=self._usage_line(usage_raw))

    @staticmethod
    def _usage_line(usage: dict | None) -> str | None:
        """把 OpenAI 风格 usage 格式化成摘要行；无数据返回 None。"""
        if not usage:
            return None

        def tok(*keys: str) -> int | None:
            for k in keys:
                v = usage.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return int(v)
            return None

        prompt = tok("prompt_tokens", "input_tokens")
        completion = tok("completion_tokens", "output_tokens")
        if prompt is None and completion is None:
            return None
        return (
            f"tokens 输入 {prompt if prompt is not None else '?'} "
            f"/ 输出 {completion if completion is not None else '?'}"
        )

    @staticmethod
    def _extract_error(body: str, status: int) -> str:
        try:
            data = json.loads(body)
            if isinstance(data, dict) and data.get("message"):
                return f"SiliconFlow HTTP {status}: {data['message']}"
        except json.JSONDecodeError:
            pass
        return f"SiliconFlow HTTP {status}: {body[:200]}"

    @staticmethod
    def _trim_history(state: _ChatState) -> None:
        if len(state.history) > MAX_HISTORY:
            del state.history[: len(state.history) - MAX_HISTORY]
