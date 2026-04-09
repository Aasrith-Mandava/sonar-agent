"""Reports router: fetch delta reports and trends."""

from typing import Annotated, List, Optional
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.repo import Repo
from app.models.observability import DeltaReport
from app.schemas.observability import DeltaReportResponse

router = APIRouter(prefix="/api", tags=["reports"])


@router.get("/reports/{scan_id}/delta", response_model=DeltaReportResponse)
async def get_delta_report(
    scan_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # A report is usually attached to the `after_scan_id` since it's generated after the rescan
    result = await db.execute(
        select(DeltaReport).where(DeltaReport.after_scan_id == scan_id)
    )
    report = result.scalar_one_or_none()
    
    # Fallback to before_scan_id
    if not report:
        result = await db.execute(
            select(DeltaReport).where(DeltaReport.before_scan_id == scan_id)
        )
        report = result.scalar_one_or_none()
        
    if not report:
        raise HTTPException(status_code=404, detail="Delta report not found")
        
    return report


@router.get("/repos/{repo_id}/trends", response_model=List[DeltaReportResponse])
async def get_repo_trends(
    repo_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 10,
):
    repo = await db.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
        
    result = await db.execute(
        select(DeltaReport)
        .where(DeltaReport.repo_id == repo_id)
        .order_by(desc(DeltaReport.created_at))
        .limit(limit)
    )
    return result.scalars().all()
