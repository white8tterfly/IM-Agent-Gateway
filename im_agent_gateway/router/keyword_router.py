"""关键字意图路由：Jev 决策模型的退化方案。

Jev 未配置 Key、调用失败或低置信度时，路由层退化到这里——提供两级能力：
- Agent 级 route()：按各 Agent 词表（BaseAgent.route_keywords）命中计分选 Agent；
- 用途级 route_kind()：两级路由第一级（用途决策）的退化方案，
  按各用途内置词表计分选出 chat / coding / office。
零配置、离线也能完成多 Agent 分流。规格见 docs/spec/keyword-route-fallback.md
与 docs/spec/agent-kind-route.md。
"""

from __future__ import annotations

import logging

from ..agents.base import BaseAgent
from ..i18n import t

logger = logging.getLogger(__name__)

# 内置编码词表：未显式声明 routing_keywords 的编码类 Agent（kind="coding"）
# 默认继承（自 dispatcher 旧版 _CODE_INTENT_RE 迁出，行为保持一致）
CODE_KEYWORDS: tuple[str, ...] = (
    "代码", "编码", "编程", "修复", "修一下", "bug", "报错", "异常", "崩溃",
    "编译", "运行", "实现", "函数", "方法", "接口", "脚本", "命令", "git",
    "commit", "push", "merge", "分支", "部署", "重构", "单元测试", "代码审查",
    "依赖", "打包", "发布", "文件", "目录", "仓库", "日志", "调试",
    "code", "fix", "debug", "implement", "refactor", "build", "deploy",
    "script", "function", "compile", "error",
)

# 内置办公词表：未显式声明 routing_keywords 的办公类 Agent（kind="office"）
# 默认继承；用途级计分同样使用。偏文档表格 / 汇总整理 / 日程沟通类词汇
OFFICE_KEYWORDS: tuple[str, ...] = (
    "文档", "表格", "会议纪要", "会议", "纪要", "PPT", "ppt", "幻灯片",
    "报告", "周报", "日报", "邮件", "文案", "汇总", "整理", "排版",
    "格式", "图表", "Excel", "excel", "Word", "word", "总结", "翻译",
    "润色", "改写", "日程", "待办", "排班", "做表", "写稿",
)

# 用途级词表映射（两级路由第一级的退化计分依据）：
# coding / office 声明内置词表；chat 不声明词表——闲聊本身无特征词，
# 全部未命中时由调用方落 DEFAULT_AGENT 所在用途兜底
KIND_KEYWORDS: dict[str, tuple[str, ...]] = {
    "coding": CODE_KEYWORDS,
    "office": OFFICE_KEYWORDS,
}


class KeywordRouter:
    """关键字意图路由器：按 Agent 关键词命中计分选择路。"""

    @staticmethod
    def _score(query: str, keywords: tuple[str, ...]) -> int:
        """统计词表在消息中的命中总次数（大小写不敏感；一个词多次出现计多次）。"""
        text = query.lower()
        return sum(text.count(kw.lower()) for kw in keywords)

    def route(self, query: str, agents: list[BaseAgent]) -> str | None:
        """返回得分最高的 Agent 名；全部未命中返回 None。

        裁决规则（保守）：
        · 得分 = 该 Agent 全部关键词在消息中的命中次数之和；
        · 最高分必须大于 0 才产生结果；
        · 多个 Agent 并列最高分时，取注册顺序中最先出现者
          （与旧版「编码命中即选第一个 coding Agent」的行为一致）。
        """
        best_name: str | None = None
        best_score = 0
        for agent in agents:
            keywords = agent.route_keywords
            if not keywords:
                continue  # 未声明词表的 Agent 不参与关键字路由
            score = self._score(query, keywords)
            if score > best_score:
                best_score = score
                best_name = agent.name
        if best_name is None:
            logger.debug(t("关键字路由无命中：%.60s", "Keyword routing: no match for %.60s"), query)
            return None
        logger.info(t("关键字路由：%.60s → %s（得分 %d）",
                      "Keyword routing: %.60s → %s (score %d)"), query, best_name, best_score)
        return best_name

    def route_kind(self, query: str, kinds: list[str]) -> str | None:
        """用途级退化计分：按各用途内置词表对消息命中计分，返回得分最高的用途。

        两级路由第一级（用途决策）在 Jev 不可用 / 低置信时退化到这里。
        裁决口径与 Agent 级 route() 完全一致：
        · 得分 = 用途词表在消息中的命中次数之和；
        · 最高分须大于 0 才产生结果，否则返回 None
          （调用方落 DEFAULT_AGENT 所在用途）；
        · kinds 为按注册顺序去重后的活跃用途列表（调用方保证），
          并列最高分时取列表中先出现者；
        · chat 未声明词表，永不命中。
        """
        best_kind: str | None = None
        best_score = 0
        for kind in kinds:
            keywords = KIND_KEYWORDS.get(kind, ())
            if not keywords:
                continue  # 未声明词表的用途不参与计分（如 chat）
            score = self._score(query, keywords)
            if score > best_score:
                best_score = score
                best_kind = kind
        if best_kind is None:
            logger.debug(t("用途级关键字路由无命中：%.60s", "Kind-level keyword routing: no match for %.60s"), query)
            return None
        logger.info(t("用途级关键字路由：%.60s → %s（得分 %d）",
                      "Kind-level keyword routing: %.60s → %s (score %d)"), query, best_kind, best_score)
        return best_kind
