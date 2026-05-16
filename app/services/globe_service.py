"""3D 地球探索：国家级标志性目的地"""

from __future__ import annotations

import asyncio
import copy
import logging

import httpx

from app.services.landmark_images import resolve_landmark_images
from app.data.world_landmarks import (
    COUNTRY_META,
    ISO_TO_COUNTRY,
    MAX_LANDMARKS_PER_COUNTRY,
    NAME_TO_COUNTRY,
    WORLD_LANDMARKS,
)
from app.utils.response import error, success

logger = logging.getLogger(__name__)


def resolve_country_key(*, country_key: str | None = None, iso_code: str | None = None, country_name: str | None = None) -> str | None:
    if country_key and country_key in WORLD_LANDMARKS:
        return country_key
    if iso_code:
        key = ISO_TO_COUNTRY.get(iso_code.lower().strip())
        if key:
            return key
    if country_name:
        key = NAME_TO_COUNTRY.get(country_name.lower().strip())
        if key:
            return key
    return None


async def _enrich_landmark(item: dict) -> dict:
    """用维基 / 联网搜索解析真实配图，忽略易失效的静态外链。"""
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
    async def resolve_country_from_coords(longitude: float, latitude: float) -> str | None:
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
            return None

        address = data.get("address") or {}
        iso = (address.get("country_code") or "").lower()
        if iso:
            key = ISO_TO_COUNTRY.get(iso)
            if key:
                return key
        country_name = address.get("country") or data.get("name")
        if country_name:
            return resolve_country_key(country_name=country_name)
        return None

    @staticmethod
    def list_countries() -> dict:
        countries = []
        for key, meta in COUNTRY_META.items():
            if key in WORLD_LANDMARKS:
                countries.append({"key": key, **meta, "landmarkCount": len(WORLD_LANDMARKS[key])})
        return success(countries)

    @staticmethod
    async def get_landmarks(country_key: str) -> dict:
        key = resolve_country_key(country_key=country_key)
        if not key:
            return error(404, "暂未收录该国家的标志性目的地，敬请期待")
        meta = COUNTRY_META.get(key, {})
        raw = WORLD_LANDMARKS.get(key, [])[:MAX_LANDMARKS_PER_COUNTRY]
        enriched = []
        for item in raw:
            enriched.append(await _enrich_landmark(item))
        attractions = [_normalize_landmark(item) for item in enriched]
        return success(
            {
                "key": key,
                "name": meta.get("name", key),
                "nameEn": meta.get("nameEn", key),
                "flag": meta.get("flag", "🌍"),
                "attractions": attractions,
            }
        )

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
