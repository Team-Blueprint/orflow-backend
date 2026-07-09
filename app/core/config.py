from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost:5432/dbname"
    REDIS_URL: str = "redis://localhost:6379"
    BACKEND_URL: str = "https://orflow-backend.onrender.com"

    # Sandbox/production base URL. Override per environment via .env.
    NOMBA_BASE_URL: str = "https://api.nomba.com"
    NOMBA_CLIENT_ID: str = ""
    NOMBA_CLIENT_SECRET: str = ""
    NOMBA_ACCOUNT_ID: str = ""
    # Sub-account ID for branded checkouts/transfers (shows team name to recipients).
    # When set, order.accountId uses this instead of NOMBA_ACCOUNT_ID.
    # Register the webhook URL on this sub-account in the Nomba dashboard.
    # Leave empty to fall back to the parent account (NOMBA_ACCOUNT_ID).
    NOMBA_SUBACCOUNT_ID: str = ""
    NOMBA_CALLBACK_URL: str = "https://orflow.vercel.app/portal/callback"
    NOMBA_HTTP_TIMEOUT: float = 30.0
    NOMBA_TOKEN_LEEWAY_SECONDS: int = 300
    NOMBA_WEBHOOK_SECRET: str = ""

    # Nomba sandbox credentials — used for all test-mode (pk_test / sk_test) requests.
    # Set these in your .env alongside the live credentials above.
    NOMBA_SANDBOX_BASE_URL: str = "https://api-sandbox.nomba.com"
    NOMBA_SANDBOX_CLIENT_ID: str = ""
    NOMBA_SANDBOX_CLIENT_SECRET: str = ""
    # Sandbox account ID — Nomba sandbox uses a separate account from live.
    # Defaults to NOMBA_ACCOUNT_ID if not set (safe for single-account setups).
    NOMBA_SANDBOX_ACCOUNT_ID: str = ""
    NOMBA_SANDBOX_SUBACCOUNT_ID: str = ""
    NOMBA_SANDBOX_CALLBACK_URL: str = "https://orflow.vercel.app/portal/callback"
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "https://orflow.vercel.app/api/auth/google/callback"
    DUNNING_GRACE_DAYS: int = 14
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_EXPIRE_DAYS: int = 14
    PORTAL_JWT_EXPIRE_MINUTES: int = 60

    FRONTEND_URL: str = "https://orflow.vercel.app"

    # Brevo Email Settings
    BREVO_API_KEY: str = ""
    DEFAULT_FROM_EMAIL: str = "abasiofon135@gmail.com"

    # Cookie settings
    COOKIE_SECURE: bool = True
    COOKIE_SAMESITE: str = "none"
    CORS_ORIGINS: str = "http://localhost:5173,https://orflow.vercel.app"

    CRON_SECRET: str = ""

    RATE_LIMIT_DEFAULT_PER_MINUTE: int = 60

    RATE_LIMIT_CACHE_TTL_SECONDS: int = 300  # 5 min


    IDEMPOTENCY_TTL_SECONDS: int = 86400

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()