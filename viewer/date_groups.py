"""Non-overlapping calendar groups for the local message list.

Day groups take precedence over weeks, followed by calendar months. The first
weekday comes from the user's system locale. No source dates are modified.
"""
from datetime import datetime, timedelta


def local_message_date(timestamp, tz=None):
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def date_group(timestamp, *, today=None, first_weekday=0, tz=None):
    """Return one label, including sensible fallbacks for missing/future dates."""
    today = today or datetime.now(tz).date()
    sent = local_message_date(timestamp, tz)
    if sent is None:
        return 'Unknown Date'
    if sent > today:
        return 'Future'
    if sent == today:
        return 'Today'
    if sent == today - timedelta(days=1):
        return 'Yesterday'
    week_start = today - timedelta(days=(today.weekday() - first_weekday) % 7)
    if sent >= week_start:
        return 'This Week'
    for weeks, label in ((1, 'Last Week'), (2, 'Two Weeks Ago'), (3, 'Three Weeks Ago')):
        if sent >= week_start - timedelta(weeks=weeks):
            return label
    month_start = today.replace(day=1)
    if sent >= month_start:
        return 'Earlier This Month'
    previous_month = (month_start - timedelta(days=1)).replace(day=1)
    if sent >= previous_month:
        return 'Last Month'
    return 'Older'


def message_date_label(timestamp, *, today=None, tz=None):
    """Short local date/time in rows; the complete original date stays in the tooltip."""
    today = today or datetime.now(tz).date()
    sent = local_message_date(timestamp, tz)
    if sent is None:
        return 'No date'
    moment = datetime.fromtimestamp(timestamp, tz)
    if sent == today:
        return moment.strftime('%H:%M')
    return moment.strftime('%d %b' if sent.year == today.year else '%d %b %Y')
