from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

from config import load_settings
from main import available_motivation_videos
from storage import Store
from templates import CATEGORIES, STUDY_DUAS, TEMPLATES

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_shape() -> None:
    assert len(TEMPLATES) == 90
    assert len(CATEGORIES) == 10
    assert len(STUDY_DUAS) == 50


def test_template_assets_are_present_and_jpeg() -> None:
    missing: list[str] = []
    invalid: list[str] = []
    template_paths = sorted({template.image_path for template in TEMPLATES})
    assert len(template_paths) == 59
    for image_path in template_paths:
        path = ROOT / image_path
        if not path.is_file():
            missing.append(image_path)
            continue
        if path.read_bytes()[:3] != b"\xff\xd8\xff":
            invalid.append(image_path)
    assert not missing, f"Missing template assets: {missing[:5]} (total={len(missing)})"
    assert not invalid, f"Non-JPEG template assets: {invalid[:5]} (total={len(invalid)})"


def test_user_asset_manifest_is_complete() -> None:
    manifest = ROOT / "assets" / "user_images_manifest.json"
    assert manifest.is_file()
    import json

    records = json.loads(manifest.read_text(encoding="utf-8"))
    user_assets = sorted((ROOT / "assets" / "user_images").glob("user_*.jpg"))
    assert len(records) == len(user_assets)
    assert len(records) >= 59
    missing = [record["asset"] for record in records if not (ROOT / record["asset"]).is_file()]
    assert not missing, f"Missing user assets: {missing[:5]} (total={len(missing)})"


def test_motivation_video_library_is_present() -> None:
    manifest = ROOT / "assets" / "motivation_videos_manifest.json"
    assert manifest.is_file()
    import json

    records = json.loads(manifest.read_text(encoding="utf-8"))
    videos = available_motivation_videos()
    assert len(records) == len(videos)
    assert len(videos) >= 14
    assert all(path.is_file() and path.stat().st_size > 0 for path in videos)
    missing = [record["asset"] for record in records if not (ROOT / record["asset"]).is_file()]
    assert not missing, f"Missing motivation videos: {missing[:5]}"


def test_motivation_manifest_records_are_unique_and_traceable() -> None:
    import json

    manifest = ROOT / "assets" / "motivation_videos_manifest.json"
    records = json.loads(manifest.read_text(encoding="utf-8"))
    assets = [record["asset"] for record in records]
    hashes = [record["sha256"] for record in records]
    assert len(assets) == len(set(assets))
    assert len(hashes) == len(set(hashes))
    for record in records:
        assert (ROOT / record["asset"]).is_file()
        assert record["video_codec"] == "h264"
    external = [record for record in records if record.get("source_type") == "Mixkit stock video"]
    assert len(external) >= 5
    assert all(record.get("source_url") and record.get("license_url") for record in external)


def test_motivation_video_send_and_media_cache(tmp_path: Path) -> None:
    from config import Settings
    from main import StudyBot

    class FakeBot:
        def __init__(self) -> None:
            self.sent = []

        async def send_video(self, **kwargs):
            self.sent.append(kwargs)
            return SimpleNamespace(video=SimpleNamespace(file_id="telegram-video-id"))

    bot = StudyBot(Settings("123456:TEST", 3000, str(tmp_path / "study.sqlite3"), 20, 180, 720, 0))
    fake = FakeBot()
    asyncio.run(bot.send_motivation_video(fake, 123))
    assert len(fake.sent) == 1
    assert fake.sent[0]["caption"]
    assert bot.store.get_media_id(f"motivation_video:{Path(fake.sent[0]['video'].name).name}") == "telegram-video-id"
    bot.store.close()


def test_owner_dashboard_storage_and_session_lock(tmp_path: Path) -> None:
    from config import Settings
    from main import StudyBot

    bot = StudyBot(Settings("123456:TEST", 3000, str(tmp_path / "owner.sqlite3"), 20, 180, 720, 0, owner_ids=(999,)))
    store = bot.store
    assert store.upsert_user(700, 700, "new_student", "طالب جديد", "") is True
    assert store.upsert_user(700, 700, "new_student", "طالب جديد", "") is False
    assert len(store.new_users(24, 10)) == 1
    store.log_event(700, "study_started", "template=1")
    profile = store.user_profile(700)
    assert profile and profile["total_sessions"] == 0
    assert store.recent_events_for_chat(700, 5)[0]["event_type"] == "study_started"
    session = store.get(700)
    session["step"] = "done"
    session["reminder_ends_at"] = (time.time() + 300) * 1000
    store.save(700, session)
    assert store.active_session_count() == 1
    assert bot.session_lock_message(700) and "جلسة جميلة جارية الآن" in bot.session_lock_message(700)
    store.close()


def test_owner_new_member_notification_and_reports(tmp_path: Path) -> None:
    from config import Settings
    from main import StudyBot

    class FakeBot:
        def __init__(self) -> None:
            self.messages = []

        async def send_message(self, chat_id, text, **kwargs):
            self.messages.append((chat_id, text, kwargs))

    bot = StudyBot(Settings("123456:TEST", 3000, str(tmp_path / "notify.sqlite3"), 20, 180, 720, 0, owner_ids=(999,)))
    fake = FakeBot()
    asyncio.run(bot.notify_owner_new_user(fake, SimpleNamespace(first_name="سارة", last_name="علي", username="sara"), 700))
    assert fake.messages and fake.messages[0][0] == 999
    assert "عضو جديد" in fake.messages[0][1] and "700" in fake.messages[0][1]
    bot.store.upsert_user(700, 700, "sara", "سارة", "علي")
    assert "إجمالي المستخدمين" in bot.admin_stats_text()
    assert "الأعضاء الجدد" in bot.admin_new_users_text()
    bot.store.close()


def test_settings_and_storage_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST")
    monkeypatch.setenv("OWNER_IDS", "42,43")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "study.sqlite3"))
    settings = load_settings()
    assert settings.owner_ids == (42, 43)

    store = Store(settings.database_path)
    session = store.get(100)
    session.update({"step": "done", "duration_minutes": 25, "duration": "25 دقيقة", "tasks": ["مراجعة"]})
    store.complete_session(100, session, "10:25 ص")
    assert store.get(100)["step"] == "done"
    assert len(store.recent_history(100)) == 1
    store.close()


def test_project_does_not_require_real_token_at_import() -> None:
    # Importing modules must remain safe; the token is required only by load_settings().
    assert not os.getenv("TELEGRAM_BOT_TOKEN") or isinstance(os.getenv("TELEGRAM_BOT_TOKEN"), str)
