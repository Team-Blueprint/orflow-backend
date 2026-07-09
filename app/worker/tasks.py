import logging
import uuid
import httpx
import hmac
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict
from urllib.parse import urlparse

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.webhooks.models import WebhookEndpoint, OutboundWebhookEvent, WebhookDeliveryAttempt, OutboundEventStatus

logger = logging.getLogger(__name__)

async def get_arq_pool():
    url = urlparse(settings.REDIS_URL)
    return await create_pool(RedisSettings(
        host=url.hostname or 'localhost',
        port=url.port or 6379,
        password=url.password,
        database=int(url.path[1:]) if url.path and url.path != '/' else 0,
        ssl=url.scheme == 'rediss',
    ))

async def schedule_subscription_expiry(subscription_id: uuid.UUID, delay_hours: int = 23):
    """
    Schedule a worker job to expire an incomplete subscription if the initial invoice remains unpaid.
    """
    pool = await get_arq_pool()
    await pool.enqueue_job(
        'expire_incomplete_subscription_job', 
        subscription_id, 
        _defer_by=timedelta(hours=delay_hours)
    )

async def expire_incomplete_subscription_job(ctx: Dict[str, Any], subscription_id: uuid.UUID):
    """
    Arq worker job to expire a subscription if it remains incomplete (first invoice unpaid).
    """
    from app.core.context import current_tenant_id
    from app.subscriptions.models import Subscription, SubscriptionStatus
    from app.subscriptions.state_machine import transition_subscription
    
    logger.info("Expiring incomplete subscription %s", subscription_id)
    
    async with AsyncSessionLocal() as session:
        sub = await session.get(Subscription, subscription_id)
        if not sub or sub.status != SubscriptionStatus.incomplete:
            logger.info("Subscription %s is not incomplete, skipping expiry.", subscription_id)
            return

        token = current_tenant_id.set(sub.tenant_id)
        try:
            await transition_subscription(session, sub, SubscriptionStatus.incomplete_expired, actor="expiry_worker")
            logger.info("Successfully expired incomplete subscription %s.", subscription_id)
        except Exception as e:
            logger.error("Failed to expire incomplete subscription %s: %s", subscription_id, e)
            await session.rollback()
        finally:
            current_tenant_id.reset(token)

async def schedule_trial_activation(subscription_id: uuid.UUID, run_at: datetime):
    pool = await get_arq_pool()
    await pool.enqueue_job(
        'activate_trial_subscription_job', 
        subscription_id, 
        _defer_until=run_at
    )

async def activate_trial_subscription_job(ctx: Dict[str, Any], subscription_id: uuid.UUID):
    from datetime import datetime, timezone
    from app.core.context import current_tenant_id
    from app.subscriptions.models import Subscription, SubscriptionStatus
    from app.subscriptions.state_machine import transition_subscription
    from app.payment_methods.service import PaymentMethodService
    from app.webhooks.outbound import enqueue_webhook_event
    from app.plans.models import Plan
    from app.subscriptions.service import _compute_period_end
    from sqlalchemy import select
    
    logger.info("Activating trialing subscription %s", subscription_id)
    
    async with AsyncSessionLocal() as session:
        sub = await session.get(Subscription, subscription_id)
        if not sub or sub.status != SubscriptionStatus.trialing:
            logger.info("Subscription %s is not trialing, skipping trial activation.", subscription_id)
            return

        token = current_tenant_id.set(sub.tenant_id)
        try:
            pm_svc = PaymentMethodService(session)
            payment_method = None
            if sub.payment_method_id:
                payment_method = await pm_svc.get(sub.payment_method_id)

            if payment_method and payment_method.provider_token:
                # Start the first real billing period now that the trial is over.
                now = datetime.now(timezone.utc)
                plan_result = await session.execute(select(Plan).where(Plan.id == sub.plan_id))
                plan = plan_result.scalar_one_or_none()
                if plan:
                    sub.current_period_start = now
                    sub.current_period_end = _compute_period_end(plan, now)
                await transition_subscription(session, sub, SubscriptionStatus.active, actor="trial_worker")
                
                await enqueue_webhook_event(
                    session,
                    tenant_id=sub.tenant_id,
                    event_type="subscription.trial_ending",
                    payload={"subscription_id": str(sub.id)}
                )
                await enqueue_webhook_event(
                    session,
                    tenant_id=sub.tenant_id,
                    event_type="subscription.activated",
                    payload={"subscription_id": str(sub.id)}
                )
                logger.info("Successfully transitioned subscription %s to active.", subscription_id)
            else:
                await transition_subscription(session, sub, SubscriptionStatus.paused, actor="trial_worker")
                
                await enqueue_webhook_event(
                    session,
                    tenant_id=sub.tenant_id,
                    event_type="subscription.trial_ending",
                    payload={"subscription_id": str(sub.id)}
                )
                await enqueue_webhook_event(
                    session,
                    tenant_id=sub.tenant_id,
                    event_type="subscription.paused",
                    payload={"subscription_id": str(sub.id)}
                )
                logger.warning("No valid payment method for subscription %s, paused it.", subscription_id)
                
        except Exception as e:
            logger.error("Failed to activate trial subscription %s: %s", subscription_id, e)
            await session.rollback()
        finally:
            current_tenant_id.reset(token)

