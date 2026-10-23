"""Jev 决策路由：调用 Makers 网关的 System One 端点，按消息内容选择最合适的 Agent。

失败或置信度不足时返回 None，由上层降级到关键词正则。
"""

from __future__ import annotations

import logging

import httpx

from ..agents.base import BaseAgent
from ..config import Config

logger = logging.getLogger(__name__)

# choice 问题的答案字段
ANSWER_KEY = "route"


class JevRouter:
    """Jev 决策路由：按消息内容选择最合适的 Agent。"""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.jev_base_url,
            timeout=httpx.Timeout(
                connect=5.0, read=cfg.jev_timeout_sec, write=10.0, pool=5.0
            ),
        )

    @property
    def available(self) -> bool:
        """是否具备决策条件（已配置 Key 且开关打开）。"""
        return bool(self.cfg.jev_api_key) and self.cfg.jev_route

    async def route(self, query: str, agents: list[BaseAgent]) -> str | None:
        """返回最合适的 Agent 名；不可用/失败/低置信度返回 None。"""
        if not self.available:
            return None
        if len(agents) <= 1:
            return agents[0].name if agents else None

        payload = {
            "model": self.cfg.jev_model,
            "state": query,
            "questions": {
                ANSWER_KEY: {
                    "type": "choice",
                    "instructions": "根据消息内容判断应该交给哪个 Agent 处理",
                    "criteria": {a.name: a.route_description for a in agents},
                }
            },
        }
        try:
            resp = await self._client.post(
                "/systemone", headers=self._headers(), json=payload
            )
        except httpx.HTTPError as exc:
            logger.warning("Jev 决策请求失败，降级默认路由: %s", exc)
            return None
        if resp.status_code != 200:
            logger.warning("Jev 决策 HTTP %d: %.200s", resp.status_code, resp.text)
            return None
        try:
            data = resp.json()
        except ValueError:
            logger.warning("Jev 决策返回非法 JSON: %.200s", resp.text)
            return None

        name = self._parse(data, agents)
        logger.info("Jev 决策：%.60s → %s", query, name or "低置信度（降级）")
        return name

    def _parse(self, data: dict, agents: list[BaseAgent]) -> str | None:
        """解析 choice 答案：选概率最高的候选，置信度不足返回 None。"""
        answer = (data.get("answers") or {}).get(ANSWER_KEY) or {}
        value = answer.get("choice") or answer.get("value") or ""
        try:
            probs = {
                k: float(v)
                for k, v in (answer.get("probabilities") or {}).items()
            }
        except (TypeError, ValueError):
            probs = {}
        names = {a.name for a in agents}
        name = max(probs, key=probs.get) if probs else value
        if name not in names:
            logger.warning("Jev 决策返回未知 Agent %r，忽略", name)
            return None
        try:
            confidence = probs.get(name) or float(answer.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < self.cfg.jev_min_confidence:
            return None
        return name

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.jev_api_key}",
            "Content-Type": "application/json",
        }

    async def close(self) -> None:
        await self._client.aclose()
