"""Trae 编码 Agent：以子进程方式驱动开源版 trae-agent（bytedance/trae-agent）完成任务。

接入方式（零框架改动）：本包实现 BaseAgent 并暴露 Agent 类，
在 .env 的 AGENTS 声明中加入 "trae" 即接入路由。
配置项自行从环境变量读取（TRAE_BIN/TRAE_CWD/TRAE_CONFIG 等），
不经框架的 Config 字段，新增 Agent 不需要改 config.py。

执行协议：trae-cli run <任务> -w <目录> --config-file <json> -t <轨迹> -ct simple；
API 凭证经环境变量注入子进程，不落盘；结果从轨迹 JSON（success/final_result）提取，
进度通过轮询轨迹文件的新增 step 推送。开源版无内置会话，网关层拼接最近几轮历史实现记忆。
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

from ..base import AgentResult, BaseAgent
from ..qoder.runner import hidden_process_kwargs, resolve_cli_argv
from ...i18n import t

logger = logging.getLogger(__name__)

# 认证失败等可识别错误的提取模式
_AUTH_REQUIRED_PAT = re.compile(
    r"authenticationerror|invalid api key|not logged in|login required|请登录|未登录|认证失败",
    re.IGNORECASE,
)
# 历史轮数与单轮摘要长度上限
MAX_HISTORY_ROUNDS = 3
MAX_HISTORY_CHARS = 600
# 轮询轨迹文件的间隔（秒）
_POLL_INTERVAL = 2.0

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class _Task:
    query: str
    model: str = ""
    on_output: Callable[[str], object] | None = None
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


@dataclass
class _ChatState:
    session_id: str
    history: list[tuple[str, str]] = field(default_factory=list)  # (任务, 结果) 摘要
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    current: asyncio.subprocess.Process | None = None
    stop_requested: bool = False


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """终止进程及其子进程（Windows 下需 taskkill 清理进程树）。"""
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
                **hidden_process_kwargs(),
            )
        except Exception:
            logger.debug(t("taskkill 失败 pid=%s", "taskkill failed pid=%s"), proc.pid)


def _step_note(step: dict) -> str:
    """把轨迹里一个已完成的 step 压缩成一行进度提示。"""
    tools = step.get("tool_calls") or []
    names = []
    for tc in tools:
        name = str(tc.get("name") or "")
        args = tc.get("arguments") or {}
        detail = ""
        if name == "bash" and isinstance(args, dict):
            detail = str(args.get("command") or args.get("cmd") or "")[:60]
        elif detail == "":
            detail = str(args)[:40] if args else ""
        names.append(f"{name} {detail}".strip())
    label = "、".join(names)[:100]
    state = str(step.get("state") or "")
    head = t(f"[Trae] 第 {step.get('step_number', '?')} 步完成（{state}）",
             f"[Trae] step {step.get('step_number', '?')} done ({state})")
    return t(f"{head}：{label}", f"{head}: {label}") if label else head


class _TrajectoryReader:
    """读取轨迹 JSON，提取最终结果、进度与用量。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.seen_steps = 0

    def _load(self) -> dict | None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def poll(self) -> list[str]:
        """轮询新增的已完成 step，返回进度提示行。"""
        data = self._load()
        if data is None:
            return []
        steps = data.get("agent_steps") or []
        notes = []
        for step in steps[self.seen_steps:]:
            if isinstance(step, dict) and step.get("state") in ("completed", "error"):
                notes.append(_step_note(step))
        self.seen_steps = len(steps)
        return notes

    def result(self) -> tuple[bool, str, str | None]:
        """读取最终结果：返回 (success, final_result, usage行)。"""
        data = self._load()
        if data is None:
            return False, "", None
        success = bool(data.get("success"))
        text = str(data.get("final_result") or "").strip()
        if not text:
            # 未 finalize（被终止/异常）时取最后一步的模型回复作为部分输出
            for step in reversed(data.get("agent_steps") or []):
                resp = (step or {}).get("llm_response") or {}
                content = str(resp.get("content") or "").strip()
                if content:
                    text = content
                    break
        usage = None
        it = ot = 0
        for item in data.get("llm_interactions") or []:
            u = (item or {}).get("response", {}).get("usage") or {}
            it += int(u.get("input_tokens") or 0)
            ot += int(u.get("output_tokens") or 0)
        if it or ot:
            usage = t(f"tokens 输入 {it} / 输出 {ot}", f"tokens in {it} / out {ot}")
        return success, text, usage


