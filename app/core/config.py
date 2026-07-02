from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./sub_eng.db"
    REDIS_URL: str = "redis://localhost:6379"

    # Sandbox/production base URL. Override per environment via .env.
    NOMBA_BASE_URL: str = "https://api.nomba.com"
    NOMBA_CLIENT_ID: str = ""
    NOMBA_CLIENT_SECRET: str = ""
    NOMBA_ACCOUNT_ID: str = ""
    NOMBA_CALLBACK_URL: str = "https://example.com/nomba/callback"
    NOMBA_HTTP_TIMEOUT: float = 30.0
    NOMBA_TOKEN_LEEWAY_SECONDS: int = 300
    NOMBA_WEBHOOK_SECRET: str = ""
    DUNNING_GRACE_DAYS: int = 14
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24
    JWT_REFRESH_EXPIRE_DAYS: int = 30

    RATE_LIMIT_DEFAULT_PER_MINUTE: int = 60

    RATE_LIMIT_CACHE_TTL_SECONDS: int = 300  # 5 min


    IDEMPOTENCY_TTL_SECONDS: int = 86400

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
