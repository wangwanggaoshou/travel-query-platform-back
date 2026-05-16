from fastapi import APIRouter, Query

from app.services.globe_service import GlobeService

router = APIRouter(prefix="/globe", tags=["3D地球"])


@router.get("/countries")
def list_globe_countries():
    return GlobeService.list_countries()


@router.get("/resolve")
async def resolve_country(
    lon: float = Query(..., ge=-180, le=180),
    lat: float = Query(..., ge=-90, le=90),
):
    key = await GlobeService.resolve_country_from_coords(lon, lat)
    if not key:
        return {"code": 404, "message": "暂未收录该国家/地区的标志性目的地", "data": None}
    return await GlobeService.get_landmarks(key)


@router.get("/landmarks/{country_key}")
async def get_country_landmarks(country_key: str):
    return await GlobeService.get_landmarks(country_key)


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
