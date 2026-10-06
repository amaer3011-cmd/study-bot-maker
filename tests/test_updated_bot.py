from __future__ import annotations

import asyncio
import hashlib
import json
import time
import unicodedata
from pathlib import Path

from config import Settings
from main import StudyBot, available_motivation_videos, available_template_images, build_application
from storage import Store
from templates import CATEGORIES, STUDY_DUAS, TEMPLATES, preview, random_dua, render_card

ROOT = Path(__file__).resolve().parents[1]


def _strip_diacritics(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", value) if unicodedata.category(char) != "Mn")


def test_updated_bot_integration(tmp_path: Path) -> None:
    assert len(TEMPLATES) == 90
    assert len(STUDY_DUAS) == 50
    assert len(CATEGORIES) == 10
    assert all(random_dua() in STUDY_DUAS for _ in range(20))
    assert all(template.dua for template in TEMPLATES)
    assert all((ROOT / template.image_path).is_file() for template in TEMPLATES)
    assert all(
        "<i>" in render_card({"template_id": template.id, "duration": "ساعة", "start_time": "01:00 م", "end_time": "02:00 م", "tasks": ["مراجعة"]})
        for template in TEMPLATES
    )
    assert all(
        template.quote in render_card({"template_id": template.id, "duration": "ساعة", "start_time": "01:00 م", "end_time": "02:00 م", "tasks": ["مراجعة"]})
        for template in TEMPLATES
    )
    assert "Session #7" in render_card({"template_id": 1, "duration": "25 دقيقة", "tasks": ["مراجعة"], "session_number": 7})
    assert "دعاء للمذاكرة" in preview(1)
    for dua in STUDY_DUAS:
        assert any(
            keyword in _strip_diacritics(dua)
            for keyword in ("علم", "مذاكر", "دراس", "فهم", "حفظ", "راجع", "تركيز", "مهم", "مهام", "وقت", "نجاح", "إنجاز", "درس", "معلوم", "ذاكر", "اختبار", "جلس", "تعلم", "خط")
        )

    image_manifest = json.loads((ROOT / "assets/user_images_manifest.json").read_text(encoding="utf-8"))
    available_images = available_template_images()
    user_images = [path for path in available_images if path.parent.name == "user_images"]
    assert len(available_images) == len({str(path) for path in available_images})
    assert len(user_images) == len(image_manifest)
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"] for path, record in zip(sorted(user_images), image_manifest))
    assert len(available_motivation_videos()) == len(json.loads((ROOT / "assets/motivation_videos_manifest.json").read_text(encoding="utf-8")))

    db_path = str(tmp_path / "study.sqlite3")
    store = Store(db_path)
    try:
        session = store.get(77)
        session.update({
            "step": "done", "template_id": 1, "duration": "ساعة",
            "duration_minutes": 60, "start_time": "01:00 م", "tasks": ["مراجعة"],
            "total_sessions": 1, "total_minutes": 60, "streak_days": 1,
            "last_study_date": "2026-08-24", "reminder_ends_at": 0.0,
        })
        store.complete_session(77, session, "02:00 م")
        assert store.get(77)["step"] == "done"
        assert len(store.recent_history(77, 10)) == 1
        assert store.daily_session_count(77) == 1
        second = dict(session)
        second["total_sessions"] = 2
        store.complete_session(77, second, "03:00 م")
        assert store.daily_session_count(77) == 2
        assert "idx_history_chat_id" in {row[1] for row in store.connection.execute("PRAGMA index_list('study_history')")}
        assert "idx_sessions_reminder_ends" in {row[1] for row in store.connection.execute("PRAGMA index_list('sessions')")}
        store.save_media_id("asset:test", "telegram-file-id")
        assert store.get_media_id("asset:test") == "telegram-file-id"
    finally:
        store.close()

    default_settings = Settings("123456:TEST", 3000, ":memory:", 20, 180, 720, 0)
    default_bot = StudyBot(default_settings)
    try:
        application = build_application(default_bot)
        assert application.update_processor.max_concurrent_updates == 8
        assert default_settings.timer_update_interval_seconds == 10.0
        assert default_settings.connection_pool_size == 16
        assert default_settings.pool_timeout_seconds == 5.0
    finally:
        default_bot.store.close()

    timer_settings = Settings("123456:TEST", 3000, ":memory:", 20, 180, 720, 0, timer_update_interval_seconds=1.0)

    class FakeBot:
        def __init__(self) -> None:
            self.captions: list[str] = []

        async def edit_message_caption(self, **kwargs) -> None:
            self.captions.append(kwargs["caption"])

    async def timer_smoke_test() -> None:
        timer_bot = StudyBot(timer_settings)
        fake = FakeBot()
        try:
            timer_bot.schedule_live_timer(123, 456, "<i>بطاقة</i>", time.time() + 2.1, fake)
            await asyncio.sleep(3.35)
            assert len(fake.captions) >= 2
            assert "انتهت الجلسة" in fake.captions[-1]
        finally:
            timer_bot.store.close()

    asyncio.run(timer_smoke_test())
