"""
Deterministic multi-agent pipeline orchestrator.

Replaces the previous LangGraph LLM-supervisor (which never actually ran the
real agents). This version invokes ScannerAgent → FixerAgent → ReviewerAgent
→ ReporterAgent in order, each as a real Python coroutine, with:

  • Pause / resume / stop checkpoints between every stage
  • WebSocket broadcasts of structured progress events
  • Persistent AgentLog + PipelineRun rows for observability
  • Graceful per-stage error handling (a stage failure marks the scan failed
    but still cleans up and broadcasts the reason).
"""

import asyncio
import logging
from datetime import datetime, UTC
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agents.fixer import FixerAgent
from app.agents.reporter import ReporterAgent
from app.agents.reviewer import ReviewerAgent
from app.agents.scan_controller import scan_controller, ScanStoppedError
from app.agents.scanner import ScannerAgent
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.fix import Fix
from app.models.observability import PipelineRun
from app.models.quality_gate import QualityGate
from app.models.repo import Repo
from app.models.scan import Issue, ScanRun
from app.services.github import github_service

logger = logging.getLogger(__name__)
settings = get_settings()


class PipelineTimeoutError(Exception):
    """Raised when the pipeline exceeds its total or per-stage timeout budget."""


async def _broadcast(scan_run_id: str, payload: dict) -> None:
    """Send a structured event to BOTH the per-scan and global log channels."""
    payload.setdefault("ts", datetime.now(UTC).isoformat())
    try:
        from app.websockets.pipeline import manager
        await manager.broadcast_pipeline(scan_run_id, payload)
        await manager.broadcast_log(payload)
    except Exception as exc:
        logger.debug(f"WS broadcast skipped: {exc}")


