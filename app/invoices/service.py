from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import BaseRepository
from app.invoices.models import Invoice


class InvoiceService(BaseRepository[Invoice]):
    """
    Tenant-scoped repository for invoices.

    Status changes do NOT go through this service — use
    ``app.invoices.state_machine.transition_invoice`` instead.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Invoice, session)
