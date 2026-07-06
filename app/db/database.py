from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.core.config import settings


class _EngineProxy:
    _instance = None

    def __init__(self):
        self._engine = None

    def _get(self):
        if self._engine is None:
            self._engine = create_async_engine(
                settings.DATABASE_URL,
                echo=True,
                future=True,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
            )
        return self._engine

    def __getattr__(self, name):
        return getattr(self._get(), name)


engine = _EngineProxy()
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an AsyncSession per request."""
    async with AsyncSessionLocal() as session:
        yield session