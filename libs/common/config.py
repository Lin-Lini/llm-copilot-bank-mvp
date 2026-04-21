from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_env: str = 'dev'

    internal_auth_token: str = 'dev-internal-token'
    internal_auth_signing_key: str = 'dev-internal-signing-key'
    internal_auth_ttl_sec: int = 45
    internal_auth_allow_legacy_token: bool = True

    database_url: str = 'postgresql+asyncpg://postgres:postgres@postgres:5432/copilot'
    redis_url: str = 'redis://redis:6379/0'

    minio_endpoint: str = 'minio:9000'
    minio_access_key: str = 'minioadmin'
    minio_secret_key: str = 'minioadmin'
    minio_bucket: str = 'copilot-docs'
    minio_secure: bool = False

    kafka_bootstrap: str = 'kafka:9092'
    kafka_enabled: bool = True

    mcp_tools_url: str = 'http://mcp-tools:8090'

    llm_analyze_url: str = ''
    llm_draft_url: str = ''
    llm_explain_url: str = ''
    llm_ghost_stream_url: str = ''

    llm_api_key: str = ''
    llm_provider: str = 'stub'
    llm_base_url: str = ''

    llm_analyze_model: str = ''
    llm_draft_model: str = ''
    llm_explain_model: str = ''
    llm_ghost_model: str = ''

    llm_temperature: float = 0.2
    llm_max_tokens: int = 800

    embed_provider: str = 'stub'
    embed_base_url: str = ''
    embed_model: str = ''

    rag_dim: int = 64
    rag_seed_dir: str = 'docs/rag_corpus'

    worker_lease_ttl_sec: int = 45
    worker_heartbeat_interval_sec: int = 10
    worker_reclaim_batch: int = 20
    worker_result_ttl_sec: int = 3600
    worker_cancel_ttl_sec: int = 3600


settings = Settings()