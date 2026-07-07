"""Portal service — auth, session tokens, and email dispatch."""
import logging
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.customers.models import Customer

logger = logging.getLogger(__name__)


# ── JWT helpers ───────────────────────────────────────────────────────────────

def create_portal_session_token(customer_id: str, subscription_id: str) -> str:
    """Issue a short-lived JWT carrying only customer_id and subscription_id."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.PORTAL_JWT_EXPIRE_MINUTES
    )
    payload = {
        "sub": customer_id,
        "subscription_id": subscription_id,
        "exp": expire,
        "iss": "orflow-portal",
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_portal_session_token(token: str) -> dict:
    """Decode and validate a portal session JWT. Raises jwt.InvalidTokenError on failure."""
    return jwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=[settings.JWT_ALGORITHM],
        issuer="orflow-portal",
    )


# ── PIN helpers ───────────────────────────────────────────────────────────────

def hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()


def verify_pin(pin: str, hashed: str) -> bool:
    return bcrypt.checkpw(pin.encode(), hashed.encode())


# ── Customer lookup ───────────────────────────────────────────────────────────

async def get_customer_by_slug(session: AsyncSession, token_slug: str) -> Customer | None:
    result = await session.execute(
        select(Customer).where(Customer.portal_token_slug == token_slug)
    )
    return result.scalar_one_or_none()


# ── Email (Brevo) ─────────────────────────────────────────────────────────────

_EMAIL_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ margin:0; padding:0; background:#f4f4f5; font-family:-apple-system,"Segoe UI",system-ui,sans-serif; }}
  .viewport {{ background:#f4f4f5; padding:40px 16px; }}
  .card {{ background:#ffffff; max-width:520px; margin:0 auto; padding:40px; }}
  h1 {{ color:#09090b; font-size:22px; font-weight:700; margin:0 0 8px; }}
  p {{ color:#09090b; font-size:14px; line-height:1.6; margin:0 0 16px; }}
  .pin {{ font-size:32px; font-weight:700; letter-spacing:8px; color:#09090b;
           margin:24px 0; text-align:center; }}
  .btn {{ display:block; width:100%; background:#ea580c; color:#ffffff;
           text-align:center; padding:14px; font-size:15px; font-weight:600;
           text-decoration:none; border-radius:0; margin-top:24px; }}
  .footer {{ color:#71717a; font-size:12px; margin-top:24px; text-align:center; }}
</style>
</head>
<body>
<div class="viewport">
  <div class="card">
    <h1>Your subscription is active</h1>
    <p>You now have access to your customer portal where you can view your
    subscription, payment history, and update your card.</p>
    <p style="margin-bottom:4px;font-weight:600;">Your one-time access PIN:</p>
    <div class="pin">{pin}</div>
    <p>Use this PIN together with the link below to sign in. You can change it
    after your first login.</p>
    <a class="btn" href="{portal_link}">Access your portal</a>
    <p class="footer">This email was sent by {merchant_name} via orflow.<br>
    If you did not make a purchase, you can safely ignore this email.</p>
  </div>
</div>
</body>
</html>"""


async def send_portal_onboarding_email(customer: Customer, raw_pin: str) -> None:
    """Send the light-mode portal onboarding email via Brevo."""
    if not settings.BREVO_API_KEY:
        logger.warning("BREVO_API_KEY not set — skipping portal onboarding email")
        return

    portal_link = (
        f"{settings.FRONTEND_URL}/portal/access/{customer.portal_token_slug}"
    )
    html_body = _EMAIL_TEMPLATE.format(
        pin=raw_pin,
        portal_link=portal_link,
        merchant_name="orflow",
    )

    payload = {
        "sender": {"name": "orflow", "email": settings.DEFAULT_FROM_EMAIL},
        "to": [{"email": customer.email, "name": customer.name}],
        "subject": "Your portal access credentials",
        "htmlContent": html_body,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                json=payload,
                headers={
                    "api-key": settings.BREVO_API_KEY,
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code not in (200, 201):
                logger.warning(
                    "Brevo email failed: status=%s body=%s",
                    resp.status_code, resp.text,
                )
            else:
                logger.info("Portal onboarding email sent to %s", customer.email)
    except Exception as exc:
        logger.warning("Portal onboarding email exception: %s", exc)
