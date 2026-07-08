import os
from jinja2 import Environment, FileSystemLoader

# Setup Jinja2 environment pointing to the templates directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates", "emails")
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))

def get_invoice_receipt_template(
    customer_name: str, 
    invoice_id: str, 
    currency: str, 
    amount_formatted: str, 
    paid_at_str: str
) -> str:
    """Returns the HTML template for an invoice receipt using Jinja2."""
    template = env.get_template("invoice-receipt.html")
    return template.render(
        customer_name=customer_name,
        invoice_id=invoice_id,
        currency=currency,
        amount_formatted=amount_formatted,
        paid_at_str=paid_at_str
    )

def get_portal_access_template(
    customer_name: str, 
    token_slug: str, 
    raw_pin: str
) -> str:
    """Returns the HTML template for the portal access email."""
    from app.core.config import settings
    template = env.get_template("portal_access.html")
    access_link = f"{settings.FRONTEND_URL}/portal/access/{token_slug}"
    return template.render(
        customer_name=customer_name,
        accessUrl=access_link,
        generatedPin=raw_pin
    )
