"""FastAPI main application entrypoint."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.database import AsyncSessionLocal, engine, Base
from app.config import get_settings
from app.models.agent_config import AgentConfig
from app.models.llm_provider import LLMProvider
from app.routers import (
    auth, repos, scans, fixes, reviews,
    quality_gates, settings as settings_router, observability, reports
)
from app.websockets import pipeline as ws_pipeline
from app import log_handler as app_log

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Silence very verbose sqlalchemy echo
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
settings = get_settings()


def _export_provider_keys_to_env() -> None:
    """
    Pydantic-settings loads .env into the Settings object but does NOT push
    the values into os.environ. Many third-party SDKs (Anthropic, Google,
    Groq, OpenAI's underlying httpx transport when api_key is omitted) read
    keys from the process environment directly. Export them here so any code
    path that bypasses our llm_router still works.
    """
    import os
    mapping = {
        "OPENAI_API_KEY":     settings.openai_api_key,
        "ANTHROPIC_API_KEY":  settings.anthropic_api_key,
        "GOOGLE_API_KEY":     settings.google_api_key,
        "GOOGLE_GENAI_API_KEY": settings.google_api_key,  # langchain_google_genai
        "GROQ_API_KEY":       settings.groq_api_key,
        "SONARQUBE_URL":      settings.sonarqube_url,
        "SONARQUBE_TOKEN":    settings.sonarqube_token,
        "GITHUB_DEFAULT_PAT": settings.github_default_pat,
    }
    for k, v in mapping.items():
        if v and not os.environ.get(k):
            os.environ[k] = v


_export_provider_keys_to_env()

# Install the broadcast handler so all Python logs are captured
app_log.install_handler()


async def seed_default_data():
    """Seed the 4 default agents and 4 core providers if they do not exist."""
    async with AsyncSessionLocal() as db:
        agent_roles = {
            "scanner": "Triggers SonarQube scans, parses reports, classifies issues by severity, maps them to quality gate thresholds, and selects issues for fixing.",
            "fixer": "Reads source files from the repo, analyzes each selected issue with its surrounding code context, generates minimal targeted code fixes, and produces unified diff patches.",
            "reviewer": "Validates proposed fixes for correctness, checks for potential regressions or side effects, assigns a confidence score (0-100), and generates a human-readable review summary.",
            "reporter": "After re-scan, computes before/after deltas across all severity levels, generates narrative improvement reports, and tracks historical quality trends.",
        }
        for name, role in agent_roles.items():
            result = await db.execute(select(AgentConfig).where(AgentConfig.agent_name == name))
            if not result.scalar_one_or_none():
                db.add(AgentConfig(agent_name=name, agent_role=role))

        providers = [
            {"name": "openai",    "display": "OpenAI",        "env": "OPENAI_API_KEY"},
            {"name": "anthropic", "display": "Anthropic",     "env": "ANTHROPIC_API_KEY"},
            {"name": "google",    "display": "Google Gemini", "env": "GOOGLE_API_KEY"},
            {"name": "groq",      "display": "Groq",          "env": "GROQ_API_KEY"},
        ]
        for pdata in providers:
            result = await db.execute(select(LLMProvider).where(LLMProvider.provider_name == pdata["name"]))
            if not result.scalar_one_or_none():
                db.add(LLMProvider(
                    provider_name=pdata["name"],
                    display_name=pdata["display"],
                    env_key_name=pdata["env"],
                    is_active=True,
                ))

        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting SonarAgent backend…")
    await seed_default_data()
    yield
    logger.info("Shutting down…")
    await engine.dispose()


app = FastAPI(
    title="SonarQube Auto-Fix Agent",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow both Vite dev ports
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(repos.router)
app.include_router(scans.router)
app.include_router(fixes.router)
app.include_router(reviews.router)
app.include_router(quality_gates.router)
app.include_router(settings_router.router)
app.include_router(observability.router)
app.include_router(reports.router)
app.include_router(ws_pipeline.router)


# ── SSE: application log stream ───────────────────────────────────────────────

@app.get("/api/logs/stream")
async def stream_app_logs(request: Request):
    """
    Server-Sent Events stream of Python application logs.
    Replays the last 500 buffered entries, then streams new ones live.
    """
    async def event_generator():
        # Catch-up: send ring buffer
        for entry in app_log.get_ring_snapshot():
            yield f"data: {json.dumps(entry)}\n\n"

        q = app_log.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(entry)}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive comment
                    yield ": keepalive\n\n"
        finally:
            app_log.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
def health_check():
    return {"status": "ok", "environment": settings.environment}
