import asyncio
import json
import sqlite3
import tempfile
import time
import unicodedata
from pathlib import Path

root = Path(__file__).parent
for name in ('config.py', 'storage.py', 'utils.py', 'templates.py', 'main.py'):
    source = (root / name).read_text(encoding='utf-8')
    compile(source, name, 'exec')

from templates import CATEGORIES, STUDY_DUAS, TEMPLATES, render_card, preview, random_dua
from storage import Store

assert len(TEMPLATES) == 90
assert len(STUDY_DUAS) >= 40
assert all(random_dua() in STUDY_DUAS for _ in range(20))
assert all(template.dua for template in TEMPLATES)
assert all((root / template.image_path).is_file() for template in TEMPLATES)
assert all('<i>' in render_card({'template_id': template.id, 'duration': 'ساعة', 'start_time': '01:00 م', 'end_time': '02:00 م', 'tasks': ['مراجعة']}) for template in TEMPLATES)
assert all(template.quote in render_card({'template_id': template.id, 'duration': 'ساعة', 'start_time': '01:00 م', 'end_time': '02:00 م', 'tasks': ['مراجعة']}) for template in TEMPLATES)
assert 'Session #7' in render_card({'template_id': 1, 'duration': '25 دقيقة', 'tasks': ['مراجعة'], 'session_number': 7})
assert 'دعاء للمذاكرة' in preview(1)

with tempfile.TemporaryDirectory() as tmp:
    db_path = str(Path(tmp) / 'test.sqlite3')
    store = Store(db_path)
    session = store.get(77)
    session.update({
        'step': 'done', 'template_id': 1, 'duration': 'ساعة',
        'duration_minutes': 60, 'start_time': '01:00 م',
        'tasks': ['مراجعة'], 'total_sessions': 1,
        'total_minutes': 60, 'streak_days': 1,
        'last_study_date': '2026-08-24', 'reminder_ends_at': 0.0,
    })
    store.complete_session(77, session, '02:00 م')
    assert store.get(77)['step'] == 'done'
    assert len(store.recent_history(77, 10)) == 1
    assert store.daily_session_count(77) == 1
    second = dict(session)
    second['total_sessions'] = 2
    store.complete_session(77, second, '03:00 م')
    assert store.daily_session_count(77) == 2
    indexes = {row[1] for row in store.connection.execute("PRAGMA index_list('study_history')")}
    assert 'idx_history_chat_id' in indexes
    indexes = {row[1] for row in store.connection.execute("PRAGMA index_list('sessions')")}
    assert 'idx_sessions_reminder_ends' in indexes
    store.save_media_id('asset:test', 'telegram-file-id')
    assert store.get_media_id('asset:test') == 'telegram-file-id'
    store.close()

from main import StudyBot, available_motivation_videos, available_template_images, build_application
from config import Settings
settings = Settings('123456:TEST', 3000, ':memory:', 20, 180, 720, 0)
bot = StudyBot(settings)
application = build_application(bot)
assert application.update_processor.max_concurrent_updates == 8
assert settings.update_concurrency == 8
assert settings.connection_pool_size == 16
assert settings.pool_timeout_seconds == 5.0
assert settings.timer_update_interval_seconds == 1.0
available_images = available_template_images()
assert len(available_images) == len({str(path) for path in available_images})
assert len(available_images) == 165
assert len(CATEGORIES) == 10
assert len(STUDY_DUAS) == 50
def strip_diacritics(value):
    return ''.join(char for char in unicodedata.normalize('NFKD', value) if unicodedata.category(char) != 'Mn')

assert all(any(keyword in strip_diacritics(dua) for keyword in ('علم', 'مذاكر', 'دراس', 'فهم', 'حفظ', 'راجع', 'تركيز', 'مهم', 'مهام', 'وقت', 'نجاح', 'إنجاز', 'درس', 'معلوم', 'ذاكر', 'اختبار', 'جلس', 'تعلم', 'خط')) for dua in STUDY_DUAS)
assert sum(1 for path in available_images if path.name.startswith('user_')) == 106
assert len(available_motivation_videos()) >= 19
random_images = [str(bot._random_template_image(321)) for _ in range(12)]
assert len(set(random_images)) >= 2
assert all(random_images[i] != random_images[i - 1] for i in range(1, len(random_images)))
assert StudyBot._format_remaining(3661) == '01:01:01'
assert len(StudyBot.rating_keyboard().inline_keyboard) == 2
assert len(StudyBot.completed_tasks_keyboard(10).inline_keyboard) == 3
assert StudyBot._format_remaining(65) == '01:05'
assert 'الوقت المتبقي: 05:00' in StudyBot._timer_caption('<i>بطاقة</i>', 300)
assert 'انتهت الجلسة' in StudyBot._timer_caption('<i>بطاقة</i>', 0)

class FakeBot:
    def __init__(self):
        self.captions = []

    async def edit_message_caption(self, **kwargs):
        self.captions.append(kwargs['caption'])

async def timer_smoke_test():
    timer_bot = StudyBot(settings)
    fake = FakeBot()
    timer_bot.schedule_live_timer(123, 456, '<i>بطاقة</i>', time.time() + 2.1, fake)
    await asyncio.sleep(3.35)
    assert len(fake.captions) >= 2
    assert 'انتهت الجلسة' in fake.captions[-1]

asyncio.run(timer_smoke_test())
print(json.dumps({'status': 'ok', 'templates': len(TEMPLATES), 'application_concurrency': 8}, ensure_ascii=False))
