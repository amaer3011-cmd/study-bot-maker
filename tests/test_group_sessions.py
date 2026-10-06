from __future__ import annotations

from pathlib import Path

from group_sessions import GroupCamp, normalize_camp_value, render_group_camp


def test_group_camp_rendering_and_normalization() -> None:
    root = Path(__file__).resolve().parents[1]
    compile((root / "main.py").read_text(encoding="utf-8"), "main.py", "exec")
    camp = GroupCamp(
        subject="الفيزياء",
        lesson="الفصل الأول | القياس الفيزيائي",
        topics=["عناصر عملية القياس", "الكميات الأساسية والمشتقة"],
        time_range="10:00 إلى 12:00",
        motivation="شد حيلك وهتفرح بالنتيجة.",
        link="https://t.me/elmologia",
    )
    text = render_group_camp(camp)
    assert "الفيزياء" in text
    assert "عناصر عملية القياس" in text
    assert "10:00 إلى 12:00" in text
    assert "https://t.me/elmologia" in text
    assert "•" in text
    assert "<" not in text.replace("https://t.me/elmologia", "")
    assert normalize_camp_value("topics", "أ\n\nب\nج") == ["أ", "ب", "ج"]
    assert normalize_camp_value("subject", "  رياضيات  ") == "رياضيات"
