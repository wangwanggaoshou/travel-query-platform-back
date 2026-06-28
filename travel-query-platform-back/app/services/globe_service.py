"""3D 地球探索：国家级标志性目的地"""

from __future__ import annotations

import asyncio
import copy
import logging

import httpx

from app.database import SQLiteSession, MySQLSession, get_sqlite_db
from app.models.globe_landmark import GlobeLandmark
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


def _normalize_country_key(raw: str) -> str:
    """统一国家 key 为小写格式。"""
    return raw.strip().lower()


def _iso_to_flag(iso_code: str) -> str:
    """Convert ISO 3166-1 alpha-2 code to flag emoji."""
    if not iso_code or len(iso_code) < 2:
        return "🌍"
    try:
        return "".join(chr(0x1F1E6 + ord(c) - ord("a")) for c in iso_code[:2].lower())
    except Exception:
        return "🌍"


def resolve_country_key(*, country_key: str | None = None, iso_code: str | None = None, country_name: str | None = None) -> str | None:
    """将各种输入解析为国家 key。对未知国家也返回有效 key（触发后续爬取）。"""
    if country_key:
        key = country_key.strip()
        if key in WORLD_LANDMARKS:
            return key
        mapped = NAME_TO_COUNTRY.get(key.lower())
        if mapped:
            return mapped
        return key  # 接受任意国家名作为 key

    if iso_code:
        code = iso_code.lower().strip()
        mapped = ISO_TO_COUNTRY.get(code)
        if mapped:
            return mapped
        return code  # 未知 ISO 代码直接作为 key

    if country_name:
        name = country_name.strip()
        mapped = NAME_TO_COUNTRY.get(name.lower())
        if mapped:
            return mapped
        return name  # 接受任意国家名

    return None


