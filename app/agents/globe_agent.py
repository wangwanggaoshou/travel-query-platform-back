"""全球景点发现 Agent：根据国家名，用联网搜索 + LLM 实时生成 3-5 个推荐景点/活动。"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from app.agents.config import is_agent_configured, is_web_search_configured
from app.agents.llm import chat_completion, parse_json_from_llm
from app.agents.tools.web_search import web_search

logger = logging.getLogger(__name__)

# 内存缓存：国家名 → 景点列表（避免同一国家重复搜索）
_cache: dict[str, tuple[list[dict], float]] = {}
_CACHE_TTL = 3600  # 1 小时

GLOBE_ATTRACTIONS_PROMPT = """你是旅途智览的全球旅游目的地规划助手。
根据用户指定的国家，为该国家推荐 3～5 个标志性旅游目的地（可包含地标建筑、自然奇观、文化遗产、节庆活动、必做体验等）。

每个目的地必须包含：
- name：中文名称
- nameEn：英文名称
- location：所在城市/地区（中文）
- description：60～120 字的中文介绍，突出特色与旅行理由
- type：类型（landmark / nature / event）
- guideTopic：用于生成 AI 攻略的搜索主题（中文）
- guideCategory：攻略风格（history / nature / city / food）
- imageQuery：用于搜索配图的英文关键词（如"Eiffel Tower Paris"）

输出合法 JSON（不要 markdown 代码块）：
{
  "attractions": [
    {
      "name": "埃菲尔铁塔",
      "nameEn": "Eiffel Tower",
      "location": "巴黎",
      "description": "法国象征，登顶俯瞰巴黎全景…",
      "type": "landmark",
      "guideTopic": "巴黎埃菲尔铁塔游览攻略",
      "guideCategory": "city",
      "imageQuery": "Eiffel Tower Paris France"
    }
  ]
}
若确实找不到合适结果，返回 { "attractions": [] }。"""


class GlobeAgent:
    @staticmethod
    def is_ready() -> bool:
        return is_agent_configured() and is_web_search_configured()

    @staticmethod
    async def discover_country_attractions(
        country_name: str,
        country_en: str = "",
        limit: int = 5,
    ) -> list[dict]:
        """实时发现某国家的标志性旅游目的地。

        Args:
            country_name: 中文国名
            country_en: 英文国名
            limit: 最多返回多少个（3~5）

        Returns:
            [{name, nameEn, location, description, type, guideTopic, guideCategory, imageQuery}, …]
        """
        limit = min(max(3, limit), 5)
        cache_key = f"{country_name}|{country_en}"

        # 检查缓存
        cached = _cache.get(cache_key)
        if cached:
            data, ts = cached
            if time.time() - ts < _CACHE_TTL:
                return data[:limit]

        if not GlobeAgent.is_ready():
            logger.warning("GlobeAgent 未配置，无法实时发现景点")
            return []

        try:
            # Step 1: 联网搜索
            search_query = f"top {limit} must-visit attractions activities in {country_en or country_name} travel guide"
            search_results = []
            if is_web_search_configured():
                try:
                    search_results = await web_search(search_query, max_results=6)
                except Exception as exc:
                    logger.warning("GlobeAgent 联网搜索失败: %s", exc)

            # Step 2: LLM 生成结构化结果
            refs = "\n".join(
                f"- {(r.get('title') or '')}: {(r.get('snippet') or '')[:200]}"
                for r in search_results[:5]
            ) or "（无联网摘要，请根据常识推荐）"

            user_msg = (
                f"请为「{country_name}」({country_en or '未知英文名'}) 推荐 {limit} 个标志性旅游目的地。\n\n"
                f"联网搜索结果：\n{refs}"
            )

            raw = await chat_completion(GLOBE_ATTRACTIONS_PROMPT, user_msg)
            data = parse_json_from_llm(raw)
            attractions = data.get("attractions") or []
            if isinstance(attractions, dict):
                attractions = [attractions]

            # 给每个景点加上唯一 ID
            result = []
            for i, item in enumerate(attractions[:limit]):
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                item["id"] = f"globe-ai-{country_en or country_name}-{i + 1}"
                result.append(item)

            # 写入缓存
            _cache[cache_key] = (result, time.time())

            return result[:limit]

        except Exception as exc:
            logger.exception("GlobeAgent 发现景点失败(%s): %s", country_name, exc)
            return []
