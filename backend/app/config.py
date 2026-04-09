"""SonarQube Auto-Fix Agent — Application Configuration."""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    secret_key: str = "dev-secret-key-change-in-production"
    database_url: str = "sqlite+aiosqlite:///./app.db"
    environment: str = "development"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # SonarQube
    sonarqube_url: str = "https://sonarqube.ai-launch-pad.com"
    sonarqube_token: str = ""

    # GitHub
    github_default_pat: Optional[str] = None

    # LLM Provider Keys
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None

    # Default agent model assignments (provider/model format)
    scanner_agent_model: str = "openai/gpt-4o"
    fixer_agent_model: str = "google/gemini-1.5-flash"
    reviewer_agent_model: str = "anthropic/claude-sonnet-4-6"
    reporter_agent_model: str = "groq/llama-3.1-70b-versatile"

    # Pipeline timeouts (seconds). The orchestrator wraps the entire pipeline
    # in asyncio.wait_for() with this budget; long-running scans will be
    # cancelled cleanly and marked 'failed' with a timeout error.
    pipeline_total_timeout_seconds: int  = 1200   # 20 min for the whole pipeline
    pipeline_stage_timeout_seconds: int  = 600    # 10 min per stage
    sonar_task_poll_timeout_seconds: int = 600    # 10 min waiting for sonar-scanner background task

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    def get_provider_key(self, provider_name: str) -> Optional[str]:
        """Get the API key for a given provider name."""
        key_map = {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "google": self.google_api_key,
            "groq": self.groq_api_key,
        }
        return key_map.get(provider_name.lower())


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
