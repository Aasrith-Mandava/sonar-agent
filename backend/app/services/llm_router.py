"""LLM Factory: returns LangChain ChatModels mapped from DB and .env to native SDKs."""

import logging
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlalchemy import select

from app.config import get_settings
from app.models.agent_config import AgentConfig

logger = logging.getLogger(__name__)


# Default model per provider, used when an agent has a provider but no model
# row assigned in the DB.
_DEFAULT_MODEL = {
    "openai":    "gpt-4o",
    "anthropic": "claude-sonnet-4-5",
    "google":    "gemini-2.0-flash",
    "groq":      "llama-3.3-70b-versatile",
}


def _get_api_key(provider_name: str) -> Optional[str]:
    """
    Read the provider API key from the application settings (which loads .env
    via pydantic-settings). Reading from os.environ would NOT work because
    pydantic-settings does not export .env values to the process environment.
    """
    s = get_settings()
    return s.get_provider_key(provider_name)


def _resolve_default_for_agent(agent_name: str) -> tuple[Optional[str], Optional[str]]:
    """
    Return (provider, model_id) using the per-agent default from settings
    (e.g. SCANNER_AGENT_MODEL='openai/gpt-4o').
    """
    s = get_settings()
    default = getattr(s, f"{agent_name}_agent_model", None) or "openai/gpt-4o"
    if "/" in default:
        provider, model = default.split("/", 1)
        return provider.strip().lower(), model.strip()
    return "openai", default.strip()


def _build_chat_model(provider: str, model: str, temperature: float = 0.0,
                       max_tokens: Optional[int] = None) -> BaseChatModel:
    """Instantiate the LangChain chat model for the given provider, always
    passing the api_key explicitly so we never depend on os.environ."""
    api_key = _get_api_key(provider)
    if not api_key:
        raise ValueError(
            f"No API key configured for provider '{provider}'. "
            f"Add it under Settings → LLM Providers, or set the appropriate "
            f"*_API_KEY in backend/.env and restart."
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model, temperature=temperature, max_tokens=max_tokens, api_key=api_key,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model, temperature=temperature, max_tokens=max_tokens or 4096, api_key=api_key,
        )

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model, temperature=temperature, max_output_tokens=max_tokens, google_api_key=api_key,
        )

    if provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(
            model=model, temperature=temperature, max_tokens=max_tokens, api_key=api_key,
        )

    raise ValueError(f"Unsupported LLM provider: {provider}")


async def get_agent_llm(agent_name: str, db: AsyncSession) -> BaseChatModel:
    """
    Look up the agent's configured (provider, model) from the database.
    Falls back to the default in settings (e.g. SCANNER_AGENT_MODEL) if either
    the row, the provider, or the model is missing — in every fallback path
    we still pass the api_key explicitly.
    """
    result = await db.execute(
        select(AgentConfig)
        .options(joinedload(AgentConfig.provider), joinedload(AgentConfig.model))
        .where(AgentConfig.agent_name == agent_name)
    )
    config = result.scalar_one_or_none()

    # ── No row at all → use the per-agent default from settings ───────────
    if not config:
        provider, model = _resolve_default_for_agent(agent_name)
        logger.warning(
            f"[llm_router] No AgentConfig row for '{agent_name}'. "
            f"Using default {provider}/{model}."
        )
        return _build_chat_model(provider, model)

    temp = config.temperature if config.temperature is not None else 0.0
    tokens = config.max_tokens

    # ── Provider missing → fall back ──────────────────────────────────────
    if not config.provider:
        provider, model = _resolve_default_for_agent(agent_name)
        logger.warning(
            f"[llm_router] AgentConfig '{agent_name}' has no provider. "
            f"Using default {provider}/{model}."
        )
        return _build_chat_model(provider, model, temp, tokens)

    provider = config.provider.provider_name.lower()

    # ── Provider OK but no model assigned → use the provider's default ────
    if not config.model:
        model = _DEFAULT_MODEL.get(provider, "gpt-4o")
        logger.warning(
            f"[llm_router] AgentConfig '{agent_name}' has provider={provider} "
            f"but no model assigned. Using {provider}/{model} as a default. "
            f"Set one explicitly in Settings → Agent Config to silence this."
        )
        return _build_chat_model(provider, model, temp, tokens)

    # ── Happy path: both provider AND model present ───────────────────────
    return _build_chat_model(provider, config.model.model_id, temp, tokens)