from datetime import datetime
from dateutil.relativedelta import relativedelta
from app.plans.models import PlanInterval

def advance_billing_period(start_date: datetime, interval: PlanInterval, count: int) -> datetime:
    """
    Safely advances a datetime by a specific billing interval and count.
    Uses dateutil.relativedelta to correctly handle edge cases like
    adding a month to January 31st (results in Feb 28/29).
    """
    if interval == PlanInterval.daily:
        return start_date + relativedelta(days=count)
    elif interval == PlanInterval.weekly:
        return start_date + relativedelta(weeks=count)
    elif interval == PlanInterval.monthly:
        return start_date + relativedelta(months=count)
    elif interval == PlanInterval.quarterly:
        return start_date + relativedelta(months=3 * count)
    elif interval == PlanInterval.yearly or interval == PlanInterval.annually:
        return start_date + relativedelta(years=count)
    elif interval == PlanInterval.biannually:
        return start_date + relativedelta(months=6 * count)
    raise ValueError(f"Unknown plan interval: {interval}")
