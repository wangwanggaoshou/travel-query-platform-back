from sqlalchemy.orm import Session
from typing import Optional
import asyncio
import logging

from app.agents.config import is_google_images_configured, is_web_search_configured
from app.agents.guide_agent import (
    GUIDE_AGENT_AUTHOR, GuideAgent, _format_search_results,
    _build_user_prompt, _normalize_tags, SYSTEM_PROMPT
)
from app.agents.tools.image_search import find_cover_image
from app.agents.tools.web_search import web_search
from app.agents.llm import chat_completion, parse_json_from_llm
from app.models.scenic import Scenic
from app.utils.response import success, error

logger = logging.getLogger(__name__)

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
        cover_image: Optional[str] = None,
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
        cover = cover_image or await GuideService._resolve_cover(
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

    @staticmethod
    async def generate_stream(
        db: Session,
        topic: str,
        scenic_id: Optional[int] = None,
        scenic_name: Optional[str] = None,
        location: Optional[str] = None,
        category: Optional[str] = None,
        cover_image: Optional[str] = None,
    ):
        if not GuideAgent.is_ready():
            yield {"error": "攻略 Agent 未配置，请设置 GUIDE_AGENT_LLM_API_KEY 与 GUIDE_AGENT_LLM_BASE_URL"}
            return

        resolved_scenic_name = (scenic_name or "").strip() or None
        resolved_location = (location or "").strip() or None
        if scenic_id:
            scenic = db.query(Scenic).filter(Scenic.id == scenic_id).first()
            if scenic:
                resolved_scenic_name = resolved_scenic_name or scenic.name
                resolved_location = resolved_location or scenic.location

        # Step 0: Initialize
        yield {"step": 0, "progress": 10, "message": "正在准备大模型参数，开始探索..."}
        await asyncio.sleep(0.05)

        # Step 1: Web search
        search_query = f"{topic} 旅游攻略 行程 景点"
        if resolved_scenic_name:
            search_query = f"{resolved_scenic_name} {search_query}"

        search_results: list[dict] = []
        if is_web_search_configured():
            yield {"step": 0, "progress": 25, "message": "正在联网搜索目的地资料..."}
            try:
                search_results = await web_search(search_query)
            except Exception as exc:
                logger.warning("联网搜索失败: %s", exc)

        # Step 2: AI reading
        yield {"step": 1, "progress": 40, "message": f"正在阅读与提炼 {len(search_results)} 条联网参考资料..."}
        refs_text = _format_search_results(search_results)
        user_prompt = _build_user_prompt(topic, resolved_scenic_name, category, refs_text)

        # Step 3: LLM writing
        yield {"step": 3, "progress": 65, "message": "AI 正在撰写攻略内容（日程安排、游玩要点与注意事项）..."}

        try:
            raw = await chat_completion(SYSTEM_PROMPT, user_prompt)
            data = parse_json_from_llm(raw)
        except Exception:
            data = {
                "title": topic,
                "summary": raw[:120] if raw else "",
                "tags": ["AI生成"],
                "content": f"<p>{raw}</p>" if raw else "<p>生成失败</p>",
            }

        tags = _normalize_tags(data.get("tags"))
        title = (data.get("title") or topic)[:200]

        # Step 4: Cover
        yield {"step": 4, "progress": 90, "message": "正文生成完毕，正在搜索适配的精美配图..."}

        cover = cover_image
        if not cover:
            cover = data.get("cover")
            cover = await GuideService._resolve_cover(
                topic,
                tags,
                cover,
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

        final_data = {
            "title": title,
            "topic": topic,
            "cover": cover,
            "summary": (data.get("summary") or "")[:500],
            "author": data.get("author") or GUIDE_AGENT_AUTHOR,
            "tags": tags,
            "content": data.get("content") or "",
            "scenic": scenic_payload,
        }

        yield {"done": True, "progress": 100, "message": "攻略撰写完成！", "result": {"code": 200, "data": final_data}}
