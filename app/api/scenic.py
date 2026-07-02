from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
import json
from sqlalchemy.orm import Session
from typing import Optional, List
from app.database import get_db
from app.schemas.recommend import RecommendAgentRequest, RecommendMoreRequest
from app.services.scenic_service import ScenicService
from app.services.recommend_service import RecommendService

router = APIRouter(prefix="/scenic", tags=["景点"])


@router.get("/list")
def get_scenic_list(
    page: int = Query(1, ge=1),
    pageSize: int = Query(10, ge=1, le=50),
    category: Optional[str] = None,
    region: Optional[str] = None,
    sortBy: Optional[str] = None,
    db: Session = Depends(get_db)
):
    return ScenicService.get_list(db, page, pageSize, category, region, sortBy)


@router.get("/detail/{scenic_id}")
def get_scenic_detail(scenic_id: int, db: Session = Depends(get_db)):
    return ScenicService.get_detail(db, scenic_id)


@router.get("/search")
def search_scenic(
    keyword: str = Query(...),
    page: int = Query(1, ge=1),
    pageSize: int = Query(10, ge=1, le=50),
    category: Optional[str] = None,
    region: Optional[str] = None,
    sortBy: Optional[str] = None,
    discover: bool = Query(False, description="无结果时尝试爬虫子系统聚合并入库"),
    city: Optional[str] = Query(None, description="高德搜索限定城市，如「杭州市」"),
    db: Session = Depends(get_db)
):
    return ScenicService.search(db, keyword, page, pageSize, category, region, sortBy, discover, city)


@router.get("/categories")
def get_scenic_categories(db: Session = Depends(get_db)):
    return ScenicService.get_categories(db)


@router.get("/enrich-images")
async def enrich_scenic_images(
    name: str = Query(..., min_length=1),
    location: str | None = Query(None),
):
    """为景点补充配图（维基 + 联网搜索），供前端图片加载失败时回退。"""
    from app.agents.tools.image_search import find_cover_image
    cover = await find_cover_image(name, scenic_name=name, location=location)
    if cover:
        return {"code": 200, "data": {"image": cover, "images": [cover]}}
    return {"code": 404, "message": "未找到配图", "data": None}


@router.get("/hot")
def get_hot_scenic(limit: int = Query(6, ge=1, le=20), db: Session = Depends(get_db)):
    return ScenicService.get_hot(db, limit)


@router.get("/recommend/agent/status")
def get_recommend_agent_status():
    return RecommendService.agent_status()


@router.post("/recommend/agent")
async def recommend_scenic_agent(
    body: RecommendAgentRequest,
    db: Session = Depends(get_db),
):
    return await RecommendService.agent_recommend(
        db,
        departure_city=body.departureCity,
        travel_styles=body.travelStyles,
        budget_min=body.budgetMin,
        budget_max=body.budgetMax,
        days=body.days,
        custom_prompt=body.customPrompt,
        limit=body.limit,
    )


@router.post("/recommend/agent/more")
async def recommend_scenic_agent_more(
    body: RecommendMoreRequest,
    db: Session = Depends(get_db),
):
    return await RecommendService.agent_recommend_more(
        db,
        departure_city=body.departureCity,
        travel_styles=body.travelStyles,
        budget_min=body.budgetMin,
        budget_max=body.budgetMax,
        days=body.days,
        custom_prompt=body.customPrompt,
        limit=body.limit,
        exclude_ids=body.excludeIds,
    )


@router.get("/recommend")
def get_recommend_scenic(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return RecommendService.get_scenic_recommend(db, None, limit)


@router.get("/recommend/agent/stream")
async def recommend_scenic_agent_stream(
    departureCity: str = Query(...),
    travelStyles: List[str] = Query(None),
    budgetMin: float = Query(0.0),
    budgetMax: float = Query(0.0),
    days: int = Query(5),
    customPrompt: Optional[str] = Query(None),
    limit: int = Query(3),
    db: Session = Depends(get_db)
):
    # If travelStyles is passed as list of query params, FastAPI handles it. 
    # But sometimes front-end might send travelStyles=A,B or multiple times.
    # Query(None) will load them as a list of strings if sent like travelStyles=A&travelStyles=B.
    # If they are comma-separated in a single string, we split it:
    styles = []
    if travelStyles:
        for s in travelStyles:
            if ',' in s:
                styles.extend(s.split(','))
            else:
                styles.append(s)
    else:
        styles = []

    async def event_generator():
        async for chunk in RecommendService.agent_recommend_stream(
            db,
            departure_city=departureCity,
            travel_styles=styles,
            budget_min=budgetMin,
            budget_max=budgetMax,
            days=days,
            custom_prompt=customPrompt,
            limit=limit,
        ):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
