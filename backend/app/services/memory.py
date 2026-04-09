"""Agentic Memory Service — store and retrieve agent context across runs."""

import json
import logging
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_memory import AgentMemory

logger = logging.getLogger(__name__)


class MemoryService:
    async def store(
        self,
        db: AsyncSession,
        agent_name: str,
        entity_key: str,
        memory_type: str,
        content: str,
        scan_run_id: Optional[str] = None,
    ) -> AgentMemory:
        """Store or update a memory entry for an agent."""
        result = await db.execute(
            select(AgentMemory).where(
                and_(
                    AgentMemory.agent_name == agent_name,
                    AgentMemory.entity_key == entity_key,
                    AgentMemory.memory_type == memory_type,
                )
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.content = content
            existing.scan_run_id = scan_run_id
            existing.updated_at = datetime.now(UTC)
            return existing
        else:
            mem = AgentMemory(
                agent_name=agent_name,
                entity_key=entity_key,
                memory_type=memory_type,
                content=content,
                scan_run_id=scan_run_id,
            )
            db.add(mem)
            await db.flush()
            return mem

    async def recall(
        self,
        db: AsyncSession,
        agent_name: str,
        entity_key: str,
        memory_type: Optional[str] = None,
    ) -> list[AgentMemory]:
        """Recall memories for an agent and entity key, updating recall count."""
        conditions = [
            AgentMemory.agent_name == agent_name,
            AgentMemory.entity_key == entity_key,
        ]
        if memory_type:
            conditions.append(AgentMemory.memory_type == memory_type)

        result = await db.execute(select(AgentMemory).where(and_(*conditions)))
        memories = result.scalars().all()

        for mem in memories:
            mem.recall_count += 1
            mem.last_used_at = datetime.now(UTC)

        return list(memories)

    async def recall_summary(
        self,
        db: AsyncSession,
        agent_name: str,
        entity_key: str,
    ) -> str:
        """Return a formatted summary of all memories for context injection."""
        memories = await self.recall(db, agent_name, entity_key)
        if not memories:
            return ""
        lines = [f"[Memory: {m.memory_type}] {m.content}" for m in memories]
        return "\n".join(lines)

    async def list_memories(
        self,
        db: AsyncSession,
        agent_name: Optional[str] = None,
        limit: int = 100,
    ) -> list[AgentMemory]:
        q = select(AgentMemory).order_by(AgentMemory.updated_at.desc()).limit(limit)
        if agent_name:
            q = q.where(AgentMemory.agent_name == agent_name)
        result = await db.execute(q)
        return result.scalars().all()


memory_service = MemoryService()
