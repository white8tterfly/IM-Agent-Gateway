"""Qoder CN 编码 Agent：以子进程方式驱动 qoderclicn 完成编码任务。

每个企微会话对应一个持久 Qoder 会话，全局串行执行避免文件编辑冲突。
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
from ...i18n import t
from ..base import BaseAgent, CommandSpec

logger = logging.getLogger(__name__)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\]8;.*?\x07|\r")
_SESSION_NOT_FOUND_PAT = re.compile(
    r"invalid session|not found|does not exist|不存在|无法找到", re.IGNORECASE
)
# 工具调用进度提示的最大条数（避免刷屏）
MAX_TOOL_NOTES = 60


def chat_session_key(chat_key: str) -> str:
    """为企微会话生成稳定的 UUID（qoder --session-id 必须是合法 UUID）。"""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "wecom:" + chat_key))


def resolve_cli_argv(args: list[str]) -> list[str]:
    """把 CLI 入口解析为可执行参数；Windows 下 .cmd/.bat 需经 cmd.exe 执行。"""
    bin_path = args[0]
    if not os.path.isabs(bin_path):
        found = shutil.which(bin_path)
        if found:
            bin_path = found
    resolved = [bin_path] + args[1:]
    if bin_path.lower().endswith((".cmd", ".bat")):
        resolved = [os.environ.get("COMSPEC", "cmd.exe"), "/c"] + resolved
    return resolved


@dataclass
class TaskResult:
    ok: bool
    text: str
    session_id: str | None = None
    error: str | None = None
    usage: str | None = None


@dataclass
class ChatState:
    session_id: str
    queue: asyncio.Queue["_Task"] = field(default_factory=asyncio.Queue)
    current: asyncio.subprocess.Process | None = None
    stop_requested: bool = False


@dataclass
class _Task:
    query: str
    cwd: str
    model: str = ""
    on_output: Callable[[str], object] | None = None
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class QoderRunner(BaseAgent):
    """Qoder CN 编码 Agent：管理子进程与会话状态。"""

    name = "qoder"
    label = "Qoder CN（编码）"
    label_en = "Qoder CN (Coding)"
    kind = "coding"
    routing_hint = "编码任务：写代码、修复 bug、运行命令、git 操作、仓库文件读写"
    routing_hint_en = "Coding tasks: write code, fix bugs, run commands, git operations, repo file access"
    # 意图关键词：编码词表由 kind=coding 默认继承，这里只追加名字直达词
    routing_keywords = ("qoder",)

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.cfg = config
        self._cwds: dict[str, str] = {}  # chat_key -> 工作目录（/cd、/ws 切换）
        self._chats: dict[str, ChatState] = {}
        self._workers: set[asyncio.Task] = set()
        self._active: set[str] = set()  # 正在运行的 worker 对应的 chat_key
        self._global_lock = asyncio.Semaphore(1)  # 全局串行，避免并发改同一目录

    # ---------- 入口 ----------

    @property
    def available(self) -> bool:
        """Qoder CN CLI 是否可执行；未安装时注册中心拒绝开启。"""
        return shutil.which(self.cfg.qoder_bin) is not None

    @property
    def default_model(self) -> str:
        return self.cfg.qoder_model

    def cwd_for(self, chat_key: str) -> str:
        """当前会话的工作目录（/cd、/ws 切换后生效）。"""
        return self._cwds.get(chat_key, str(self.cfg.qoder_cwd_resolved))

    def start_hint(self, cwd: str = "", model: str = "") -> str:
        folder = cwd or self.cfg.qoder_cwd_resolved
        return t(f"· {self.display_label}处理中… 目录：{folder}\n\n",
                 f"· {self.display_label} is working… dir: {folder}\n\n")

    async def submit(
        self,
        chat_key: str,
        query: str,
        cwd: str,
        on_output: Callable[[str], object] | None = None,
        model: str = "",
    ) -> asyncio.Future:
        """提交任务到会话队列；返回在任务结束时 resolve 的 Future[TaskResult]。"""
        state = self._chats.setdefault(
            chat_key, ChatState(session_id=chat_session_key(chat_key))
        )
        state.stop_requested = False
        task = _Task(query=query, cwd=cwd, model=model, on_output=on_output)
        state.queue.put_nowait(task)
        if chat_key not in self._active:
            self._active.add(chat_key)
            worker = asyncio.create_task(self._worker(chat_key, state))
            self._workers.add(worker)
            worker.add_done_callback(lambda t: self._workers.discard(t))
        logger.info(t("会话 %s 已排队任务: %.120s", "Session %s queued task: %.120s"), chat_key, query)
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
        """丢弃该会话的历史，下次任务开启全新 Qoder 会话。"""
        state = self._chats.get(chat_key)
        if state is not None:
            state.stop_requested = False
            state.session_id = str(uuid.uuid4())  # 全新 UUID，旧会话自然作废
        logger.info(t("会话 %s 已重置", "Session %s reset"), chat_key)

    async def list_models(self) -> list[str]:
        """列出 Qoder CN CLI 可用模型（qoderclicn --list-models）。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                *resolve_cli_argv([self.cfg.qoder_bin, "--list-models"]),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, _err = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise TimeoutError(t("获取模型列表超时，请稍后重试",
                                     "Timed out fetching models, please retry later")) from None
        except FileNotFoundError as exc:
            raise RuntimeError(t(
                f"找不到 {self.cfg.qoder_bin}，请确认已安装 Qoder CN CLI",
                f"Cannot find {self.cfg.qoder_bin}; install the Qoder CN CLI first",
            )) from exc

        models: list[str] = []
        for ln in out.decode("utf-8", errors="replace").splitlines():
            ln = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", ln).strip()
            if ln and ln.upper() != "MODEL":  # 去掉表头
                models.append(ln)
        return models

    # ---------- 指令（编码 Agent 特有） ----------

    def commands(self) -> list[CommandSpec]:
        return super().commands() + [
            CommandSpec(t("/cd <目录>", "/cd <dir>"), t("切换工作目录（支持工作区名）",
                                                       "Switch working directory (workspace name allowed)")),
            CommandSpec(t("/ws [名称]", "/ws [name]"), t("列出工作区 / 切换到命名工作区",
                                                       "List workspaces / switch to a named workspace")),
        ]

    async def handle_command(
        self, chat_key: str, cmd: str, arg: str, send: Callable[[str], object]
    ) -> bool:
        """处理 /cd、/ws；其余交回基类。"""
        if cmd == "/cd":
            if not arg:
                await send(t("用法：/cd <目录或工作区名>，如 /cd e:/my-project；/ws 查看工作区列表",
                             "Usage: /cd <dir or workspace>, e.g. /cd e:/my-project; /ws lists workspaces"))
                return True
            # 优先匹配命名工作区，其次按目录路径处理
            ws = self.cfg.workspaces.get(arg)
            path = ws if ws is not None else Path(arg).expanduser()
            if not path.is_dir():
                await send(t(f"目录不存在：{path}", f"Directory does not exist: {path}"))
                return True
            self._cwds[chat_key] = str(path.resolve())
            await send(t(f"工作目录已切换为 {path.resolve()}",
                         f"Working directory switched to {path.resolve()}"))
            return True
        if cmd in ("/ws", "/workspaces"):
            if arg:
                ws = self.cfg.workspaces.get(arg)
                if ws is None:
                    await send(t(f"未找到工作区「{arg}」，发送 /ws 查看列表",
                                 f"Workspace \"{arg}\" not found; send /ws to list them"))
                    return True
                if not ws.is_dir():
                    await send(t(f"工作区目录不存在：{ws}", f"Workspace directory does not exist: {ws}"))
                    return True
                self._cwds[chat_key] = str(ws.resolve())
                await send(t(f"已切换到工作区 {arg}：{ws.resolve()}",
                             f"Switched to workspace {arg}: {ws.resolve()}"))
                return True
            current = Path(self.cwd_for(chat_key)).resolve()
            lines = [t("工作区列表：", "Workspaces:")]
            if self.cfg.workspaces:
                for name, path in self.cfg.workspaces.items():
                    mark = t(" ← 当前", " ← current") if path.expanduser().resolve() == current else ""
                    missing = t("（不存在）", " (missing)") if not path.is_dir() else ""
                    lines.append(f"• {name}: {path}{missing}{mark}")
            else:
                lines.append(t("（未配置命名工作区，可在 .env 的 WORKSPACES 中添加）",
                               "(No named workspaces; add WORKSPACES in .env)"))
            lines.append(t(f"\n默认目录: {self.cfg.qoder_cwd_resolved}",
                           f"\nDefault directory: {self.cfg.qoder_cwd_resolved}"))
            lines.append(t("切换: /ws <名称> 或 /cd <目录>",
                           "Switch: /ws <name> or /cd <dir>"))
            await send("\n".join(lines))
            return True
        return await super().handle_command(chat_key, cmd, arg, send)

    def busy(self, chat_key: str) -> bool:
        state = self._chats.get(chat_key)
        if state is None:
            return False
        return chat_key in self._active or not state.queue.empty()

    # ---------- 会话工作循环 ----------

    async def _worker(self, chat_key: str, state: ChatState) -> None:
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
                    logger.exception(t("会话 %s 任务执行异常", "Session %s task failed"), chat_key)
                    if not task.done.done():
                        task.done.set_result(TaskResult(ok=False, text="", error=str(exc)))
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

    async def _run_task(self, chat_key: str, state: ChatState, task: _Task) -> None:
        state.current = None
        result = await self._run_process(chat_key, state, task)
        state.current = None
        logger.info(
            t("会话 %s 任务完成 ok=%s 输出 %d 字%s",
              "Session %s task done ok=%s output %d chars%s"),
            chat_key, result.ok, len(result.text),
            t(f"（{result.error}）", f" ({result.error})") if result.error else "",
        )
        if not task.done.done():
            task.done.set_result(result)

    # ---------- 进程执行 ----------

    def _build_args(self, query: str, cwd: str, model: str) -> list[str]:
        args = [
            self.cfg.qoder_bin,
            "-p", query,
            "-w", cwd,
            "--permission-mode", self.cfg.qoder_permission_mode,
            "--max-output-tokens", str(self.cfg.qoder_max_output_tokens),
            "-o", "stream-json",
        ]
        model = model or self.cfg.qoder_model
        if model:
            args += ["-m", model]
        return args

    def _build_env(self) -> dict:
        env = dict(os.environ)
        if self.cfg.qodercn_pat:
            env["QODERCN_PERSONAL_ACCESS_TOKEN"] = self.cfg.qodercn_pat
        return env

    async def _run_process(self, chat_key: str, state: ChatState, task: _Task) -> TaskResult:
        """执行一次 qoderclicn；支持 --session-id 新会话与 -r 续接。"""
        args = self._build_args(task.query, task.cwd, task.model)
        # 优先续接已有会话；若会话不存在则新建
        args_resume = args + ["-r", state.session_id]
        result = await self._exec(chat_key, args_resume, task.on_output)
        if result.ok or not self._is_session_missing(result.error or ""):
            return result
        logger.info(t("会话 %s 不存在，新建会话", "Session %s not found; creating a new session"),
                    state.session_id)
        result = await self._exec(chat_key, args + ["--session-id", state.session_id], task.on_output)
        return result

    @staticmethod
    def _is_session_missing(stderr: str) -> bool:
        return bool(_SESSION_NOT_FOUND_PAT.search(stderr))

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
                logger.debug(t("taskkill 失败 pid=%s", "taskkill failed pid=%s"), proc.pid)

    def _resolve_argv(self, args: list[str]) -> list[str]:
        """把 CLI 入口解析为可执行参数；Windows 下 .cmd/.bat 需经 cmd.exe 执行。"""
        return resolve_cli_argv(args)

    async def _exec(
        self,
        chat_key: str,
        args: list[str],
        on_output: Callable[[str], object] | None = None,
    ) -> TaskResult:
        logger.info(t("启动 qoder: %s", "Starting qoder: %s"), " ".join(args[:3]) + " ...")
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._resolve_argv(args),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(),
                cwd=self.cfg.qoder_cwd_resolved,
            )
        except FileNotFoundError:
            return TaskResult(
                ok=False, text="",
                error=t(f"找不到 {self.cfg.qoder_bin}，请确认已安装 Qoder CN CLI",
                        f"Cannot find {self.cfg.qoder_bin}; install the Qoder CN CLI first"),
            )

        state = self._chats.get(chat_key)
        if state is not None:
            state.current = proc

        parser = _StreamParser()
        collected: list[str] = []
        session_id: str | None = None

        async def pump() -> None:
            nonlocal session_id
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line_s = line.decode("utf-8", errors="replace").rstrip("\n")
                if not line_s.strip():
                    continue
                sid, text = parser.parse(line_s)
                if sid:
                    session_id = sid
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
                    logger.warning("qoder stderr: %.300s", s)

        stderr_task: asyncio.Task | None = None
        try:
            async with asyncio.timeout(self.cfg.qoder_timeout_sec):
                stderr_task = asyncio.create_task(read_stderr())
                await pump()
                await proc.wait()
                await stderr_task
        except TimeoutError:
            await self._kill_tree(proc)
            await proc.wait()
            text = "\n".join(collected)
            return TaskResult(
                ok=False, text=text, session_id=session_id,
                error=t(f"任务超时（>{self.cfg.qoder_timeout_sec:.0f}s），已终止",
                        f"Task timed out (>{self.cfg.qoder_timeout_sec:.0f}s) and was stopped"),
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
        stderr_text = "\n".join(stderr_lines)
        if proc.returncode != 0:
            return TaskResult(
                ok=False, text=text, session_id=session_id,
                error=t(f"qoder 退出码 {proc.returncode}", f"qoder exited with {proc.returncode}")
                + (f": {stderr_text}" if stderr_text else ""),
            )
        if not text:
            return TaskResult(
                ok=False, text="", session_id=session_id,
                error=stderr_text or t("qoder 无输出", "qoder produced no output"),
            )
        return TaskResult(ok=True, text=text, session_id=session_id, usage=parser.usage_line())

    # ---------- 输出解析 ----------

    @staticmethod
    def _parse_stream_line(line: str) -> tuple[str | None, str]:
        """兼容保留：无状态解析入口（测试用），业务路径请用 _StreamParser。"""
        return _StreamParser().parse(line)


class _StreamParser:
    """stream-json 逐行解析：提取增量文本与工具进度，去重并压制刷屏。"""

    def __init__(self) -> None:
        self._seen_texts: set[tuple[str, str]] = set()
        self._seen_tools: set[str] = set()
        self._tool_notes = 0
        self._collected_plain: list[str] = []
        self._usage_raw: dict | None = None  # result 事件的 usage 字段
        self._cost_usd: float | None = None  # result 事件的 total_cost_usd

    def parse(self, line: str) -> tuple[str | None, str]:
        if not line.startswith("{"):
            text = ANSI_RE.sub("", line)
            self._collected_plain.append(text)
            return None, text
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            text = ANSI_RE.sub("", line)
            self._collected_plain.append(text)
            return None, text

        session_id = evt.get("session_id")
        etype = evt.get("type")

        # 流式增量：{"type":"stream_event","event":{"type":"content_block_delta",...}}
        if etype == "stream_event":
            event = evt.get("event") or {}
            delta = event.get("delta") or {}
            if isinstance(delta, dict) and delta.get("type") in ("text_delta", "input_json_delta"):
                text = str(delta.get("text", ""))
                self._collected_plain.append(text)
                return session_id, text
            return session_id, ""

        # assistant 消息：文本块 + 工具调用
        if etype == "assistant":
            message = evt.get("message") or {}
            msg_id = message.get("id", "")
            parts: list[str] = []
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = str(block.get("text", ""))
                    key = (msg_id, text)
                    if text and key not in self._seen_texts:
                        self._seen_texts.add(key)
                        self._collected_plain.append(text)
                        parts.append(text)
                elif block.get("type") == "tool_use":
                    if self._tool_notes < MAX_TOOL_NOTES:
                        note = self._tool_note(block)
                        if note:
                            self._tool_notes += 1
                            parts.append(note)
            return session_id, "".join(parts)

        # 最终结果：与已收集文本重复时跳过
        if etype == "result":
            self._capture_usage(evt)
            if evt.get("is_error"):
                return session_id, ""
            result = evt.get("result")
            if isinstance(result, str) and result:
                collected_all = "".join(self._collected_plain)
                if result not in collected_all:
                    self._collected_plain.append(result)
                    return session_id, result
            return session_id, ""

        return session_id, ""

    def _capture_usage(self, evt: dict) -> None:
        """从 result 事件提取 token 用量与费用（缺字段时保留已捕获值）。"""
        usage = evt.get("usage")
        if isinstance(usage, dict):
            self._usage_raw = usage
        cost = evt.get("total_cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            self._cost_usd = float(cost)

    def usage_line(self) -> str | None:
        """格式化本次任务的用量摘要（tokens + 费用）；无数据返回 None。"""

        def tok(*keys: str) -> int | None:
            for k in keys:
                v = (self._usage_raw or {}).get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return int(v)
            return None

        in_t = tok("input_tokens", "inputTokens")
        out_t = tok("output_tokens", "outputTokens")
        parts: list[str] = []
        if in_t is not None or out_t is not None:
            parts.append(t(
                f"tokens 输入 {in_t if in_t is not None else '?'} / 输出 {out_t if out_t is not None else '?'}",
                f"tokens in {in_t if in_t is not None else '?'} / out {out_t if out_t is not None else '?'}",
            ))
        if self._cost_usd is not None and self._cost_usd > 0:
            parts.append(t(f"费用 ${self._cost_usd:.4f}", f"cost ${self._cost_usd:.4f}"))
        return " · ".join(parts) if parts else None

    @staticmethod
    def _tool_note(block: dict) -> str:
        name = block.get("name", "")
        tool_id = block.get("id", "")
        if not name:
            return ""
        key = f"{name}:{tool_id}"
        inp = block.get("input") or {}
        if not isinstance(inp, dict):
            return ""

        def brief(s: str, n: int = 60) -> str:
            s = str(s).replace("\n", " ").strip()
            return s[:n] + ("…" if len(s) > n else "")

        detail = ""
        for k in ("file_path", "path", "pattern", "regex"):
            if inp.get(k):
                detail = brief(inp[k])
                break
        if not detail and inp.get("command"):
            detail = brief(inp["command"])
        if not detail and inp.get("query"):
            detail = brief(inp["query"], 40)
        if not detail and inp.get("description"):
            detail = brief(inp["description"], 40)
        return t(f"\n[工具] {name}", f"\n[tool] {name}") + (f" · {detail}" if detail else "")
