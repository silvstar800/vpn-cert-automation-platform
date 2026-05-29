"""Time and date utility helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def now_utc_ts() -> int:
    """Return current UTC timestamp as int seconds."""
    return int(datetime.now(tz=timezone.utc).timestamp())


def now_in_timezone(display_timezone: Any) -> datetime:
    """Return current datetime in the provided timezone."""
    return datetime.now(tz=display_timezone)


def format_display_datetime(value: Any, display_timezone: Any) -> str:
    """Format datetime as Korea-time display string."""
    if not value:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(display_timezone).strftime("%Y-%m-%d %H:%M:%S KST")
    return str(value)


def estimate_expire_date(created_at: Any, days: int = 3650) -> str:
    """Estimate expiration date as created_at + days."""
    if not isinstance(created_at, datetime):
        return ""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (created_at + timedelta(days=days)).date().isoformat()
