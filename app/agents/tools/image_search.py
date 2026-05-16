"""根据用户提示词（主题）检索攻略封面图 URL；支持维基、Google（Serper/CSE）、Tavily。"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.config import settings
from crawler.mediawiki import resolve_wikipedia

logger = logging.getLogger(__name__)

_IMAGE_EXT = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)", re.I)
_SKIP_TAGS = frozenset({
    "ai生成", "智能攻略", "budget", "自由行攻略", "攻略", "旅游",
})


def _is_valid_image_url(url: str) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    if len(url) > 480:
        return False
    lower = url.lower()
    if any(b in lower for b in ("logo", "icon", "avatar", "sprite", "1x1")):
        return False
    host = (urlparse(url).netloc or "").lower()
    if host.endswith("gstatic.com"):
        return False
    path = (urlparse(url).path or "").lower()
    if _IMAGE_EXT.search(path) or _IMAGE_EXT.search(url):
        return True
    if "image" in path or "photo" in path or "thumb" in path:
        return True
    return False


def _pick_best(candidates: list[str]) -> Optional[str]:
    seen: set[str] = set()
    for url in candidates:
        url = (url or "").strip()
        if url in seen:
            continue
        seen.add(url)
        if _is_valid_image_url(url):
            return url
    return None


def _google_serper_api_key() -> str:
    """Serper.dev 提供的 Google 图片/网页搜索 API Key。"""
    dedicated = (settings.GUIDE_AGENT_GOOGLE_API_KEY or "").strip()
    if dedicated:
        return dedicated
    provider = (settings.GUIDE_AGENT_WEB_SEARCH_PROVIDER or "tavily").lower()
    if provider in ("google", "serper"):
        return (settings.GUIDE_AGENT_WEB_SEARCH_API_KEY or "").strip()
    return ""


def _is_google_cse_configured() -> bool:
    return bool((settings.GOOGLE_CSE_API_KEY or "").strip() and (settings.GOOGLE_CSE_CX or "").strip())


def _normalize_tag(tag: str) -> bool:
    t = str(tag).strip()
    if len(t) < 2:
        return False
    return t.lower() not in _SKIP_TAGS


def _queries_from_user_prompt(topic: str, tags: Optional[list[str]] = None) -> list[str]:
    seen: set[str] = set()
    queries: list[str] = []

    def add(q: str) -> None:
        q = (q or "").strip()
        if len(q) < 2 or q in seen:
            return
        seen.add(q)
        queries.append(q)

    add(topic)

    if tags:
        for t in tags:
            if _normalize_tag(t):
                add(str(t))

    for q in list(queries):
        for suffix in ("三日游", "五日游", "深度体验", "高性价比", "游览", "自由行", "旅游攻略", "攻略", "旅游"):
            if q.endswith(suffix) and len(q) > len(suffix) + 1:
                add(q[: -len(suffix)])
        if q.endswith("市") and len(q) > 2:
            add(q[:-1])

    return queries


async def _search_serper_images(
    query: str,
    max_results: int = 10,
    *,
    api_key: Optional[str] = None,
) -> list[str]:
    key = (api_key or _google_serper_api_key() or "").strip()
    if not key:
        return []

    url = "https://google.serper.dev/images"
    headers = {
        "X-API-KEY": key,
        "Content-Type": "application/json",
    }
    payload = {"q": query, "num": max_results}
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    urls: list[str] = []
    for item in data.get("images") or []:
        for field in ("imageUrl", "thumbnailUrl", "url"):
            u = item.get(field)
            if u:
                urls.append(u)
    return urls


async def _search_google_cse_images(query: str, max_results: int = 10) -> list[str]:
    """Google Custom Search JSON API — searchType=image。"""
    api_key = (settings.GOOGLE_CSE_API_KEY or "").strip()
    cx = (settings.GOOGLE_CSE_CX or "").strip()
    if not api_key or not cx:
        return []

    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "searchType": "image",
        "num": min(max_results, 10),
        "safe": "active",
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    urls: list[str] = []
    for item in data.get("items") or []:
        link = item.get("link")
        if link:
            urls.append(link)
        img = item.get("image") or {}
        thumb = img.get("thumbnailLink") or img.get("contextLink")
        if thumb:
            urls.append(thumb)
    return urls


async def _search_tavily_images(query: str, max_results: int = 8) -> list[str]:
    if not settings.GUIDE_AGENT_WEB_SEARCH_API_KEY:
        return []
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": settings.GUIDE_AGENT_WEB_SEARCH_API_KEY,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_images": True,
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

    urls: list[str] = []
    for img in data.get("images") or []:
        if isinstance(img, str):
            urls.append(img)
        elif isinstance(img, dict):
            u = img.get("url") or img.get("src")
            if u:
                urls.append(u)
    return urls


async def search_web_images_multi(query: str, max_results: int = 10) -> list[str]:
    """
    多源图片爬取：Google（Serper）→ Google CSE → Tavily（按配置依次尝试并去重）。
    """
    seen: set[str] = set()
    collected: list[str] = []

    def extend(urls: list[str], source: str) -> None:
        for u in urls:
            u = (u or "").strip()
            if u in seen:
                continue
            if _is_valid_image_url(u):
                seen.add(u)
                collected.append(u)
                if len(collected) >= max_results:
                    return

    # 1. Google 图片（Serper / google.serper.dev）
    serper_key = _google_serper_api_key()
    if serper_key:
        try:
            extend(
                await _search_serper_images(query, max_results, api_key=serper_key),
                "google-serper",
            )
        except Exception as exc:
            logger.warning("Google(Serper) 图片搜索失败 (%s): %s", query, exc)

    if len(collected) >= max_results:
        return collected[:max_results]

    # 2. Google 官方自定义搜索图片 API
    if _is_google_cse_configured():
        try:
            extend(await _search_google_cse_images(query, max_results), "google-cse")
        except Exception as exc:
            logger.warning("Google(CSE) 图片搜索失败 (%s): %s", query, exc)

    if len(collected) >= max_results:
        return collected[:max_results]

    # 3. Tavily（或其它已配置的主搜索）
    provider = (settings.GUIDE_AGENT_WEB_SEARCH_PROVIDER or "tavily").lower()
    if provider == "tavily" and settings.GUIDE_AGENT_WEB_SEARCH_API_KEY:
        try:
            extend(await _search_tavily_images(query, max_results), "tavily")
        except Exception as exc:
            logger.warning("Tavily 图片搜索失败 (%s): %s", query, exc)

    return collected[:max_results]


async def _search_web_images(keyword: str) -> list[str]:
    """兼容旧调用：走多源图片搜索。"""
    if not _google_serper_api_key() and not _is_google_cse_configured() and not settings.GUIDE_AGENT_WEB_SEARCH_API_KEY:
        return []
    query = f"{keyword} 旅游 风景 高清"
    return await search_web_images_multi(query, max_results=10)


async def _search_wikipedia_thumbnail(keyword: str) -> Optional[str]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        wiki = await resolve_wikipedia(client, keyword, None)
        if wiki and wiki.get("thumbnail"):
            return wiki["thumbnail"]
    return None


async def find_cover_image(
    topic: str,
    *,
    tags: Optional[list[str]] = None,
    scenic_name: Optional[str] = None,
    location: Optional[str] = None,
) -> Optional[str]:
    topic = (topic or "").strip()
    if not topic and not tags and not scenic_name:
        return None

    if scenic_name:
        from app.services.landmark_images import resolve_landmark_images

        resolved = await resolve_landmark_images(
            scenic_name,
            location=location,
            image_query=scenic_name,
            max_images=4,
        )
        if resolved.get("image"):
            logger.info("封面(景点): %s -> %s", scenic_name, resolved["image"][:80])
            return resolved["image"]

    queries = _queries_from_user_prompt(topic, tags)
    for keyword in queries[:5]:
        if any("\u4e00" <= c <= "\u9fff" for c in keyword):
            search_q = f"{keyword} 风景 地标 高清"
        else:
            search_q = f"{keyword} landmark scenic photo"
        candidates = await search_web_images_multi(search_q, max_results=10)
        picked = _pick_best(candidates)
        if picked:
            logger.info("封面(联网): %s -> %s", keyword, picked[:80])
            return picked

        thumb = await _search_wikipedia_thumbnail(keyword)
        if thumb and _is_valid_image_url(thumb):
            logger.info("封面(维基): %s -> %s", keyword, thumb[:80])
            return thumb

    return None
