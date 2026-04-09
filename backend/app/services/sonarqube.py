"""SonarQube REST API client service."""

import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class SonarQubeService:
    def __init__(self):
        self.base_url = settings.sonarqube_url.rstrip("/")
        self.token = settings.sonarqube_token

    @property
    def _auth(self):
        return (self.token, "")

    async def _get(self, path: str, params: dict = None) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.base_url}{path}", params=params, auth=self._auth)
            resp.raise_for_status()
            return resp.json()

    async def _post(self, path: str, data: dict = None) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self.base_url}{path}", data=data, auth=self._auth)
            resp.raise_for_status()
            return resp.json()

    async def create_project(self, project_key: str, name: str) -> dict:
        """
        Try to create the SonarQube project. If the token lacks 'Create Project'
        permission (401/403), or the project already exists (400), this is NOT
        a fatal error — the project may already exist on the server, in which
        case we can still fetch issues from it. Return {} on any expected
        failure and let the caller proceed.
        """
        try:
            return await self._post("/api/projects/create", {"project": project_key, "name": name})
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code == 400:
                logger.info(f"SonarQube project {project_key} already exists")
                return {}
            if code in (401, 403):
                logger.warning(
                    f"SonarQube token lacks 'Create Project' permission "
                    f"({code} on /api/projects/create). Will assume project "
                    f"'{project_key}' already exists and proceed to fetch issues."
                )
                return {}
            logger.error(f"SonarQube create_project failed ({code}): {e.response.text[:300]}")
            raise

    def _write_sonar_properties(self, clone_path: str, project_key: str, project_name: str) -> str:
        props = (
            f"sonar.projectKey={project_key}\n"
            f"sonar.projectName={project_name}\n"
            f"sonar.sources=.\n"
            f"sonar.host.url={self.base_url}\n"
            f"sonar.token={self.token}\n"
            f"sonar.scm.disabled=true\n"
        )
        props_path = Path(clone_path) / "sonar-project.properties"
        props_path.write_text(props)
        return str(props_path)

    def is_scanner_available(self) -> bool:
        """Return True if the sonar-scanner CLI is on PATH."""
        return shutil.which("sonar-scanner") is not None

    def trigger_scan(self, clone_path: str, project_key: str, project_name: str) -> str:
        """
        Run sonar-scanner CLI if available and return the task ID.
        If sonar-scanner is NOT installed locally, log a warning and return ""
        — the caller should then fall back to the SonarQube REST API to fetch
        whichever issues already exist for the project.
        """
        if not self.is_scanner_available():
            logger.warning(
                "sonar-scanner CLI not found on PATH — skipping local scan and "
                "falling back to fetching existing issues from SonarQube REST API."
            )
            return ""

        self._write_sonar_properties(clone_path, project_key, project_name)
        try:
            result = subprocess.run(
                ["sonar-scanner", f"-Dproject.settings={clone_path}/sonar-project.properties"],
                cwd=clone_path,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"sonar-scanner timed out after 600s: {exc}")
        except FileNotFoundError:
            logger.warning("sonar-scanner disappeared mid-execution — falling back to REST")
            return ""

        logger.info(f"sonar-scanner stdout (tail): {result.stdout[-1500:]}")
        if result.returncode != 0:
            raise RuntimeError(f"sonar-scanner failed: {result.stderr[-1000:]}")

        # Parse task ID from output
        for line in result.stdout.splitlines():
            if "ceTaskId=" in line:
                return line.split("ceTaskId=")[-1].strip()
        # Fallback: check report-task.txt
        report_file = Path(clone_path) / ".scannerwork" / "report-task.txt"
        if report_file.exists():
            for line in report_file.read_text().splitlines():
                if line.startswith("ceTaskId="):
                    return line.split("=", 1)[1].strip()
        return ""

    async def get_task_status(self, task_id: str) -> dict:
        return await self._get("/api/ce/task", {"id": task_id})

    async def wait_for_task(self, task_id: str, timeout: int = 300) -> bool:
        """Poll until task is SUCCESS or FAILED."""
        for _ in range(timeout // 5):
            data = await self.get_task_status(task_id)
            task = data.get("task", {})
            status = task.get("status", "")
            if status == "SUCCESS":
                return True
            if status in ("FAILED", "CANCELLED"):
                return False
            await asyncio.sleep(5)
        return False

    async def get_issues(
        self,
        project_key: str,
        severities: list[str] | None = None,
        statuses: list[str] | None = None,
        page_size: int = 500,
        max_pages: int = 20,
    ) -> list[dict]:
        """
        Fetch issues for a project. Returns [] (with a warning) if the project
        does not exist or the token cannot read it — never raises on 401/403/404.
        """
        issues: list[dict] = []
        page = 1
        try:
            while page <= max_pages:
                params = {"componentKeys": project_key, "ps": page_size, "p": page}
                if severities:
                    params["severities"] = ",".join(severities)
                if statuses:
                    params["statuses"] = ",".join(statuses)
                data = await self._get("/api/issues/search", params)
                issues.extend(data.get("issues", []))
                total = data.get("paging", {}).get("total", 0)
                if len(issues) >= total:
                    break
                page += 1
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (401, 403, 404):
                logger.warning(
                    f"SonarQube /api/issues/search returned {code} for "
                    f"project '{project_key}'. Returning empty issue list. "
                    f"Body: {exc.response.text[:200]}"
                )
                return []
            raise
        return issues

    async def get_quality_gate_status(self, project_key: str) -> dict:
        return await self._get("/api/qualitygates/project_status", {"projectKey": project_key})

    async def get_rule(self, rule_key: str) -> dict:
        try:
            data = await self._get("/api/rules/show", {"key": rule_key})
            return data.get("rule", {})
        except Exception:
            return {}

    async def delete_project(self, project_key: str) -> bool:
        """
        Delete a SonarQube project (and all its scan history). Returns True on
        success, False if the token lacks permission or the project does not
        exist. Never raises — deletion is best-effort cleanup.
        """
        try:
            await self._post("/api/projects/delete", {"project": project_key})
            logger.info(f"SonarQube project '{project_key}' deleted")
            return True
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code == 404:
                logger.info(f"SonarQube project '{project_key}' did not exist (already deleted?)")
                return True
            logger.warning(
                f"Could not delete SonarQube project '{project_key}' "
                f"(HTTP {code}): {exc.response.text[:200]}"
            )
            return False
        except Exception as exc:
            logger.warning(f"Could not delete SonarQube project '{project_key}': {exc}")
            return False

    async def cancel_task(self, task_id: str) -> bool:
        """Best-effort cancellation of a running CE task. Never raises."""
        if not task_id:
            return False
        try:
            await self._post("/api/ce/cancel", {"id": task_id})
            logger.info(f"SonarQube CE task {task_id} cancelled")
            return True
        except Exception as exc:
            logger.debug(f"Could not cancel CE task {task_id}: {exc}")
            return False


sonarqube_service = SonarQubeService()