async def _stage_already_completed(db, scan_run_id: str, stage: str) -> bool:
    """
    Check if a PipelineRun row exists for this scan with the given stage AND
    status='completed'. Used by the retry logic to skip stages that already
    succeeded in a previous attempt.
    """
    result = await db.execute(
        select(PipelineRun).where(
            PipelineRun.scan_run_id == scan_run_id,
            PipelineRun.stage == stage,
            PipelineRun.status == "completed",
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _delete_failed_stage_rows(db, scan_run_id: str, stage: str) -> None:
    """
    Before re-running a failed stage on retry, drop any failed/in-progress
    PipelineRun rows for that stage so we don't accumulate dupes across
    retry attempts.
    """
    from sqlalchemy import delete as sql_delete
    await db.execute(
        sql_delete(PipelineRun).where(
            PipelineRun.scan_run_id == scan_run_id,
            PipelineRun.stage == stage,
            PipelineRun.status != "completed",
        )
    )
    await db.commit()


async def _set_status(db, scan_run: ScanRun, status: str) -> None:
    """
    Update the scan_run.status — but never clobber a user-driven 'paused' /
    'stopping' / 'stopped' state. Without this guard, the orchestrator's own
    session would overwrite a status set by the pause/stop API in a different
    session, making pause appear broken from the user's perspective.
    """
    # Refresh from DB so we see any status set by the pause/stop API
    await db.refresh(scan_run)
    if scan_run.status in ("paused", "stopping", "stopped"):
        return
    if scan_controller.is_paused(scan_run.id) or scan_controller.should_stop(scan_run.id):
        return
    scan_run.status = status
    await db.commit()


async def _run_with_timeout(coro, seconds: int, label: str):
    """Run a coroutine with an overall timeout. On timeout, raises PipelineTimeoutError."""
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        raise PipelineTimeoutError(f"{label} exceeded {seconds}s timeout")


class PipelineOrchestrator:
    """Runs the full Scanner → Fixer → Reviewer → Reporter pipeline."""

    async def run_scan_pipeline(self, scan_run_id: str) -> None:
        """Public entry — wraps the actual pipeline in a total-time timeout."""
        try:
            await asyncio.wait_for(
                self._run_pipeline_inner(scan_run_id),
                timeout=settings.pipeline_total_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error(
                f"Pipeline {scan_run_id} exceeded total timeout of "
                f"{settings.pipeline_total_timeout_seconds}s — forcing failure."
            )
            await self._mark_timeout(scan_run_id)
        finally:
            scan_controller.cleanup(scan_run_id)

    async def _mark_timeout(self, scan_run_id: str) -> None:
        """Mark a scan as failed due to timeout. Runs in its own session to
        avoid contention with the cancelled inner session."""
        try:
            async with AsyncSessionLocal() as db:
                scan_run = await db.get(ScanRun, scan_run_id)
                if scan_run and scan_run.status not in ("completed", "stopped", "failed"):
                    scan_run.status = "failed"
                    scan_run.completed_at = datetime.now(UTC)
                    await db.commit()
            await _broadcast(scan_run_id, {
                "type":    "failed",
                "agent":   "orchestrator",
                "status":  "failed",
                "message": (
                    f"⏱ Pipeline timed out after "
                    f"{settings.pipeline_total_timeout_seconds}s. "
                    f"The scan was automatically cancelled."
                ),
            })
        except Exception as exc:
            logger.exception(f"Failed to mark scan {scan_run_id} as timed out: {exc}")

    async def _run_pipeline_inner(self, scan_run_id: str) -> None:
        async with AsyncSessionLocal() as db:
            scan_run = await db.get(ScanRun, scan_run_id)
            if not scan_run:
                logger.error(f"ScanRun {scan_run_id} not found — aborting")
                return

            repo = await db.get(Repo, scan_run.repo_id)
            if not repo:
                logger.error(f"Repo {scan_run.repo_id} not found — aborting")
                scan_run.status = "failed"
                await db.commit()
                return

            # Quality gate (optional)
            qg_result = await db.execute(
                select(QualityGate).where(QualityGate.repo_id == repo.id)
            )
            quality_gate = qg_result.scalar_one_or_none()

            scan_controller.register(scan_run_id)

            await _broadcast(scan_run_id, {
                "type":    "init",
                "agent":   "orchestrator",
                "status":  "pending",
                "message": f"Pipeline started for '{repo.name}' on branch '{repo.branch}'.",
            })

            stage_timeout = settings.pipeline_stage_timeout_seconds

            try:
                await _run_with_timeout(
                    self._stage_clone(db, scan_run, repo),
                    stage_timeout, "clone stage",
                )
                issues = await _run_with_timeout(
                    self._stage_scan(db, scan_run, repo, quality_gate),
                    stage_timeout, "scan stage",
                )
                fixes = await _run_with_timeout(
                    self._stage_fix(db, scan_run, repo, issues),
                    stage_timeout, "fix stage",
                )
                await _run_with_timeout(
                    self._stage_review(db, scan_run, fixes),
                    stage_timeout, "review stage",
                )
                await _run_with_timeout(
                    self._stage_report(db, scan_run, repo),
                    stage_timeout, "report stage",
                )

                scan_run.status = "completed"
                scan_run.completed_at = datetime.now(UTC)
                await db.commit()

                await _broadcast(scan_run_id, {
                    "type":    "complete",
                    "agent":   "orchestrator",
                    "status":  "completed",
                    "message": (
                        f"Pipeline completed. {len(issues)} issue(s) found, "
                        f"{len(fixes)} fix(es) generated."
                    ),
                })

            except ScanStoppedError as exc:
                logger.info(f"Scan {scan_run_id} stopped by user: {exc}")
                scan_run.status = "stopped"
                scan_run.completed_at = datetime.now(UTC)
                await db.commit()
                await _broadcast(scan_run_id, {
                    "type":    "stopped",
                    "agent":   "orchestrator",
                    "status":  "stopped",
                    "message": "Pipeline stopped by user.",
                })

            except PipelineTimeoutError as exc:
                logger.error(f"Pipeline {scan_run_id} stage timeout: {exc}")
                scan_run.status = "failed"
                scan_run.completed_at = datetime.now(UTC)
                await db.commit()
                await _broadcast(scan_run_id, {
                    "type":    "failed",
                    "agent":   "orchestrator",
                    "status":  "failed",
                    "message": f"⏱ Pipeline timeout: {exc}",
                })

            except Exception as exc:
                logger.exception(f"Pipeline failed for scan {scan_run_id}")
                scan_run.status = "failed"
                scan_run.completed_at = datetime.now(UTC)
                await db.commit()
                await _broadcast(scan_run_id, {
                    "type":    "failed",
                    "agent":   "orchestrator",
                    "status":  "failed",
                    "message": f"Pipeline failed: {type(exc).__name__}: {str(exc)[:400]}",
                })

    # ── Stages ────────────────────────────────────────────────────────────

    async def _stage_clone(self, db, scan_run: ScanRun, repo: Repo) -> None:
        await scan_controller.checkpoint(scan_run.id)
        await _set_status(db, scan_run, "scanning")

        # Retry-skip: if a previous attempt completed this stage AND the
        # local clone still exists on disk, reuse it.
        if await _stage_already_completed(db, scan_run.id, "clone"):
            from pathlib import Path
            if repo.clone_path and Path(repo.clone_path).exists():
                logger.info(f"[clone] Skipping — already completed in a previous attempt ({repo.clone_path})")
                await _broadcast(scan_run.id, {
                    "type":    "log",
                    "agent":   "orchestrator",
                    "action":  "skip_stage",
                    "message": f"⏭ Skipping clone stage — reusing previous clone at {repo.clone_path}",
                })
                return
            logger.info("[clone] Previous clone path missing — re-cloning")

        await _delete_failed_stage_rows(db, scan_run.id, "clone")
        pr = PipelineRun(
            scan_run_id=scan_run.id,
            stage="clone",
            status="running",
            started_at=datetime.now(UTC),
        )
        db.add(pr)
        await db.commit()

        await _broadcast(scan_run.id, {
            "type":    "agent_start",
            "agent":   "orchestrator",
            "action":  "clone_repo",
            "status":  "scanning",
            "message": f"Cloning {repo.github_url} (branch: {repo.branch})…",
        })

        try:
            clone_path = github_service.clone_or_pull(repo)
            repo.clone_path = str(clone_path)
            await db.commit()

            pr.status = "completed"
            pr.completed_at = datetime.now(UTC)
            pr.details = f'{{"clone_path": "{clone_path}"}}'
            await db.commit()

            await _broadcast(scan_run.id, {
                "type":    "log",
                "agent":   "orchestrator",
                "action":  "clone_repo",
                "message": f"Repository ready at {clone_path}",
            })
        except Exception as exc:
            pr.status = "failed"
            pr.error_message = str(exc)[:1000]
            pr.completed_at = datetime.now(UTC)
            await db.commit()
            # Non-fatal: scanner can still call REST API. Warn and continue.
            logger.warning(f"Repo clone failed but pipeline will continue: {exc}")
            await _broadcast(scan_run.id, {
                "type":    "log",
                "agent":   "orchestrator",
                "action":  "clone_repo",
                "message": (
                    f"⚠ Could not clone repo ({exc}). "
                    "Continuing with REST-API-only mode (no local scan, no file fixes)."
                ),
            })
            repo.clone_path = None
            await db.commit()

    async def _preflight_sonarqube(self, scan_run_id: str) -> None:
        """
        Verify SonarQube credentials work BEFORE we do anything expensive.
        Raises a RuntimeError with an actionable message if not configured
        or unreachable.
        """
        s = settings
        if not s.sonarqube_token:
            raise RuntimeError(
                "SonarQube token is not configured. Open Settings → SonarQube "
                "in the UI and paste a valid User token, then re-run the scan. "
                "(You can generate one in SonarQube → My Account → Security.)"
            )

        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{s.sonarqube_url.rstrip('/')}/api/authentication/validate",
                    auth=(s.sonarqube_token, ""),
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Cannot reach SonarQube at {s.sonarqube_url}: {exc}. "
                "Check the URL in Settings → SonarQube."
            )

        if resp.status_code != 200:
            raise RuntimeError(
                f"SonarQube auth check returned HTTP {resp.status_code}. "
                "Verify the token in Settings → SonarQube → Test Connection."
            )

        data = resp.json()
        if not data.get("valid"):
            raise RuntimeError(
                "SonarQube token is INVALID or expired. Generate a new User "
                "token in SonarQube → My Account → Security and update it in "
                "Settings → SonarQube."
            )

    async def _stage_scan(self, db, scan_run: ScanRun, repo: Repo, quality_gate) -> list[Issue]:
        await scan_controller.checkpoint(scan_run.id)
        await self._preflight_sonarqube(scan_run.id)
        await _set_status(db, scan_run, "scanning")

        # Retry-skip: if scan stage already completed AND we still have selected
        # issues in the DB, reuse them.
        if await _stage_already_completed(db, scan_run.id, "scan"):
            existing = (await db.execute(
                select(Issue).where(
                    Issue.scan_run_id == scan_run.id,
                    Issue.selected_for_fix == True,  # noqa: E712
                )
            )).scalars().all()
            if existing:
                logger.info(f"[scan] Skipping — reusing {len(existing)} previously-selected issues")
                await _broadcast(scan_run.id, {
                    "type":    "log",
                    "agent":   "orchestrator",
                    "action":  "skip_stage",
                    "message": f"⏭ Skipping scan stage — reusing {len(existing)} previously-selected issue(s)",
                })
                return list(existing)

        await _delete_failed_stage_rows(db, scan_run.id, "scan")
        pr = PipelineRun(
            scan_run_id=scan_run.id,
            stage="scan",
            status="running",
            started_at=datetime.now(UTC),
        )
        db.add(pr)
        await db.commit()

        await _broadcast(scan_run.id, {
            "type":    "agent_start",
            "agent":   "scanner",
            "status":  "scanning",
            "message": "Scanner Agent: querying SonarQube and selecting issues…",
        })

        try:
            scanner = ScannerAgent()
            selected = await scanner.run(db, scan_run, repo, quality_gate)
            await db.commit()

            pr.status = "completed"
            pr.completed_at = datetime.now(UTC)
            pr.details = f'{{"selected_for_fix": {len(selected)}, "total_issues": {scan_run.total_issues}}}'
            await db.commit()

            await _broadcast(scan_run.id, {
                "type":    "log",
                "agent":   "scanner",
                "action":  "scan_complete",
                "message": (
                    f"Scanner finished. {scan_run.total_issues} total issue(s), "
                    f"{len(selected)} selected for fixing."
                ),
            })
            return selected
        except Exception as exc:
            pr.status = "failed"
            pr.error_message = str(exc)[:1000]
            pr.completed_at = datetime.now(UTC)
            await db.commit()
            raise

    async def _stage_fix(self, db, scan_run: ScanRun, repo: Repo, issues: list[Issue]) -> list[Fix]:
        await scan_controller.checkpoint(scan_run.id)

        if not issues:
            await _broadcast(scan_run.id, {
                "type":    "log",
                "agent":   "fixer",
                "message": "No issues selected — skipping Fixer stage.",
            })
            return []

        if not repo.clone_path:
            await _broadcast(scan_run.id, {
                "type":    "log",
                "agent":   "fixer",
                "message": "⚠ No local clone available — Fixer cannot read source files. Skipping.",
            })
            return []

        await _set_status(db, scan_run, "fixing")

        # Retry-skip: if fix stage already completed AND fixes exist, reuse.
        if await _stage_already_completed(db, scan_run.id, "fix"):
            existing = (await db.execute(
                select(Fix).where(Fix.scan_run_id == scan_run.id)
            )).scalars().all()
            if existing:
                logger.info(f"[fix] Skipping — reusing {len(existing)} previously-generated fixes")
                await _broadcast(scan_run.id, {
                    "type":    "log",
                    "agent":   "orchestrator",
                    "action":  "skip_stage",
                    "message": f"⏭ Skipping fix stage — reusing {len(existing)} previously-generated fix(es)",
                })
                return list(existing)

        await _delete_failed_stage_rows(db, scan_run.id, "fix")
        pr = PipelineRun(
            scan_run_id=scan_run.id,
            stage="fix",
            status="running",
            started_at=datetime.now(UTC),
        )
        db.add(pr)
        await db.commit()

        await _broadcast(scan_run.id, {
            "type":    "agent_start",
            "agent":   "fixer",
            "status":  "fixing",
            "message": f"Fixer Agent: generating patches for {len(issues)} issue(s)…",
        })

        try:
            fixer = FixerAgent()
            fixes = await fixer.run(db, issues, scan_run.id, repo.clone_path, repo.id)
            await db.commit()

            pr.status = "completed"
            pr.completed_at = datetime.now(UTC)
            pr.details = f'{{"fixes_generated": {len(fixes)}}}'
            await db.commit()

            await _broadcast(scan_run.id, {
                "type":    "log",
                "agent":   "fixer",
                "action":  "fix_complete",
                "message": f"Fixer generated {len(fixes)} patch(es).",
            })
            return fixes
        except Exception as exc:
            pr.status = "failed"
            pr.error_message = str(exc)[:1000]
            pr.completed_at = datetime.now(UTC)
            await db.commit()
            raise

    async def _stage_review(self, db, scan_run: ScanRun, fixes: list[Fix]) -> None:
        await scan_controller.checkpoint(scan_run.id)

        if not fixes:
            return

        await _set_status(db, scan_run, "reviewing")

        if await _stage_already_completed(db, scan_run.id, "review"):
            logger.info("[review] Skipping — already completed in a previous attempt")
            await _broadcast(scan_run.id, {
                "type":    "log",
                "agent":   "orchestrator",
                "action":  "skip_stage",
                "message": "⏭ Skipping review stage — already completed in a previous attempt",
            })
            return

        await _delete_failed_stage_rows(db, scan_run.id, "review")
        pr = PipelineRun(
            scan_run_id=scan_run.id,
            stage="review",
            status="running",
            started_at=datetime.now(UTC),
        )
        db.add(pr)
        await db.commit()

        await _broadcast(scan_run.id, {
            "type":    "agent_start",
            "agent":   "reviewer",
            "status":  "reviewing",
            "message": f"Reviewer Agent: validating {len(fixes)} proposed fix(es)…",
        })

        # Re-fetch with eager-loaded issue relationship to avoid async lazy load errors
        fix_ids = [f.id for f in fixes]
        result = await db.execute(
            select(Fix)
            .options(selectinload(Fix.issue))
            .where(Fix.id.in_(fix_ids))
        )
        loaded_fixes = list(result.scalars().all())

        try:
            reviewer = ReviewerAgent()
            await reviewer.run(db, loaded_fixes, scan_run.id)
            await db.commit()

            avg_conf = (
                sum((f.confidence_score or 0) for f in loaded_fixes) / len(loaded_fixes)
                if loaded_fixes else 0
            )
            pr.status = "completed"
            pr.completed_at = datetime.now(UTC)
            pr.details = f'{{"reviewed": {len(loaded_fixes)}, "avg_confidence": {avg_conf:.1f}}}'
            await db.commit()

            await _broadcast(scan_run.id, {
                "type":    "log",
                "agent":   "reviewer",
                "action":  "review_complete",
                "message": f"Reviewer finished. Avg confidence: {avg_conf:.0f}/100.",
            })
        except Exception as exc:
            pr.status = "failed"
            pr.error_message = str(exc)[:1000]
            pr.completed_at = datetime.now(UTC)
            await db.commit()
            raise

    async def _stage_report(self, db, scan_run: ScanRun, repo: Repo) -> None:
        await scan_controller.checkpoint(scan_run.id)
        await _set_status(db, scan_run, "reporting")

        if await _stage_already_completed(db, scan_run.id, "report"):
            logger.info("[report] Skipping — already completed in a previous attempt")
            await _broadcast(scan_run.id, {
                "type":    "log",
                "agent":   "orchestrator",
                "action":  "skip_stage",
                "message": "⏭ Skipping report stage — already completed in a previous attempt",
            })
            return

        await _delete_failed_stage_rows(db, scan_run.id, "report")
        pr = PipelineRun(
            scan_run_id=scan_run.id,
            stage="report",
            status="running",
            started_at=datetime.now(UTC),
        )
        db.add(pr)
        await db.commit()

        await _broadcast(scan_run.id, {
            "type":    "agent_start",
            "agent":   "reporter",
            "status":  "reporting",
            "message": "Reporter Agent: compiling improvement report…",
        })

        # Find a previous completed scan to compare against (if any).
        prev_result = await db.execute(
            select(ScanRun)
            .where(ScanRun.repo_id == repo.id)
            .where(ScanRun.id != scan_run.id)
            .where(ScanRun.status == "completed")
            .order_by(ScanRun.created_at.desc())
            .limit(1)
        )
        before_scan = prev_result.scalar_one_or_none() or scan_run  # self-compare on first run

        try:
            reporter = ReporterAgent()
            await reporter.run(db, before_scan=before_scan, after_scan=scan_run, repo_id=repo.id)
            await db.commit()

            pr.status = "completed"
            pr.completed_at = datetime.now(UTC)
            await db.commit()

            await _broadcast(scan_run.id, {
                "type":    "log",
                "agent":   "reporter",
                "action":  "report_complete",
                "message": "Reporter generated delta report.",
            })
        except Exception as exc:
            pr.status = "failed"
            pr.error_message = str(exc)[:1000]
            pr.completed_at = datetime.now(UTC)
            await db.commit()
            # Reporter failure is non-fatal — keep going.
            logger.warning(f"Reporter stage failed (non-fatal): {exc}")
            await _broadcast(scan_run.id, {
                "type":    "log",
                "agent":   "reporter",
                "message": f"⚠ Reporter failed: {exc}",
            })


orchestrator = PipelineOrchestrator()