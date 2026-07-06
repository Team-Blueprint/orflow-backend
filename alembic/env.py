import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context


from app.core.config import settings

# Import all models so autogenerate can detect them
import app.tenants.models  # noqa: F401
import app.customers.models  # noqa: F401
import app.plans.models  # noqa: F401
import app.payment_methods.models  # noqa: F401
import app.subscriptions.models  # noqa: F401
import app.invoices.models  # noqa: F401
import app.audit.models  # noqa: F401
import app.webhooks.models  # noqa: F401
import app.reconciliation.models  # noqa: F401
import app.projects.models  # noqa: F401
import app.subscription_pages.models  # noqa: F401

from app.db.base import Base


# Alembic Config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# Offline mode (generates SQL without a live connection)

def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()



# Online mode (runs against a live async connection)

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection, 
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=False
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = settings.DATABASE_URL
    connectable = async_engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())



if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
