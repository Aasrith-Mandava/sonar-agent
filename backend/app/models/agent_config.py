from typing import Optional
"""SQLAlchemy ORM model for Agent Configurations."""

import uuid
from datetime import datetime

from sqlalchemy import Float, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    agent_name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # scanner|fixer|reviewer|reporter
    agent_role: Mapped[str] = mapped_column(String, nullable=False)
    provider_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("llm_providers.id"), nullable=True)
    model_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("llm_models.id"), nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    system_prompt_override: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    provider: Mapped[Optional["LLMProvider"]] = relationship("LLMProvider", back_populates="agent_configs")
    model: Mapped[Optional["LLMModel"]] = relationship("LLMModel")
