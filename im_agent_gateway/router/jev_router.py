"""Jev 决策路由：调用 Makers 网关的 System One 端点，按消息内容做两级选择。

两级路由（用途 → 复杂度）各问一次 choice：
- 第一问 route_kind()：判定消息属于哪个用途（chat / coding / office）；
- 第二问 route_complexity()：同一用途多 Agent 且都配了档位时，按问题复杂度选档位。
另有 Agent 级 route()：单级直选具体 Agent（历史口径，单用途场景仍使用）。
失败或置信度不足时一律返回 None，由上层降级到关键词路由或声明顺序兜底。
"""

from __future__ import annotations

import logging

import httpx

from ..agents.base import BaseAgent
from ..config import Config

logger = logging.getLogger(__name__)

# Agent 级直选问题的答案字段
ANSWER_KEY = "route"
# 用途决策（第一问）的答案字段
KIND_ANSWER_KEY = "kind"
# 复杂度档位决策（第二问）的答案字段
COMPLEXITY_ANSWER_KEY = "complexity"

# 各用途的一句话描述（第一问 choice 的 criteria，与 base.route_description 默认文案同源）
KIND_DESCRIPTIONS: dict[str, str] = {
    "chat": "日常聊天与通用问答",
    "coding": "编码任务：写代码、修复 bug、运行命令、git 操作、仓库文件读写",
    "office": "办公任务：文档、表格、PPT 等办公文件处理与办公自动化",
}

# 复杂度档位语义描述（第二问 choice 的 criteria）
COMPLEXITY_DESCRIPTIONS: dict[str, str] = {
    "simple": "简单直接的问题：一句话能答、无需多步推理或长文产出",
    "mid": "中等复杂度：需要一定上下文理解、多步整理或中等篇幅产出",
    "complex": "复杂推理与长任务：需要深入分析、多轮规划或大量产出",
}


class JevRouter:
    """Jev 决策路由：用途选择、复杂度档位选择与 Agent 直选共用一套请求与解析。"""

    def __init__(self, cfg: Config, transport=None) -> None:
        """transport 仅供测试注入 MockTransport，正常运行不传。"""
        self.cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.jev_base_url,
            timeout=httpx.Timeout(
                connect=5.0, read=cfg.jev_timeout_sec, write=10.0, pool=5.0
            ),
            transport=transport,
        )

    @property
    def available(self) -> bool:
        """是否具备决策条件（已配置 Key 且开关打开）。"""
        return bool(self.cfg.jev_api_key) and self.cfg.jev_route

    # ---------- 通用请求与解析 ----------

    async def _ask(self, payload: dict, tag: str) -> dict | None:
        """发一次 /systemone 决策请求；失败（网络 / 非 200 / 非 JSON）返回 None。"""
        try:
            resp = await self._client.post(
                "/systemone", headers=self._headers(), json=payload
            )
        except httpx.HTTPError as exc:
            logger.warning("Jev %s请求失败: %s", tag, exc)
            return None
        if resp.status_code != 200:
            logger.warning("Jev %s HTTP %d: %.200s", tag, resp.status_code, resp.text)
            return None
        try:
            return resp.json()
        except ValueError:
            logger.warning("Jev %s返回非法 JSON: %.200s", tag, resp.text)
            return None

    def _parse_choice(self, data: dict, answer_key: str, candidates) -> str | None:
        """解析 choice 答案：取概率最高候选；候选外或置信度不足返回 None。"""
        answer = (data.get("answers") or {}).get(answer_key) or {}
        value = answer.get("choice") or answer.get("value") or ""
        try:
            probs = {
                k: float(v)
                for k, v in (answer.get("probabilities") or {}).items()
            }
        except (TypeError, ValueError):
            probs = {}
        cand = set(candidates)
        name = max(probs, key=probs.get) if probs else value
        if name not in cand:
            logger.warning("Jev 决策返回未知候选 %r，忽略", name)
            return None
        try:
            confidence = probs.get(name) or float(answer.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < self.cfg.jev_min_confidence:
            return None
        return name

    def _choice_payload(self, state: str, answer_key: str, instructions: str,
                        criteria: dict) -> dict:
        """组装一次 choice 决策请求体（state 为本次消息原文）。"""
        return {
            "model": self.cfg.jev_model,
            "state": state,
            "questions": {
                answer_key: {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": criteria,
                }
            },
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.jev_api_key}",
            "Content-Type": "application/json",
        }

    # ---------- Agent 级直选（历史口径） ----------

    async def route(self, query: str, agents: list[BaseAgent]) -> str | None:
        """返回最合适的 Agent 名；不可用/失败/低置信度返回 None。"""
        if not self.available:
            return None
        if len(agents) <= 1:
            return agents[0].name if agents else None
        payload = self._choice_payload(
            query, ANSWER_KEY, "根据消息内容判断应该交给哪个 Agent 处理",
            {a.name: a.route_description for a in agents},
        )
        data = await self._ask(payload, "Agent 直选")
        if data is None:
            return None
        name = self._parse_choice(data, ANSWER_KEY, {a.name for a in agents})
        logger.info("Jev 决策：%.60s → %s", query, name or "低置信度（降级）")
        return name

    # ---------- 第一问：用途决策 ----------

    async def route_kind(self, query: str, kinds: list[str]) -> str | None:
        """判定消息属于哪个用途；不可用/失败/低置信返回 None（调用方降级关键词）。"""
        if not self.available:
            return None
        # 单一候选（含空列表由调用方保证不达）无需决策，直接短路省一次请求
        if len(kinds) <= 1:
            return kinds[0] if kinds else None
        payload = self._choice_payload(
            query, KIND_ANSWER_KEY, "根据消息内容判断它属于哪一类用途",
            {k: KIND_DESCRIPTIONS.get(k, k) for k in kinds},
        )
        data = await self._ask(payload, "用途决策")
        if data is None:
            return None
        kind = self._parse_choice(data, KIND_ANSWER_KEY, kinds)
        logger.info("Jev 用途决策：%.60s → %s", query, kind or "低置信度（降级关键词）")
        return kind

    # ---------- 第二问：复杂度档位决策 ----------

    async def route_complexity(self, query: str, levels: list[str]) -> str | None:
        """判定问题复杂度档位；不可用/失败/低置信返回 None（调用方取声明顺序第一个）。

        levels 为候选档位列表（调用方给出去重后的已配置档位）。
        """
        if not self.available:
            return None
        if not levels:
            return None
        # 只有一种档位无需决策，直接短路
        if len(levels) == 1:
            return levels[0]
        payload = self._choice_payload(
            query, COMPLEXITY_ANSWER_KEY, "根据消息内容判断问题的复杂程度",
            {lv: COMPLEXITY_DESCRIPTIONS.get(lv, lv) for lv in levels},
        )
        data = await self._ask(payload, "复杂度决策")
        if data is None:
            return None
        level = self._parse_choice(data, COMPLEXITY_ANSWER_KEY, levels)
        logger.info("Jev 复杂度决策：%.60s → %s", query, level or "低置信度（取第一个）")
        return level

    async def close(self) -> None:
        await self._client.aclose()
