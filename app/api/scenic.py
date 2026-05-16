from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.schemas.recommend import RecommendAgentRequest
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
    discover: bool = Query(False, description="无结果时尝试爬虫子系统聚合并入库"),
    city: Optional[str] = Query(None, description="高德搜索限定城市，如「杭州市」"),
    db: Session = Depends(get_db)
):
    return ScenicService.search(db, keyword, page, pageSize, category, region, discover, city)


@router.get("/categories")
def get_scenic_categories(db: Session = Depends(get_db)):
    return ScenicService.get_categories(db)


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


@router.get("/recommend")
def get_recommend_scenic(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return RecommendService.get_scenic_recommend(db, None, limit)
