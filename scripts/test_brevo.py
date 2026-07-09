import asyncio
import sys
import os

# Add the project root to the python path so it can import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.email import send_email_async
from app.core.config import settings

async def test_email():
    if not settings.BREVO_API_KEY:
        print("WARNING: BREVO_API_KEY is empty in your configuration.")
        
    print("Sending test email to abasiofon135@gmail.com...")
    try:
        response = await send_email_async(
            to="abasiofon135@gmail.com",
            subject="Test Email from Orflow (Brevo Integration)",
            html="<h1>Success!</h1><p>The Brevo email integration is working perfectly.</p>"
        )
        print("Success! Response from Brevo:")
        print(response)
    except Exception as e:
        print(f"Failed to send email: {e}")
        if hasattr(e, 'response') and getattr(e, 'response'):
            print(f"Response body: {e.response.text}")

if __name__ == "__main__":
    asyncio.run(test_email())
