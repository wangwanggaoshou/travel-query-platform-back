from sqlalchemy import Column, Integer, String, Date, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class Visa(Base):
    __tablename__ = "visas"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    country = Column(String(100), nullable=False)
    visa_type = Column(String(50), nullable=True)

    issue_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=True)

    status = Column(String(20), default="valid")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="visas")
