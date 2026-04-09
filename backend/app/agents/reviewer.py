"""Reviewer Agent — validates fixes, assigns confidence score 0-100."""

import logging
import re
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.agents.scan_controller import scan_controller
from app.models.fix import Fix
from app.models.observability import AgentLog

logger = logging.getLogger(__name__)


class ReviewerAgent(BaseAgent):
    agent_name = "reviewer"

    async def run(self, db: AsyncSession, fixes: list[Fix], scan_run_id: str) -> list[Fix]:
        for fix in fixes:
            await scan_controller.checkpoint(scan_run_id)
            try:
                await self._review_fix(db, fix, scan_run_id)
            except Exception as e:
                logger.error(f"Reviewer failed for fix {fix.id}: {e}")
                log = AgentLog(
                    agent_name=self.agent_name,
                    scan_run_id=scan_run_id,
                    action="review_fix",
                    status="error",
                    error_message=str(e),
                )
                db.add(log)
        await db.flush()
        return fixes

    async def _review_fix(self, db: AsyncSession, fix: Fix, scan_run_id: str) -> None:
        issue = fix.issue

        # Build prompt
        prompt = self._build_prompt(fix, issue)
        response = await self.llm(
            [{"role": "user", "content": prompt}],
            db=db, scan_run_id=scan_run_id,
        )

        confidence, summary = self._parse_response(response)
        fix.confidence_score = confidence
        fix.reviewer_summary = summary

        # Store memory about confidence patterns
        entity_key = f"rule:{issue.rule_key}" if issue else f"fix:{fix.id}"
        await self.remember(
            db, entity_key, "observation",
            f"Confidence {confidence}/100 for fix of {issue.rule_key if issue else 'unknown'}: {summary[:200]}",
            scan_run_id=scan_run_id,
        )

    def _build_prompt(self, fix: Fix, issue) -> str:
        issue_info = ""
        if issue:
            issue_info = f"""
## Issue Being Fixed
- Rule: {issue.rule_key} ({issue.severity})
- Message: {issue.message}
- File: {issue.component}, Line: {issue.line}
"""
        return f"""You are a senior code reviewer analyzing an AI-generated fix for a SonarQube issue.
{issue_info}
## Original Code
```
{fix.original_code[:3000]}
```

## Proposed Fix (Diff)
```diff
{fix.diff_patch[:2000]}
```

## Fixer's Explanation
{fix.explanation or "No explanation provided"}

## Your Task
Evaluate this fix and respond with EXACTLY this format:

CONFIDENCE: <integer 0-100>
SUMMARY: <2-3 sentences: Does it address the issue? Any regression risk? Is it minimal?>

Scoring guide:
- 90-100: Correct fix, no risks, minimal change
- 70-89: Likely correct, minor concerns
- 50-69: Partially addresses issue or has side-effect risk
- 0-49: Incorrect, introduces bugs, or changes behavior unexpectedly
"""

    def _parse_response(self, response: str) -> tuple[int, str]:
        conf_match = re.search(r"CONFIDENCE:\s*(\d+)", response)
        sum_match = re.search(r"SUMMARY:\s*(.+?)(?:\n\n|\Z)", response, re.DOTALL)
        confidence = int(conf_match.group(1)) if conf_match else 50
        confidence = max(0, min(100, confidence))
        summary = sum_match.group(1).strip() if sum_match else response[:300]
        return confidence, summary
