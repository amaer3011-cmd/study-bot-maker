from __future__ import annotations

from functools import lru_cache

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from templates import CATEGORIES


def _button(text: str, style: str, **kwargs) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, style=style, **kwargs)


@lru_cache(maxsize=1)
def categories_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(CATEGORIES), 2):
        rows.append([
            _button(category[1], "primary", callback_data=f"cat:{category[0]}")
            for category in CATEGORIES[index:index + 2]
        ])
    rows.append([_button("🎲 تصميم عشوائي", "success", callback_data="random")])
    rows.append([
        _button("📊 إحصائياتي", "primary", callback_data="stats"),
        _button("🕘 سجل الجلسات", "primary", callback_data="history"),
    ])
    return InlineKeyboardMarkup(rows)


@lru_cache(maxsize=128)
def template_nav_keyboard(category_id: int, template_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            _button("◀️ السابق", "primary", callback_data=f"nav:{category_id}:prev"),
            _button("✅ اختيار هذا التصميم", "success", callback_data=f"pick:{template_id}"),
            _button("التالي ▶️", "primary", callback_data=f"nav:{category_id}:next"),
        ],
        [
            _button("🔙 الأقسام", "primary", callback_data="start"),
            _button("🎲 تصميم عشوائي", "success", callback_data="random"),
        ],
    ])


@lru_cache(maxsize=1)
def time_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("⚡ الآن فورًا", "success", callback_data="time:now")],
        [
            _button("🔄 تصميم مختلف", "primary", callback_data="start"),
            _button("❌ إلغاء", "danger", callback_data="cancel_flow"),
        ],
    ])


@lru_cache(maxsize=1)
def duration_keyboard() -> InlineKeyboardMarkup:
    short = (15, 30, 45)
    medium = (60, 90, 120)
    long_ = (150, 180, 240)
    rows = [
        [_button(f"{minutes} دقيقة", "primary", callback_data=f"dur:{minutes}") for minutes in group]
        for group in (short, medium, long_)
    ]
    rows.append([
        _button("◀️ رجوع", "primary", callback_data="back_to_time"),
        _button("❌ إلغاء", "danger", callback_data="cancel_flow"),
    ])
    return InlineKeyboardMarkup(rows)


@lru_cache(maxsize=2)
def task_keyboard(has_tasks: bool) -> InlineKeyboardMarkup:
    rows = [[_button("✅ انتهيت من إضافة المهام", "success", callback_data="finalize")]]
    second_row = []
    if has_tasks:
        second_row.append(_button("↩️ حذف آخر مهمة", "danger", callback_data="del_last"))
    second_row.append(_button("❌ إلغاء الجلسة", "danger", callback_data="cancel_flow"))
    rows.append(second_row)
    return InlineKeyboardMarkup(rows)


@lru_cache(maxsize=1)
def final_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("📝 جلسة جديدة", "success", callback_data="new")],
        [
            _button("📊 إحصائياتي", "primary", callback_data="stats"),
            _button("🕘 سجل الجلسات", "primary", callback_data="history"),
        ],
    ])


def timer_keyboard(share_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("🔗 مشاركة المؤقت", "primary", url=share_url)],
        *final_keyboard().inline_keyboard,
    ])


def completed_tasks_keyboard(total: int) -> InlineKeyboardMarkup:
    total = max(1, min(int(total), 20))
    values = list(range(0, total + 1))
    rows = []
    for index in range(0, len(values), 5):
        row = []
        for value in values[index:index + 5]:
            style = "danger" if value == 0 else "success" if value == total else "primary"
            row.append(_button(f"{value}/{total}", style, callback_data=f"done_count:{value}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


@lru_cache(maxsize=1)
def rating_keyboard() -> InlineKeyboardMarkup:
    def rating_style(value: int) -> str:
        if value <= 4:
            return "danger"
        if value >= 8:
            return "success"
        return "primary"

    return InlineKeyboardMarkup([
        [_button(str(value), rating_style(value), callback_data=f"rate:{value}") for value in range(1, 6)],
        [_button(str(value), rating_style(value), callback_data=f"rate:{value}") for value in range(6, 11)],
    ])


@lru_cache(maxsize=1)
def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("🟢 حالة البوت", "success", callback_data="admin:status"), _button("📊 الإحصائيات", "primary", callback_data="admin:stats")],
        [_button("👥 كل الأعضاء", "primary", callback_data="admin:users"), _button("🆕 أعضاء جدد", "primary", callback_data="admin:new_users")],
        [_button("🔎 بحث عن عضو", "primary", callback_data="admin:lookup"), _button("🧾 السجلات", "primary", callback_data="admin:logs")],
        [_button("🚫 حظر عضو", "danger", callback_data="admin:block"), _button("✅ فك الحظر", "success", callback_data="admin:unblock")],
        [_button("📢 إذاعة رسالة", "primary", callback_data="admin:broadcast"), _button("⏹ إيقاف الإذاعة", "danger", callback_data="admin:broadcast_cancel")],
        [_button("🔒 الاشتراك الإجباري", "primary", callback_data="admin:subscription")],
    ])


def subscription_keyboard(channels: tuple[str, ...]) -> InlineKeyboardMarkup:
    rows = [[
        _button(f"📢 اشترك في {channel}", "primary", url=f"https://t.me/{channel.lstrip('@')}")
    ] for channel in channels]
    rows.append([_button("✅ تحقّق من الاشتراك", "success", callback_data="subscription:check")])
    return InlineKeyboardMarkup(rows)


def group_camp_keyboard(camp_id: int, participants: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        _button(f"🙋‍♂️ انضم للمعسكر ({participants})", "success", callback_data=f"camp:join:{camp_id}"),
    ]])
