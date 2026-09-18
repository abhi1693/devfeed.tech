from datetime import date
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class SolverService(BaseModel):
    model_config = {"extra": "forbid", "hide_input_in_errors": True}

    provider: Literal["flaresolverr"]
    url: str
    timeout_seconds: int = Field(default=30, ge=5, le=60)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value):
        from urllib.parse import urlsplit

        if value is None:
            return None
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
            or any(ord(c) < 33 or ord(c) == 127 for c in value)
        ):
            raise ValueError("Solver service requires a trusted HTTP(S) service origin")
        _ = parts.port
        return value.rstrip("/")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEVFEED_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    database_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    redis_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    redis_sentinel_nodes: list[tuple[str, int]] = []
    redis_sentinel_master: str | None = None
    redis_sentinel_username: str | None = None
    redis_sentinel_password: SecretStr | None = None
    redis_sentinel_ssl: bool = False
    # PgBouncer owns connection reuse; opt in only for direct PostgreSQL clients.
    database_pool_enabled: bool = False
    api_max_concurrent_requests: int = Field(default=16, ge=1, le=128)
    api_max_concurrent_streams: int = Field(default=32, ge=1, le=256)
    database_pool_size: int = Field(default=5, ge=1, le=20)
    database_max_overflow: int = Field(default=5, ge=0, le=20)
    database_pool_timeout_seconds: float = Field(default=2, gt=0, le=30)
    database_pool_recycle_seconds: int = Field(default=300, ge=30, le=3600)
    database_connect_timeout_seconds: int = Field(default=3, ge=2, le=30)
    database_keepalives_idle_seconds: int = Field(default=5, ge=1, le=60)
    database_keepalives_interval_seconds: int = Field(default=2, ge=1, le=30)
    database_keepalives_count: int = Field(default=2, ge=1, le=10)
    database_tcp_user_timeout_ms: int = Field(default=8000, ge=1000, le=60000)
    cors_origins: list[str] = []
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    metrics_enabled: bool = False
    metrics_host: str = "127.0.0.1"
    metrics_port: int = Field(default=9100, ge=1024, le=65535)
    telemetry_environment: Literal["development", "test", "production"] = "development"
    otlp_endpoint: str | None = None
    trace_sample_ratio: float = Field(default=0.1, ge=0, le=1)
    pyroscope_server: str | None = None
    profiling_sample_rate: int = Field(default=19, ge=1, le=100)
    exporter_refresh_seconds: int = Field(default=60, ge=15, le=300)
    exporter_query_timeout_ms: int = Field(default=3000, ge=100, le=10000)
    job_log_max_entries: int = Field(default=1000, ge=100, le=10000)
    job_log_ttl_seconds: int = Field(default=604800, ge=3600, le=2592000)
    feed_user_agent: str = "DevFeed/0.1 (+https://devfeed.tech)"
    feed_max_bytes: int = Field(default=5_000_000, ge=1024, le=20_000_000)
    feed_max_entries: int = Field(default=500, ge=1, le=2000)
    feed_timeout_seconds: int = Field(default=20, ge=1, le=30)
    page_max_bytes: int = Field(default=2_000_000, ge=1024, le=5_000_000)
    article_page_max_bytes: int = Field(default=10_000_000, ge=1024, le=20_000_000)
    source_page_max_bytes: int = Field(default=10_000_000, ge=1024, le=20_000_000)
    page_timeout_seconds: int = Field(default=15, ge=1, le=30)
    solver_services: list[SolverService] = Field(default_factory=list, max_length=4)
    solver_queue_enabled: bool = False
    solver_timeout_seconds: int = Field(default=45, ge=5, le=60)
    scheduler_batch_size: int = Field(default=100, ge=1, le=1000)
    sitemap_refresh_seconds: int = Field(default=900, ge=60, le=3600)
    image_storage_enabled: bool = False
    image_storage_endpoint: str | None = None
    image_storage_bucket: str | None = None
    image_public_url: str | None = None
    image_storage_access_key: SecretStr | None = None
    image_storage_secret_key: SecretStr | None = None
    imgproxy_url: str | None = None
    imgproxy_key: SecretStr | None = None
    imgproxy_salt: SecretStr | None = None
    image_max_bytes: int = Field(default=10_000_000, ge=1024, le=20_000_000)

    @field_validator("image_storage_endpoint", "image_public_url", "imgproxy_url")
    @classmethod
    def validate_image_origin(cls, value):
        return SolverService.validate_url(value) if value else None

    search_enabled: bool = False
    search_url: str | None = None
    search_query_key: SecretStr | None = None
    search_admin_key: SecretStr | None = None
    search_collection_prefix: str = Field(default="devfeed", pattern=r"^[a-z][a-z0-9_]{0,40}$")
    search_index_batch_size: int = Field(default=200, ge=10, le=500)

    @field_validator("search_url")
    @classmethod
    def validate_search_url(cls, value):
        return SolverService.validate_url(value)

    cache_enabled: bool = True
    cache_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    cache_metadata_ttl_seconds: int = Field(default=600, ge=1, le=3600)
    cache_max_bytes: int = Field(default=1_000_000, ge=1024, le=5_000_000)
    ai_enabled: bool = False
    ai_content_not_before: date | None = None

    @field_validator("ai_content_not_before", mode="before")
    @classmethod
    def optional_ai_content_date(cls, value):
        return None if value == "" else value

    auto_approve_topics: bool = False
    auto_approve_topic_relationships: bool = False
    auto_research_imports: bool = False
    full_automation: bool = False
    article_topic_proposals_enabled: bool = True
    topic_correction_max_attempts: int = Field(default=3, ge=1, le=5)
    auto_reanalyze_topics: bool = False
    auto_research_relationships: bool = False
    auto_link_tags: bool = True
    relationship_research_batch_size: int = Field(default=100, ge=1, le=500)
    relationship_research_max_pending: int = Field(default=4, ge=1, le=50)
    job_payload_retention_days: int = Field(default=30, ge=7, le=3650)
    job_payload_prune_batch_size: int = Field(default=100, ge=1, le=1000)

    automation_batch_size: int = Field(default=50, ge=1, le=500)
    ai_compact_article_prompts: bool = False
    # Opt in after a reviewed workflow benchmark; legacy deployments stay stable.
    ai_tiered_routing_enabled: bool = False
    ai_bounded_topics_enabled: bool = False
    ai_fast_model: str = "gpt-5.6-luna"
    ai_research_model: str = "gpt-5.6-luna"
    ai_escalation_model: str = "gpt-5.6-terra"
    topic_decision_max_calls: int = Field(default=5, ge=3, le=10)
    topic_decision_max_tokens: int = Field(default=64000, ge=8000, le=200000)
    topic_decision_call_tokens: int = Field(default=40000, ge=2000, le=40000)
    topic_decision_max_searches: int = Field(default=4, ge=1, le=8)
    topic_decision_max_seconds: int = Field(default=240, ge=30, le=600)
    topic_evidence_max_age_seconds: int = Field(default=86400, ge=300, le=604800)
    topic_decision_max_pending: int = Field(default=8, ge=1, le=20)
    topic_decision_worker_buffer: int = Field(default=2, ge=1, le=4)
    topic_evidence_concurrency: int = Field(default=3, ge=1, le=3)
    topic_evidence_reuse_seconds: int = Field(default=300, ge=0, le=300)
    # Background discovery gets no inference while actionable topic reviews remain.
    relationship_pause_for_topic_backlog: bool = True
    relationship_daily_call_budget: int = Field(default=40, ge=0, le=10000)
    analysis_max_candidates: int = Field(default=80, ge=1, le=500)
    analysis_fallback_candidates: int = Field(default=8, ge=0, le=50)
    ai_quota_pacing_enabled: bool = False
    ai_quota_reserve_percent: float = Field(default=20, ge=0, le=50)
    ai_capacity_cooldown_seconds: int = Field(default=300, ge=30, le=86400)
    ai_server_overload_cooldown_seconds: int = Field(default=30, ge=30, le=86400)
    evidence_timeout_seconds: int = Field(default=45, ge=5, le=60)
    codex_app_server_url: str | None = None
    codex_model: str | None = None
    codex_auth_token: SecretStr | None = None
    codex_timeout_seconds: int = Field(default=90, ge=10, le=120)
    notifications_enabled: bool = False
    chimely_user_environment: str | None = None

    @model_validator(mode="after")
    def validate_redis_configuration(self):
        from urllib.parse import urlsplit

        if bool(self.redis_sentinel_nodes) != bool(self.redis_sentinel_master):
            raise ValueError("Redis Sentinel requires both nodes and master name")
        for host, port in self.redis_sentinel_nodes:
            if not host.strip() or host != host.strip() or not 1 <= port <= 65535:
                raise ValueError("Redis Sentinel nodes require a host and port between 1 and 65535")
        if self.redis_sentinel_master:
            if not self.redis_sentinel_master.strip():
                raise ValueError("Redis Sentinel master name must not be blank")
            if urlsplit(self.redis_url).scheme not in {"redis", "rediss"}:
                raise ValueError("Redis Sentinel requires a redis or rediss data URL")
        elif (
            self.redis_sentinel_username or self.redis_sentinel_password or self.redis_sentinel_ssl
        ):
            raise ValueError("Redis Sentinel options require nodes and master name")
        return self

    @model_validator(mode="after")
    def validate_ai_configuration(self):
        from urllib.parse import urlsplit

        if self.ai_bounded_topics_enabled:
            self.ai_tiered_routing_enabled = True
        if self.full_automation:
            self.ai_enabled = True
            self.auto_research_imports = True
            self.auto_approve_topics = True
            self.auto_research_relationships = True
            self.auto_approve_topic_relationships = True
            self.auto_reanalyze_topics = True
            self.auto_link_tags = True

        if self.ai_enabled and (
            not self.codex_app_server_url or not self.codex_model or not self.codex_model.strip()
        ):
            raise ValueError("AI requires DEVFEED_CODEX_APP_SERVER_URL and DEVFEED_CODEX_MODEL")
        if self.codex_app_server_url:
            url = urlsplit(self.codex_app_server_url)
            if url.scheme == "unix":
                if (
                    url.netloc
                    or not url.path.startswith("/")
                    or url.path == "/"
                    or url.query
                    or url.fragment
                    or "%" in url.path
                    or "\x00" in url.path
                ):
                    raise ValueError("Codex Unix endpoint must be an absolute socket path")
                return self
            if (
                url.scheme not in {"ws", "wss"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError(
                    "Codex endpoint must be a ws/wss URL without credentials, query or fragment"
                )
            if url.scheme == "ws" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError(
                    "Remote Codex connections require wss; ws is restricted to loopback"
                )
            if url.scheme == "wss" and (
                not self.codex_auth_token or not self.codex_auth_token.get_secret_value().strip()
            ):
                raise ValueError("Remote Codex connections require DEVFEED_CODEX_AUTH_TOKEN")
        return self

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
