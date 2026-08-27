from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("study-bot.utils")

# Egypt has observed DST (UTC+3 in summer, UTC+2 in winter) again since 2023,
# so a fixed offset can't be exactly right year-round. ZoneInfo("Africa/Cairo")
# handles that automatically, but it depends on the "tzdata" package (or the
# host OS's own zoneinfo database) being present. On minimal/slim hosting
# images neither may exist, which makes ZoneInfo either raise or silently use
# stale rules — the most common cause of a bot that's "close but a bit off"
# on time. We try the real zoneinfo database first and only fall back to a
# fixed UTC+3 approximation (with a loud warning) if it's truly unavailable,
# so the bot degrades safely instead of failing quietly.
_FALLBACK_OFFSET = timezone(timedelta(hours=3), name="EEST-fallback")

try:
    CAIRO: ZoneInfo | timezone = ZoneInfo("Africa/Cairo")
    _CAIRO_IS_REAL_TZDATA = True
except ZoneInfoNotFoundError:
    logger.error(
        "tzdata / Africa/Cairo zoneinfo not found on this system — falling back to a "
        "fixed UTC+3 offset. Install the 'tzdata' package (see requirements.txt) to get "
        "correct automatic daylight-saving handling instead of this approximation."
    )
    CAIRO = _FALLBACK_OFFSET
    _CAIRO_IS_REAL_TZDATA = False

_DIGIT_TRANSLATION = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Compensates for the *host machine's* system clock running a few minutes
# fast or slow — a different problem from timezone/DST above. Some shared
# hosting platforms don't keep their clock perfectly synced (NTP drift), so
# datetime.now() itself returns a slightly wrong instant no matter how
# correct the timezone math is. There's no way to fix the underlying OS
# clock from inside the bot process, so this lets an operator who has
# confirmed a consistent drift (e.g. "always ~7 minutes behind") correct for
# it via CLOCK_OFFSET_MINUTES. Set once at startup by config.load_settings().
_clock_offset = timedelta(0)
_TIME_RE = re.compile(
    r"(\d{1,2})(?:[:.]([0-5]\d))?\s*"
    r"(صباحًا|صباحا|الصبح|ص|am|مساء|مساءً|مساءا|المساء|الظهر|ظهر|العصر|بالليل|م|pm)?"
)
_DURATION_HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*ساع")
_DURATION_MINUTES_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:دقيق|د(?:\s|$))")
_DURATION_ENGLISH_HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*h(?:r|our)?s?")
_DURATION_ENGLISH_MINUTES_RE = re.compile(r"(\d+(?:\.\d+)?)\s*m(?:in(?:ute)?s?)?")
_DURATION_LABELS = {
    15: "ربع ساعة", 30: "نصف ساعة", 45: "45 دقيقة", 60: "ساعة", 90: "ساعة ونصف",
    120: "ساعتان", 150: "ساعتان ونصف", 180: "3 ساعات", 240: "4 ساعات",
}


def set_clock_offset_minutes(minutes: int) -> None:
    global _clock_offset
    _clock_offset = timedelta(minutes=minutes)
    if minutes:
        logger.info("Applying manual clock offset of %+d minute(s) to all bot timestamps", minutes)


def cairo_now() -> datetime:
    return datetime.now(CAIRO) + _clock_offset


def timezone_diagnostics() -> dict:
    """Runtime info for startup logging / health checks, so a misconfigured
    server timezone or missing tzdata shows up immediately instead of being
    discovered days later from user complaints."""
    now = cairo_now()
    return {
        "using_real_tzdata": _CAIRO_IS_REAL_TZDATA,
        "cairo_time": now.isoformat(),
        "utc_offset": str(now.utcoffset()),
        "clock_offset_minutes": int(_clock_offset.total_seconds() // 60),
    }


def normalize_digits(text: str) -> str:
    return text.translate(_DIGIT_TRANSLATION)


def format_time(value: datetime) -> str:
    hour = value.hour % 12 or 12
    suffix = "ص" if value.hour < 12 else "م"
    return f"{hour:02d}:{value.minute:02d} {suffix}"


def parse_time(text: str) -> datetime | None:
    normalized = normalize_digits(text.strip().lower()).replace("ً", "")
    now = cairo_now()
    if normalized in {"الآن", "دلوقتي", "حاليا", "حالياً", "now"}:
        return now

    match = _TIME_RE.search(normalized)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    marker = match.group(3) or ""
    if hour > 23:
        return None
    if marker in {"م", "مساء", "مساءً", "مساءا", "المساء", "الظهر", "ظهر", "العصر", "بالليل", "pm"} and hour < 12:
        hour += 12
    elif marker in {"ص", "صباحًا", "صباحا", "الصبح", "am"} and hour == 12:
        hour = 0
    if hour > 23:
        return None
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def parse_duration(text: str) -> int:
    value = normalize_digits(text.strip().lower())
    if value in {"ساعة", "ساعه", "1 ساعة", "1 ساعه", "1h", "1 hour"}:
        return 60
    if "ساعتين ونص" in value or "ساعتين ونصف" in value:
        return 150
    if "ساعتين" in value or value in {"2h", "2 ساعة", "2 ساعه", "2"}:
        return 120
    if "ساعة ونص" in value or "ساعة ونصف" in value or "ساعه ونص" in value:
        return 90
    if "ربع ساعة" in value or "ربع ساعه" in value:
        return 15
    if "نص ساعة" in value or "نصف ساعة" in value or "نص ساعه" in value:
        return 30
    match = _DURATION_HOURS_RE.search(value)
    if match:
        return round(float(match.group(1)) * 60)
    match = _DURATION_MINUTES_RE.search(value)
    if match:
        return round(float(match.group(1)))
    match = _DURATION_ENGLISH_HOURS_RE.search(value)
    if match:
        return round(float(match.group(1)) * 60)
    match = _DURATION_ENGLISH_MINUTES_RE.search(value)
    if match:
        return round(float(match.group(1)))
    if value.isdigit():
        number = int(value)
        return number * 60 if number <= 12 else number
    return 0


def duration_label(minutes: int) -> str:
    return _DURATION_LABELS.get(minutes, f"{minutes} دقيقة")
