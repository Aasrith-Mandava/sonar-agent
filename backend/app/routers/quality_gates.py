"""Quality Gates router: configure repo scan criteria."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.repo import Repo
from app.models.quality_gate import QualityGate
from app.schemas.settings import QualityGateUpdate, QualityGateResponse

router = APIRouter(prefix="/api/repos", tags=["quality_gates"])


@router.get("/{repo_id}/quality-gate", response_model=QualityGateResponse)
async def get_quality_gate(
    repo_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    repo = await db.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    result = await db.execute(select(QualityGate).where(QualityGate.repo_id == repo_id))
    qg = result.scalar_one_or_none()
    
    if not qg:
        qg = QualityGate(repo_id=repo_id)
        db.add(qg)
        await db.commit()
        await db.refresh(qg)

    return qg


@router.put("/{repo_id}/quality-gate", response_model=QualityGateResponse)
async def update_quality_gate(
    repo_id: str,
    body: QualityGateUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    repo = await db.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    result = await db.execute(select(QualityGate).where(QualityGate.repo_id == repo_id))
    qg = result.scalar_one_or_none()
    
    if not qg:
        qg = QualityGate(repo_id=repo_id)
        db.add(qg)

    if body.min_severity is not None:
        qg.min_severity = body.min_severity
    if body.max_issues_per_run is not None:
        qg.max_issues_per_run = body.max_issues_per_run
    if body.auto_fix_enabled is not None:
        qg.auto_fix_enabled = body.auto_fix_enabled
    if body.file_exclusions is not None:
        qg.file_exclusions = json.dumps(body.file_exclusions)
    if body.rule_exclusions is not None:
        qg.rule_exclusions = json.dumps(body.rule_exclusions)

    await db.commit()
    await db.refresh(qg)
    return qg
