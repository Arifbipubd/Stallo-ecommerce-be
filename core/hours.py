"""
Opening hours and takeaway pickup slots.

Outlets open 10:00 every day and close at 22:00 (Sunday to Thursday) or 23:00 (Friday and
Saturday). Customers collect on a 15-minute grid, at least PICKUP_LEAD_MINUTES from now, on the
today through PICKUP_DAYS_AHEAD more days (default one week), and no later than one step before closing. Keep the
wording on the storefront (`hours` in src/data/site.ts) in line with this.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

OPENS = time(10, 0)
CLOSES_WEEKDAY = time(22, 0)  # Sunday to Thursday
CLOSES_WEEKEND = time(23, 0)  # Friday and Saturday
LEAD_GRACE_MINUTES = 10
_WEEKEND_DAYS = {4, 5}  # datetime.weekday(): Monday is 0, so Friday is 4 and Saturday is 5


def _closes(day: date) -> time:
    return CLOSES_WEEKEND if day.weekday() in _WEEKEND_DAYS else CLOSES_WEEKDAY


def _at(day: date, t: time) -> datetime:
    return timezone.make_aware(datetime.combine(day, t), timezone.get_current_timezone())


def _ceil(dt: datetime, step: int) -> datetime:
    """Round up to the next slot boundary (e.g. 3:32 becomes 3:45 with a 15 minute step)."""
    rounded = dt.replace(second=0, microsecond=0)
    if rounded < dt:
        rounded += timedelta(minutes=1)
    remainder = rounded.minute % step
    if remainder:
        rounded += timedelta(minutes=step - remainder)
    return rounded


def _window(day: date) -> tuple[datetime, datetime]:
    """First and last pickup time on `day`."""
    return _at(day, OPENS), _at(day, _closes(day)) - timedelta(minutes=settings.PICKUP_STEP_MINUTES)


def is_open_now(now: datetime | None = None) -> bool:
    now = timezone.localtime(now or timezone.now())
    return _at(now.date(), OPENS) <= now <= _at(now.date(), _closes(now.date()))


def pickup_days(now: datetime | None = None) -> list[dict]:
    """Every pickup time the customer can choose, grouped by day. Days with none left are left out."""
    now = timezone.localtime(now or timezone.now())
    step = settings.PICKUP_STEP_MINUTES
    earliest = _ceil(now + timedelta(minutes=settings.PICKUP_LEAD_MINUTES), step)
    days = []
    for offset in range(settings.PICKUP_DAYS_AHEAD + 1):
        day = now.date() + timedelta(days=offset)
        first, last = _window(day)
        slot = max(first, earliest)
        slots = []
        while slot <= last:
            slots.append({"value": slot.isoformat(), "label": format_time(slot)})
            slot += timedelta(minutes=step)
        if slots:
            days.append({"date": day.isoformat(), "label": ("Today", "Tomorrow")[offset] if offset < 2 else day.strftime("%a %d %b"), "slots": slots})
    return days


def format_time(dt: datetime) -> str:
    """3:45 pm"""
    dt = timezone.localtime(dt)
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


def check_pickup_time(value: datetime, now: datetime | None = None) -> str | None:
    """Return a message the customer can read if `value` isn't a bookable pickup time, else None."""
    now = timezone.localtime(now or timezone.now())
    value = timezone.localtime(value)
    step = settings.PICKUP_STEP_MINUTES
    if value.second or value.microsecond or value.minute % step:
        return f"Choose a time on the quarter hour, e.g. {format_time(_ceil(now + timedelta(minutes=settings.PICKUP_LEAD_MINUTES), step))}."
    # A few minutes of grace: someone who picks the earliest slot and then takes a while to fill in
    # the form should not be turned away.
    if value < now + timedelta(minutes=max(settings.PICKUP_LEAD_MINUTES - LEAD_GRACE_MINUTES, 0)):
        return f"Please choose a time at least {settings.PICKUP_LEAD_MINUTES} minutes from now."
    if value.date() > now.date() + timedelta(days=settings.PICKUP_DAYS_AHEAD):
        return "Pickup times are for today only." if settings.PICKUP_DAYS_AHEAD == 0 else "That day is too far ahead."
    first, last = _window(value.date())
    if not first <= value <= last:
        return f"We're open {format_time(first)} to {format_time(last + timedelta(minutes=step))} that day. Choose a time in between."
    return None
