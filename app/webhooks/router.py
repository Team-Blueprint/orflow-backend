import base64
import hmac
import hashlib
import json
import logging

from fastapi import APIRouter, Request, Header, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_async_db
from app.core.config import settings
from app.webhooks.schemas import NombaWebhookPayload
from app.webhooks.service import process_nomba_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/inbound", tags=["Webhooks"])


def _safe(val: object) -> str:
    if val is None:
        return ""
    s = str(val)
    if s.lower() == "null":
        return ""
    return s


async def verify_nomba_signature(
    request: Request,
    nomba_signature: str = Header("", alias="nomba-signature"),
    nomba_sig_value: str = Header("", alias="nomba-sig-value"),
    nomba_timestamp: str = Header("", alias="nomba-timestamp"),
):
    # Prefer nomba-sig-value (newer header) if present, fall back to nomba-signature.
    # Both carry the same value today; Nomba docs note the version may evolve.
    effective_signature = nomba_sig_value or nomba_signature

    logger.info(
        "Nomba webhook received: path=%s method=%s sig_prefix=%s ts=%s",
        request.url.path,
        request.method,
        effective_signature[:16] if effective_signature else "none",
        nomba_timestamp,
    )

    headers_dict = dict(request.headers)
    logger.info("Nomba webhook headers: %s", headers_dict)

    if not effective_signature or not nomba_timestamp:
        raise HTTPException(status_code=400, detail="Missing Nomba signature headers")

    if not settings.NOMBA_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    payload_bytes = await request.body()
    try:
        body = json.loads(payload_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    data = body.get("data") or {}
    merchant = data.get("merchant") or {}
    transaction = data.get("transaction") or {}

    event_type = _safe(body.get("event_type"))
    request_id = _safe(body.get("requestId"))
    user_id = _safe(merchant.get("userId"))
    wallet_id = _safe(merchant.get("walletId"))
    transaction_id = _safe(transaction.get("transactionId"))
    transaction_type = _safe(transaction.get("type"))
    transaction_time = _safe(transaction.get("time"))
    response_code = _safe(transaction.get("responseCode"))

    hashing_payload = (
        f"{event_type}:{request_id}:{user_id}:{wallet_id}:"
        f"{transaction_id}:{transaction_type}:{transaction_time}:"
        f"{response_code}:{nomba_timestamp}"
    )

    secret_bytes = settings.NOMBA_WEBHOOK_SECRET.encode("utf-8")
    digest = hmac.new(secret_bytes, hashing_payload.encode("utf-8"), hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode()

    if not hmac.compare_digest(computed, effective_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse and attach the validated payload to request.state so the endpoint
    # handler can use it without reading the body a second time (the stream is
    # only readable once).
    try:
        request.state.nomba_payload = NombaWebhookPayload(**body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid payload shape: {exc}")


@router.post("/nomba")
async def nomba_webhook(
    request: Request,
    session: AsyncSession = Depends(get_async_db),
    _: None = Depends(verify_nomba_signature),
):
    payload: NombaWebhookPayload = request.state.nomba_payload
    # Use requestId from the body as the idempotency key (it is the stable unique
    # identifier Nomba documents; fall back to nomba-signature if absent).
    event_id = payload.requestId or request.headers.get("nomba-sig-value") or request.headers.get("nomba-signature", "")

    logger.info(
        "Nomba webhook parsed payload: event_id=%s event_type=%s requestId=%s data=%s",
        event_id, payload.event_type, payload.requestId,
        payload.data.model_dump() if payload.data else None,
    )

    await process_nomba_webhook(session, event_id, payload)

    return {"status": "ok"}
