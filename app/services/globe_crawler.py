"""3D 地球 — 联网爬取国家标志性地标（网络搜索 + LLM 结构化）"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from app.agents.config import is_llm_configured, is_web_search_configured
from app.database import MySQLSession, get_sqlite_db
from app.models.globe_landmark import GlobeLandmark

logger = logging.getLogger(__name__)

_CRAWL_LOCK: dict[str, asyncio.Lock] = {}
_CRAWL_LOCKS_LOCK = asyncio.Lock()

_FALLBACK_TITLE_RE = re.compile(
    r"^\d+[\.\)]\s*(.+?)(?:\s*[-–—]\s*|\s*\(|\s*$)", re.UNICODE
)


async def _get_crawl_lock(key: str) -> asyncio.Lock:
    async with _CRAWL_LOCKS_LOCK:
        if key not in _CRAWL_LOCK:
            _CRAWL_LOCK[key] = asyncio.Lock()
        return _CRAWL_LOCK[key]


def _build_landmark_id(country_key: str, idx: int) -> str:
    safe = re.sub(r"[^a-z0-9-]", "", country_key.lower().replace(" ", "-"))[:30]
    return f"globe-{safe}-{idx}"


def _save_to_db(
    country_key: str,
    country_name: str,
    country_name_en: str,
    country_flag: str,
    iso_code: str | None,
    attractions: list[dict],
    source: str,
) -> None:
    """将爬取结果存入 MySQL。"""
    db = MySQLSession()
    try:
        existing = db.query(GlobeLandmark).filter(
            GlobeLandmark.country_key == country_key
        ).first()
        if existing:
            existing.attractions = attractions
            existing.source = source
        else:
            record = GlobeLandmark(
                country_key=country_key,
                country_name=country_name,
                country_name_en=country_name_en,
                country_flag=country_flag,
                iso_code=iso_code,
                attractions=attractions,
                source=source,
            )
            db.add(record)
        db.commit()
        logger.info("GlobeLandmark 已入库 MySQL: %s (%d 个景点)", country_key, len(attractions))
    except Exception as exc:
        db.rollback()
        logger.warning("GlobeLandmark 入库失败 (%s): %s", country_key, exc)
    finally:
        db.close()


def _check_db_cache(country_key: str) -> list[dict] | None:
    """检查缓存：先 SQLite → 再 MySQL。"""
    for factory in [get_sqlite_db, lambda: MySQLSession()]:
        db = factory()
        try:
            row = db.query(GlobeLandmark).filter(
                GlobeLandmark.country_key == country_key
            ).first()
            if row and row.attractions:
                return row.attractions
        finally:
            db.close()
    return None


async def _search_for_landmarks(country_name: str) -> list[dict]:
    """网络搜索该国的著名地标。"""
    from app.agents.tools.web_search import web_search

    results = await web_search(
        f"famous landmarks and top tourist attractions in {country_name}", max_results=5
    )
    if not results:
        results = await web_search(
            f"{country_name} must-see destinations sightseeing spots", max_results=5
        )
    return results


async def _llm_extract_landmarks(country_name: str, search_results: list[dict]) -> list[dict] | None:
    """用 LLM 从搜索结果提取地标结构化数据。"""
    if not is_llm_configured():
        return None

    from app.agents.llm import chat_completion, parse_json_from_llm

    snippets = "\n\n".join(
        f"- {r.get('title','')}: {r.get('snippet','')}" for r in search_results
    )

    system = (
        "You are a world travel expert. Given a country name and web search snippets "
        "about its tourist attractions, return the 5 most iconic landmarks or destinations.\n\n"
        "Return ONLY a JSON array (no other text). Each item:\n"
        '{"name": "Chinese name (use English if unsure)", '
        '"nameEn": "English name", '
        '"location": "city or region", '
        '"description": "one Chinese sentence describing it (40-80 chars)", '
        '"type": "landmark|nature|event", '
        '"guideCategory": "history|nature|city|roadtrip"}'
    )

    user = f"Country: {country_name}\n\nWeb search results:\n{snippets}"

    try:
        raw = await chat_completion(system, user)
        data = parse_json_from_llm(raw)
        if isinstance(data, list) and len(data) > 0:
            return data[:5]
    except Exception as exc:
        logger.warning("LLM 提取地标失败 (%s): %s", country_name, exc)

    return None


def _parse_landmarks_from_snippets(country_name: str, search_results: list[dict]) -> list[dict]:
    """从搜索片段中解析地标名（LLM 不可用时的降级方案）。"""
    landmarks: list[dict] = []
    seen: set[str] = set()

    for result in search_results:
        title = (result.get("title") or "").strip()
        if not title or len(title) < 3:
            continue

        # 跳过含数字排名的前缀 "10 Best", "Top 5" 等
        cleaned = re.sub(
            r"^(?:\d+\s*(?:Best|Top|Most|Famous|Must|Popular|Iconic|Amazing)\s*)+\s*",
            "",
            title,
            flags=re.IGNORECASE,
        )

        # 从标题拆出景点名
        parts = re.split(r"\s*(?:[-–—]\s*|:\s*|\s+in\s+|\s+–\s+)", cleaned)
        for part in parts:
            name = part.strip().rstrip(".")
            if (
                len(name) >= 3
                and name.lower() not in seen
                and name.lower() != country_name.lower()
                and not re.match(
                    r"^(best|top|most|famous|guide|travel|visit|tour|attractions?|things?|places?|destinations?)$",
                    name,
                    re.IGNORECASE,
                )
            ):
                seen.add(name.lower())
                landmarks.append({
                    "name": name,
                    "nameEn": name,
                    "location": country_name,
                    "description": f"{country_name} 著名旅游景点",
                    "type": "landmark",
                    "guideCategory": "city",
                })
                if len(landmarks) >= 5:
                    return landmarks

    return landmarks


async def _llm_generate_landmarks(country_name: str) -> list[dict] | None:
    """无网络搜索时，直接用 LLM 内置知识生成地标（降级方案）。"""
    if not is_llm_configured():
        return None

    from app.agents.llm import chat_completion, parse_json_from_llm

    system = (
        "You are a world travel expert. List the 5 most iconic tourist landmarks, "
        "natural wonders or festivals in a given country.\n\n"
        "Return ONLY a JSON array (no other text). Each item:\n"
        '{"name": "Chinese name (use English if unsure)", '
        '"nameEn": "English name", '
        '"location": "city or region", '
        '"description": "one Chinese sentence describing it (40-80 chars)", '
        '"type": "landmark|nature|event", '
        '"guideCategory": "history|nature|city|roadtrip"}'
    )

    user = f"Country: {country_name}\nList exactly 5 most famous tourist destinations."

    try:
        raw = await chat_completion(system, user)
        data = parse_json_from_llm(raw)
        if isinstance(data, list) and len(data) > 0:
            return data[:5]
    except Exception as exc:
        logger.warning("LLM 直接生成地标失败 (%s): %s", country_name, exc)

    return None


async def _wikipedia_crawl_landmarks(country_name: str) -> list[dict] | None:
    """通过 Wikipedia API 获取国家标志性地标（无需任何 API Key 的终极降级方案）。"""
    import httpx

    from crawler.mediawiki import search_country_landmarks

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            results = await search_country_landmarks(
                client, country_name, max_results=5
            )
            if results:
                logger.info("Wikipedia 成功获取 %s 的 %d 个地标", country_name, len(results))
                return results
    except Exception as exc:
        logger.warning("Wikipedia 地标搜索失败 (%s): %s", country_name, exc)

    return None


def _build_attractions(country_key: str, raw_landmarks: list[dict]) -> list[dict]:
    attractions: list[dict] = []
    for i, lm in enumerate(raw_landmarks):
        existing_image = str(lm.get("image", "")).strip()
        attractions.append({
            "id": _build_landmark_id(country_key, i + 1),
            "name": str(lm.get("name", "")).strip(),
            "nameEn": str(lm.get("nameEn", lm.get("name", ""))).strip(),
            "location": str(lm.get("location", "")).strip(),
            "description": str(lm.get("description", "")).strip(),
            "image": existing_image,
            "images": [existing_image] if existing_image else [],
            "type": lm.get("type") in ("landmark", "nature", "event") and lm["type"] or "landmark",
            "guideTopic": f"{lm.get('name', '')}旅游攻略".strip(),
            "guideCategory": lm.get("guideCategory") in ("history", "nature", "city", "roadtrip")
            and lm["guideCategory"] or "city",
        })
    return attractions


async def crawl_landmarks_for_country(
    country_name: str,
    *,
    country_iso: str | None = None,
    country_flag: str = "🌍",
) -> list[dict] | None:
    """
    联网爬取某国的标志性旅游地标。
    返回地标列表（与 WORLD_LANDMARKS 格式一致），失败返回 None。

    降级链：网络搜索+LLM → LLM 无搜索 → Wikipedia API → 失败
    """
    key = country_name.strip().lower()
    if not key or len(key) < 2:
        return None

    # 并发锁：同一国家只爬一次
    lock = await _get_crawl_lock(key)
    async with lock:
        # Double-check: 可能上一个请求已入库
        cached = _check_db_cache(key)
        if cached:
            return cached

        logger.info("开始爬取 %s 的地标数据...", country_name)

        raw = None
        source = "llm"

        # 1. 尝试网络搜索 + LLM 提取
        if is_web_search_configured():
            search_results = await _search_for_landmarks(country_name)
            if search_results:
                raw = await _llm_extract_landmarks(country_name, search_results)
                if not raw:
                    raw = _parse_landmarks_from_snippets(country_name, search_results)
                    source = "web_search"
            else:
                logger.warning("未搜索到 %s 的地标信息", country_name)
        else:
            logger.info("联网搜索未配置，尝试用 LLM 内置知识生成: %s", country_name)

        # 2. LLM 直接生成（无网络搜索时的降级方案）
        if not raw and is_llm_configured():
            raw = await _llm_generate_landmarks(country_name)
            source = "llm"

        # 3. Wikipedia 抓取（无需任何 API Key 的终极降级方案）
        if not raw:
            raw = await _wikipedia_crawl_landmarks(country_name)
            source = "wikipedia"

        if not raw:
            return None

        attractions = _build_attractions(key, raw)

        # 4. 入库
        _save_to_db(
            country_key=key,
            country_name=country_name.strip(),
            country_name_en=country_name.strip(),
            country_flag=country_flag,
            iso_code=country_iso,
            attractions=attractions,
            source=source,
        )

    return attractions
