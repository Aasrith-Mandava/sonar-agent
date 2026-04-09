"""Fixes router: view fixes, generate fixes, apply fixes to a fix branch + PR."""

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.scan import ScanRun
from app.models.repo import Repo
from app.models.fix import Fix
from app.schemas.fix import (
    ApplyFixesRequest, ApplyFixesResult, FixResponse, FixListResponse,
)
from app.services.github import github_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["fixes"])


@router.get("/scans/{scan_id}/fixes", response_model=FixListResponse)
async def list_fixes_for_scan(
    scan_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return all fixes generated for a scan, with the source issue eager-loaded."""
    scan_run = await db.get(ScanRun, scan_id)
    if not scan_run:
        raise HTTPException(status_code=404, detail="ScanRun not found")

    result = await db.execute(
        select(Fix)
        .options(selectinload(Fix.issue))
        .where(Fix.scan_run_id == scan_id)
        .order_by(Fix.created_at.asc())
    )
    fixes = list(result.scalars().all())
    return FixListResponse(items=fixes, total=len(fixes))


@router.get("/fixes/{fix_id}", response_model=FixResponse)
async def get_fix(
    fix_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(Fix).options(selectinload(Fix.issue)).where(Fix.id == fix_id)
    )
    fix = result.scalar_one_or_none()
    if not fix:
        raise HTTPException(status_code=404, detail="Fix not found")
    return fix


@router.post("/scans/{scan_id}/apply-fixes", response_model=ApplyFixesResult)
async def apply_fixes(
    scan_id: str,
    body: ApplyFixesRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Apply all generated fixes for this scan to a new git branch.
    Optionally pushes the branch to origin and opens a pull request.

    Pre-validates the per-repo PAT against GitHub before doing any expensive
    work, so missing scopes / invalid tokens fail fast with a clear, actionable
    error message instead of silently producing a 403 deep in the flow.
    """
    scan_run = await db.get(ScanRun, scan_id)
    if not scan_run:
        raise HTTPException(status_code=404, detail="ScanRun not found")

    repo = await db.get(Repo, scan_run.repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    result = await db.execute(
        select(Fix)
        .options(selectinload(Fix.issue))
        .where(Fix.scan_run_id == scan_id)
    )
    fixes = list(result.scalars().all())
    if not fixes:
        raise HTTPException(status_code=400, detail="No fixes to apply for this scan")

    # ── Preflight: validate the per-repo PAT before doing any work ──────────
    pat_source: Optional[str] = None
    if body.push_to_github or body.create_pr:
        validation = await github_service.validate_pat(repo)
        pat_source = validation.get("source")
        if not validation.get("ok"):
            raise HTTPException(status_code=400, detail=validation.get("message"))
        logger.info(f"[apply_fixes] {validation.get('message')}")

    # ── Step 1: clone (or pull) the repo ────────────────────────────────────
    try:
        clone_dir = github_service.clone_or_pull(repo)
        repo.clone_path = str(clone_dir)
        await db.commit()
    except Exception as exc:
        logger.exception("Could not clone repo")
        raise HTTPException(status_code=500, detail=f"Could not clone repo: {exc}")

    # ── Step 2: create branch + write files + commit ────────────────────────
    try:
        branch_name = github_service.create_fix_branch(repo, scan_id)
        written = github_service.apply_fixes(repo, fixes)
        if written == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No fix files could be written to disk. Check that the "
                    "issue file paths in SonarQube match paths in the cloned repo."
                ),
            )
        github_service.commit_fixes(repo, fixes)
    except HTTPException:
        raise
    except RuntimeError as exc:
        # Surfaces the friendly message from commit_fixes / push_branch
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to apply fixes locally")
        raise HTTPException(status_code=500, detail=f"Failed to apply fixes locally: {exc}")

    # ── Step 3: push the branch ─────────────────────────────────────────────
    pushed = False
    if body.push_to_github:
        try:
            github_service.push_branch(repo, branch_name)
            pushed = True
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            logger.exception("Push failed")
            raise HTTPException(status_code=500, detail=f"Push failed: {exc}")

    # ── Step 4: create the PR ───────────────────────────────────────────────
    pr_url: Optional[str] = None
    pr_existed = False
    if body.create_pr and pushed:
        summary_lines = [
            f"This PR was generated by **SonarAgent** for scan `{scan_id}`.",
            "",
            f"It applies **{len(fixes)} automated fix(es)** for SonarQube findings:",
            "",
        ]
        for f in fixes[:30]:
            conf = f.confidence_score or 0
            rule = f.issue.rule_key if f.issue else "unknown"
            summary_lines.append(f"- `{f.file_path}` — {rule} (confidence {conf}/100)")
        if len(fixes) > 30:
            summary_lines.append(f"- … and {len(fixes) - 30} more.")

        pr_result = await github_service.create_pr(
            repo, branch_name,
            title=f"SonarAgent: auto-fix {len(fixes)} issue(s) [{branch_name}]",
            body="\n".join(summary_lines),
        )
        pat_source = pr_result.get("source") or pat_source
        if not pr_result.get("ok"):
            # Surface GitHub's actual error to the UI so the user knows what to fix
            raise HTTPException(status_code=400, detail=pr_result.get("error"))
        pr_url = pr_result.get("url")
        pr_existed = bool(pr_result.get("existed"))

    # ── Step 5: mark fixes as applied ───────────────────────────────────────
    for f in fixes:
        f.status = "applied"
    await db.commit()

    parts = [f"Applied {len(fixes)} fix(es) on branch {branch_name}"]
    if pat_source:
        parts.append(f"(using {pat_source})")
    if pushed:
        parts.append("and pushed to origin")
    if pr_url:
        parts.append(
            "— reused existing PR" if pr_existed else "— PR opened"
        )
        parts.append(pr_url)

    return ApplyFixesResult(
        applied=len(fixes),
        branch=branch_name,
        pr_url=pr_url,
        pushed=pushed,
        pr_existed=pr_existed,
        pat_source=pat_source,
        message=" ".join(parts),
    )