from __future__ import annotations

from dataclasses import dataclass, field
from html import escape


DEFAULT_MOTIVATION = (
    "لحظة فرحة النتيجة ورؤية الفخر في عيون أهلك هتكون أجمل مكافأة "
    "لكل تعبك، وشعور لا يُعوض يستحق تبذل كل جهدك. 🔥"
)


@dataclass
class GroupCamp:
    """A group/canal camp, used both while composing and after publishing."""

    camp_id: int | None = None
    chat_id: int | None = None
    creator_id: int | None = None
    step: str = "subject"
    subject: str = ""
    lesson: str = ""
    topics: list[str] = field(default_factory=list)
    time_range: str = ""
    motivation: str = DEFAULT_MOTIVATION
    link: str = ""
    start_time: str = ""
    duration_minutes: int = 0
    starts_at: float = 0.0
    ends_at: float = 0.0
    status: str = "draft"
    announcement_message_id: int | None = None
    participants: set[int] = field(default_factory=set)


def _lines(value: str, limit: int = 8) -> list[str]:
    return [line.strip()[:180] for line in value.splitlines() if line.strip()][:limit]


def render_group_camp(camp: GroupCamp) -> str:
    """Render a Telegram HTML announcement in the requested Egyptian style."""
    topics = camp.topics or ["ابدأ بالجزء المحدد من الدرس"]
    topic_lines = "\n".join(f"• {escape(topic)}" for topic in topics)
    link_line = f" ({escape(camp.link)})" if camp.link else ""
    return (
        "الـسلام عـلـيـكـم... 💗\n"
        "يلا بينا أول معسڪر إنجاز انهارده🌷🎓\n\n"
        "مهام الڪامب 💪🏻\n"
        f"☜ هنذاڪر ⚡ {escape(camp.subject)}\n"
        f"📖 {escape(camp.lesson)}\n"
        f"{topic_lines}\n\n"
        "⏰ مـدة الڪامـب\n"
        f"  من الساعة {escape(camp.time_range)}\n\n"
        "✓ مــهـم\n"
        f"  💕 {escape(camp.motivation)}{link_line}\n\n"
        "📵 إياڪ تفتح الموبايل وقت المعسڪر\n\n"
        "قوم دلوقتي.\n"
        " •• جهّز كتابك وكشكولك، اقفل كل المشتتات، وابدأ بتركيز.\n"
        "صلي علي النبي واسمع الفيديو\n"
        "هشارك سيب ريأڪت 🫆🎮"
    )


def normalize_camp_value(step: str, text: str) -> str | list[str]:
    text = text.strip()
    if step == "topics":
        return _lines(text)
    return text[:500]
