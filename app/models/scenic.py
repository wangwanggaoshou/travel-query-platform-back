from sqlalchemy import Column, Integer, String, Text, Float, JSON, DateTime
from datetime import datetime
from app.database import Base


class Scenic(Base):
    __tablename__ = "scenics"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)
    region = Column(String(20), nullable=False, default="domestic")
    location = Column(String(200), nullable=True)
    address = Column(String(500), nullable=True)

    price = Column(Float, default=0.0)

    image = Column(String(500), nullable=True)
    images = Column(JSON, nullable=True, default=list)

    description = Column(Text, nullable=True)
    opening_hours = Column(String(100), nullable=True)
    best_season = Column(String(50), nullable=True)
    tips = Column(Text, nullable=True)

    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    tags = Column(JSON, nullable=True, default=list)
    review_count = Column(Integer, default=0)
    view_count = Column(Integer, default=0)

    is_hot = Column(Integer, default=0)
    is_active = Column(Integer, default=1)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
