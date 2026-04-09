import logging
import httpx
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class ModelFetcher:
    """Service to fetch available models from various LLM provider APIs."""

    @staticmethod
    async def fetch_openai_models(api_key: str) -> List[Dict[str, Any]]:
        """Fetch models from OpenAI API."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"}
                )
                if resp.status_code != 200:
                    logger.error(f"OpenAI Models API error: {resp.text}")
                    return []
                
                data = resp.json()
                # Filter for chat models usually starting with gpt-
                return [
                    {"model_id": m["id"], "display_name": m["id"]}
                    for m in data.get("data", [])
                    if m["id"].startswith("gpt-") or "-gpt-" in m["id"]
                ]
        except Exception as e:
            logger.error(f"Failed to fetch OpenAI models: {e}")
            return []

    @staticmethod
    async def fetch_anthropic_models(api_key: str) -> List[Dict[str, Any]]:
        """Fetch models from Anthropic API with Sonnet 4.6 fallback."""
        fallback = [{"model_id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6 (Fallback)"}]
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01"
                    }
                )
                if resp.status_code != 200:
                    logger.warning(f"Anthropic Models API error: {resp.text}. Using fallback.")
                    return fallback
                
                data = resp.json()
                models = [
                    {"model_id": m["id"], "display_name": m.get("display_name", m["id"])}
                    for m in data.get("data", [])
                ]
                return models if models else fallback
        except Exception as e:
            logger.error(f"Failed to fetch Anthropic models: {e}")
            return fallback

    @staticmethod
    async def fetch_gemini_models(api_key: str) -> List[Dict[str, Any]]:
        """Fetch models from Google Gemini API."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                )
                if resp.status_code != 200:
                    logger.error(f"Gemini Models API error: {resp.text}")
                    return []
                
                data = resp.json()
                return [
                    {"model_id": m["name"].split("/")[-1], "display_name": m["displayName"]}
                    for m in data.get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])
                ]
        except Exception as e:
            logger.error(f"Failed to fetch Gemini models: {e}")
            return []

    @staticmethod
    async def fetch_groq_models(api_key: str) -> List[Dict[str, Any]]:
        """Fetch models from Groq API."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"}
                )
                if resp.status_code != 200:
                    logger.error(f"Groq Models API error: {resp.text}")
                    return []
                
                data = resp.json()
                return [
                    {"model_id": m["id"], "display_name": m["id"]}
                    for m in data.get("data", [])
                ]
        except Exception as e:
            logger.error(f"Failed to fetch Groq models: {e}")
            return []

model_fetcher = ModelFetcher()
