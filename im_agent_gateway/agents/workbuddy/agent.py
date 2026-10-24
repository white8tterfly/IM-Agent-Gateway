"""WorkBuddy 办公 Agent：以子进程方式驱动 codebuddy CLI 完成办公类任务。

与编码 Agent 复用 stream-json 解析与进程管理逻辑（同一 CLI 协议底座）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ...config import Config
from ..base import BaseAgent
from ..qoder.runner import _StreamParser, chat_session_key, resolve_cli_argv

logger = logging.getLogger(__name__)

# 未登录时 CLI 退出码可能为 0，但输出/错误里含认证提示
_AUTH_REQUIRED_PAT = re.compile(r"authentication required|sign in|use /login", re.IGNORECASE)
# 会话缺失文案与 qoder 不同（"No conversation found"），单独维护
_WB_SESSION_MISSING_PAT = re.compile(
    r"no conversation found|invalid session|not found|does not exist|不存在|无法找到", re.IGNORECASE
)
# codebuddy 无 --list-models，但 --help 的 --model 参数说明里列出全部模型
_MODEL_LIST_PAT = re.compile(r"Currently supported:\s*\(([^)]*)\)")

# codebuddy 会话记录存放规则：~/.codebuddy/projects/<工作目录映射名>/<会话ID>.jsonl，
# 映射名由工作目录推导——去掉盘符冒号、路径分隔符替换为连字符、盘符转小写，
# 如 E:\wbwork → e-wbwork、C:\Users\xxx → c-Users-xxx。其 -p 非交互模式不会
# 自建该目录，目录缺失时写会话文件会直接 ENOENT，需在启动前补建。


def _codebuddy_project_dir(cwd: str) -> Path:
    """按 codebuddy 的目录映射规则推导项目会话目录。"""
    raw = cwd.replace(":", "").replace("\\", "-").replace("/", "-")
    sanitized = raw[:1].lower() + raw[1:] if raw else raw
    return Path.home() / ".codebuddy" / "projects" / sanitized


@dataclass
class WBResult:
    ok: bool
    text: str
    error: str | None = None
    usage: str | None = None


@dataclass
class _WBState:
    session_id: str
    queue: asyncio.Queue["_WBTask"] = field(default_factory=asyncio.Queue)
    current: asyncio.subprocess.Process | None = None
    stop_requested: bool = False


@dataclass
class _WBTask:
    query: str
    model: str = ""
    on_output: Callable[[str], object] | None = None
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class WorkBuddyAgent(BaseAgent):
    """WorkBuddy 办公 Agent：驱动 codebuddy CLI 完成文档/表格/自动化任务。"""

    name = "workbuddy"
    label = "WorkBuddy（办公）"
    kind = "office"  # 类默认用途：办公（可被 .env 的 AGENT_KINDS 覆写）
    routing_hint = "办公任务：文档、表格、PPT 等办公文件处理与办公自动化"
    # 办公意图关键词：Jev 未配置/降级时，命中即自动路由到本 Agent
    routing_keywords = (
        "文档", "表格", "幻灯片", "演示文稿", "ppt", "excel", "word", "pdf",
        "会议纪要", "纪要", "周报", "日报", "月报", "总结报告", "办公",
        "markdown", "笔记", "待办", "日程", "排期", "写一份", "整理成",
    )

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.cfg = config
        self._chats: dict[str, _WBState] = {}
        self._workers: set[asyncio.Task] = set()
        self._active: set[str] = set()  # 正在运行的 worker 对应的 chat_key
        self._global_lock = asyncio.Semaphore(1)  # 全局串行，避免并发改同一目录

    # ---------- 入口 ----------

    @property
    def available(self) -> bool:
        """是否已安装 WorkBuddy CLI。"""
        return shutil.which(self.cfg.workbuddy_bin) is not None

    @property
    def default_model(self) -> str:
        return self.cfg.workbuddy_model

    def cwd_for(self, chat_key: str) -> str:
        return self.cfg.workbuddy_cwd

    def start_hint(self, cwd: str = "", model: str = "") -> str:
        return f"WorkBuddy 处理中（目录：{cwd or self.cfg.workbuddy_cwd}）\n"

    async def submit(
        self,
        chat_key: str,
        query: str,
        cwd: str,
        on_output: Callable[[str], object] | None = None,
        model: str = "",
    ) -> asyncio.Future:
        """提交任务到会话队列；返回在任务结束时 resolve 的 Future[WBResult]。"""
        state = self._chats.setdefault(
            chat_key, _WBState(session_id=chat_session_key(chat_key))
        )
        state.stop_requested = False
        task = _WBTask(query=query, model=model, on_output=on_output)
        state.queue.put_nowait(task)
        if chat_key not in self._active:
            self._active.add(chat_key)
            worker = asyncio.create_task(self._worker(chat_key, state))
            self._workers.add(worker)
            worker.add_done_callback(lambda t: self._workers.discard(t))
        logger.info("会话 %s 已排队任务: %.120s", chat_key, query)
        return task.done

    async def stop(self, chat_key: str) -> bool:
        """终止该会话当前正在执行的任务。"""
        state = self._chats.get(chat_key)
        if state is None or state.current is None:
            return False
        state.stop_requested = True
        await self._kill_tree(state.current)
        return True

    async def reset(self, chat_key: str) -> None:
        """丢弃该会话的历史，下次任务开启全新 WorkBuddy 会话。"""
        state = self._chats.get(chat_key)
        if state is not None:
            state.stop_requested = False
            state.session_id = str(uuid.uuid4())  # 全新 UUID，旧会话自然作废
        logger.info("会话 %s 已重置", chat_key)

    async def list_models(self) -> list[str]:
        """codebuddy 无 --list-models；从 --help 的 --model 说明中解析模型列表。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                *resolve_cli_argv([self.cfg.workbuddy_bin, "--help"]),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, _err = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise TimeoutError("获取模型列表超时，请稍后重试") from None
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"找不到 {self.cfg.workbuddy_bin}，请确认已安装 WorkBuddy CLI"
            ) from exc
        text = out.decode("utf-8", errors="replace")
        m = _MODEL_LIST_PAT.search(text)
        if not m:
            return []
        return [s.strip() for s in m.group(1).split(",") if s.strip()]

    def busy(self, chat_key: str) -> bool:
        state = self._chats.get(chat_key)
        if state is None:
            return False
        return chat_key in self._active or not state.queue.empty()

    # ---------- 会话工作循环 ----------

    async def _worker(self, chat_key: str, state: _WBState) -> None:
        try:
            while True:
                task = await state.queue.get()
                try:
                    async with self._global_lock:
                        if state.stop_requested:
                            continue
                        await self._run_task(chat_key, state, task)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # 兜底：队列不能因异常停摆
                    logger.exception("会话 %s 任务执行异常", chat_key)
                    if not task.done.done():
                        task.done.set_result(WBResult(ok=False, text="", error=str(exc)))
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

    async def _run_task(self, chat_key: str, state: _WBState, task: _WBTask) -> None:
        state.current = None
        result = await self._run_process(chat_key, state, task)
        state.current = None
        logger.info(
            "会话 %s 任务完成 ok=%s 输出 %d 字%s",
            chat_key, result.ok, len(result.text),
            f"（{result.error}）" if result.error else "",
        )
        if not task.done.done():
            task.done.set_result(result)

    # ---------- 进程执行 ----------

    def _build_args(self, query: str, model: str, resume: bool, session_id: str) -> list[str]:
        args = [
            self.cfg.workbuddy_bin,
            "-p", query,
            "--output-format", "stream-json",
            # 不带 --include-partial-messages：避免 delta 与 assistant 完整消息重复收集
            "--permission-mode", self.cfg.workbuddy_permission_mode,
            "-y",  # 非交互必需：跳过权限提示（仅受信任的办公任务）
        ]
        if resume:
            args += ["--resume", session_id]
        elif session_id:
            # 新会话沿用稳定会话 ID（--session-id），后续消息才能 --resume 续接上下文
            args += ["--session-id", session_id]
        model = model or self.cfg.workbuddy_model
        if model:
            args += ["--model", model]
        return args

    def _ensure_project_dir(self) -> None:
        """补建 codebuddy 的项目会话目录（-p 模式不自建，缺失会 ENOENT）。"""
        try:
            _codebuddy_project_dir(self.cfg.workbuddy_cwd).mkdir(parents=True, exist_ok=True)
        except Exception:
            # 补建失败不阻断任务，让 codebuddy 自身报错暴露问题
            logger.debug("补建 codebuddy 项目会话目录失败", exc_info=True)

    async def _run_process(self, chat_key: str, state: _WBState, task: _WBTask) -> WBResult:
        """执行一次 codebuddy；支持 --resume 续接，会话不存在时自动新建。"""
        # 优先续接已有会话；若会话不存在则用同一稳定 ID 新建（--session-id），
        # 会话记录随任务正常落盘，后续消息仍可 --resume 续接
        result = await self._exec(
            chat_key, self._build_args(task.query, task.model, resume=True, session_id=state.session_id),
            task.on_output,
        )
        if result.ok or not _WB_SESSION_MISSING_PAT.search(result.error or ""):
            return result
        logger.info("会话 %s 不存在，新建会话", state.session_id)
        return await self._exec(
            chat_key, self._build_args(task.query, task.model, resume=False, session_id=state.session_id),
            task.on_output,
        )

    async def _kill_tree(self, proc: asyncio.subprocess.Process) -> None:
        """终止进程及其子进程（Windows 下 cmd /c 包装后需 taskkill 清理进程树）。"""
        if proc.returncode is not None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            return
        if os.name == "nt":
            try:
                await asyncio.create_subprocess_exec(
                    "taskkill", "/F", "/T", "/PID", str(proc.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except Exception:
                logger.debug("taskkill 失败 pid=%s", proc.pid)

    async def _exec(
        self,
        chat_key: str,
        args: list[str],
        on_output: Callable[[str], object] | None = None,
    ) -> WBResult:
        logger.info("启动 codebuddy: %s", " ".join(args[:3]) + " ...")
        # codebuddy -p 模式不自建项目会话目录，先补建避免写会话文件 ENOENT
        self._ensure_project_dir()
        try:
            proc = await asyncio.create_subprocess_exec(
                *resolve_cli_argv(args),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cfg.workbuddy_cwd,
            )
        except FileNotFoundError:
            return WBResult(
                ok=False, text="",
                error=f"找不到 {self.cfg.workbuddy_bin}，请先执行 npm install -g @tencent-ai/codebuddy-code 并登录",
            )

        state = self._chats.get(chat_key)
        if state is not None:
            state.current = proc

        parser = _StreamParser()
        collected: list[str] = []
        err_lines: list[str] = []  # result 事件的 errors 数组（stdout 内的错误信息）

        async def pump() -> None:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line_s = line.decode("utf-8", errors="replace").rstrip("\n")
                if not line_s.strip():
                    continue
                # 错误事件：result(is_error) / error，提取错误供判断（会话缺失/未登录等）
                if line_s.startswith("{"):
                    try:
                        evt = json.loads(line_s)
                    except json.JSONDecodeError:
                        evt = None
                    if evt is not None and evt.get("type") in ("result", "error"):
                        if evt.get("type") == "error" or evt.get("is_error"):
                            errs = evt.get("errors") or evt.get("error") or []
                            if isinstance(errs, str):
                                errs = [errs]
                            err_lines.extend(str(e) for e in errs)
                _sid, text = parser.parse(line_s)
                if text:
                    collected.append(text)
                    if on_output:
                        await on_output(text)

        stderr_lines: list[str] = []

        async def read_stderr() -> None:
            async for raw in proc.stderr:
                s = raw.decode("utf-8", errors="replace").strip()
                if s:
                    stderr_lines.append(s)
                    logger.warning("codebuddy stderr: %.300s", s)

        stderr_task: asyncio.Task | None = None
        try:
            async with asyncio.timeout(self.cfg.workbuddy_timeout_sec):
                stderr_task = asyncio.create_task(read_stderr())
                await pump()
                await proc.wait()
                await stderr_task
        except TimeoutError:
            await self._kill_tree(proc)
            await proc.wait()
            text = "\n".join(collected)
            return WBResult(
                ok=False, text=text,
                error=f"任务超时（>{self.cfg.workbuddy_timeout_sec:.0f}s），已终止",
            )
        except BaseException:
            # 输出回调异常（如长连接断开）等：终止进程，避免孤儿进程
            await self._kill_tree(proc)
            try:
                await proc.wait()
            except Exception:
                pass
            if stderr_task is not None:
                stderr_task.cancel()
            raise

        text = "\n".join(collected).strip()
        error_text = "\n".join(err_lines + stderr_lines).strip()
        # 登录态检测：未登录时输出含认证提示，转成可读引导
        if _AUTH_REQUIRED_PAT.search(error_text):
            return WBResult(
                ok=False, text="",
                error="WorkBuddy 尚未登录，请在本机终端执行 codebuddy login 登录后重试",
            )
        if proc.returncode != 0:
            return WBResult(
                ok=False, text=text,
                error=f"codebuddy 退出码 {proc.returncode}" + (f": {error_text}" if error_text else ""),
            )
        if not text:
            return WBResult(
                ok=False, text="",
                error=error_text or "codebuddy 无输出",
            )
        return WBResult(ok=True, text=text, usage=parser.usage_line())
