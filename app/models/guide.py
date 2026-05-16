from sqlalchemy import Column, Integer, String, Text, JSON, DateTime
from datetime import datetime
from app.database import Base


class Guide(Base):
    __tablename__ = "guides"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False, index=True)
    # 用户生成时输入的主题词（用于配图检索）
    topic = Column(String(200), nullable=True)
    cover = Column(String(500), nullable=True)
    summary = Column(String(500), nullable=True)

    author = Column(String(100), nullable=True)
    author_avatar = Column(String(500), nullable=True)

    category = Column(String(50), nullable=False, index=True)
    tags = Column(JSON, nullable=True, default=list)

    content = Column(Text, nullable=True)

    scenic_id = Column(Integer, nullable=True, index=True)

    # seed | wiki | agent
    source = Column(String(20), nullable=True, default="seed", index=True)

    view_count = Column(Integer, default=0)
    like_count = Column(Integer, default=0)

    is_hot = Column(Integer, default=0)
    is_active = Column(Integer, default=1)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
