from pydantic import BaseModel
from typing import Optional


class GuideListQuery(BaseModel):
    page: int = 1
    pageSize: int = 10
    category: Optional[str] = None
    scenicId: Optional[int] = None
    keyword: Optional[str] = None


class GuideItem(BaseModel):
    id: int
    title: str
    cover: Optional[str] = None
    summary: Optional[str] = None
    author: Optional[str] = None
    tags: Optional[list[str]] = None
    date: Optional[str] = None
    viewCount: Optional[int] = None
    likeCount: Optional[int] = None

    model_config = {"from_attributes": True}


class GuideDetail(GuideItem):
    authorAvatar: Optional[str] = None
    content: Optional[str] = None
    scenic: Optional[dict] = None
    relatedGuides: Optional[list[dict]] = None
    source: Optional[str] = None

    model_config = {"from_attributes": True}


class GuideGenerateRequest(BaseModel):
    topic: str
    scenicId: Optional[int] = None
    scenicName: Optional[str] = None
    location: Optional[str] = None
    category: Optional[str] = None


class GuideRecommendItem(GuideItem):
    matchReason: Optional[str] = None