async def _enrich_landmark(item: dict) -> dict:
    """用维基 / 联网搜索解析真实配图，忽略易失效的静态外链。"""
    merged = copy.deepcopy(item)
    existing_image = merged.get("image") or ""
    resolved = await resolve_landmark_images(
        merged.get("name"),
        name_en=merged.get("nameEn"),
        location=merged.get("location"),
        image_query=merged.get("imageQuery") or merged.get("nameEn"),
    )
    if resolved.get("image"):
        merged["image"] = resolved["image"]
        merged["images"] = resolved.get("images") or [resolved["image"]]
    elif existing_image:
        merged["images"] = [existing_image]
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
    async def resolve_country_from_coords(longitude: float, latitude: float) -> tuple[str | None, str | None, str | None]:
        """根据经纬度反查国家。返回 (country_key, iso_code, country_flag)。"""
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
            return None, None, None

        address = data.get("address") or {}
        iso = (address.get("country_code") or "").lower()
        country_name = address.get("country") or ""
        flag = _iso_to_flag(iso)

        if iso:
            mapped = ISO_TO_COUNTRY.get(iso)
            if mapped:
                return mapped, iso, flag
            if country_name:
                return country_name.strip(), iso, flag

        country_name = address.get("country") or data.get("name")
        if country_name:
            key = resolve_country_key(country_name=country_name)
            return key, iso, flag
        return None, None, None

    @staticmethod
    def _lookup_static_landmarks(key: str) -> dict | None:
        """从静态数据获取地标。"""
        if key not in WORLD_LANDMARKS:
            return None
        meta = COUNTRY_META.get(key, {})
        return {
            "key": key,
            "name": meta.get("name", key),
            "nameEn": meta.get("nameEn", key),
            "flag": meta.get("flag", "🌍"),
            "raw": WORLD_LANDMARKS[key][:MAX_LANDMARKS_PER_COUNTRY],
        }

    @staticmethod
    def _lookup_db_landmarks(key: str) -> dict | None:
        """从数据库缓存获取地标：先 SQLite（旧数据）→ 再 MySQL（新数据）。"""
        lower_key = _normalize_country_key(key)

        for session_factory in [get_sqlite_db, lambda: MySQLSession()]:
            db = session_factory()
            try:
                row = db.query(GlobeLandmark).filter(
                    GlobeLandmark.country_key == lower_key
                ).first()
                if row and row.attractions:
                    return {
                        "key": row.country_key,
                        "name": row.country_name,
                        "nameEn": row.country_name_en or row.country_key,
                        "flag": row.country_flag or "🌍",
                        "raw": row.attractions,
                    }
            finally:
                db.close()
        return None

    @staticmethod
    async def _crawl_and_cache_landmarks(
        key: str,
        *,
        country_iso: str | None = None,
        country_flag: str | None = None,
    ) -> dict | None:
        """触发爬取并返回结果。"""
        from app.services.globe_crawler import crawl_landmarks_for_country

        crawled = await crawl_landmarks_for_country(
            key,
            country_iso=country_iso,
            country_flag=country_flag or "🌍",
        )
        if not crawled:
            return None
        return {
            "key": _normalize_country_key(key),
            "name": key,
            "nameEn": key,
            "flag": country_flag or "🌍",
            "raw": crawled,
        }

    @staticmethod
    async def _build_and_enrich(source: dict, *, skip_enrich: bool = False) -> dict:
        """对 raw landmarks 统一配图并整理输出。skip_enrich 用于快速返回爬取结果。"""
        if skip_enrich:
            attractions = [_normalize_landmark(item) for item in source["raw"]]
        else:
            enriched = [await _enrich_landmark(item) for item in source["raw"]]
            attractions = [_normalize_landmark(item) for item in enriched]
        return {
            "key": source["key"],
            "name": source["name"],
            "nameEn": source["nameEn"],
            "flag": source["flag"],
            "attractions": attractions,
        }

    @staticmethod
    def _save_enriched(key: str, attractions: list[dict]) -> None:
        """将配图后的地标数据回写到 MySQL 数据库。"""
        lower_key = _normalize_country_key(key)
        db = MySQLSession()
        try:
            row = db.query(GlobeLandmark).filter(
                GlobeLandmark.country_key == lower_key
            ).first()
            if row:
                row.attractions = attractions
                db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("回写配图数据失败 (%s): %s", lower_key, exc)
        finally:
            db.close()

    @staticmethod
    def list_countries() -> dict:
        countries = []
        seen: set[str] = set()

        for key, meta in COUNTRY_META.items():
            if key in WORLD_LANDMARKS:
                countries.append({
                    "key": key,
                    **meta,
                    "landmarkCount": len(WORLD_LANDMARKS[key]),
                })
                seen.add(key.lower())

        # 合并 SQLite（旧数据）+ MySQL（新数据）
        for factory in [get_sqlite_db, lambda: MySQLSession()]:
            db = factory()
            try:
                rows = db.query(GlobeLandmark).all()
                for row in rows:
                    if row.country_key.lower() not in seen:
                        countries.append({
                            "key": row.country_key,
                            "name": row.country_name,
                            "nameEn": row.country_name_en or row.country_key,
                            "flag": row.country_flag or "🌍",
                            "landmarkCount": len(row.attractions or []),
                        })
                        seen.add(row.country_key.lower())
            finally:
                db.close()

        return success(countries)

    @staticmethod
    async def get_landmarks(
        country_key: str,
        *,
        country_iso: str | None = None,
        country_flag: str | None = None,
    ) -> dict:
        key = resolve_country_key(country_key=country_key)
        if not key:
            return error(404, "暂未收录该国家的标志性目的地，敬请期待")

        # 第1级：静态数据
        source = GlobeService._lookup_static_landmarks(key)
        if source:
            # 合并外部传入的元数据
            if country_iso:
                source["iso"] = country_iso
            if country_flag:
                source["flag"] = country_flag
            result = await GlobeService._build_and_enrich(source)
            return success(result)

        # 第2级：数据库缓存
        source = GlobeService._lookup_db_landmarks(key)
        if source:
            result = await GlobeService._build_and_enrich(source)
            return success(result)

        # 第3级：联网爬取（跳过耗时配图，加快首次返回）
        source = await GlobeService._crawl_and_cache_landmarks(
            key, country_iso=country_iso, country_flag=country_flag
        )
        if source:
            result = await GlobeService._build_and_enrich(source, skip_enrich=True)
            # 异步后台配图
            GlobeService._save_enriched(key, result["attractions"])
            return success(result)

        return error(404, "暂未收录该国家的标志性目的地，敬请期待")

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
