import logging
from urllib.parse import urlparse
from arq.cron import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.db.database import engine
from app.providers.deps import init_payment_providers, close_payment_providers
from app.worker.billing_cycle import process_billing_cycle
from app.dunning.worker import process_dunning_retries, process_unpaid_grace

from app.worker.installments import process_due_installment_invoices
from app.worker.reconciliation import process_reconciliation

logger = logging.getLogger(__name__)

async def startup(ctx):
    logger.info("Initializing worker resources...")
    await init_payment_providers()

async def shutdown(ctx):
    logger.info("Shutting down worker resources...")
    await close_payment_providers()
    await engine.dispose()

class WorkerSettings:
    """
    Settings for the arq worker.
    Run with: arq app.worker.main.WorkerSettings
    """
    from app.worker.tasks import (
        schedule_subscription_expiry,
        schedule_trial_activation,
        deliver_webhook_job,
        activate_trial_subscription_job,
        expire_incomplete_subscription_job,
        send_email_job
    )
    functions = [
        schedule_subscription_expiry,
        schedule_trial_activation,
        deliver_webhook_job,
        activate_trial_subscription_job,
        expire_incomplete_subscription_job,
        send_email_job
    ]


    cron_jobs = [
        cron(process_billing_cycle, minute=0),
        # Process installments at the top of the hour
        cron(process_due_installment_invoices, minute=0),
        # Dunning: re-attempt due invoices a bit after the billing run.
        cron(process_dunning_retries, minute=15),
        # Grace timer: cancel long-unpaid subscriptions once a day.
        cron(process_unpaid_grace, hour=2, minute=30),
        # Reconciliation: cross-check with Nomba daily at 3am.
        cron(process_reconciliation, hour=3, minute=0),
    ]
    on_startup = startup
    on_shutdown = shutdown
    _redis = urlparse(settings.REDIS_URL)
    redis_settings = RedisSettings(
        host=_redis.hostname or 'localhost',
        port=_redis.port or 6379,
        password=_redis.password,
        database=int(_redis.path[1:]) if _redis.path and _redis.path != '/' else 0,
        ssl=_redis.scheme == 'rediss',
    )
