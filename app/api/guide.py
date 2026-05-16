from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

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
    )
