"""攻略 Agent 联网搜索工具（支持 Tavily / Serper，需配置 API Key）."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SearchResult = dict[str, Any]


async def web_search(query: str, max_results: int = 5) -> list[SearchResult]:
    """
    根据关键词检索网络资料，供攻略 Agent 综合撰写内容。
    未配置 GUIDE_AGENT_WEB_SEARCH_API_KEY 时返回空列表。
    """
    if not settings.GUIDE_AGENT_WEB_SEARCH_API_KEY:
        logger.info("攻略 Agent 联网搜索未配置，跳过 web_search")
        return []

    provider = (settings.GUIDE_AGENT_WEB_SEARCH_PROVIDER or "tavily").lower()
    if provider in ("serper", "google"):
        return await _search_serper(query, max_results)
    return await _search_tavily(query, max_results)


async def _search_tavily(query: str, max_results: int) -> list[SearchResult]:
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": settings.GUIDE_AGENT_WEB_SEARCH_API_KEY,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

    results: list[SearchResult] = []
    for item in data.get("results") or []:
        results.append({
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "snippet": item.get("content") or "",
        })
    return results


async def _search_serper(query: str, max_results: int) -> list[SearchResult]:
    url = "https://google.serper.dev/search"
    headers = {
        "X-API-KEY": settings.GUIDE_AGENT_WEB_SEARCH_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {"q": query, "num": max_results}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    results: list[SearchResult] = []
    for item in (data.get("organic") or [])[:max_results]:
        results.append({
            "title": item.get("title") or "",
            "url": item.get("link") or "",
            "snippet": item.get("snippet") or "",
        })
    return results
