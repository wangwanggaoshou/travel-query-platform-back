"""Async fetch from zh.wikipedia.org / zh.wikivoyage.org (MediaWiki Action API)."""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import quote

import httpx

WIKI_UA = "GcsjTravelBackend/1.0 (course project; Python httpx; contact: local-dev)"


def _wiki_api_url(host: str, titles: str, extra_params: str = "") -> str:
    t = quote(titles, safe="")
    base = f"https://{host}/w/api.php?action=query&format=json&redirects=1&titles={t}"
    return base + extra_params


async def _get_json(client: httpx.AsyncClient, url: str) -> Optional[dict[str, Any]]:
    try:
        r = await client.get(url, headers={"User-Agent": WIKI_UA}, timeout=30.0)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[mediawiki] request failed: {url[:120]}… err={e}")
        return None


def _first_page(payload: Optional[dict]) -> Optional[dict[str, Any]]:
    if not payload:
        return None
    pages = payload.get("query", {}).get("pages", {})
    if not pages:
        return None
    return next(iter(pages.values()))


async def fetch_zh_wikipedia(
    client: httpx.AsyncClient, title: str
) -> Optional[dict[str, Any]]:
    """Returns resolved title, plain-text intro extract, optional thumbnail URL, coordinates."""
    url = _wiki_api_url(
        "zh.wikipedia.org",
        title,
        "&prop=extracts|pageimages|coordinates&exintro=1&explaintext=1"
        "&piprop=thumbnail&pithumbsize=960",
    )
    data = await _get_json(client, url)
    page = _first_page(data)
    if not page or page.get("missing") or int(page.get("ns", -1)) < 0:
        return None
    extract = (page.get("extract") or "").strip()
    if not extract:
        return None
    thumb = page.get("thumbnail") or {}
    thumb_url = thumb.get("source")
    coords = page.get("coordinates") or []
    lat = lon = None
    if coords:
        lat = coords[0].get("lat")
        lon = coords[0].get("lon")
    return {
        "title": page.get("title") or title,
        "extract": extract,
        "thumbnail": thumb_url,
        "latitude": lat,
        "longitude": lon,
        "source_url": f"https://zh.wikipedia.org/wiki/{quote(page.get('title') or title, safe='')}",
    }


async def fetch_zh_wikivoyage(
    client: httpx.AsyncClient, title: str
) -> Optional[dict[str, Any]]:
    url = _wiki_api_url(
        "zh.wikivoyage.org",
        title,
        "&prop=extracts|pageimages&exintro=1&explaintext=1"
        "&piprop=thumbnail&pithumbsize=960",
    )
    data = await _get_json(client, url)
    page = _first_page(data)
    if not page or page.get("missing") or int(page.get("ns", -1)) < 0:
        return None
    extract = (page.get("extract") or "").strip()
    if not extract:
        return None
    thumb = page.get("thumbnail") or {}
    thumb_url = thumb.get("source")
    return {
        "title": page.get("title") or title,
        "extract": extract,
        "thumbnail": thumb_url,
        "source_url": f"https://zh.wikivoyage.org/wiki/{quote(page.get('title') or title, safe='')}",
    }


def title_candidates(name: str, location: Optional[str] = None) -> list[str]:
    """Try several lemma titles common for scenic spots in zh wiki."""
    seen: set[str] = set()
    out: list[str] = []
    name = (name or "").strip()
    if name and name not in seen:
        seen.add(name)
        out.append(name)
    if name:
        for suffix in ("风景区", "国家森林公园", "景区", "公园", "博物馆", "遗址"):
            if name.endswith(suffix) and len(name) > len(suffix) + 1:
                short = name[: -len(suffix)].strip()
                if short and short not in seen:
                    seen.add(short)
                    out.append(short)
    loc = (location or "").strip()
    if loc:
        for token in ("省", "市", "州", "县", "区"):
            if token in loc:
                part = loc.split(token)[0] + token
                if len(part) >= 2 and part not in seen:
                    seen.add(part)
                    out.append(part)
                break
        if loc not in seen and len(loc) >= 2:
            out.append(loc)
    return out[:8]


async def resolve_wikipedia(client: httpx.AsyncClient, name: str, location: Optional[str]) -> Optional[dict]:
    for t in title_candidates(name, location):
        got = await fetch_zh_wikipedia(client, t)
        if got:
            return got
    return None


async def resolve_wikivoyage(client: httpx.AsyncClient, name: str, location: Optional[str]) -> Optional[dict]:
    for t in title_candidates(name, location):
        got = await fetch_zh_wikivoyage(client, t)
        if got:
            return got
    return None


async def fetch_en_wikipedia(
    client: httpx.AsyncClient, title: str
) -> Optional[dict[str, Any]]:
    """English Wikipedia — better coverage for international landmarks."""
    url = _wiki_api_url(
        "en.wikipedia.org",
        title,
        "&prop=extracts|pageimages&exintro=1&explaintext=1"
        "&piprop=thumbnail&pithumbsize=960",
    )
    data = await _get_json(client, url)
    page = _first_page(data)
    if not page or page.get("missing") or int(page.get("ns", -1)) < 0:
        return None
    extract = (page.get("extract") or "").strip()
    if not extract:
        return None
    thumb = page.get("thumbnail") or {}
    thumb_url = thumb.get("source")
    return {
        "title": page.get("title") or title,
        "extract": extract,
        "thumbnail": thumb_url,
        "source_url": f"https://en.wikipedia.org/wiki/{quote(page.get('title') or title, safe='')}",
    }


def landmark_title_candidates(
    name: Optional[str],
    name_en: Optional[str] = None,
    image_query: Optional[str] = None,
    location: Optional[str] = None,
) -> list[str]:
    """Titles to try on Wikipedia (zh then en), most specific first."""
    seen: set[str] = set()
    out: list[str] = []

    def add(t: str) -> None:
        t = (t or "").strip()
        if len(t) < 2 or t in seen:
            return
        seen.add(t)
        out.append(t)

    for raw in (image_query, name_en, name):
        add(raw or "")
    if name:
        short = name.split("（")[0].split("(")[0].strip()
        add(short)
    return out[:5]


async def resolve_landmark_wikipedia(
    client: httpx.AsyncClient,
    name: Optional[str],
    *,
    name_en: Optional[str] = None,
    image_query: Optional[str] = None,
    location: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    import asyncio

    titles = landmark_title_candidates(name, name_en, image_query, location)
    en_titles = [t for t in titles if not _has_cjk(t)][:2]
    zh_titles = [t for t in titles if _has_cjk(t)][:2]

    for title in en_titles:
        got = await fetch_en_wikipedia(client, title)
        if got and got.get("thumbnail"):
            return got
        await asyncio.sleep(0.4)

    for title in zh_titles:
        got = await fetch_zh_wikipedia(client, title)
        if got and got.get("thumbnail"):
            return got
        await asyncio.sleep(0.4)

    return None


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)
