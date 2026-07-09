import httpx
import logging
from typing import Dict, Any
from app.core.config import settings

logger = logging.getLogger(__name__)

async def send_email_async(to: str, subject: str, html: str) -> Dict[str, Any]:
    """
    Sends an email using the Brevo API asynchronously via httpx.
    """
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": settings.BREVO_API_KEY,
        "content-type": "application/json"
    }
    payload = {
        "sender": {"name": settings.DEFAULT_FROM_NAME, "email": settings.DEFAULT_FROM_EMAIL},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
