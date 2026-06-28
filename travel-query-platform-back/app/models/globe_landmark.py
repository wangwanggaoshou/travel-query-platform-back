from sqlalchemy import Column, Integer, String, JSON, DateTime
from datetime import datetime, timezone
from app.database import Base


class GlobeLandmark(Base):
    __tablename__ = "globe_landmarks"

    id = Column(Integer, primary_key=True, index=True)
    country_key = Column(String(100), unique=True, nullable=False, index=True)
    country_name = Column(String(100), nullable=False)
    country_name_en = Column(String(100), nullable=True)
    country_flag = Column(String(10), nullable=True, default="🌍")
    iso_code = Column(String(5), nullable=True)
    attractions = Column(JSON, nullable=False, default=list)
    source = Column(String(20), nullable=True, default="web_crawl")
    crawled_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
