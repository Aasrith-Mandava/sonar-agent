from typing import Optional
"""SQLAlchemy ORM model for Human Fix Reviews."""

import uuid
from datetime import datetime

from sqlalchemy import Float, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FixReview(Base):
    __tablename__ = "fix_reviews"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    fix_id: Mapped[str] = mapped_column(String, ForeignKey("fixes.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)  # approved|rejected|edited
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    edited_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    fix: Mapped["Fix"] = relationship("Fix", back_populates="reviews")
