from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _csv_text(name: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, "").split(",") if item.strip())


def _csv_int(name: str) -> tuple[int, ...]:
    values = []
    for item in _csv_text(name):
        try:
            values.append(int(item))
        except ValueError as exc:
            raise RuntimeError(f"Invalid integer in {name}: {item!r}") from exc
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    port: int
    database_path: str
    max_tasks: int
    max_task_chars: int
    max_session_minutes: int
    clock_offset_minutes: int
    update_concurrency: int = 8
    connection_pool_size: int = 16
    pool_timeout_seconds: float = 5.0
    timer_update_interval_seconds: float = 1.0
    owner_ids: tuple[int, ...] = ()
    force_subscription_channels: tuple[str, ...] = ()
    motivation_videos_enabled: bool = True


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Invalid {name} value: {raw!r}; use true or false")


def _positive_int(name: str, default: int, maximum: int | None = None) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid {name} value: {raw!r}") from exc
    if value <= 0 or (maximum is not None and value > maximum):
        raise RuntimeError(f"{name} must be between 1 and {maximum or 'infinity'}")
    return value


def _positive_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid {name} value: {raw!r}") from exc
    if value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _signed_int(name: str, default: int, bound: int) -> int:
    # Unlike the other settings, a clock correction can legitimately be
    # negative (server ahead), zero (server correct), or positive (server
    # behind), so this intentionally does not reuse _positive_int's
    # "value <= 0 is invalid" rule.
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid {name} value: {raw!r}") from exc
    if abs(value) > bound:
        raise RuntimeError(f"{name} must be between -{bound} and {bound}")
    return value


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    port = _positive_int("PORT", 3000, 65535)
    max_tasks = _positive_int("MAX_TASKS", 20, 100)
    max_task_chars = _positive_int("MAX_TASK_CHARS", 180, 1000)
    max_session_minutes = _positive_int("MAX_SESSION_MINUTES", 12 * 60, 24 * 60)
    # Compensates for host server clock drift (common on shared hosting),
    # not timezone. A positive value means the server clock reads behind the
    # real time, so we add minutes to correct it; negative means it's ahead.
    clock_offset_minutes = _signed_int("CLOCK_OFFSET_MINUTES", 0, 120)
    update_concurrency = _positive_int("UPDATE_CONCURRENCY", 8, 32)
    connection_pool_size = _positive_int("CONNECTION_POOL_SIZE", 16, 64)
    pool_timeout_seconds = _positive_float("POOL_TIMEOUT_SECONDS", 5.0, 1.0, 30.0)
    timer_update_interval_seconds = _positive_float("TIMER_UPDATE_SECONDS", 1.0, 0.5, 60.0)
    owner_ids = _csv_int("OWNER_IDS")
    if not owner_ids and os.getenv("OWNER_ID", "").strip():
        owner_ids = _csv_int("OWNER_ID")
    force_subscription_channels = _csv_text("FORCE_SUBSCRIPTION_CHANNELS")
    motivation_videos_enabled = _bool_env("MOTIVATION_VIDEOS_ENABLED", True)
    data_dir = os.getenv("DATA_DIR", "").strip()
    database_path = os.getenv("DATABASE_PATH", "").strip()
    if not database_path:
        database_path = str(Path(data_dir) / "study_bot.sqlite3") if data_dir else "study_bot.sqlite3"

    return Settings(
        telegram_bot_token=token,
        port=port,
        database_path=database_path,
        max_tasks=max_tasks,
        max_task_chars=max_task_chars,
        max_session_minutes=max_session_minutes,
        clock_offset_minutes=clock_offset_minutes,
        update_concurrency=update_concurrency,
        connection_pool_size=connection_pool_size,
        pool_timeout_seconds=pool_timeout_seconds,
        timer_update_interval_seconds=timer_update_interval_seconds,
        owner_ids=owner_ids,
        force_subscription_channels=force_subscription_channels,
        motivation_videos_enabled=motivation_videos_enabled,
    )
