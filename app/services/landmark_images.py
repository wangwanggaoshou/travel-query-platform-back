"""标志性目的地配图：维基缩略图 + 联网搜图，避免失效的静态外链。"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx

from app.agents.tools.image_search import _is_valid_image_url, search_web_images_multi
from crawler.mediawiki import resolve_landmark_wikipedia

logger = logging.getLogger(__name__)

_wiki_sem = asyncio.Semaphore(1)
_image_cache: dict[str, dict[str, str | list[str]]] = {}

_GUIDE_SUFFIXES = re.compile(
    r"(旅游攻略|攻略|三日游|五日游|七日游|深度游|自由行|自驾游|周末游|游览)$"
)


def _strip_guide_noise(text: str) -> str:
    t = (text or "").strip()
    while True:
        n = _GUIDE_SUFFIXES.sub("", t).strip()
        if n == t:
            break
        t = n
    return t


async def resolve_landmark_images(
    name: Optional[str],
    *,
    name_en: Optional[str] = None,
    location: Optional[str] = None,
    image_query: Optional[str] = None,
    max_images: int = 6,
) -> dict[str, str | list[str]]:
    """
    返回 { image, images }，均为经校验可访问的 URL（优先维基 API 缩略图）。
    """
    seen: set[str] = set()
    urls: list[str] = []

    def add(url: Optional[str]) -> None:
        if not url or url in seen:
            return
        if _is_valid_image_url(url):
            seen.add(url)
            urls.append(url)

    query = (image_query or name_en or _strip_guide_noise(name or "") or "").strip()
    loc = (location or "").strip() or None
    cache_key = f"{name}|{name_en}|{query}|{loc}"
    if cache_key in _image_cache:
        return _image_cache[cache_key]

    try:
        async with _wiki_sem:
            async with httpx.AsyncClient(timeout=20.0) as client:
                wiki = await resolve_landmark_wikipedia(
                    client,
                    name,
                    name_en=name_en,
                    image_query=query,
                    location=loc,
                )
                if wiki:
                    add(wiki.get("thumbnail"))
    except Exception as exc:
        logger.warning("维基配图失败 (%s): %s", query, exc)

    search_titles: list[str] = []
    for t in (query, name_en, _strip_guide_noise(name or "")):
        t = (t or "").strip()
        if t and t not in search_titles:
            search_titles.append(t)

    if len(urls) < max_images:
        for title in search_titles[:3]:
            try:
                web_q = f"{title} landmark scenic view"
                if any("\u4e00" <= c <= "\u9fff" for c in title):
                    web_q = f"{title} 风景 地标"
                for u in await search_web_images_multi(web_q, max_images=max_images):
                    add(u)
                    if len(urls) >= max_images:
                        break
            except Exception as exc:
                logger.warning("联网配图失败 (%s): %s", title, exc)

    result = {
        "image": urls[0] if urls else "",
        "images": urls[:max_images],
    }
    _image_cache[cache_key] = result
    return result
