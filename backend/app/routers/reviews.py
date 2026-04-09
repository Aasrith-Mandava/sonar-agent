"""Reviews router: approve/reject fixes, bulk actions, and apply logic."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.fix import Fix
from app.models.scan import ScanRun
from app.models.review import FixReview
from app.schemas.fix import ReviewRequest, BulkApproveRequest, ApplyFixesRequest, ReviewStatsResponse
from app.schemas.review import FixReviewResponse
from app.agents.orchestrator import orchestrator

router = APIRouter(prefix="/api", tags=["reviews"])


@router.post("/fixes/{fix_id}/review", response_model=FixReviewResponse)
async def review_fix(
    fix_id: str,
    body: ReviewRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    fix = await db.get(Fix, fix_id)
    if not fix:
        raise HTTPException(status_code=404, detail="Fix not found")

    if body.action not in ("approved", "rejected", "edited"):
        raise HTTPException(status_code=400, detail="Invalid action")

    review = FixReview(
        fix_id=fix.id,
        user_id=current_user.id,
        action=body.action,
        comment=body.comment,
        edited_code=body.edited_code if body.action == "edited" else None
    )
    db.add(review)

    fix.status = body.action
    if body.action == "edited" and body.edited_code:
        fix.fixed_code = body.edited_code
        # Might recompute diff patch in a full implementation

    await db.commit()
    await db.refresh(review)
    return review


@router.post("/scans/{scan_id}/bulk-approve", status_code=204)
async def bulk_approve(
    scan_id: str,
    body: BulkApproveRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(Fix).where(
            and_(
                Fix.scan_run_id == scan_id,
                Fix.status == "pending",
                Fix.confidence_score >= body.min_confidence
            )
        )
    )
    fixes = result.scalars().all()
    for fix in fixes:
        fix.status = "approved"
        review = FixReview(
            fix_id=fix.id,
            user_id=current_user.id,
            action="approved",
            comment="Bulk approved based on confidence threshold"
        )
        db.add(review)

    await db.commit()


@router.post("/scans/{scan_id}/apply-fixes", status_code=202)
async def apply_fixes(
    scan_id: str,
    body: ApplyFixesRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    scan = await db.get(ScanRun, scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="ScanRun not found")

    background_tasks.add_task(
        orchestrator.run_apply_rescan_pipeline,
        scan_id,
        push_to_github=body.push_to_github,
        create_pr=body.create_pr
    )
    return {"message": "Applying fixes in background"}


@router.get("/scans/{scan_id}/review-stats", response_model=ReviewStatsResponse)
async def review_stats(
    scan_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(Fix.status, func.count(Fix.id))
        .where(Fix.scan_run_id == scan_id)
        .group_by(Fix.status)
    )
    counts = dict(result.all())
    
    return ReviewStatsResponse(
        total=sum(counts.values()),
        pending=counts.get("pending", 0),
        approved=counts.get("approved", 0),
        rejected=counts.get("rejected", 0),
        edited=counts.get("edited", 0),
        applied=counts.get("applied", 0),
    )
