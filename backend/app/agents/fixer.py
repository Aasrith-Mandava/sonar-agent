"""Fixer Agent — reads source, generates LLM fix, produces unified diff patch."""

import difflib
import json
import logging
import re
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.agents.scan_controller import scan_controller
from app.models.observability import AgentLog
from app.models.scan import Issue
from app.models.fix import Fix
from app.services.sonarqube import sonarqube_service

logger = logging.getLogger(__name__)

CONTEXT_LINES = 50


class FixerAgent(BaseAgent):
    agent_name = "fixer"

    async def run(
        self,
        db: AsyncSession,
        issues: list[Issue],
        scan_run_id: str,
        clone_path: str,
        repo_id: str,
    ) -> list[Fix]:
        fixes = []
        for idx, issue in enumerate(issues, start=1):
            # Pause/stop checkpoint between every issue — this is the slowest
            # stage of the pipeline so the user must be able to interrupt it.
            await scan_controller.checkpoint(scan_run_id)

            try:
                logger.info(f"[FIXER] {idx}/{len(issues)} — {issue.component}:{issue.line}")
                fix = await self._fix_issue(db, issue, scan_run_id, clone_path, repo_id)
                if fix:
                    fixes.append(fix)
            except Exception as e:
                logger.error(f"Fixer failed for issue {issue.id}: {e}")
                log = AgentLog(
                    agent_name=self.agent_name,
                    scan_run_id=scan_run_id,
                    action="fix_issue",
                    input_summary=f"issue={issue.id}, file={issue.component}",
                    status="error",
                    error_message=str(e),
                )
                db.add(log)
        await db.flush()
        return fixes

    async def _fix_issue(
        self,
        db: AsyncSession,
        issue: Issue,
        scan_run_id: str,
        clone_path: str,
        repo_id: str,
    ) -> Optional[Fix]:
        file_path = Path(clone_path) / issue.component.lstrip("/")
        if not file_path.exists():
            logger.warning(f"File not found: {file_path}")
            return None

        original_content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = original_content.splitlines()

        # Extract context
        line_no = (issue.line or 1) - 1
        start = max(0, line_no - CONTEXT_LINES)
        end = min(len(lines), line_no + CONTEXT_LINES + 1)
        context = "\n".join(lines[start:end])

        # Recall prior memory for this rule and file
        rule_memory = await self.recall(db, f"rule:{issue.rule_key}")
        file_memory = await self.recall(db, f"repo:{repo_id}:file:{issue.component}")
        memory_ctx = "\n".join(filter(None, [rule_memory, file_memory]))

        # Fetch rule description
        rule = await sonarqube_service.get_rule(issue.rule_key)
        rule_desc = rule.get("htmlDesc", rule.get("name", issue.rule_key))

        prompt = self._build_prompt(issue, context, rule_desc, memory_ctx, start + 1)
        response = await self.llm(
            [{"role": "user", "content": prompt}],
            db=db, scan_run_id=scan_run_id
        )

        fixed_context, explanation = self._parse_response(response)
        if not fixed_context:
            return None

        # Apply fix into full file
        fixed_lines = lines.copy()
        new_lines = fixed_context.splitlines()
        fixed_lines[start:end] = new_lines
        fixed_content = "\n".join(fixed_lines)

        # Generate unified diff
        diff = "".join(difflib.unified_diff(
            original_content.splitlines(keepends=True),
            fixed_content.splitlines(keepends=True),
            fromfile=f"a/{issue.component}",
            tofile=f"b/{issue.component}",
        ))

        fix = Fix(
            issue_id=issue.id,
            scan_run_id=scan_run_id,
            file_path=issue.component,
            original_code=original_content,
            fixed_code=fixed_content,
            diff_patch=diff,
            explanation=explanation,
        )
        db.add(fix)

        # Store memory about this rule
        await self.remember(
            db, f"rule:{issue.rule_key}", "fix_template",
            f"Rule {issue.rule_key} ({issue.severity}): {explanation[:300]}",
            scan_run_id=scan_run_id,
        )
        await db.flush()
        return fix

    def _build_prompt(self, issue: Issue, context: str, rule_desc: str, memory: str, start_line: int) -> str:
        memory_section = f"\n## Prior Agent Memory\n{memory}\n" if memory else ""
        return f"""You are an expert code fixer. Fix the following SonarQube issue with a minimal, targeted change.

## Issue Details
- Severity: {issue.severity}
- Type: {issue.type}  
- Rule: {issue.rule_key}
- Rule Description: {rule_desc}
- File: {issue.component}
- Line: {issue.line}
- Message: {issue.message}
{memory_section}
## Code Context (lines {start_line}+)
```
{context}
```

## Instructions
1. Fix ONLY the specific issue at line {issue.line}
2. Make the minimal change necessary
3. Preserve code style, indentation, and surrounding logic
4. Do NOT add comments explaining the fix
5. Do NOT change unrelated code

## Response Format
Respond with EXACTLY this structure:

FIXED_CODE:
```
<the complete fixed code block replacing the context shown above>
```

EXPLANATION:
<one sentence explaining what was changed and why>
"""

    def _parse_response(self, response: str) -> tuple[str, str]:
        fix_match = re.search(r"FIXED_CODE:\s*```(?:\w+)?\n(.*?)```", response, re.DOTALL)
        exp_match = re.search(r"EXPLANATION:\s*(.+?)(?:\n|$)", response, re.DOTALL)
        code = fix_match.group(1).strip() if fix_match else ""
        explanation = exp_match.group(1).strip() if exp_match else ""
        return code, explanation
