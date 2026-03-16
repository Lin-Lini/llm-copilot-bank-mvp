from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_env: str = 'dev'

    internal_auth_token: str = 'dev-internal-token'

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

    # --- LLM integration ---
    # URLs for external LLM gateway or adapter. Leave these empty by default. When
    # populated, worker and backend will call these endpoints instead of the
    # built‑in stub. Each endpoint corresponds to a specific mode: analyze,
    # draft, explain or streaming ghost_text.
    llm_analyze_url: str = ''
    llm_draft_url: str = ''
    llm_explain_url: str = ''
    llm_ghost_stream_url: str = ''

    # API key or bearer token for external LLM gateway. By default this is blank.
    llm_api_key: str = ''

    # Provider mode:
    # - 'stub'           : deterministic local stub (default)
    # - 'contracts_http' : call custom endpoints from LLM_*_URL that return AnalyzeV1/DraftV1/ExplainV1 JSON
    # - 'openai_compat'  : call OpenAI-compatible /v1/chat/completions directly (requires models + base_url)
    llm_provider: str = 'stub'

    # OpenAI-compatible base URL, e.g. https://api.openai.com/v1 or http://llm-gateway:8000/v1
    llm_base_url: str = ''

    # Model names for OpenAI-compatible provider
    llm_analyze_model: str = ''
    llm_draft_model: str = ''
    llm_explain_model: str = ''
    llm_ghost_model: str = ''

    llm_temperature: float = 0.2
    llm_max_tokens: int = 800

    # --- Embeddings/RAG ---
    # Provider mode:
    # - 'stub'          : local deterministic hashing (default)
    # - 'openai_compat' : OpenAI-compatible /v1/embeddings
    embed_provider: str = 'stub'
    embed_base_url: str = ''
    embed_model: str = ''

    rag_dim: int = 64


settings = Settings()
