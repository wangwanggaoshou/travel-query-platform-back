from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import json
from typing import Optional

from app.database import get_db
from app.schemas.guide import GuideGenerateRequest
from app.services.guide_service import GuideService

router = APIRouter(prefix="/guide", tags=["攻略"])


@router.get("/agent/status")
def get_guide_agent_status():
    return GuideService.agent_status()


@router.post("/generate")
async def generate_guide(body: GuideGenerateRequest, db: Session = Depends(get_db)):
    return await GuideService.generate(
        db,
        body.topic,
        scenic_id=body.scenicId,
        scenic_name=body.scenicName,
        location=body.location,
        category=body.category,
        cover_image=body.coverImage,
    )


@router.get("/generate/stream")
async def generate_guide_stream(
    topic: str = Query(...),
    scenicId: Optional[int] = Query(None),
    scenicName: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    coverImage: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    async def event_generator():
        async for chunk in GuideService.generate_stream(
            db,
            topic,
            scenic_id=scenicId,
            scenic_name=scenicName,
            location=location,
            category=category,
            cover_image=coverImage,
        ):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
