"""Settings router: LLM Providers and Agent configs using .env context."""

import os
import re
from typing import Annotated, List, Optional
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import get_db
from app.middleware.auth import get_current_user, require_admin
from app.models.user import User
from app.models.llm_provider import LLMProvider, LLMModel
from app.models.agent_config import AgentConfig
from app.schemas.settings import (
    ProviderCreate,
    ProviderResponse,
    ModelResponse,
    AgentConfigUpdate,
    AgentConfigResponse,
)

from app.config import get_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])
settings = get_settings()

class EnvUpdate(BaseModel):
    env_key: str
    env_value: str

# ----------------- Providers -----------------

@router.get("/providers", response_model=List[ProviderResponse])
async def list_providers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(LLMProvider).order_by(LLMProvider.created_at))
    providers = result.scalars().all()
    
    # Calculate model counts and check if key exists in env
    for p in providers:
        p.model_count = await db.scalar(select(func.count(LLMModel.id)).where(LLMModel.provider_id == p.id)) or 0
        
    return providers

@router.post("/providers", response_model=ProviderResponse)
async def add_provider(
    body: ProviderCreate,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(LLMProvider).where(LLMProvider.provider_name == body.provider_name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Provider already exists")

    env_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "groq": "GROQ_API_KEY",
        "azure_openai": "AZURE_OPENAI_API_KEY",
    }
    
    provider = LLMProvider(
        provider_name=body.provider_name,
        display_name=body.display_name or body.provider_name.title(),
        base_url=body.base_url,
        env_key_name=env_map.get(body.provider_name),
    )
    db.add(provider)
    await db.flush()
    await db.refresh(provider)
    
    provider.model_count = 0
    return provider

@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: str,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    provider = await db.get(LLMProvider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    await db.delete(provider)
    await db.commit()

@router.post("/providers/{provider_id}/fetch-models")
async def provider_fetch_models(
    provider_id: str,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.services.model_fetcher import model_fetcher
    
    provider = await db.get(LLMProvider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    settings = get_settings()
    api_key = settings.get_provider_key(provider.provider_name)
    
    if not api_key:
        raise HTTPException(status_code=400, detail=f"API Key for {provider.provider_name} missing in .env")

    models_data = []
    p_name = provider.provider_name.lower()
    
    if p_name == "openai":
        models_data = await model_fetcher.fetch_openai_models(api_key)
    elif p_name == "anthropic":
        models_data = await model_fetcher.fetch_anthropic_models(api_key)
    elif p_name == "google":
        models_data = await model_fetcher.fetch_gemini_models(api_key)
    elif p_name == "groq":
        models_data = await model_fetcher.fetch_groq_models(api_key)

    if not models_data:
        provider.is_connected = False
        await db.commit()
        raise HTTPException(status_code=400, detail="Failed to fetch models from provider API.")

    provider.is_connected = True
    
    # Empty existing models and sync
    await db.execute(select(LLMModel).where(LLMModel.provider_id == provider.id))
    # Note: deletion might be safer via session or explicit query
    from sqlalchemy import delete
    await db.execute(delete(LLMModel).where(LLMModel.provider_id == provider.id))
    
    for mdata in models_data:
        m = LLMModel(
            provider_id=provider.id,
            model_id=mdata["model_id"],
            model_name=mdata["display_name"],
            is_available=True
        )
        db.add(m)
        
    await db.commit()
    return {"message": f"Successfully synced {len(models_data)} models for {provider.display_name}"}

@router.get("/providers/{provider_id}/models", response_model=List[ModelResponse])
async def list_models(
    provider_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(LLMModel).where(LLMModel.provider_id == provider_id))
    return result.scalars().all()


# ----------------- Env updating (.env) -----------------

def _update_env_file(key: str, value: str):
    env_path = ".env"
    if not os.path.exists(env_path):
        with open(env_path, "w") as f:
            f.write(f"{key}={value}\n")
        return
        
    with open(env_path, "r") as f:
        lines = f.readlines()
        
    found = False
    with open(env_path, "w") as f:
        for line in lines:
            if line.startswith(f"{key}="):
                f.write(f"{key}={value}\n")
                found = True
            else:
                f.write(line)
        if not found:
            f.write(f"{key}={value}\n")

@router.post("/env", status_code=200)
async def update_env_variable(
    body: EnvUpdate,
    current_user: Annotated[User, Depends(require_admin)],
):
    _update_env_file(body.env_key, body.env_value)
    # Hot-update the in-memory settings singleton
    if hasattr(settings, body.env_key.lower()):
        setattr(settings, body.env_key.lower(), body.env_value)
    # Also push into os.environ so any SDK that reads the process env
    # picks up the new value without a backend restart.
    os.environ[body.env_key] = body.env_value
    return {"message": f"Updated {body.env_key}"}


# ----------------- SonarQube credentials -----------------

class SonarQubeConfig(BaseModel):
    sonarqube_url: str
    sonarqube_token: str  # write-only on PUT; on GET it's masked


def _mask(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "•" * len(token)
    return token[:4] + "•" * (len(token) - 8) + token[-4:]


@router.get("/sonarqube")
async def get_sonarqube_config(
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Return the currently-configured SonarQube URL + a masked token preview."""
    s = get_settings()
    return {
        "sonarqube_url":   s.sonarqube_url,
        "sonarqube_token": _mask(s.sonarqube_token or ""),
        "configured":      bool(s.sonarqube_token),
        "scanner_cli_installed": _sonar_scanner_installed(),
    }


@router.put("/sonarqube")
async def update_sonarqube_config(
    body: SonarQubeConfig,
    current_user: Annotated[User, Depends(require_admin)],
):
    """
    Persist SonarQube URL + token to .env AND hot-update the in-memory settings
    singleton so the next scan picks it up without a backend restart.
    """
    url = body.sonarqube_url.strip().rstrip("/")
    token = body.sonarqube_token.strip()
    if not url:
        raise HTTPException(status_code=400, detail="sonarqube_url is required")
    if not token:
        raise HTTPException(status_code=400, detail="sonarqube_token is required")

    _update_env_file("SONARQUBE_URL", url)
    _update_env_file("SONARQUBE_TOKEN", token)

    settings.sonarqube_url = url
    settings.sonarqube_token = token

    # Push into process env too, in case anything reads it directly
    os.environ["SONARQUBE_URL"] = url
    os.environ["SONARQUBE_TOKEN"] = token

    # Refresh the live SonarQubeService singleton
    from app.services.sonarqube import sonarqube_service
    sonarqube_service.base_url = url
    sonarqube_service.token = token

    return {"message": "SonarQube credentials updated", "sonarqube_url": url}


@router.post("/sonarqube/test")
async def test_sonarqube_connection(
    current_user: Annotated[User, Depends(get_current_user)],
):
    """
    Live-test the configured SonarQube credentials by calling /api/authentication/validate.
    Returns {ok: bool, valid: bool, user: ..., message: ...}.
    """
    import httpx
    s = get_settings()
    if not s.sonarqube_token:
        return {
            "ok":      False,
            "valid":   False,
            "message": "No SonarQube token configured. Set one in Settings → SonarQube.",
        }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{s.sonarqube_url.rstrip('/')}/api/authentication/validate",
                auth=(s.sonarqube_token, ""),
            )
        if resp.status_code != 200:
            return {
                "ok":      False,
                "valid":   False,
                "message": f"SonarQube returned HTTP {resp.status_code}: {resp.text[:200]}",
            }

        valid = resp.json().get("valid", False)
        if not valid:
            return {
                "ok":      True,
                "valid":   False,
                "message": "SonarQube reachable but token is INVALID. Generate a new User token in SonarQube → My Account → Security.",
            }

        # Also fetch current user to confirm permissions
        async with httpx.AsyncClient(timeout=10) as client:
            user_resp = await client.get(
                f"{s.sonarqube_url.rstrip('/')}/api/users/current",
                auth=(s.sonarqube_token, ""),
            )
        user = user_resp.json() if user_resp.status_code == 200 else {}

        return {
            "ok":      True,
            "valid":   True,
            "user":    user.get("login") or user.get("name"),
            "permissions": user.get("permissions", {}).get("global", []),
            "scanner_cli_installed": _sonar_scanner_installed(),
            "message": f"Connected as {user.get('login') or user.get('name', 'unknown')}",
        }
    except httpx.HTTPError as exc:
        return {
            "ok":      False,
            "valid":   False,
            "message": f"Connection failed: {exc}",
        }


def _sonar_scanner_installed() -> bool:
    import shutil
    return shutil.which("sonar-scanner") is not None


# ----------------- Agents -----------------

@router.get("/agents", response_model=List[AgentConfigResponse])
async def list_agents(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(AgentConfig)
        .options(joinedload(AgentConfig.provider), joinedload(AgentConfig.model))
        .order_by(AgentConfig.agent_name)
    )
    agents = result.scalars().all()
    # add computed fields
    for a in agents:
        a.provider_name = a.provider.provider_name if a.provider else None
        a.model_name = a.model.model_id if a.model else None
    return agents

@router.put("/agents/{agent_id}", response_model=AgentConfigResponse)
async def update_agent(
    agent_id: str,
    body: AgentConfigUpdate,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await db.get(AgentConfig, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if body.provider_id is not None:
        agent.provider_id = body.provider_id
    if body.model_id is not None:
        agent.model_id = body.model_id
    if body.temperature is not None:
        agent.temperature = body.temperature
    if body.max_tokens is not None:
        agent.max_tokens = body.max_tokens
    if body.system_prompt_override is not None:
        agent.system_prompt_override = body.system_prompt_override

    await db.commit()
    
    # Reload with relations
    result = await db.execute(
        select(AgentConfig)
        .options(joinedload(AgentConfig.provider), joinedload(AgentConfig.model))
        .where(AgentConfig.id == agent.id)
    )
    agent_reloaded = result.scalar_one()
    agent_reloaded.provider_name = agent_reloaded.provider.provider_name if agent_reloaded.provider else None
    agent_reloaded.model_name = agent_reloaded.model.model_id if agent_reloaded.model else None
    
    return agent_reloaded
