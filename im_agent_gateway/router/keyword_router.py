"""关键字意图路由：Jev 决策模型的退化方案。

Jev 未配置 Key、调用失败或低置信度时，路由层退化到这里——按各 Agent
声明的意图关键词（BaseAgent.route_keywords）对消息做简单的命中计分，
零配置、离线也能完成多 Agent 分流。规格见 docs/spec/keyword-route-fallback.md。
"""

from __future__ import annotations

import logging

from ..agents.base import BaseAgent

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
            logger.debug("关键字路由无命中：%.60s", query)
            return None
        logger.info("关键字路由：%.60s → %s（得分 %d）", query, best_name, best_score)
        return best_name
