"""Observability router: view agent logs, token usage, pipeline runs."""

from typing import Annotated, List, Optional
from datetime import datetime, UTC, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.observability import AgentLog, PipelineRun
from app.schemas.observability import (
    AgentLogResponse,
    AgentLogListResponse,
    PipelineRunResponse,
    TokenUsageResponse,
    CostSummaryResponse
)

router = APIRouter(prefix="/api/observability", tags=["observability"])


@router.get("/logs", response_model=AgentLogListResponse)
async def get_logs(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    agent: Optional[str] = None,
    scan_run_id: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
):
    query = select(AgentLog)
    
    if agent:
        query = query.where(AgentLog.agent_name == agent)
    if scan_run_id:
        query = query.where(AgentLog.scan_run_id == scan_run_id)
    if status:
        query = query.where(AgentLog.status == status)

    total_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(total_query)).scalar() or 0

    query = query.order_by(desc(AgentLog.created_at)).limit(page_size).offset((page - 1) * page_size)
    result = await db.execute(query)
    logs = result.scalars().all()

    return AgentLogListResponse(items=logs, total=total)


@router.get("/pipeline-runs", response_model=List[PipelineRunResponse])
async def get_pipeline_runs(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    scan_run_id: Optional[str] = None,
    limit: int = 100,
):
    query = select(PipelineRun)
    if scan_run_id:
        query = query.where(PipelineRun.scan_run_id == scan_run_id)
    query = query.order_by(desc(PipelineRun.started_at)).limit(limit)
    
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/token-usage", response_model=CostSummaryResponse)
async def get_token_usage(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    days: int = 30,
):
    cutoff = datetime.now(UTC) - timedelta(days=days)
    
    # By Provider
    p_result = await db.execute(
        select(
            AgentLog.provider_used,
            func.sum(AgentLog.tokens_in).label("tokens_in"),
            func.sum(AgentLog.tokens_out).label("tokens_out"),
            func.sum(AgentLog.cost_estimate).label("cost"),
            func.count(AgentLog.id).label("count")
        )
        .where(AgentLog.created_at >= cutoff)
        .where(AgentLog.provider_used.isnot(None))
        .group_by(AgentLog.provider_used)
    )
    p_rows = p_result.all()
    by_provider = [
        TokenUsageResponse(
            group=row.provider_used,
            tokens_in=row.tokens_in or 0,
            tokens_out=row.tokens_out or 0,
            total_tokens=(row.tokens_in or 0) + (row.tokens_out or 0),
            cost_estimate=row.cost or 0.0,
            call_count=row.count or 0
        ) for row in p_rows
    ]

    # By Agent
    a_result = await db.execute(
        select(
            AgentLog.agent_name,
            func.sum(AgentLog.tokens_in).label("tokens_in"),
            func.sum(AgentLog.tokens_out).label("tokens_out"),
            func.sum(AgentLog.cost_estimate).label("cost"),
            func.count(AgentLog.id).label("count")
        )
        .where(AgentLog.created_at >= cutoff)
        .group_by(AgentLog.agent_name)
    )
    a_rows = a_result.all()
    by_agent = [
        TokenUsageResponse(
            group=row.agent_name,
            tokens_in=row.tokens_in or 0,
            tokens_out=row.tokens_out or 0,
            total_tokens=(row.tokens_in or 0) + (row.tokens_out or 0),
            cost_estimate=row.cost or 0.0,
            call_count=row.count or 0
        ) for row in a_rows
    ]
    
    total_cost = sum(x.cost_estimate for x in by_provider)
    total_tokens = sum(x.total_tokens for x in by_provider)

    return CostSummaryResponse(
        total_cost=total_cost,
        total_tokens=total_tokens,
        by_provider=by_provider,
        by_agent=by_agent
    )


@router.get("/errors", response_model=AgentLogListResponse)
async def get_errors(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 50,
):
    query = select(AgentLog).where(AgentLog.status == "error")
    total_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(total_query)).scalar() or 0

    query = query.order_by(desc(AgentLog.created_at)).limit(limit)
    result = await db.execute(query)
    logs = result.scalars().all()

    return AgentLogListResponse(items=logs, total=total)
