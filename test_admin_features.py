import os
import tempfile
from pathlib import Path

os.environ['TELEGRAM_BOT_TOKEN'] = '123456:TEST'
os.environ['OWNER_IDS'] = '42,43'
os.environ['FORCE_SUBSCRIPTION_CHANNELS'] = '@study_channel'

from config import load_settings
from storage import Store
from main import StudyBot

settings = load_settings()
assert settings.owner_ids == (42, 43)
assert settings.force_subscription_channels == ('@study_channel',)
assert StudyBot(settings).is_owner(42)
assert not StudyBot(settings).is_owner(99)

with tempfile.TemporaryDirectory() as tmp:
    store = Store(str(Path(tmp) / 'admin.sqlite3'))
    store.upsert_user(100, 100, 'student', 'طالب', '')
    store.upsert_user(200, 200, '', 'طالبة', '')
    assert store.user_count() == 2
    store.set_blocked(100, True)
    assert store.is_blocked(100)
    store.set_blocked(100, False)
    assert not store.is_blocked(100)
    store.log_event(100, 'study_started', 'template=1')
    assert store.recent_events(10, 10)[0]['event_type'] == 'study_started'
    totals = store.bot_totals()
    assert totals['users'] == 2
    assert store.broadcast_targets() == [100, 200]
    store.close()

print({'status': 'ok', 'owner_ids': settings.owner_ids, 'channels': settings.force_subscription_channels})
