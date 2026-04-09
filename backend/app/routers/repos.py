"""Repos router: CRUD for repositories."""

import logging
import shutil
from pathlib import Path
from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user, require_admin
from app.models.repo import Repo
from app.models.scan import ScanRun
from app.models.user import User
from app.schemas.repo import RepoCreate, RepoResponse, RepoUpdate
from app.services.sonarqube import sonarqube_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/repos", tags=["repos"])


@router.get("", response_model=List[RepoResponse])
async def list_repos(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    query = select(Repo)
    if current_user.role != "admin":
        query = query.where(Repo.user_id == current_user.id)
    query = query.order_by(Repo.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=RepoResponse, status_code=201)
async def create_repo(
    body: RepoCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    repo = Repo(
        user_id=current_user.id,
        name=body.name,
        github_url=body.github_url,
        pat=body.pat,
        branch=body.branch,
        sonar_project_key=body.name.lower().replace(" ", "-"),
    )
    db.add(repo)
    await db.flush()
    await db.refresh(repo)
    return repo


@router.get("/{repo_id}", response_model=RepoResponse)
async def get_repo(
    repo_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    repo = await db.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    if current_user.role != "admin" and repo.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return repo


@router.put("/{repo_id}", response_model=RepoResponse)
async def update_repo(
    repo_id: str,
    body: RepoUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    repo = await db.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    if current_user.role != "admin" and repo.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    if body.name is not None:
        repo.name = body.name
    if body.branch is not None:
        repo.branch = body.branch
    if body.pat is not None:
        repo.pat = body.pat

    await db.flush()
    await db.refresh(repo)
    return repo


@router.delete("/{repo_id}")
async def delete_repo(
    repo_id: str,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    delete_sonar_project: bool = False,
    delete_local_clone:    bool = True,
):
    """
    Delete a repository and (cascade) all of its scan runs, issues, fixes,
    pipeline runs, and quality gate.

    Optional cleanup:
      • delete_sonar_project=true → also call SonarQube /api/projects/delete
        which removes the project AND all its scan history on the SonarQube
        server.
      • delete_local_clone=true (default) → remove the cloned working dir
        under backend/repos/<repo_id>/.

    Stops any in-flight scans for this repo before deleting.
    """
    repo = await db.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    project_key = repo.sonar_project_key
    clone_path  = repo.clone_path

    # Stop any running pipelines for this repo so the orchestrator releases
    # the DB session before we drop the rows.
    from app.agents.scan_controller import scan_controller
    running_scans = (await db.execute(
        select(ScanRun).where(
            ScanRun.repo_id == repo_id,
            ScanRun.status.in_(["pending", "scanning", "fixing", "reviewing", "reporting", "paused", "resuming"]),
        )
    )).scalars().all()
    for s in running_scans:
        if scan_controller.is_registered(s.id):
            scan_controller.request_stop(s.id)
        if s.sonar_task_id:
            await sonarqube_service.cancel_task(s.sonar_task_id)

    sonar_deleted = False
    if delete_sonar_project and project_key:
        sonar_deleted = await sonarqube_service.delete_project(project_key)

    await db.delete(repo)
    await db.commit()

    # Best-effort filesystem cleanup AFTER the DB row is gone
    if delete_local_clone and clone_path:
        try:
            cp = Path(clone_path)
            if cp.exists() and "/repos/" in str(cp.resolve()):
                shutil.rmtree(cp, ignore_errors=True)
                logger.info(f"Removed local clone {cp}")
        except Exception as exc:
            logger.warning(f"Could not remove local clone {clone_path}: {exc}")

    return {
        "deleted":             True,
        "sonar_project":       project_key,
        "sonar_deleted":       sonar_deleted,
        "local_clone_removed": delete_local_clone and bool(clone_path),
        "running_scans_stopped": len(running_scans),
    }
