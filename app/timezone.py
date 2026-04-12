"""Timezone-aware datetime helper.

Reads the TZ environment variable (default: UTC) and provides a
consistent now() function for the entire application.
"""

import os
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Python < 3.9 fallback
    ZoneInfo = None

_tz_name = os.environ.get("TZ", "UTC")

if ZoneInfo:
    try:
        APP_TZ = ZoneInfo(_tz_name)
    except KeyError:
        APP_TZ = timezone.utc
else:
    APP_TZ = timezone.utc


def now():
    """Return the current timezone-aware datetime."""
    return datetime.now(APP_TZ)


def now_str(fmt="%Y-%m-%d %H:%M:%S"):
    """Return the current time as formatted string."""
    return now().strftime(fmt)
