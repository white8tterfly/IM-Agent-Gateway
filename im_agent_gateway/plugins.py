"""插件加载：按配置声明实例化 IM 通道与 Agent，核心代码不感知具体实现。

接入约定：
- IM 通道模块暴露 Channel 类（IMChannel 子类），构造签名 Channel(cfg)
- Agent 模块暴露 Agent 类（BaseAgent 子类），构造签名 Agent(cfg)
- 配置声明支持短名（内置 channels/<名> / agents/<名> 目录约定）
  或 "模块路径:类名"（任意第三方包），逗号分隔多个
"""

from __future__ import annotations

import importlib

from .agents.base import BaseAgent
from .config import Config
from .gateway.base import IMChannel

BUILTIN_CHANNELS_ROOT = "im_agent_gateway.gateway.channels"
BUILTIN_AGENTS_ROOT = "im_agent_gateway.agents"


def _load_class(spec: str, builtin_root: str, default_attr: str) -> type:
    """把配置声明解析为类：短名按内置目录约定，"模块路径:类名" 按完整路径。"""
    spec = spec.strip()
    if ":" in spec:
        module_path, _, attr = spec.partition(":")
        attr = attr.strip() or default_attr
    else:
        module_path, attr = f"{builtin_root}.{spec}", default_attr
    try:
        module = importlib.import_module(module_path)
        cls = getattr(module, attr)
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(f"无法加载接入声明 {spec!r}：{exc}") from exc
    if not isinstance(cls, type):
        raise RuntimeError(f"接入声明 {spec!r} 解析到的 {attr} 不是类")
    return cls


def load_channel(spec: str, cfg: Config) -> IMChannel:
    """实例化一个 IM 通道；未满足 IMChannel 契约时直接报错。"""
    cls = _load_class(spec, BUILTIN_CHANNELS_ROOT, "Channel")
    if not issubclass(cls, IMChannel):
        raise RuntimeError(f"{spec!r} 对应的 {cls.__name__} 未实现 IMChannel 接口，无法接入网关")
    return cls(cfg)


def load_agent(spec: str, cfg: Config) -> BaseAgent:
    """实例化一个 Agent；未满足 BaseAgent 契约时直接报错。"""
    cls = _load_class(spec, BUILTIN_AGENTS_ROOT, "Agent")
    if not issubclass(cls, BaseAgent):
        raise RuntimeError(f"{spec!r} 对应的 {cls.__name__} 未实现 BaseAgent 接口，无法参与路由")
    return cls(cfg)
