"""攻略 Agent 所用大模型客户端（OpenAI 兼容接口）."""

from __future__ import annotations

import json
import logging
import re

import httpx

from app.agents.config import is_llm_configured
from app.config import settings

logger = logging.getLogger(__name__)


def _build_chat_url(base_url: str) -> str:
    """构建 chat/completions URL，自动处理各种常见配置格式。"""
    base = base_url.strip().rstrip("/")
    # 如果用户配了完整路径，直接使用，避免重复拼接
    if base.endswith("/chat/completions"):
        return base
    # 去掉可能多余的后缀
    for suffix in ("/v1",):
        if base.endswith(suffix):
            # 保留 /v1，后面再拼 /chat/completions
            break
    return f"{base}/chat/completions"


async def chat_completion(system: str, user: str) -> str:
    if not is_llm_configured():
        raise RuntimeError("攻略 Agent 大模型未配置")

    url = _build_chat_url(settings.GUIDE_AGENT_LLM_BASE_URL)
    headers = {
        "Authorization": f"Bearer {settings.GUIDE_AGENT_LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.GUIDE_AGENT_LLM_MODEL or "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.7,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("大模型返回为空")
    return (choices[0].get("message") or {}).get("content") or ""


def parse_json_from_llm(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)
