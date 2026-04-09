"""Base Agent class — shared LLM call helper, logging, memory access."""

import logging
import time
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.observability import AgentLog
from app.services.llm_router import get_agent_llm
from app.services.memory import memory_service

logger = logging.getLogger(__name__)


def _to_lc_messages(messages: list[dict]):
    """Convert simple {role, content} dicts to LangChain message objects."""
    out = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            out.append(SystemMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
        else:
            out.append(HumanMessage(content=content))
    return out


class BaseAgent:
    """Abstract base for all pipeline agents."""

    agent_name: str = "base"

    async def llm(
        self,
        messages: list[dict],
        db: Optional[AsyncSession] = None,
        scan_run_id: Optional[str] = None,
    ) -> str:
        """
        Invoke the configured chat model for this agent and return the text response.
        Persists an AgentLog row with latency + token info when db is provided.
        """
        if db is None:
            raise RuntimeError("BaseAgent.llm() requires an active db session")

        chat = await get_agent_llm(self.agent_name, db)
        lc_messages = _to_lc_messages(messages)

        t0 = time.monotonic()
        try:
            response = await chat.ainvoke(lc_messages)
        except Exception as exc:
            logger.error(f"[{self.agent_name}] LLM call failed: {exc}")
            db.add(AgentLog(
                agent_name=self.agent_name,
                scan_run_id=scan_run_id,
                action="llm_call",
                input_summary=f"{len(messages)} message(s)",
                status="error",
                error_message=str(exc)[:1000],
            ))
            await db.flush()
            raise

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Extract text content (LangChain messages)
        content = getattr(response, "content", "")
        if isinstance(content, list):
            content = " ".join(
                (b.get("text", "") if isinstance(b, dict) else str(b))
                for b in content
            )
        text = str(content)

        # Token usage if available
        tokens_in = tokens_out = None
        usage = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("usage")
        if usage:
            tokens_in = usage.get("input_tokens") or usage.get("prompt_tokens")
            tokens_out = usage.get("output_tokens") or usage.get("completion_tokens")

        db.add(AgentLog(
            agent_name=self.agent_name,
            scan_run_id=scan_run_id,
            action="llm_call",
            input_summary=f"{len(messages)} msg(s)",
            output_summary=text[:2000],
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=elapsed_ms,
            status="success",
        ))
        await db.flush()
        logger.info(f"[{self.agent_name}] LLM call ok ({elapsed_ms} ms, {len(text)} chars)")
        return text

    async def remember(
        self,
        db: AsyncSession,
        entity_key: str,
        memory_type: str,
        content: str,
        scan_run_id: Optional[str] = None,
    ) -> None:
        await memory_service.store(db, self.agent_name, entity_key, memory_type, content, scan_run_id)

    async def recall(
        self,
        db: AsyncSession,
        entity_key: str,
        memory_type: Optional[str] = None,
    ) -> str:
        return await memory_service.recall_summary(db, self.agent_name, entity_key)