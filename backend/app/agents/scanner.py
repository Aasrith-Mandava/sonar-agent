"""Scanner Agent — triggers SonarQube scan, parses issues, selects for fixing."""

import json
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.agents.scan_controller import scan_controller
from app.models.observability import AgentLog
from app.models.scan import Issue, ScanRun
from app.services.sonarqube import sonarqube_service

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"BLOCKER": 0, "CRITICAL": 1, "MAJOR": 2, "MINOR": 3, "INFO": 4}


class ScannerAgent(BaseAgent):
    agent_name = "scanner"

    async def run(
        self,
        db: AsyncSession,
        scan_run: ScanRun,
        repo,
        quality_gate=None,
    ) -> list[Issue]:
        """
        Full scan pipeline:
        1. Trigger sonar-scanner CLI
        2. Poll for task completion
        3. Fetch issues, filter by quality gate
        4. Store in DB, select top N for fixing
        """
        project_key = repo.sonar_project_key
        clone_path = repo.clone_path

        log = AgentLog(
            agent_name=self.agent_name,
            scan_run_id=scan_run.id,
            action="trigger_scan",
            input_summary=f"project={project_key}, clone={clone_path}",
        )
        db.add(log)

        try:
            await scan_controller.checkpoint(scan_run.id)

            # Ensure project exists (non-fatal if token lacks perms)
            await sonarqube_service.create_project(project_key, repo.name)
            await scan_controller.checkpoint(scan_run.id)

            # Run scanner (only if clone exists AND CLI is installed)
            task_id = ""
            if clone_path and sonarqube_service.is_scanner_available():
                task_id = sonarqube_service.trigger_scan(clone_path, project_key, repo.name)
                scan_run.sonar_task_id = task_id
                await db.flush()

                if task_id:
                    await scan_controller.checkpoint(scan_run.id)
                    success = await sonarqube_service.wait_for_task(task_id)
                    if not success:
                        raise RuntimeError("SonarQube background task did not finish successfully")
            else:
                logger.info(
                    f"Skipping local sonar-scanner run for {project_key} "
                    f"(clone={'yes' if clone_path else 'no'}, "
                    f"cli={'yes' if sonarqube_service.is_scanner_available() else 'no'}). "
                    f"Will fetch existing issues via REST API."
                )

            await scan_controller.checkpoint(scan_run.id)

            # Fetch issues
            min_sev = quality_gate.min_severity if quality_gate else "MAJOR"
            max_n = quality_gate.max_issues_per_run if quality_gate else 20
            file_excl = json.loads(quality_gate.file_exclusions) if quality_gate else []
            rule_excl = json.loads(quality_gate.rule_exclusions) if quality_gate else []

            raw_issues = await sonarqube_service.get_issues(project_key, statuses=["OPEN"])

            # If we got 0 issues AND we couldn't run a local scan, that's
            # almost certainly a setup problem — surface it loudly instead of
            # letting the pipeline silently complete with no fixes.
            if not raw_issues and not task_id:
                if not sonarqube_service.is_scanner_available():
                    raise RuntimeError(
                        f"SonarQube returned 0 issues for project '{project_key}' "
                        f"and the local sonar-scanner CLI is NOT installed, so we "
                        f"could not produce any. Install it with "
                        f"'brew install sonar-scanner' (macOS) and re-run the scan. "
                        f"Alternatively, ensure the project '{project_key}' has been "
                        f"previously scanned in SonarQube and that your token has "
                        f"'Browse' permission on it."
                    )
                logger.warning(
                    f"Project '{project_key}' returned 0 issues from REST. "
                    f"Either it has no issues, or your token can't read it."
                )

            issues = []
            for raw in raw_issues:
                sev = raw.get("severity", "INFO")
                if SEVERITY_ORDER.get(sev, 99) > SEVERITY_ORDER.get(min_sev, 99):
                    continue
                comp = raw.get("component", "")
                if any(pat in comp for pat in file_excl):
                    continue
                if raw.get("rule") in rule_excl:
                    continue

                issue = Issue(
                    scan_run_id=scan_run.id,
                    sonar_key=raw.get("key", ""),
                    severity=sev,
                    type=raw.get("type", "CODE_SMELL"),
                    rule_key=raw.get("rule", ""),
                    rule_name=raw.get("message", "")[:200],
                    component=comp.split(":")[-1] if ":" in comp else comp,
                    line=raw.get("line"),
                    message=raw.get("message"),
                    effort=raw.get("effort"),
                )
                issues.append(issue)
                db.add(issue)

            await db.flush()

            # Sort by severity and select top N
            issues.sort(key=lambda x: SEVERITY_ORDER.get(x.severity, 99))
            selected = issues[:max_n]

            # Use LLM to rank if over limit — store result in memory
            if len(issues) > max_n:
                memory = await self.recall(db, f"repo:{repo.id}", "pattern")
                ranking_prompt = self._build_ranking_prompt(issues, max_n, memory)
                ranking_response = await self.llm(
                    [{"role": "user", "content": ranking_prompt}],
                    db=db, scan_run_id=scan_run.id
                )
                selected = self._parse_ranking(ranking_response, issues, max_n)
                await self.remember(db, f"repo:{repo.id}", "pattern",
                    f"LLM selected {max_n} out of {len(issues)} issues for scan {scan_run.id}",
                    scan_run_id=scan_run.id)

            for issue in selected:
                issue.selected_for_fix = True

            # Update scan summary
            by_sev: dict[str, int] = {}
            for i in issues:
                by_sev[i.severity] = by_sev.get(i.severity, 0) + 1
            scan_run.total_issues = len(issues)
            scan_run.issues_by_severity = json.dumps(by_sev)

            log.output_summary = f"Found {len(issues)} issues, selected {len(selected)} for fix"
            log.status = "success"
            await db.flush()

            logger.info(f"Scanner: {len(issues)} issues found, {len(selected)} selected")
            return selected

        except Exception as e:
            log.status = "error"
            log.error_message = str(e)
            await db.flush()
            raise

    def _build_ranking_prompt(self, issues: list[Issue], max_n: int, prior_memory: str) -> str:
        issue_list = "\n".join(
            f"{i+1}. [{iss.severity}] {iss.type} - {iss.rule_key} in {iss.component}:{iss.line} - {iss.message}"
            for i, iss in enumerate(issues[:50])
        )
        memory_section = f"\nPrior context:\n{prior_memory}\n" if prior_memory else ""
        return (
            f"You are a code quality expert. Select the {max_n} most impactful issues to fix from this list."
            f"{memory_section}"
            f"Prioritize: security vulnerabilities > bugs > code smells. Prefer fixable issues.\n\n"
            f"Issues:\n{issue_list}\n\n"
            f"Respond with ONLY a comma-separated list of issue numbers (1-indexed): e.g. 1,3,5,7"
        )

    def _parse_ranking(self, response: str, issues: list[Issue], max_n: int) -> list[Issue]:
        try:
            indices = [int(x.strip()) - 1 for x in response.split(",") if x.strip().isdigit()]
            selected = [issues[i] for i in indices if 0 <= i < len(issues)]
            return selected[:max_n]
        except Exception:
            return issues[:max_n]
