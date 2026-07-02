"""3D 地球探索：国家级标志性目的地（AI 实时发现 + 全球地理编码）。"""

from __future__ import annotations

import asyncio
import copy
import logging

import httpx

from app.agents.globe_agent import GlobeAgent
from app.agents.tools.image_search import find_cover_image
from app.services.landmark_images import resolve_landmark_images
from app.data.world_landmarks import (
    COUNTRY_META,
    ISO_TO_COUNTRY,
    MAX_LANDMARKS_PER_COUNTRY,
    NAME_TO_COUNTRY,
)
from app.utils.response import error, success

logger = logging.getLogger(__name__)


def resolve_country_key(*, country_key: str | None = None, iso_code: str | None = None, country_name: str | None = None) -> str | None:
    if iso_code:
        key = ISO_TO_COUNTRY.get(iso_code.lower().strip())
        if key:
            return key
    if country_name:
        key = NAME_TO_COUNTRY.get(country_name.lower().strip())
        if key:
            return key
    if country_key:
        key = country_key.strip()
        if key in COUNTRY_META:
            return key
    return None


async def _enrich_landmark(item: dict) -> dict:
    """用维基 / 联网搜索解析真实配图（优先 AI 生成的关键词，回退到攻略封面级多层搜索）。"""
    merged = copy.deepcopy(item)
    resolved = await resolve_landmark_images(
        merged.get("name"),
        name_en=merged.get("nameEn"),
        location=merged.get("location"),
        image_query=merged.get("imageQuery") or merged.get("nameEn"),
    )
    if resolved.get("image"):
        merged["image"] = resolved["image"]
        merged["images"] = resolved.get("images") or [resolved["image"]]
    else:
        # 回退：复用 AI 攻略封面的多层搜索链（多关键词 + 维基）
        cover = await find_cover_image(
            merged.get("guideTopic") or merged.get("name", ""),
            scenic_name=merged.get("name"),
            location=merged.get("location"),
        )
        if cover:
            merged["image"] = cover
            merged["images"] = [cover]
        else:
            merged["image"] = ""
            merged["images"] = []
    return merged


def _normalize_landmark(item: dict) -> dict:
    return {
        **item,
        "image": item.get("image") or "",
        "images": list(item.get("images") or [])[:8],
    }


class GlobeService:
    @staticmethod
    async def resolve_country_from_coords(longitude: float, latitude: float) -> tuple[str | None, str]:
        """经纬度 → (country_key, 原始国名)。country_key 为 None 时用原始国名 AI 搜索。"""
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get(
                    "https://nominatim.openstreetmap.org/reverse",
                    params={
                        "lat": latitude,
                        "lon": longitude,
                        "format": "json",
                        "zoom": 3,
                        "addressdetails": 1,
                    },
                    headers={"User-Agent": "TravelGlobeExplorer/1.0"},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("Nominatim reverse geocode failed: %s", exc)
            return None, ""

        address = data.get("address") or {}
        iso = (address.get("country_code") or "").lower()
        raw_name = address.get("country") or data.get("name") or ""

        # 优先 ISO 映射
        if iso:
            key = ISO_TO_COUNTRY.get(iso)
            if key:
                return key, raw_name

        # 再试中文/英文国名
        if raw_name:
            key = resolve_country_key(country_name=raw_name)
            if key:
                return key, raw_name

        # 非预设国家：返回原始国名供 AI 搜索
        return None, raw_name

    @staticmethod
    def list_countries() -> dict:
        countries = []
        for key, meta in COUNTRY_META.items():
            countries.append({"key": key, **meta, "landmarkCount": 5})
        return success(countries)

    @staticmethod
    async def get_landmarks(country_key: str) -> dict:
        """获取某国景点：优先 AI 实时发现，回退到通用世界知识。"""
        key = resolve_country_key(country_key=country_key)
        if not key:
            # 非预设国家，尝试直接用国名 AI 搜索
            return await GlobeService._discover_for_any_country(country_key)

        meta = COUNTRY_META.get(key, {})
        country_name = meta.get("name", key)
        country_en = meta.get("nameEn", key)

        # 1) 尝试 AI 实时发现
        ai_attractions = await GlobeAgent.discover_country_attractions(
            country_name, country_en, limit=MAX_LANDMARKS_PER_COUNTRY
        )

        if ai_attractions:
            # 2) 用图片搜索丰富每个景点
            enriched = []
            for item in ai_attractions:
                enriched.append(await _enrich_landmark(item))
            attractions = [_normalize_landmark(item) for item in enriched]
            return success({
                "key": key,
                "name": country_name,
                "nameEn": country_en,
                "flag": meta.get("flag", "🌍"),
                "attractions": attractions,
                "source": "ai",
            })

        # 3) AI 不可用时回退到空列表（提示用户）
        logger.warning("GlobeAgent 未能为 %s 生成景点", country_name)
        return error(503, f"AI 服务不可用，无法为「{country_name}」实时发现景点，请稍后重试")

    @staticmethod
    async def _discover_for_any_country(country_name: str) -> dict:
        """对非预设国家/地区，直接用 AI 搜索。"""
        ai_attractions = await GlobeAgent.discover_country_attractions(
            country_name, "", limit=MAX_LANDMARKS_PER_COUNTRY
        )
        if ai_attractions:
            enriched = []
            for item in ai_attractions:
                enriched.append(await _enrich_landmark(item))
            attractions = [_normalize_landmark(item) for item in enriched]
            return success({
                "key": country_name.lower().replace(" ", "-"),
                "name": country_name,
                "nameEn": "",
                "flag": "🌍",
                "attractions": attractions,
                "source": "ai",
            })
        return error(503, f"AI 服务不可用，无法为「{country_name}」实时发现景点")

    @staticmethod
    async def enrich_landmark_images(
        keyword: str,
        max_images: int = 6,
        *,
        name_en: str | None = None,
        location: str | None = None,
    ) -> dict:
        keyword = (keyword or "").strip()
        if len(keyword) < 2:
            return error(400, "请提供景点名称")

        resolved = await resolve_landmark_images(
            keyword,
            name_en=name_en,
            location=location,
            image_query=name_en or keyword,
            max_images=max_images,
        )
        return success(resolved)
