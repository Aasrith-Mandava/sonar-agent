"""LangGraph Agent Nodes (Scanner, Fixer, Reviewer, Reporter)."""

import json
import logging
import time
from datetime import datetime, UTC
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.prebuilt import ToolNode
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.state import AgentState
from app.services.llm_router import get_agent_llm
from app.agents.mcp_client import mcp_provider
from app.agents.scan_controller import scan_controller, ScanStoppedError
from app.models.observability import AgentLog
from app.models.scan import ScanRun

logger = logging.getLogger(__name__)

# Map agent name → scan_run status string stored in DB
_STATUS_MAP = {
    "scanner":  "scanning",
    "fixer":    "fixing",
    "reviewer": "reviewing",
    "reporter": "reporting",
}

_STEP_LABELS = {
    "scanner":  "Scanner Agent is querying SonarQube and analysing issues…",
    "fixer":    "Fixer Agent is reading source files and generating patches…",
    "reviewer": "Reviewer Agent is validating proposed fixes…",
    "reporter": "Reporter Agent is compiling the improvement report…",
}

_AGENT_COLORS = {
    "scanner":  "blue",
    "fixer":    "orange",
    "reviewer": "purple",
    "reporter": "green",
}


async def _broadcast(scan_run_id: str, payload: dict) -> None:
    """Broadcast to both the per-scan pipeline WS and the global agent-log WS."""
    try:
        from app.websockets.pipeline import manager
        await manager.broadcast_pipeline(scan_run_id, payload)
        # Also fan-out to global log stream
        await manager.broadcast_log(payload)
    except Exception as exc:
        logger.debug(f"WS broadcast skipped: {exc}")


async def create_supervisor_node(db: AsyncSession):
    async def supervisor(state: AgentState):
        """Rule-based supervisor: routes between agents based on state."""
        current = state.get("current_agent", "start")

        if current in ("start", ""):
            logger.info("[Supervisor] Starting pipeline → routing to Scanner")
            return {"current_agent": "scanner"}

        messages = state.get("messages", [])
        last_message = messages[-1] if messages else None
        content = (
            last_message.content.lower()
            if last_message and hasattr(last_message, "content")
            else ""
        )

        if current == "scanner":
            next_agent = "fixer" if state.get("issues_queue") else "reporter"
            logger.info(f"[Supervisor] scanner done → {next_agent}")
            return {"current_agent": next_agent}

        if current == "fixer":
            logger.info("[Supervisor] fixer done → reviewer")
            return {"current_agent": "reviewer"}

        if current == "reviewer":
            if "reject" in content and state.get("revision_count", 0) < 3:
                rev = state.get("revision_count", 0) + 1
                logger.info(f"[Supervisor] reviewer rejected — revision #{rev} → fixer")
                return {"current_agent": "fixer", "revision_count": rev}
            logger.info("[Supervisor] reviewer approved → reporter")
            return {"current_agent": "reporter"}

        logger.info(f"[Supervisor] {current} done → END")
        return {"current_agent": "end"}

    return supervisor


