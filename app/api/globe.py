from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
import json

from app.services.globe_service import GlobeService

router = APIRouter(prefix="/globe", tags=["3D地球"])


@router.get("/countries")
def list_globe_countries():
    return GlobeService.list_countries()


@router.get("/resolve/stream")
async def resolve_country_stream(
    lon: float = Query(..., ge=-180, le=180),
    lat: float = Query(..., ge=-90, le=90),
):
    async def event_generator():
        async for chunk in GlobeService.resolve_country_stream(lon, lat):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/resolve")
async def resolve_country(
    lon: float = Query(..., ge=-180, le=180),
    lat: float = Query(..., ge=-90, le=90),
):
    key, raw_name = await GlobeService.resolve_country_from_coords(lon, lat)
    if key:
        return await GlobeService.get_landmarks(key)
    if raw_name:
        # 非预设国家，直接用国名走 AI 发现
        return await GlobeService._discover_for_any_country(raw_name)
    return {"code": 404, "message": "无法识别该位置所属国家/地区，请尝试其他区域", "data": None}


@router.get("/landmarks/images")
async def get_landmark_images(
    keyword: str = Query(..., min_length=2),
    max: int = Query(6, ge=1, le=12, alias="max"),
    nameEn: str | None = Query(None),
    location: str | None = Query(None),
):
    return await GlobeService.enrich_landmark_images(
        keyword, max_images=max, name_en=nameEn, location=location
    )


@router.get("/landmarks/{country_key}")
async def get_country_landmarks(country_key: str):
    return await GlobeService.get_landmarks(country_key)
