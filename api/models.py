from sqlalchemy import Column, Integer, String, Float, DateTime, func
from api.database import Base
from datetime import datetime, timezone

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    amount = Column(Float, nullable=False)
    items_count = Column(Integer, nullable=False)
    status = Column(String, default="NEW", nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    processed_at = Column(DateTime(timezone=True), nullable=True)