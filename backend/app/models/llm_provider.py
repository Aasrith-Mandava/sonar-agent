from typing import Optional
"""SQLAlchemy ORM models for LLM Providers and Models."""

import uuid
from datetime import datetime

from sqlalchemy import Float, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LLMProvider(Base):
    __tablename__ = "llm_providers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    provider_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # openai|anthropic|google|groq|ollama|azure_openai
    display_name: Mapped[str] = mapped_column(String, nullable=True)
    env_key_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # e.g. OPENAI_API_KEY
    base_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    models: Mapped[list["LLMModel"]] = relationship("LLMModel", back_populates="provider", cascade="all, delete-orphan")
    agent_configs: Mapped[list["AgentConfig"]] = relationship("AgentConfig", back_populates="provider")


class LLMModel(Base):
    __tablename__ = "llm_models"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    provider_id: Mapped[str] = mapped_column(String, ForeignKey("llm_providers.id"), nullable=False)
    model_id: Mapped[str] = mapped_column(String, nullable=False)
    model_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    context_window: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    provider: Mapped["LLMProvider"] = relationship("LLMProvider", back_populates="models")
