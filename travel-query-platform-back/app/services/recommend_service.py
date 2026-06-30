from sqlalchemy.orm import Session
from typing import Optional

from app.agents.config import is_web_search_configured
from app.agents.recommend_agent import RecommendAgent
from app.models.scenic import Scenic
from app.models.guide import Guide
from app.utils.response import success, error


class RecommendService:
    @staticmethod
    def agent_status() -> dict:
        return success({
            "configured": RecommendAgent.is_ready(),
            "webSearchConfigured": is_web_search_configured(),
        })

    @staticmethod
    async def agent_recommend(
        db: Session,
        *,
        departure_city: str,
        travel_styles: list[str],
        budget_min: float,
        budget_max: float,
        days: int,
        custom_prompt: Optional[str] = None,
        limit: int = 3,
    ) -> dict:
        if not RecommendAgent.is_ready():
            return error(3003, "推荐 Agent 未配置，请设置 GUIDE_AGENT_LLM_API_KEY 与 GUIDE_AGENT_LLM_BASE_URL")

        departure_city = (departure_city or "").strip()
        if len(departure_city) < 2:
            return error(400, "请填写出发地")

        if not travel_styles and not (custom_prompt or "").strip():
            return error(400, "请至少选择旅行类型或填写自定义需求")

        try:
            payload = await RecommendAgent.recommend(
                db,
                departure_city=departure_city,
                travel_styles=travel_styles,
                budget_min=budget_min,
                budget_max=budget_max,
                days=days,
                custom_prompt=custom_prompt,
                limit=limit,
            )
        except RuntimeError as exc:
            return error(3003, str(exc))
        except Exception as exc:
            return error(500, f"智能推荐失败: {exc}")

        return success(payload)

    @staticmethod
    def get_scenic_recommend(db: Session, user_id: Optional[int] = None, limit: int = 10) -> dict:
        items = db.query(Scenic).filter(
            Scenic.is_active == 1, Scenic.is_hot == 1
        ).order_by(Scenic.view_count.desc(), Scenic.id.desc()).limit(limit).all()

        scenic_list = [{
            "id": item.id,
            "name": item.name,
            "category": item.category,
            "location": item.location,
            "price": item.price,
            "image": item.image,
            "description": item.description,
            "matchReason": "热门推荐",
        } for item in items]
        return success({"list": scenic_list})

    @staticmethod
    def get_guide_recommend(db: Session, user_id: Optional[int] = None, limit: int = 10) -> dict:
        items = db.query(Guide).filter(
            Guide.is_active == 1, Guide.is_hot == 1
        ).order_by(Guide.view_count.desc()).limit(limit).all()

        guide_list = [{
            "id": item.id,
            "title": item.title,
            "cover": item.cover,
            "summary": item.summary,
            "author": item.author,
            "tags": item.tags or [],
            "date": str(item.created_at.date()) if item.created_at else None,
            "matchReason": "热门推荐"
        } for item in items]
        return success({"list": guide_list})