class TraeAgent(BaseAgent):
    """Trae 编码 Agent：驱动开源版 trae-agent 完成任务。"""

    name = "trae"
    label = "Trae（编码）"
    label_en = "Trae (Coding)"
    kind = "coding"
    routing_hint = "Trae 编码任务：代码生成、修复、重构、执行命令（另一个编码 Agent，可用 /agent trae 指定）"
    routing_hint_en = ("Trae coding tasks: code generation, fixes, refactoring, running commands "
                       "(another coding agent, select with /agent trae)")
    # 意图关键词：编码词表由 kind=coding 默认继承，这里只追加名字直达词
    routing_keywords = ("trae",)

    def __init__(self, cfg=None) -> None:
        super().__init__()
        self._bin = _env("TRAE_BIN", "trae-cli")
        self._cwd = _env("TRAE_CWD") or str(PROJECT_ROOT)
        self._config = _env("TRAE_CONFIG")
        self._model = _env("TRAE_MODEL")
        self._timeout = float(_env("TRAE_TIMEOUT_SEC", "1800"))
        self._traj_dir = Path(_env("TRAE_TRAJ_DIR", str(Path(self._cwd) / ".trae_trajectories")))
        self._chats: dict[str, _ChatState] = {}
        self._workers: set[asyncio.Task] = set()
        self._active: set[str] = set()
        self._global_lock = asyncio.Semaphore(1)  # 全局串行，避免并发改同一目录

    # ---------- 入口 ----------

    @property
    def available(self) -> bool:
        """是否可找到 trae-cli 且配置了模型端点。"""
        return shutil.which(self._bin) is not None and bool(self._config)

    @property
    def default_model(self) -> str:
        return self._model

    def cwd_for(self, chat_key: str) -> str:
        return self._cwd

    def start_hint(self, cwd: str = "", model: str = "") -> str:
        folder = cwd or self._cwd
        return t(f"· {self.display_label}处理中… 目录：{folder}\n\n",
                 f"· {self.display_label} is working… dir: {folder}\n\n")

    async def submit(
        self,
        chat_key: str,
        query: str,
        cwd: str = "",
        on_output: Callable[[str], object] | None = None,
        model: str = "",
    ) -> asyncio.Future:
        """提交任务到会话队列；返回任务结束时 resolve 的 Future。"""
        state = self._chats.setdefault(chat_key, _ChatState(session_id=str(uuid.uuid4())))
        state.stop_requested = False
        task = _Task(query=query, model=model, on_output=on_output)
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
        await _kill_tree(state.current)
        return True

    async def reset(self, chat_key: str) -> None:
        """丢弃会话历史（网关层拼接的记忆随之清空）。"""
        state = self._chats.get(chat_key)
        if state is not None:
            state.stop_requested = False
            state.session_id = str(uuid.uuid4())
            state.history.clear()
        logger.info(t("会话 %s 已重置", "Session %s reset"), chat_key)

    def busy(self, chat_key: str) -> bool:
        state = self._chats.get(chat_key)
        if state is None:
            return False
        return chat_key in self._active or not state.queue.empty()

    # ---------- 会话工作循环 ----------

    async def _worker(self, chat_key: str, state: _ChatState) -> None:
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
                        task.done.set_result(AgentResult(ok=False, text="", error=str(exc)))
                finally:
                    state.queue.task_done()
                if state.queue.empty():
                    return
        finally:
            self._active.discard(chat_key)
            if not state.queue.empty():  # 竞态兜底：退出瞬间又排入新任务
                self._active.add(chat_key)
                worker = asyncio.create_task(self._worker(chat_key, state))
                self._workers.add(worker)
                worker.add_done_callback(lambda t: self._workers.discard(t))

    async def _run_task(self, chat_key: str, state: _ChatState, task: _Task) -> None:
        state.current = None
        result = await self._run_process(state, task)
        state.current = None
        logger.info(t("会话 %s 任务完成 ok=%s 输出 %d 字%s",
                      "Session %s task done ok=%s output %d chars%s"),
                    chat_key, result.ok, len(result.text),
                    t(f"（{result.error}）", f" ({result.error})") if result.error else "")
        state.history.append((task.query, result.text))
        del state.history[:-MAX_HISTORY_ROUNDS]
        if not task.done.done():
            task.done.set_result(result)

    # ---------- 进程执行 ----------

    def _compose_query(self, state: _ChatState, query: str) -> str:
        """开源版无内置会话记忆，把最近几轮任务与结果拼进当前任务。"""
        if not state.history:
            return query
        lines = [t("【会话历史】（同一会话的先前任务与结果，供参考）",
                   "[Chat history] (earlier tasks and results in this chat, for reference)")]
        for i, (q, r) in enumerate(state.history, 1):
            lines.append(t(f"{i}. 任务：{q[:200]}", f"{i}. Task: {q[:200]}"))
            lines.append(t(f"   结果：{r[:MAX_HISTORY_CHARS]}", f"   Result: {r[:MAX_HISTORY_CHARS]}"))
        lines.append("")
        lines.append(t("【当前任务】", "[Current task]"))
        lines.append(query)
        return "\n".join(lines)

    def _build_args(self, query: str, model: str, traj_file: Path) -> list[str]:
        model = model or self._model
        args = [
            self._bin, "run", query,
            "-w", self._cwd,
            "--config-file", self._config,
            "-t", str(traj_file),
            "-ct", "simple",
        ]
        if model:
            args += ["-m", model]
        return args

    def _child_env(self) -> dict[str, str]:
        """子进程环境：注入 API 凭证（不落盘）与去色终端设置。"""
        env = dict(os.environ)
        key = _env("SILICONFLOW_API_KEY") or _env("API_KEY")
        if key:
            env.setdefault("OPENROUTER_API_KEY", key)
        env["NO_COLOR"] = "1"
        env["COLUMNS"] = "200"
        return env

    async def _run_process(self, state: _ChatState, task: _Task) -> AgentResult:
        self._traj_dir.mkdir(parents=True, exist_ok=True)
        traj_file = self._traj_dir / f"{state.session_id}_{len(state.history) + 1}.json"
        traj_file.unlink(missing_ok=True)
        args = self._build_args(self._compose_query(state, task.query), task.model, traj_file)
        logger.info(t("启动 trae-cli（轨迹 %s）", "Starting trae-cli (trace %s)"), traj_file)
        try:
            proc = await asyncio.create_subprocess_exec(
                *resolve_cli_argv(args),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env=self._child_env(),
                **hidden_process_kwargs(),
            )
        except FileNotFoundError:
            return AgentResult(
                ok=False, text="",
                error=t(f"找不到 {self._bin}，请安装开源版 trae-agent（git clone bytedance/trae-agent + uv sync）",
                        f"Cannot find {self._bin}; install the open-source trae-agent "
                        f"(git clone bytedance/trae-agent + uv sync)"),
            )
        state.current = proc

        reader = _TrajectoryReader(traj_file)
        stdout_tail: list[str] = []
        stderr_lines: list[str] = []

        async def read_stderr() -> None:
            async for raw in proc.stderr:
                s = raw.decode("utf-8", errors="replace").strip()
                if s:
                    stderr_lines.append(s)
                    logger.warning("trae-cli stderr: %.300s", s)

        async def pump() -> None:
            """读 stdout 存尾部，并轮询轨迹文件推送进度。"""
            async def read_stdout() -> None:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    s = line.decode("utf-8", errors="replace").rstrip()
                    if s:
                        stdout_tail.append(s)
                        del stdout_tail[:-30]
            out_task = asyncio.create_task(read_stdout())
            while not out_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(out_task), timeout=_POLL_INTERVAL)
                except asyncio.TimeoutError:
                    pass
                if task.on_output is not None:
                    for note in reader.poll():
                        await task.on_output(note)
            await out_task

        stderr_task: asyncio.Task | None = None
        try:
            async with asyncio.timeout(self._timeout):
                stderr_task = asyncio.create_task(read_stderr())
                await pump()
                await proc.wait()
                await stderr_task
        except TimeoutError:
            await _kill_tree(proc)
            await proc.wait()
            return AgentResult(
                ok=False, text=reader.result()[1],
                error=t(f"任务超时（>{self._timeout:.0f}s），已终止",
                        f"Task timed out (>{self._timeout:.0f}s) and was stopped"),
            )
        except BaseException:
            await _kill_tree(proc)
            try:
                await proc.wait()
            except Exception:
                pass
            if stderr_task is not None:
                stderr_task.cancel()
            raise

        success, text, usage = reader.result()
        error_text = "\n".join(stderr_lines[-5:]).strip()
        if _AUTH_REQUIRED_PAT.search("\n".join(stderr_lines + stdout_tail)):
            return AgentResult(
                ok=False, text="",
                error=t("模型端点认证失败，请检查 SILICONFLOW_API_KEY 配置",
                        "Model endpoint authentication failed; check SILICONFLOW_API_KEY"),
            )
        if not success:
            detail = error_text or "\n".join(stdout_tail[-10:])[:400]
            if text:
                return AgentResult(ok=False, text=text, usage=usage,
                                   error=detail or t("任务未成功完成", "Task did not complete successfully"))
            return AgentResult(
                ok=False, text="",
                error=detail or t(f"trae-cli 退出码 {proc.returncode}",
                                  f"trae-cli exited with {proc.returncode}"))
        if not text:
            return AgentResult(
                ok=False, text="",
                error=error_text[:400] or t("trae-cli 无有效输出", "trae-cli produced no usable output"),
            )
        return AgentResult(ok=True, text=text, usage=usage)
