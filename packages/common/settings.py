"""Application settings.

Every deployable (API, worker, scripts) reads configuration from the same place so a
project can never be configured two different ways depending on which process loaded it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

if TYPE_CHECKING:
    from common.crypto import EncryptionKey


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "ai-memory-layer"
    app_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    log_json: bool = True

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/memory"
    database_pool_size: int = 10
    database_max_overflow: int = 10
    database_echo: bool = False

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Language engine (deterministic; no external model provider)
    # The vector width of the local lexical embedder. Changing it requires a migration of
    # the embeddings column and a re-embed, so it is deliberately a deploy-time decision.
    embedding_dimensions: int = 1536
    # Proper-noun entity guessing is useful for B2B text and noisy for consumer apps.
    nlp_detect_proper_nouns: bool = True
    # Maximum memories a single event may produce.
    nlp_max_memories_per_event: int = 6
    # Sentences an extractive summary may use.
    nlp_summary_sentences: int = 3

    # Authentication
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_minutes: int = 60
    jwt_refresh_token_days: int = 30
    api_key_secret: str = "change-me-in-production"
    # Encrypts the two secrets that cannot be hashed because signing needs the value back:
    # outbound webhook secrets and inbound integration signing secrets. Hold this in a KMS
    # and inject it; a database dump alone is then not enough to forge a delivery.
    # Empty means "store them as they were" — supported so an existing deployment can turn
    # encryption on without a flag day, but refused in production.
    secrets_encryption_key: str = ""
    # Older keys, newest first, kept readable while rows are rewritten under a new key.
    previous_encryption_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Email. SMTP rather than a provider SDK: every provider speaks it, so switching one
    # for another is four environment variables and no code. Unset SMTP_HOST logs messages
    # instead of sending them, which is the development default and is refused in
    # production — a deployment that silently swallows invitations is worse than one that
    # refuses to start.
    # "auto" sends when SMTP_HOST is set and logs when it is not. "console" and "null"
    # force those transports — "null" is how a deployment says it wants no mail at all,
    # which is a legitimate choice and the one thing production will not infer.
    email_transport: Literal["auto", "console", "null"] = "auto"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    # Implicit TLS (465). Otherwise the connection is upgraded with STARTTLS (587).
    smtp_ssl: bool = False
    smtp_starttls: bool = True
    smtp_timeout_seconds: float = 15.0
    email_from: str = "no-reply@localhost"
    email_from_name: str = "Memora"
    # Where an invitation link points. Separate from app_url so a deployment can send mail
    # to a public hostname while the dashboard is also reachable internally.
    email_link_base_url: str = ""

    # How old the secrets encryption key may get before the nightly check complains. Zero
    # disables the check. 90 days is the usual compliance answer; it is a setting because
    # the right number is somebody else's policy, not ours.
    key_rotation_max_age_days: int = 90
    # Where the rotation warning goes. Empty means log only.
    ops_alert_email: str = ""

    # CORS. NoDecode keeps pydantic-settings from trying to JSON-parse the value, so a
    # plain comma-separated list works in .env files and in real deployments alike.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # Memory engine
    memory_min_event_importance: float = 0.2
    # Calibrated for the local lexical embedder: restatements of the same issue land
    # around 0.45-0.65 blended similarity, unrelated statements below 0.15.
    memory_consolidation_similarity: float = 0.45
    memory_auto_merge_similarity: float = 0.82
    memory_default_decay_days: int = 90
    memory_context_token_budget: int = 2000
    memory_retrieval_candidates: int = 50

    # Ranking weights
    rank_weight_similarity: float = 0.35
    rank_weight_importance: float = 0.20
    rank_weight_confidence: float = 0.20
    rank_weight_recency: float = 0.15
    rank_weight_relationship: float = 0.10

    # Privacy
    pii_redaction_enabled: bool = True

    # Retention (days, 0 = indefinite)
    retention_event_days: int = 90
    retention_memory_days: int = 0
    retention_conversation_days: int = 30

    @field_validator("cors_origins", "previous_encryption_keys", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept ``a,b`` and ``["a","b"]`` so env files and orchestrators both work.

        Used for every list-valued setting: they are all written as comma-separated values
        in ``.env`` files and as JSON arrays by orchestrators.
        """
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass
            return [origin.strip() for origin in text.split(",") if origin.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def encryption_keys(self) -> list[EncryptionKey]:
        """The active key first, then any previous ones, for reading rotated rows."""
        from common.crypto import derive_key

        if not self.secrets_encryption_key:
            return []
        return [
            derive_key(self.secrets_encryption_key),
            *[derive_key(key) for key in self.previous_encryption_keys if key],
        ]

    @property
    def encryption_enabled(self) -> bool:
        return bool(self.secrets_encryption_key)

    @property
    def sync_database_url(self) -> str:
        """Alembic and other synchronous tooling need a non-async driver (psycopg 3)."""
        url = self.database_url
        if "+asyncpg" in url:
            return url.replace("+asyncpg", "+psycopg")
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    def check_production_safety(self) -> list[str]:
        """Return a list of configuration problems that must not ship to production."""
        problems: list[str] = []
        if not self.is_production:
            return problems
        if self.jwt_secret == "change-me-in-production" or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be set to a strong value (>= 32 chars).")
        if self.api_key_secret == "change-me-in-production" or len(self.api_key_secret) < 32:
            problems.append("API_KEY_SECRET must be set to a strong value (>= 32 chars).")
        if self.embedding_dimensions < 64:
            problems.append("EMBEDDING_DIMENSIONS must be at least 64 for usable retrieval.")
        if "*" in self.cors_origins:
            problems.append("CORS_ORIGINS must not be a wildcard in production.")
        if len(self.secrets_encryption_key) < 32:
            problems.append(
                "SECRETS_ENCRYPTION_KEY must be set to a strong value (>= 32 chars) so "
                "webhook and integration secrets are encrypted at rest."
            )
        if self.email_transport == "auto" and not self.smtp_host:
            problems.append(
                "SMTP_HOST must be set in production, or invitations are logged instead of "
                "sent and nobody finds out until somebody asks why they never arrived. Set "
                "EMAIL_TRANSPORT=null to deliberately send no mail."
            )
        if self.email_transport == "console":
            problems.append(
                "EMAIL_TRANSPORT=console writes invitation links into the log stream. Use "
                "null to send nothing, or configure SMTP_HOST."
            )
        return problems

    @property
    def link_base_url(self) -> str:
        """Where a link in an email should point."""
        return (self.email_link_base_url or self.app_url).rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
