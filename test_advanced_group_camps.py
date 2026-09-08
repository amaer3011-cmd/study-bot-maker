from pathlib import Path
import tempfile

from storage import Store

with tempfile.TemporaryDirectory() as tmp:
    store = Store(str(Path(tmp) / "camps.sqlite3"))
    camp_id = store.create_group_camp({
        "chat_id": -100123,
        "creator_id": 77,
        "subject": "الفيزياء",
        "lesson": "الفصل الأول",
        "topics": ["القياس", "الوحدات"],
        "time_range": "10:00 ص إلى 12:00 م",
        "motivation": "شد حيلك",
        "link": "https://t.me/example",
        "start_time": "10:00 ص",
        "duration_minutes": 120,
        "starts_at": 100.0,
        "ends_at": 200.0,
        "status": "scheduled",
    })
    assert camp_id is not None
    camp = store.get_group_camp(camp_id)
    assert camp["topics"] == ["القياس", "الوحدات"]
    joined, count = store.add_group_camp_participant(camp_id, {"user_id": 1, "first_name": "طالب"})
    assert joined and count == 1
    joined_again, count_again = store.add_group_camp_participant(camp_id, {"user_id": 1, "first_name": "طالب"})
    assert not joined_again and count_again == 1
    assert store.scheduled_group_camps(150)[0]["id"] == camp_id
    assert store.update_group_camp(camp_id, status="active")
    assert store.get_group_camp(camp_id)["status"] == "active"
    store.close()

print({"status": "ok", "feature": "advanced_group_camps"})
