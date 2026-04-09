"""Scans router: trigger scans, fetch statuses and issues, pause/resume/stop."""

from sqlalchemy import select, func
from typing import Annotated, List, Optional
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.repo import Repo
from app.models.scan import ScanRun, Issue
from app.schemas.scan import ScanRunResponse, IssueResponse, IssueListResponse
from app.models.observability import AgentLog, PipelineRun
from app.agents.orchestrator import orchestrator
from app.agents.scan_controller import scan_controller

router = APIRouter(prefix="/api/scans", tags=["scans"])

_TERMINAL = {"completed", "failed", "stopped"}
_RUNNING  = {"pending", "scanning", "analyzing", "fixing", "reviewing", "reporting", "resuming"}


@router.post("/repos/{repo_id}/scan", response_model=ScanRunResponse, status_code=202)
async def trigger_scan(
    repo_id: str,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    repo = await db.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    
    scan_run = ScanRun(
        repo_id=repo.id,
        triggered_by=current_user.id,
        status="pending",
        scan_type="initial"
    )
    db.add(scan_run)
    await db.flush()
    await db.refresh(scan_run)

    # Trigger orchestrator pipeline in background
    background_tasks.add_task(orchestrator.run_scan_pipeline, scan_run.id)

    return scan_run


@router.get("/{scan_id}", response_model=ScanRunResponse)
async def get_scan_status(
    scan_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    scan_run = await db.get(ScanRun, scan_id)
    if not scan_run:
        raise HTTPException(status_code=404, detail="ScanRun not found")
    return scan_run


@router.get("/{scan_id}/issues", response_model=IssueListResponse)
async def list_issues(
    scan_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    severity: Optional[str] = None,
    type: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
):
    scan_run = await db.get(ScanRun, scan_id)
    if not scan_run:
        raise HTTPException(status_code=404, detail="ScanRun not found")

    query = select(Issue).where(Issue.scan_run_id == scan_id)
    if severity:
        query = query.where(Issue.severity == severity)
    if type:
        query = query.where(Issue.type == type)

    total_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(total_query)).scalar() or 0

    query = query.limit(page_size).offset((page - 1) * page_size)
    result = await db.execute(query)
    issues = result.scalars().all()

    return IssueListResponse(
        items=issues,
        total=total,
        page=page,
        page_size=page_size
    )

@router.post("/{scan_id}/pause", response_model=ScanRunResponse)
async def pause_scan(
    scan_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    scan_run = await db.get(ScanRun, scan_id)
    if not scan_run:
        raise HTTPException(status_code=404, detail="ScanRun not found")
    if scan_run.status not in _RUNNING:
        raise HTTPException(status_code=400, detail=f"Scan is not running (status: {scan_run.status})")
    if not scan_controller.is_registered(scan_id):
        raise HTTPException(status_code=400, detail="No active pipeline found for this scan")

    scan_controller.request_pause(scan_id)
    scan_run.status = "paused"

    from app.websockets.pipeline import manager
    await manager.broadcast_pipeline(scan_id, {
        "type": "paused",
        "agent": "orchestrator",
        "status": "paused",
        "message": "Pipeline paused by user.",
        "ts": datetime.now(UTC).isoformat(),
    })
    return scan_run


@router.post("/{scan_id}/resume", response_model=ScanRunResponse)
async def resume_scan(
    scan_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    scan_run = await db.get(ScanRun, scan_id)
    if not scan_run:
        raise HTTPException(status_code=404, detail="ScanRun not found")
    if scan_run.status != "paused":
        raise HTTPException(status_code=400, detail="Scan is not paused")
    if not scan_controller.is_registered(scan_id):
        raise HTTPException(status_code=400, detail="No active pipeline found for this scan")

    scan_controller.request_resume(scan_id)
    scan_run.status = "resuming"

    from app.websockets.pipeline import manager
    await manager.broadcast_pipeline(scan_id, {
        "type": "resumed",
        "agent": "orchestrator",
        "status": "resuming",
        "message": "Pipeline resumed. Continuing from last checkpoint…",
        "ts": datetime.now(UTC).isoformat(),
    })
    return scan_run


@router.post("/{scan_id}/stop", response_model=ScanRunResponse)
async def stop_scan(
    scan_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    scan_run = await db.get(ScanRun, scan_id)
    if not scan_run:
        raise HTTPException(status_code=404, detail="ScanRun not found")
    if scan_run.status in _TERMINAL:
        raise HTTPException(status_code=400, detail=f"Scan already finished (status: {scan_run.status})")
    if not scan_controller.is_registered(scan_id):
        raise HTTPException(status_code=400, detail="No active pipeline found for this scan")

    scan_controller.request_stop(scan_id)
    # Status will be set to "stopped" by orchestrator once the current node unblocks

    from app.websockets.pipeline import manager
    await manager.broadcast_pipeline(scan_id, {
        "type": "stopping",
        "agent": "orchestrator",
        "status": "stopping",
        "message": "Stop requested. Pipeline will halt after current step.",
        "ts": datetime.now(UTC).isoformat(),
    })
    return scan_run


@router.delete("/{scan_id}", status_code=204)
async def delete_scan(
    scan_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Delete a single ScanRun and everything attached to it (issues, fixes,
    pipeline runs). If the scan is currently running, request a stop first
    so the orchestrator unblocks before we tear it down.
    """
    scan_run = await db.get(ScanRun, scan_id)
    if not scan_run:
        raise HTTPException(status_code=404, detail="ScanRun not found")

    if scan_run.status in _RUNNING:
        if scan_controller.is_registered(scan_id):
            scan_controller.request_stop(scan_id)
        # Try to cancel the SonarQube background task as well
        if scan_run.sonar_task_id:
            from app.services.sonarqube import sonarqube_service
            await sonarqube_service.cancel_task(scan_run.sonar_task_id)

    await db.delete(scan_run)
    await db.commit()
    return None


# Pipeline stages in execution order — used by retry/rerun-stages.
_PIPELINE_STAGES = ["clone", "scan", "fix", "review", "report"]


@router.post("/{scan_id}/retry", response_model=ScanRunResponse, status_code=202)
async def retry_scan(
    scan_id: str,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    from_stage: Optional[str] = None,
):
    """
    Retry a scan, optionally re-running from a specific stage.

    • Without `from_stage`: resumes from whichever stage failed (the orchestrator
      reuses every previously-completed stage via its skip-on-completed logic).
    • With `from_stage=<clone|scan|fix|review|report>`: clears that stage AND
      every downstream stage (their PipelineRun rows + their artifacts), so the
      orchestrator re-runs them from scratch. Useful when you want to e.g. swap
      the Fixer model and regenerate fixes from cached issues.

    Allowed even on `completed` scans when `from_stage` is set, so you can
    re-run a single stage without retriggering the whole pipeline.
    """
    scan_run = await db.get(ScanRun, scan_id)
    if not scan_run:
        raise HTTPException(status_code=404, detail="ScanRun not found")
    if scan_run.status in _RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Scan is currently {scan_run.status}; stop it before retrying",
        )
    if scan_run.status == "completed" and not from_stage:
        raise HTTPException(
            status_code=400,
            detail="Scan already completed; specify from_stage=<clone|scan|fix|review|report> "
                   "to re-run a specific stage, or trigger a new scan instead.",
        )

    if from_stage:
        if from_stage not in _PIPELINE_STAGES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid stage '{from_stage}'. Must be one of {_PIPELINE_STAGES}",
            )
        await _clear_stages_from(db, scan_id, from_stage)

    # Reset to pending so the orchestrator picks it up. The orchestrator will
    # see the remaining completed PipelineRun rows and skip those stages.
    scan_run.status = "pending"
    scan_run.completed_at = None
    await db.commit()
    await db.refresh(scan_run)

    background_tasks.add_task(orchestrator.run_scan_pipeline, scan_run.id)
    return scan_run


async def _clear_stages_from(db: AsyncSession, scan_id: str, from_stage: str) -> None:
    """
    Delete the PipelineRun rows for `from_stage` and every stage after it,
    plus the downstream artifacts (issues, fixes) so they get regenerated.

    Stage → artifact mapping (everything *produced* by that stage and beyond):
      clone:  drop nothing extra (clone dir gets re-cloned by clone_or_pull)
      scan:   delete all Issues + Fixes for this scan
      fix:    delete all Fixes for this scan
      review: nothing extra (reviewer mutates Fix.confidence_score in place,
                              we'll just let it overwrite on re-run)
      report: nothing extra (reporter writes a DeltaReport row keyed to scan_id)
    """
    from sqlalchemy import delete as sql_delete
    from app.models.fix import Fix

    idx = _PIPELINE_STAGES.index(from_stage)
    stages_to_clear = _PIPELINE_STAGES[idx:]

    # Drop PipelineRun rows for the cleared stages so the orchestrator no
    # longer treats them as 'already completed'.
    await db.execute(sql_delete(PipelineRun).where(
        PipelineRun.scan_run_id == scan_id,
        PipelineRun.stage.in_(stages_to_clear),
    ))

    # Drop downstream artifacts so the agents recreate them cleanly.
    if "scan" in stages_to_clear:
        # Deleting issues will cascade to their fixes via the Issue→Fix relation,
        # but we drop fixes explicitly first for clarity & to avoid surprises if
        # the cascade direction changes.
        await db.execute(sql_delete(Fix).where(Fix.scan_run_id == scan_id))
        await db.execute(sql_delete(Issue).where(Issue.scan_run_id == scan_id))
    elif "fix" in stages_to_clear:
        await db.execute(sql_delete(Fix).where(Fix.scan_run_id == scan_id))

    await db.commit()


@router.get("/{scan_id}/summary")
async def get_scan_summary(
    scan_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Aggregate view of a scan: status, counts of issues + fixes, latest error
    message (if any), and per-stage pipeline run statuses.
    """
    scan_run = await db.get(ScanRun, scan_id)
    if not scan_run:
        raise HTTPException(status_code=404, detail="ScanRun not found")

    from sqlalchemy import desc
    from app.models.fix import Fix

    issue_count = (await db.execute(
        select(func.count()).select_from(Issue).where(Issue.scan_run_id == scan_id)
    )).scalar() or 0
    fix_count = (await db.execute(
        select(func.count()).select_from(Fix).where(Fix.scan_run_id == scan_id)
    )).scalar() or 0
    selected_count = (await db.execute(
        select(func.count()).select_from(Issue).where(
            Issue.scan_run_id == scan_id, Issue.selected_for_fix == True  # noqa
        )
    )).scalar() or 0

    # Latest error log for this scan
    err_result = await db.execute(
        select(AgentLog)
        .where(AgentLog.scan_run_id == scan_id, AgentLog.status == "error")
        .order_by(desc(AgentLog.created_at))
        .limit(1)
    )
    latest_error_row = err_result.scalar_one_or_none()
    latest_error = latest_error_row.error_message if latest_error_row else None

    # Pipeline stage progress
    stage_result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.scan_run_id == scan_id)
        .order_by(PipelineRun.started_at.asc())
    )
    stages = [
        {
            "stage":         row.stage,
            "status":        row.status,
            "started_at":    row.started_at.isoformat() if row.started_at else None,
            "completed_at":  row.completed_at.isoformat() if row.completed_at else None,
            "error_message": row.error_message,
            "details":       row.details,
        }
        for row in stage_result.scalars().all()
    ]

    return {
        "scan_id":            scan_id,
        "status":             scan_run.status,
        "total_issues":       scan_run.total_issues,
        "selected_for_fix":   selected_count,
        "issues_in_db":       issue_count,
        "fixes_generated":    fix_count,
        "latest_error":       latest_error,
        "stages":             stages,
        "created_at":         scan_run.created_at.isoformat() if scan_run.created_at else None,
        "completed_at":       scan_run.completed_at.isoformat() if scan_run.completed_at else None,
    }


@router.get("/repos/{repo_id}/scan-history", response_model=List[ScanRunResponse])
async def list_repo_scans(
    repo_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(ScanRun)
        .where(ScanRun.repo_id == repo_id)
        .order_by(ScanRun.created_at.desc())
    )
    return result.scalars().all()
