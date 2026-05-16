from pydantic import BaseModel, Field
from typing import Optional


class RecommendAgentRequest(BaseModel):
    departureCity: str = Field(..., min_length=2, max_length=50, description="出发地城市")
    travelStyles: list[str] = Field(default_factory=list, description="旅行类型标签")
    budgetMin: float = Field(0, ge=0)
    budgetMax: float = Field(10000, ge=0)
    days: int = Field(5, ge=1, le=90)
    customPrompt: Optional[str] = Field(None, max_length=500, description="自定义需求描述")
    limit: int = Field(3, ge=1, le=3)
