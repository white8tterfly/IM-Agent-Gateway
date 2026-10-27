"""运行语言：中文 / English 双语。

全局默认语言来自配置项 LANGUAGE；Web 管理端点按请求头（X-Ui-Language，
缺省时回落到 Accept-Language）临时切换（ContextVar），因此同一个服务进程
可以同时服务不同语言的页面，而 IM 通道、看门狗等没有请求上下文的场景
使用全局默认语言。

文案在调用处以 t(中文, English) 就地给出，不集中维护词表：
同一句文案的两种语言放在一起，改动时不会漏掉另一半。
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager

SUPPORTED = ("zh", "en")
DEFAULT = "zh"

# 未显式设置时为空串，get_language 回落到全局默认值
_current: contextvars.ContextVar[str] = contextvars.ContextVar("language", default="")
_default = DEFAULT


def normalize(lang: str) -> str:
    """把外部输入的语言标记收敛到受支持的两种：zh / en。"""
    value = (lang or "").strip().lower()
    if value.startswith("en"):
        return "en"
    if value.startswith("zh"):
        return "zh"
    return DEFAULT


def set_language(lang: str) -> str:
    """设置全局默认语言（配置加载与热更新时调用）。

    只改全局默认值，不写 ContextVar：已被复制的任务上下文（IM 长连接、看门狗）
    仍持有启动时的旧语言，写 ContextVar 只影响当前协程，切换后这些任务不会更新。
    """
    global _default
    _default = normalize(lang)
    return _default


def get_language() -> str:
    """当前生效语言：请求上下文优先，其次全局默认。"""
    return _current.get() or _default


@contextmanager
def use_language(lang: str):
    """在请求范围内临时切换语言，退出时恢复。"""
    token = _current.set(normalize(lang))
    try:
        yield
    finally:
        _current.reset(token)


def t(zh: str, en: str) -> str:
    """按当前语言返回中文或英文文案。"""
    return en if get_language() == "en" else zh


def parse_accept_language(header: str) -> str:
    """按 q 权重挑选第一个受支持的语言；无法识别时用全局默认。"""
    best, best_q = "", -1.0
    for part in (header or "").split(","):
        name, _, params = part.strip().partition(";")
        code = name.strip().lower()
        if not code:
            continue
        q = 1.0
        for p in params.split(";"):
            p = p.strip()
            if p.startswith("q="):
                try:
                    q = float(p[2:])
                except ValueError:
                    q = 0.0
        if code.split("-")[0] not in SUPPORTED:
            continue
        if q > best_q:
            best, best_q = code, q
    return normalize(best) if best else _default
