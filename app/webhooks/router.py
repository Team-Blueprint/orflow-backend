import base64
import hmac
import hashlib
import json

from fastapi import APIRouter, Request, Header, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_async_db
from app.core.config import settings
from app.webhooks.schemas import NombaWebhookPayload
from app.webhooks.service import process_nomba_webhook

router = APIRouter(prefix="/webhooks/inbound", tags=["Webhooks"])


def _safe(val: object) -> str:
    if val is None:
        return ""
    s = str(val)
    if s == "null":
        return ""
    return s


async def verify_nomba_signature(
    request: Request,
    nomba_signature: str = Header(..., alias="nomba-signature"),
    nomba_timestamp: str = Header("", alias="nomba-timestamp"),
):
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

    if not hmac.compare_digest(computed, nomba_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    return True


@router.post("/nomba")
async def nomba_webhook(
    request: Request,
    payload: NombaWebhookPayload,
    session: AsyncSession = Depends(get_async_db),
    _ = Depends(verify_nomba_signature),
):
    event_id = request.headers.get("nomba-event-id") or request.headers.get("nomba-signature", "")

    await process_nomba_webhook(session, event_id, payload)

    return {"status": "ok"}
