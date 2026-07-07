from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost:5432/dbname"
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

    # Nomba sandbox credentials — used for all test-mode (pk_test / sk_test) requests.
    # Set these in your .env alongside the live credentials above.
    NOMBA_SANDBOX_BASE_URL: str = "https://api-sandbox.nomba.com"
    NOMBA_SANDBOX_CLIENT_ID: str = ""
    NOMBA_SANDBOX_CLIENT_SECRET: str = ""
    NOMBA_SANDBOX_ACCOUNT_ID: str = ""
    NOMBA_SANDBOX_CALLBACK_URL: str = "https://example.com/nomba/callback"
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "https://orflow.vercel.app/api/auth/google/callback"
    DUNNING_GRACE_DAYS: int = 14
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_EXPIRE_DAYS: int = 14

    FRONTEND_URL: str = "https://orflow.vercel.app"

    # Brevo Email Settings
    BREVO_API_KEY: str = ""
    DEFAULT_FROM_EMAIL: str = "noreply@example.com"

    # Cookie settings
    COOKIE_SECURE: bool = True
    COOKIE_SAMESITE: str = "none"
    CORS_ORIGINS: str = "http://localhost:5173,https://orflow.vercel.app"

    RATE_LIMIT_DEFAULT_PER_MINUTE: int = 60

    RATE_LIMIT_CACHE_TTL_SECONDS: int = 300  # 5 min


    IDEMPOTENCY_TTL_SECONDS: int = 86400

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