async def enqueue_webhook_delivery(event_id: uuid.UUID):
    """
    Enqueue an asynchronous job to deliver an outbound webhook event.
    """
    pool = await get_arq_pool()
    await pool.enqueue_job('deliver_webhook_job', event_id)

async def deliver_webhook_job(ctx: Dict[str, Any], event_id: uuid.UUID):
    """
    Arq worker function to deliver a webhook event to all active endpoints for the tenant.
    """
    logger.info("Starting delivery for webhook event %s", event_id)
    
    async with AsyncSessionLocal() as session:
        # Get event
        result = await session.execute(
            select(OutboundWebhookEvent).where(OutboundWebhookEvent.id == event_id)
        )
        event = result.scalar_one_or_none()
        if not event:
            logger.error("Event %s not found", event_id)
            return

        # Get active endpoints for tenant
        endpoints_result = await session.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.tenant_id == event.tenant_id,
                WebhookEndpoint.is_active.is_(True),
                WebhookEndpoint.is_test == event.is_test,
            )
        )
        endpoints = endpoints_result.scalars().all()
        
        if not endpoints:
            logger.info("No active endpoints for tenant %s. Marking event successful.", event.tenant_id)
            event.status = OutboundEventStatus.successful
            await session.commit()
            return

        payload_bytes = json.dumps(event.payload).encode('utf-8')
        all_successful = True
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            for endpoint in endpoints:
                # Sign payload
                signature = hmac.new(
                    endpoint.secret.encode('utf-8'),
                    payload_bytes,
                    hashlib.sha256
                ).hexdigest()
                
                headers = {
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": signature
                }
                
                # Fetch attempts to determine retry count
                attempts_result = await session.execute(
                    select(WebhookDeliveryAttempt).where(WebhookDeliveryAttempt.event_id == event_id)
                )
                attempt_number = len(attempts_result.scalars().all()) + 1
                
                status_code = None
                response_body = None
                is_success = False
                
                try:
                    resp = await client.post(endpoint.url, content=payload_bytes, headers=headers)
                    status_code = resp.status_code
                    response_body = resp.text[:1000] # limit size
                    is_success = resp.is_success
                except Exception as e:
                    response_body = str(e)
                    
                # Log attempt
                attempt = WebhookDeliveryAttempt(
                    event_id=event.id,
                    status_code=status_code,
                    response_body=response_body,
                    attempt_number=attempt_number
                )
                session.add(attempt)
                
                if not is_success:
                    all_successful = False
                    logger.warning("Delivery to %s failed (status %s): %s", endpoint.url, status_code, response_body)
                    
        # Retries
        if not all_successful:
            if ctx['job_try'] < 4: # 1 initial + 3 retries
                from arq import Retry
                backoff_mins = {1: 1, 2: 5, 3: 15}
                defer_by = timedelta(minutes=backoff_mins[ctx['job_try']])
                logger.info("Event %s delivery failed. Retrying in %s (attempt %s)", event_id, defer_by, ctx['job_try'] + 1)
                await session.commit()
                raise Retry(defer=defer_by)
            else:
                logger.error("Event %s failed after max retries.", event_id)
                event.status = OutboundEventStatus.failed
        else:
            logger.info("Event %s delivered successfully.", event_id)
            event.status = OutboundEventStatus.successful
            
        await session.commit()

async def enqueue_email(to: str, subject: str, html: str):
    """
    Enqueue an asynchronous job to send an email via Brevo.
    """
    pool = await get_arq_pool()
    await pool.enqueue_job('send_email_job', to, subject, html)

async def send_email_job(ctx: Dict[str, Any], to: str, subject: str, html: str):
    """
    Arq worker job to send an email asynchronously using Brevo.
    """
    from app.core.email import send_email_async
    
    logger.info("Sending email to %s", to)
    try:
        await send_email_async(to, subject, html)
        logger.info("Successfully sent email to %s", to)
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to, e)
        from arq import Retry
        if ctx.get('job_try', 1) < 3:
            raise Retry(defer=timedelta(minutes=1))
        raise
