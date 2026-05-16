from pydantic import BaseModel
from typing import Optional


class ScenicListQuery(BaseModel):
    page: int = 1
    pageSize: int = 10
    category: Optional[str] = None
    region: Optional[str] = None
    sortBy: Optional[str] = None
    keyword: Optional[str] = None


class ScenicItem(BaseModel):
    id: int
    name: str
    category: str
    region: Optional[str] = None
    location: Optional[str] = None
    price: Optional[float] = None
    image: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None

    model_config = {"from_attributes": True}


class ScenicDetail(ScenicItem):
    images: Optional[list[str]] = None
    openingHours: Optional[str] = None
    bestSeason: Optional[str] = None
    tips: Optional[str] = None
    coordinates: Optional[dict] = None

    model_config = {"from_attributes": True}


class ScenicRecommendItem(ScenicItem):
    matchReason: Optional[str] = None
