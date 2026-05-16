"""高德地图 Web 服务 API：地点搜索、POI 详情（国内景点）。"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

AMAP_BASE = "https://restapi.amap.com/v3"
# 风景名胜 / 公园广场 / 自然风光 / 文物古迹相关
SCENIC_TYPES = "110000|110100|110200|110300|140000|140100|140200"
# 无高德 POI 类型信息时使用
CATEGORY_NONE = "none"


def _parse_location(loc: str | None) -> tuple[Optional[float], Optional[float]]:
    if not loc or "," not in loc:
        return None, None
    parts = loc.split(",", 1)
    try:
        lng, lat = float(parts[0]), float(parts[1])
        return lat, lng
    except (TypeError, ValueError):
        return None, None


def amap_type_to_category(typecode: str | None, type_name: str | None = None) -> str:
    code = (typecode or "").strip()[:6]
    name = (type_name or "").strip()
    if not code and not name:
        return CATEGORY_NONE
    if code.startswith("140") or any(x in name for x in ("博物馆", "遗址", "文物", "纪念馆")):
        return "history"
    if any(x in name for x in ("乐园", "迪士尼", "欢乐谷", "游乐")):
        return "theme_park"
    if any(x in name for x in ("海滨", "海滩", "沙滩", "海湾")):
        return "beach"
    if any(x in name for x in ("山", "峰", "岳", "岭", "雪山")):
        return "mountain"
    if code.startswith("1102") or any(x in name for x in ("自然", "森林", "湿地", "瀑布", "湖")):
        return "nature"
    if any(x in name for x in ("广场", "街区", "古镇", "步行街")):
        return "city"
    return "nature"


class AmapClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=35.0, follow_redirects=True)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not self.api_key:
            return None
        q = {**params, "key": self.api_key, "output": "json"}
        url = f"{AMAP_BASE}/{path}?{urlencode(q)}"
        try:
            r = await self._client.get(url)
            r.raise_for_status()
            data = r.json()
            if str(data.get("status")) != "1":
                print(f"[amap] API error: {data.get('info')} ({path})")
                return None
            return data
        except Exception as e:
            print(f"[amap] request failed {path}: {e}")
            return None

    async def text_search(
        self,
        keywords: str,
        *,
        city: str | None = None,
        city_limit: bool = True,
        types: str = SCENIC_TYPES,
        page: int = 1,
        offset: int = 20,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "keywords": keywords,
            "types": types,
            "page": page,
            "offset": min(offset, 25),
            "extensions": "all",
        }
        if city:
            params["city"] = city
            if city_limit:
                params["citylimit"] = "true"
        data = await self._get("place/text", params)
        if not data:
            return []
        return list(data.get("pois") or [])

    async def place_detail(self, poi_id: str) -> Optional[dict[str, Any]]:
        data = await self._get("place/detail", {"id": poi_id, "extensions": "all"})
        if not data:
            return None
        pois = data.get("pois") or []
        return pois[0] if pois else None

    @staticmethod
    def pick_best_poi(pois: list[dict[str, Any]], keyword: str) -> Optional[dict[str, Any]]:
        if not pois:
            return None
        kw = keyword.strip()
        kw_norm = re.sub(r"\s+", "", kw)

        def score(p: dict[str, Any]) -> int:
            name = (p.get("name") or "").strip()
            name_norm = re.sub(r"\s+", "", name)
            s = 0
            if name == kw or name_norm == kw_norm:
                s += 100
            elif kw in name or kw_norm in name_norm:
                s += 60
            elif name in kw or name_norm in kw_norm:
                s += 40
            type_name = p.get("type") or ""
            if any(x in type_name for x in ("风景", "旅游", "公园", "景区", "博物", "古迹")):
                s += 20
            return s

        return max(pois, key=score)

    async def resolve_scenic(
        self, keyword: str, city: str | None = None
    ) -> Optional[dict[str, Any]]:
        """解析关键词为最佳 POI，并拉取详情（含图片）。"""
        pois = await self.text_search(keyword, city=city, offset=15)
        if not pois and city:
            pois = await self.text_search(keyword, city=None, city_limit=False, offset=15)
        poi = self.pick_best_poi(pois, keyword)
        if not poi:
            return None

        detail = await self.place_detail(poi.get("id") or "")
        merged = {**poi, **(detail or {})}
        return self.normalize_poi(merged)

    @staticmethod
    def normalize_poi(poi: dict[str, Any]) -> dict[str, Any]:
        lat, lng = _parse_location(poi.get("location"))
        photos: list[str] = []
        for ph in poi.get("photos") or []:
            url = (ph.get("url") if isinstance(ph, dict) else None) or (
                ph if isinstance(ph, str) else None
            )
            if url and url not in photos:
                photos.append(url)

        cityname = poi.get("cityname") or ""
        adname = poi.get("adname") or ""
        address = poi.get("address") or ""
        location_text = f"{cityname}{adname}".strip() or address

        return {
            "id": poi.get("id"),
            "name": (poi.get("name") or "").strip(),
            "type": poi.get("type"),
            "typecode": poi.get("typecode"),
            "address": address,
            "location_text": location_text,
            "cityname": cityname,
            "adname": adname,
            "tel": poi.get("tel"),
            "latitude": lat,
            "longitude": lng,
            "photos": photos,
            "image": photos[0] if photos else None,
            "source_url": f"https://www.amap.com/place/{poi.get('id')}" if poi.get("id") else None,
        }


async def resolve_amap_scenic(
    keyword: str, city: str | None = None, *, api_key: str | None = None
) -> Optional[dict[str, Any]]:
    from app.config import settings

    key = api_key or settings.AMAP_KEY
    if not key:
        return None
    client = AmapClient(key)
    try:
        return await client.resolve_scenic(keyword, city)
    finally:
        await client.close()


def extract_city_from_location(location: str | None) -> Optional[str]:
    """从「湖南省张家界市」类字符串提取城市名供高德 city 参数使用。"""
    if not location:
        return None
    m = re.search(r"([\u4e00-\u9fa5]{2,8}市)", location)
    if m:
        return m.group(1)
    m = re.search(r"([\u4e00-\u9fa5]{2,8}州)", location)
    if m:
        return m.group(1)
    for direct in ("北京", "上海", "天津", "重庆"):
        if direct in location:
            return f"{direct}市"
    return None
