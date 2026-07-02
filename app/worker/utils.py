from datetime import datetime
from dateutil.relativedelta import relativedelta
from app.plans.models import PlanInterval

def advance_billing_period(start_date: datetime, interval: PlanInterval, count: int) -> datetime:
    """
    Safely advances a datetime by a specific billing interval and count.
    Uses dateutil.relativedelta to correctly handle edge cases like
    adding a month to January 31st (results in Feb 28/29).
    """
    if interval == PlanInterval.day:
        return start_date + relativedelta(days=count)
    elif interval == PlanInterval.week:
        return start_date + relativedelta(weeks=count)
    elif interval == PlanInterval.month:
        return start_date + relativedelta(months=count)
    elif interval == PlanInterval.year:
        return start_date + relativedelta(years=count)
    raise ValueError(f"Unknown plan interval: {interval}")
