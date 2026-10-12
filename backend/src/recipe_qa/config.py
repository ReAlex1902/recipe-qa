from datetime import datetime
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    """All runtime configuration. Values come from the environment (or a local .env)."""

    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"), extra="ignore"
    )

    google_api_key: SecretStr

    # Fallback chain, comma-separated, cheapest first (ADR-0002).
    llm_models: str = (
        "gemini-2.5-flash-lite,gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-2.5-flash"
    )
    llm_temperature: float = 0.0
    llm_max_output_tokens: int = 2048
    llm_timeout_s: float = 20.0
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768

    top_k: int = 5
    corpus_path: Path = BACKEND_DIR / "data" / "corpus.json"
    static_dir: Path = REPO_DIR / "frontend" / "dist"

    cache_size: int = 512
    cache_ttl_s: float = 24 * 3600

    rate_limit_per_minute: int = 10
    daily_llm_budget: int = 2000
    # Number of reverse proxies in front of the app that append to X-Forwarded-For.
    # 0 = use the socket peer address (local / tests).
    trusted_proxy_hops: int = 0

    # Bearer token for GET /ops/status and /ops/logs. Unset = endpoints disabled (404).
    ops_token: SecretStr | None = None
    # Time-limited token for reviewers: accepted only before the expiry (UTC, ISO 8601).
    # Without an expiry it is never accepted.
    ops_review_token: SecretStr | None = None
    ops_review_token_expires_at: datetime | None = None

    log_level: str = "INFO"
    # Set by the image build (Docker) or the deploy script (Vercel); "dev" locally.
    git_sha: str = Field(default="dev", validation_alias=AliasChoices("GIT_SHA"))

    @property
    def llm_model_chain(self) -> list[str]:
        return [m.strip() for m in self.llm_models.split(",") if m.strip()]
