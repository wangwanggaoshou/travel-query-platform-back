from sqlalchemy.orm import Session
from typing import Optional

from app.agents.config import is_google_images_configured, is_web_search_configured
from app.agents.guide_agent import GUIDE_AGENT_AUTHOR, GuideAgent
from app.agents.tools.image_search import find_cover_image
from app.models.scenic import Scenic
from app.utils.response import success, error

_SKIP_PROMPT_TAGS = frozenset({
    "ai生成", "智能攻略", "budget", "自由行攻略",
})


class GuideService:
    @staticmethod
    def agent_status() -> dict:
        return success({
            "configured": GuideAgent.is_ready(),
            "webSearchConfigured": is_web_search_configured(),
            "googleImagesConfigured": is_google_images_configured(),
        })

    @staticmethod
    async def _resolve_cover(
        topic: str,
        tags: list | None,
        existing: str | None,
        *,
        scenic_name: Optional[str] = None,
        location: Optional[str] = None,
    ) -> str | None:
        if existing:
            return existing
        try:
            return await find_cover_image(
                topic,
                tags=tags,
                scenic_name=scenic_name,
                location=location,
            )
        except Exception:
            return None

    @staticmethod
    async def generate(
        db: Session,
        topic: str,
        scenic_id: Optional[int] = None,
        scenic_name: Optional[str] = None,
        location: Optional[str] = None,
        category: Optional[str] = None,
    ) -> dict:
        if not GuideAgent.is_ready():
            return error(3002, "攻略 Agent 未配置，请设置 GUIDE_AGENT_LLM_API_KEY 与 GUIDE_AGENT_LLM_BASE_URL")

        resolved_scenic_name = (scenic_name or "").strip() or None
        resolved_location = (location or "").strip() or None
        if scenic_id:
            scenic = db.query(Scenic).filter(Scenic.id == scenic_id).first()
            if scenic:
                resolved_scenic_name = resolved_scenic_name or scenic.name
                resolved_location = resolved_location or scenic.location

        try:
            generated = await GuideAgent.generate(
                topic,
                scenic_name=resolved_scenic_name,
                location=resolved_location,
                category=category,
            )
        except ValueError as exc:
            return error(400, str(exc))
        except RuntimeError as exc:
            return error(3002, str(exc))
        except Exception as exc:
            return error(500, f"攻略生成失败: {exc}")

        tags = generated.get("tags") or ["AI生成"]
        cover = await GuideService._resolve_cover(
            topic,
            tags,
            generated.get("cover"),
            scenic_name=resolved_scenic_name,
            location=resolved_location,
        )

        scenic_payload = None
        if scenic_id or resolved_scenic_name:
            scenic = db.query(Scenic).filter(Scenic.id == scenic_id).first() if scenic_id else None
            scenic_payload = {
                "id": scenic_id,
                "name": scenic.name if scenic else resolved_scenic_name,
            }

        return success({
            "title": generated["title"],
            "topic": topic,
            "cover": cover,
            "summary": generated["summary"],
            "author": generated.get("author") or GUIDE_AGENT_AUTHOR,
            "tags": tags,
            "content": generated["content"],
            "scenic": scenic_payload,
        })
