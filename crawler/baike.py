"""Async fetch from baike.baidu.com — 百度百科，比 Wikipedia 快得多（国内 ~1s）。"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional
from urllib.parse import quote, unquote

import httpx
from bs4 import BeautifulSoup

BAIKE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


async def _fetch_page(client: httpx.AsyncClient, url: str) -> Optional[BeautifulSoup]:
    """获取百度百科页面，失败返回 None。"""
    try:
        r = await client.get(
            url,
            headers={
                "User-Agent": BAIKE_UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=15.0,
            follow_redirects=True,
        )
        r.raise_for_status()
        # 百度百科可能返回"百度百科错误"页面但 HTTP 200
        if "百度百科错误" in r.text and "未收录" in r.text:
            return None
        return BeautifulSoup(r.text, "html.parser")
    except Exception:
        return None


def _extract_summary(soup: BeautifulSoup) -> Optional[str]:
    """提取百科摘要（优先 meta description，其次正文首段）。"""
    # 方式1：meta description — 最可靠
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        desc = meta["content"].strip()
        # 去掉常见的"百度百科"前缀
        desc = re.sub(r'^百度百科[，。！]?\s*', '', desc)
        # 太短的不行（如"欢迎来到百度百科"）
        if len(desc) >= 30:
            return desc[:2000]

    # 方式2：正文首段
    summary_div = soup.find("div", class_="lemma-summary")
    if summary_div:
        text = summary_div.get_text("\n", strip=True)
        if len(text) >= 30:
            return text[:2000]

    return None


def _extract_image(soup: BeautifulSoup) -> Optional[str]:
    """提取百科页面的概要图。"""
    # 概要图
    pic = soup.find("div", class_="summary-pic")
    if pic:
        img = pic.find("img")
        if img and img.get("src"):
            src = img["src"]
            if not src.startswith("http"):
                src = "https:" + src
            return src

    # 正文第一张大图
    for img in soup.select(".para img, .lemma-picture img"):
        src = img.get("src") or img.get("data-src")
        if src and "baike" in src:
            if not src.startswith("http"):
                src = "https:" + src
            if "nofigure" not in src and "ico" not in src.lower():
                return src

    return None


def _extract_title(soup: BeautifulSoup) -> Optional[str]:
    """提取百科页面的词条标题。"""
    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(strip=True)
        # 百度百科标题末尾常有"（中国大陆...）"括号，去掉
        text = re.sub(r'（[^）]*）$', '', text)
        return text.strip()
    return None


def _build_url(title: str) -> str:
    """构造百度百科词条 URL。"""
    return f"https://baike.baidu.com/item/{quote(title)}"


async def fetch_baidu_baike(
    client: httpx.AsyncClient, title: str
) -> Optional[dict[str, Any]]:
    """获取百度百科词条的摘要、图片、来源链接。

    Returns:
        {"title": str, "extract": str, "thumbnail": str|None, "source_url": str} or None
    """
    soup = await _fetch_page(client, _build_url(title))
    if not soup:
        return None

    page_title = _extract_title(soup) or title
    extract = _extract_summary(soup)
    if not extract:
        return None

    thumbnail = _extract_image(soup)

    return {
        "title": page_title,
        "extract": extract,
        "thumbnail": thumbnail,
        "latitude": None,
        "longitude": None,
        "source_url": f"https://baike.baidu.com/item/{quote(page_title)}",
    }


def baike_title_candidates(name: str, location: Optional[str] = None) -> list[str]:
    """生成百度百科词条候选标题（与 Wikipedia 逻辑不同，百度百科更宽松）。"""
    seen: set[str] = set()
    out: list[str] = []

    def add(t: str) -> None:
        t = (t or "").strip()
        if len(t) >= 2 and t not in seen:
            seen.add(t)
            out.append(t)

    add(name)
    if name:
        # 去掉地点后缀（如"三亚湾路189-9号"只取"三亚湾"）
        short = name.split("路")[0].split("号")[0].strip()
        if short != name:
            add(short)
        # 去掉景区常见后缀
        for suffix in ("风景区", "景区", "公园", "博物馆", "遗址", "古城", "古镇"):
            if name.endswith(suffix) and len(name) > len(suffix) + 1:
                add(name[: -len(suffix)].strip())

    loc = (location or "").strip()
    if loc:
        # 提取城市名
        for sep in ("市", "省", "区", "县"):
            if sep in loc:
                city = loc.split(sep)[0] + sep
                if len(city) >= 3:
                    add(city)
                break
        add(loc)

    return out[:6]


async def resolve_baidu_baike(
    client: httpx.AsyncClient, name: str, location: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """按候选标题逐个尝试百度百科，返回第一个命中。"""
    for i, t in enumerate(baike_title_candidates(name, location)):
        if i > 0:
            await asyncio.sleep(0.15)  # 轻微节流
        result = await fetch_baidu_baike(client, t)
        if result:
            return result
    return None
