from __future__ import annotations

from pathlib import Path

from config import load_settings
from main import StudyBot
from storage import Store


def test_admin_settings_and_storage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST")
    monkeypatch.setenv("OWNER_IDS", "42,43")
    monkeypatch.setenv("FORCE_SUBSCRIPTION_CHANNELS", "@study_channel")
    database = tmp_path / "admin.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(database))

    settings = load_settings()
    assert settings.owner_ids == (42, 43)
    assert settings.force_subscription_channels == ("@study_channel",)
    bot = StudyBot(settings)
    try:
        assert bot.is_owner(42)
        assert not bot.is_owner(99)
        store = bot.store
        store.upsert_user(100, 100, "student", "طالب", "")
        store.upsert_user(200, 200, "", "طالبة", "")
        assert store.user_count() == 2
        store.set_blocked(100, True)
        assert store.is_blocked(100)
        store.set_blocked(100, False)
        assert not store.is_blocked(100)
        store.log_event(100, "study_started", "template=1")
        assert store.recent_events(10, 10)[0]["event_type"] == "study_started"
        assert store.bot_totals()["users"] == 2
        assert store.broadcast_targets() == [100, 200]
    finally:
        bot.store.close()
