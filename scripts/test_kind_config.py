# Agent 用途与复杂度配置测试：AGENT_KINDS / AGENT_COMPLEXITY / COMPLEXITY_ROUTE
# 覆盖：配置解析（含非法值告警忽略）、注册后元数据覆写、内置 Agent 类默认用途（无网络依赖）
# 用法：python scripts/test_kind_config.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from im_agent_gateway.agents.base import AgentResult, BaseAgent
from im_agent_gateway.agents.registry import AgentRegistry
from im_agent_gateway.config import Config, load_config
from im_agent_gateway.i18n import set_language


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        sys.exit(1)


class FakeAgent(BaseAgent):
    """最小 Agent 实现：仅承载用途/档位元数据。"""

    def __init__(self, name: str, kind: str = "chat") -> None:
        super().__init__()
        self.name = name
        self.label = name
        self.kind = kind

    async def submit(self, chat_key, query, cwd="", on_output=None, model=""):
        async def _run() -> AgentResult:
            return AgentResult(ok=True, text=query)
        return _run()

    async def stop(self, chat_key) -> bool:
        return False

    async def reset(self, chat_key) -> None:
        pass

    def busy(self, chat_key) -> bool:
        return False


def load_with_env(**pairs) -> Config:
    """注入环境变量后加载配置；结束还原现场（值 None 表示按未配置处理）。"""
    saved = {}
    for key, val in pairs.items():
        saved[key] = os.environ.get(key)
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val
    try:
        cfg = load_config()
        set_language("zh")  # 断言中文文案：固定语言，避免受本机 .env 的 LANGUAGE 影响
        return cfg
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def main() -> None:
    # ===== 一、默认值：三项未配置时与现状一致 =====
    cfg = load_with_env(AGENT_KINDS=None, AGENT_COMPLEXITY=None, COMPLEXITY_ROUTE=None)
    check(cfg.agent_kinds == {}, "AGENT_KINDS 默认空映射（用各 Agent 类默认用途）")
    check(cfg.agent_complexity == {}, "AGENT_COMPLEXITY 默认空映射（不参与档位选择）")
    check(cfg.complexity_route is False, "COMPLEXITY_ROUTE 默认关闭")

    # ===== 二、AGENT_KINDS 解析 =====
    cfg = load_with_env(AGENT_KINDS="workbuddy=office,qoder=coding")
    check(cfg.agent_kinds == {"workbuddy": "office", "qoder": "coding"},
          "AGENT_KINDS 正常解析为 名称→用途 映射")
    cfg = load_with_env(AGENT_KINDS="workbuddy=office,foo=bar,,qoder")
    check(cfg.agent_kinds == {"workbuddy": "office"},
          "非法用途值与空项告警并忽略，不影响其余项")
    cfg = load_with_env(AGENT_KINDS="WorkBuddy=Office")
    check(cfg.agent_kinds == {"WorkBuddy": "office"},
          "用途值大小写不敏感（统一小写），名称保留原样")

    # ===== 三、AGENT_COMPLEXITY 解析 =====
    cfg = load_with_env(AGENT_COMPLEXITY="a=simple,b=mid,c=complex")
    check(cfg.agent_complexity == {"a": "simple", "b": "mid", "c": "complex"},
          "AGENT_COMPLEXITY 三档位正常解析")
    cfg = load_with_env(AGENT_COMPLEXITY="a=simple,d=huge,e=")
    check(cfg.agent_complexity == {"a": "simple"},
          "非法档位与空值告警并忽略")

    # ===== 四、COMPLEXITY_ROUTE 开关 =====
    cfg = load_with_env(COMPLEXITY_ROUTE="1")
    check(cfg.complexity_route is True, "COMPLEXITY_ROUTE=1 开启")
    cfg = load_with_env(COMPLEXITY_ROUTE="0")
    check(cfg.complexity_route is False, "COMPLEXITY_ROUTE=0 关闭")

    # ===== 五、覆写应用：注册后按映射改实例 kind / complexity =====
    wb = FakeAgent("wb", kind="chat")
    coder = FakeAgent("coder", kind="coding")
    reg = AgentRegistry()
    reg.register(wb)
    reg.register(coder)
    check(wb.complexity == "", "Agent 实例 complexity 默认空（未声明档位）")

    # ghost 未注册：静默忽略（配置允许声明未接入的 Agent，不算错误）
    reg.apply_meta_overrides({"wb": "office"}, {"coder": "complex", "ghost": "simple"})
    check(wb.kind == "office", "AGENT_KINDS 覆写实例 kind 生效")
    check(coder.complexity == "complex", "AGENT_COMPLEXITY 覆写实例档位生效")
    check(coder.kind == "coding", "只覆写映射内的项，其余保持类默认")

    # ===== 六、内置 Agent 类默认用途 =====
    from im_agent_gateway.agents.qoder.runner import QoderRunner
    from im_agent_gateway.agents.siliconflow.agent import SiliconFlowAgent
    from im_agent_gateway.agents.trae.agent import TraeAgent
    from im_agent_gateway.agents.workbuddy.agent import WorkBuddyAgent
    check(WorkBuddyAgent.kind == "office", "workbuddy 类默认用途为 office（办公）")
    check(QoderRunner.kind == "coding", "qoder 类默认用途为 coding（编码）")
    check(TraeAgent.kind == "coding", "trae 类默认用途为 coding（编码）")
    check(SiliconFlowAgent.kind == "chat", "siliconflow 类默认用途为 chat（对话）")

    # ===== 七、office 默认路由描述（未声明 routing_hint 时按用途生成） =====
    check("办公" in FakeAgent("x", kind="office").route_description,
          "office 用途有默认路由描述")
    check("编码" in FakeAgent("x", kind="coding").route_description,
          "coding 用途默认路由描述保持不变")
    check("聊天" in FakeAgent("x", kind="chat").route_description,
          "chat 用途默认路由描述保持不变")

    print("\n全部通过")


main()
