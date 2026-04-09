"""Reporter Agent — computes before/after deltas, generates narrative summary."""

import json
import logging
from collections import defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.models.observability import AgentLog, DeltaReport
from app.models.scan import Issue, ScanRun

logger = logging.getLogger(__name__)


class ReporterAgent(BaseAgent):
    agent_name = "reporter"

    async def run(
        self,
        db: AsyncSession,
        before_scan: ScanRun,
        after_scan: ScanRun,
        repo_id: str,
    ) -> DeltaReport:
        before_issues = await self._get_issues(db, before_scan.id)
        after_issues = await self._get_issues(db, after_scan.id)

        total_before = len(before_issues)
        total_after = len(after_issues)
        improvement = round((1 - total_after / max(total_before, 1)) * 100, 1) if total_before else 0.0

        # Compute breakdowns
        before_keys = {i.sonar_key for i in before_issues}
        after_keys = {i.sonar_key for i in after_issues}
        fixed_count = len(before_keys - after_keys)
        new_count = len(after_keys - before_keys)

        sev_before = self._breakdown_by_severity(before_issues)
        sev_after = self._breakdown_by_severity(after_issues)
        severity_breakdown = {
            sev: {"before": sev_before.get(sev, 0), "after": sev_after.get(sev, 0)}
            for sev in set(list(sev_before.keys()) + list(sev_after.keys()))
        }

        rule_before = self._breakdown_by_rule(before_issues)
        rule_after = self._breakdown_by_rule(after_issues)
        rule_breakdown = {
            rule: {"before": rule_before.get(rule, 0), "after": rule_after.get(rule, 0)}
            for rule in set(list(rule_before.keys()) + list(rule_after.keys()))
        }

        # Generate narrative via LLM
        memory = await self.recall(db, f"repo:{repo_id}")
        narrative_prompt = self._build_prompt(
            total_before, total_after, fixed_count, new_count,
            severity_breakdown, improvement, memory
        )
        try:
            narrative = await self.llm(
                [{"role": "user", "content": narrative_prompt}],
                db=db, scan_run_id=after_scan.id
            )
        except Exception as e:
            logger.error(f"Reporter LLM failed: {e}")
            narrative = f"Fixed {fixed_count} issues. Total reduced from {total_before} to {total_after} ({improvement}% improvement)."

        # Store memory
        await self.remember(
            db, f"repo:{repo_id}", "summary",
            f"Scan pair {before_scan.id[:8]}→{after_scan.id[:8]}: {improvement}% improvement, {fixed_count} fixed",
            scan_run_id=after_scan.id,
        )

        report = DeltaReport(
            repo_id=repo_id,
            before_scan_id=before_scan.id,
            after_scan_id=after_scan.id,
            total_before=total_before,
            total_after=total_after,
            fixed_count=fixed_count,
            new_count=new_count,
            improvement_pct=improvement,
            severity_breakdown=json.dumps(severity_breakdown),
            rule_breakdown=json.dumps(rule_breakdown),
            summary_narrative=narrative,
        )
        db.add(report)

        # Update after scan status
        after_scan.status = "completed"
        await db.flush()
        logger.info(f"Reporter: {improvement}% improvement, {fixed_count} issues fixed")
        return report

    async def _get_issues(self, db: AsyncSession, scan_run_id: str) -> list[Issue]:
        result = await db.execute(select(Issue).where(Issue.scan_run_id == scan_run_id))
        return result.scalars().all()

    def _breakdown_by_severity(self, issues: list[Issue]) -> dict:
        counts: dict[str, int] = defaultdict(int)
        for i in issues:
            counts[i.severity] += 1
        return dict(counts)

    def _breakdown_by_rule(self, issues: list[Issue]) -> dict:
        counts: dict[str, int] = defaultdict(int)
        for i in issues:
            counts[i.rule_key] += 1
        return dict(counts)

    def _build_prompt(self, total_before, total_after, fixed, new, sev_breakdown, pct, memory) -> str:
        sev_lines = "\n".join(
            f"  {sev}: {v['before']} → {v['after']}" for sev, v in sev_breakdown.items()
        )
        memory_section = f"\nHistorical context:\n{memory}\n" if memory else ""
        return f"""You are a software quality analyst. Write a concise, professional report on code quality improvements.

## Scan Results
- Before: {total_before} issues
- After: {total_after} issues  
- Fixed: {fixed} issues ({pct}% improvement)
- New issues introduced: {new}

## Severity Breakdown
{sev_lines}
{memory_section}
Write a 2-3 paragraph narrative summary covering:
1. Overall improvement achieved
2. Most significant severity reductions
3. Any concerns (new issues, remaining critical items)

Be specific with numbers. Use a professional, actionable tone.
"""
