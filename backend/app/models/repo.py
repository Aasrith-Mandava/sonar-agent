from typing import Optional
"""SQLAlchemy ORM model for Repositories."""

import uuid
from datetime import datetime

from sqlalchemy import Float, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    github_url: Mapped[str] = mapped_column(String, nullable=False)
    pat: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # per-repo PAT (plain, stored in .env pattern)
    branch: Mapped[str] = mapped_column(String, default="main")
    clone_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sonar_project_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_scan_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="repos")
    scan_runs: Mapped[list["ScanRun"]] = relationship("ScanRun", back_populates="repo", cascade="all, delete-orphan")
    quality_gate: Mapped[Optional["QualityGate"]] = relationship("QualityGate", back_populates="repo", uselist=False, cascade="all, delete-orphan")