async def create_agent_node(agent_name: str, system_prompt: str, db: AsyncSession):
    async def agent_node(state: AgentState):
        scan_run_id = state["scan_run_id"]
        t_start = time.monotonic()

        # ── 0. Pause / stop checkpoint ────────────────────────────────────
        if scan_controller.is_paused(scan_run_id):
            await _broadcast(scan_run_id, {
                "type":    "paused",
                "agent":   agent_name,
                "status":  "paused",
                "message": f"Pipeline paused before {agent_name} agent. Waiting for resume…",
                "ts":      datetime.now(UTC).isoformat(),
            })
        await scan_controller.checkpoint(scan_run_id)  # raises ScanStoppedError if stopped

        db_status = _STATUS_MAP.get(agent_name, agent_name)

        # ── 1. Update ScanRun status in DB ────────────────────────────────
        try:
            scan_run = await db.get(ScanRun, scan_run_id)
            if scan_run:
                scan_run.status = db_status
                await db.commit()
        except Exception as exc:
            logger.warning(f"Could not update scan_run status: {exc}")

        # ── 2. Broadcast agent-start ──────────────────────────────────────
        logger.info(f"[{agent_name.upper()}] Starting — scan_run={scan_run_id}")
        await _broadcast(scan_run_id, {
            "type":    "agent_start",
            "agent":   agent_name,
            "status":  db_status,
            "message": _STEP_LABELS.get(agent_name, f"{agent_name.title()} Agent running…"),
            "ts":      datetime.now(UTC).isoformat(),
        })

        # ── 3. Invoke LLM ─────────────────────────────────────────────────
        llm = await get_agent_llm(agent_name, db)
        tools = mcp_provider.get_tools()
        llm_with_tools = llm.bind_tools(tools)

        full_system_prompt = (
            f"{system_prompt}\n\n"
            "## OPERATIONAL GUIDELINES\n"
            f"- Current Active Repository Path: {state['clone_path']}\n"
            "- You HAVE direct access to MCP tools. Use them to investigate before assuming state.\n"
            "- Always look at the repository map provided in the chat history.\n"
            "- REASONING: Before calling a tool, briefly state your rationale.\n"
        )
        sys_msg = SystemMessage(content=full_system_prompt)
        response = await llm_with_tools.ainvoke([sys_msg] + list(state["messages"]))

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        # ── 4. Build output payload ───────────────────────────────────────
        action_name   = "agent_reasoning"
        output_summary = ""

        if hasattr(response, "content") and response.content:
            if isinstance(response.content, list):
                # Some models return a list of content blocks
                output_summary = " ".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in response.content
                )
            else:
                output_summary = str(response.content)

        tool_calls_info = []
        if hasattr(response, "tool_calls") and response.tool_calls:
            action_name = "tool_invocation_plan"
            for tc in response.tool_calls:
                tool_calls_info.append({
                    "name": tc["name"],
                    "args": tc["args"],
                })
            tool_lines = [
                f"Tool: {tc['name']}  Args: {json.dumps(tc['args'])}"
                for tc in response.tool_calls
            ]
            output_summary = (output_summary + "\n" + "\n".join(tool_lines)).strip()

        # ── 5. Broadcast reasoning / tool-plan ───────────────────────────
        log_snippet = (output_summary[:600].strip()) if output_summary else "(no output)"
        payload: dict = {
            "type":       "log",
            "agent":      agent_name,
            "action":     action_name,
            "message":    log_snippet,
            "elapsed_ms": elapsed_ms,
            "ts":         datetime.now(UTC).isoformat(),
        }
        if tool_calls_info:
            payload["tool_calls"] = tool_calls_info

        logger.info(f"[{agent_name.upper()}] {action_name} — {log_snippet[:120]}")
        await _broadcast(scan_run_id, payload)

        # ── 6. Persist to AgentLog ────────────────────────────────────────
        log_entry = AgentLog(
            agent_name=agent_name,
            scan_run_id=scan_run_id,
            action=action_name,
            input_summary=f"Context: {len(state['messages'])} messages in history.",
            output_summary=output_summary[:2000],
            status="success",
        )
        db.add(log_entry)
        await db.commit()

        return {"messages": [response], "current_agent": agent_name}

    return agent_node


# ── Custom ToolNode with broadcast ────────────────────────────────────────────

class BroadcastingToolNode:
    """Wraps LangGraph ToolNode and broadcasts tool results to WebSocket."""

    def __init__(self, tools):
        self._node = ToolNode(tools)

    async def __call__(self, state: AgentState):
        result_state = await self._node.ainvoke(state)
        scan_run_id = state.get("scan_run_id")

        if scan_run_id:
            messages = result_state.get("messages", [])
            for msg in messages:
                if isinstance(msg, ToolMessage):
                    content_str = str(msg.content)[:500] if msg.content else "(empty)"
                    tool_name   = getattr(msg, "name", "unknown_tool")
                    logger.info(f"[TOOL] {tool_name} → {content_str[:100]}")
                    try:
                        from app.websockets.pipeline import manager
                        payload = {
                            "type":    "tool_result",
                            "agent":   "tools",
                            "action":  "tool_result",
                            "tool":    tool_name,
                            "message": content_str,
                            "ts":      datetime.now(UTC).isoformat(),
                        }
                        await manager.broadcast_pipeline(scan_run_id, payload)
                        await manager.broadcast_log(payload)
                    except Exception as exc:
                        logger.debug(f"Tool result broadcast skipped: {exc}")

        return result_state


def create_tool_node():
    return BroadcastingToolNode(mcp_provider.get_tools())
