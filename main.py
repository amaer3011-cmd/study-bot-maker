from __future__ import annotations

import asyncio
import html
import json
import logging
import random
import time
from functools import lru_cache
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import RetryAfter
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Settings, load_settings
from storage import Store, reset_flow
from templates import CATEGORIES, CATEGORY_BY_ID, TEMPLATE_BY_ID, TEMPLATES, preview, random_dua, render_card
from utils import (
    cairo_now,
    duration_label,
    format_time,
    parse_duration,
    parse_time,
    set_clock_offset_minutes,
    timezone_diagnostics,
)


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("study-bot")
APP_STARTED_AT = time.time()
TIMER_UPDATE_INTERVAL_SECONDS = 1.0


@lru_cache(maxsize=1)
def available_template_images() -> tuple[Path, ...]:
    assets_root = Path(__file__).resolve().parent / "assets"
    template_images = assets_root.joinpath("templates").glob("template_*.jpg")
    user_images = assets_root.joinpath("user_images").glob("user_*.jpg")
    return tuple(sorted((*template_images, *user_images)))


@lru_cache(maxsize=1)
def available_motivation_videos() -> tuple[Path, ...]:
    assets_root = Path(__file__).resolve().parent / "assets"
    return tuple(sorted(assets_root.joinpath("motivation_videos").glob("video_*.mp4")))


class StudyBot:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.database_path)
        saved_channels = self.store.get_setting("force_subscription_channels", "")
        if saved_channels:
            try:
                channels = tuple(str(item) for item in json.loads(saved_channels) if str(item).strip())
                self.settings = settings.__class__(**{**settings.__dict__, "force_subscription_channels": channels})
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.warning("Ignoring invalid persisted subscription settings")
        self.nav: dict[int, tuple[int, int]] = {}
        self.reminder_tasks: dict[int, asyncio.Task] = {}
        self.chat_locks: dict[int, asyncio.Lock] = {}
        self.chat_last_seen: dict[int, float] = {}
        self.cleanup_task: asyncio.Task | None = None
        self.timer_tasks: dict[int, asyncio.Task] = {}
        self.last_template_images: dict[int, Path] = {}
        self.last_motivation_videos: dict[int, Path] = {}
        # Telegram file_ids make subsequent previews nearly free: the bot
        # uploads each local image once per process and reuses Telegram's CDN.
        self.template_photo_ids: dict[str, str] = {}
        self.motivation_video_ids: dict[str, str] = {}
        self.admin_pending: dict[int, str] = {}
        self.subscription_cache: dict[int, tuple[float, bool]] = {}
        self.broadcast_task: asyncio.Task | None = None
        self.bot_username: str | None = None

    def chat_lock(self, chat_id: int) -> asyncio.Lock:
        lock = self.chat_locks.get(chat_id)
        self.chat_last_seen[chat_id] = time.monotonic()
        if lock is None:
            lock = asyncio.Lock()
            self.chat_locks[chat_id] = lock
        return lock

    def session_lock_message(self, chat_id: int) -> str | None:
        session = self.store.get(chat_id)
        step = session.get("step", "idle")
        if step == "done" and session.get("reminder_ends_at", 0) > time.time() * 1000:
            remaining = max(0, session["reminder_ends_at"] / 1000 - time.time())
            return (
                "⏳ <b>جلسة جميلة جارية الآن</b>\n\n"
                f"متبقي تقريبًا: <b>{self._format_remaining(remaining)}</b>.\n"
                "خلّيك مع جلستك الحالية، وبعد ما تخلص هتقدر تبدأ جلسة جديدة بكل هدوء 💛📚"
            )
        if step in {"waiting_start_time", "waiting_duration", "waiting_tasks"}:
            return (
                "📝 <b>أنت بالفعل تجهّز جلسة مذاكرة</b>\n\n"
                "كمّل الخطوات الحالية بدل ما نبدأ جلسة ثانية بالخطأ. أنا معك خطوة بخطوة 🤝"
            )
        return None

    async def tell_session_is_locked(self, message, chat_id: int) -> bool:
        notice = self.session_lock_message(chat_id)
        if not notice:
            return False
        await message.reply_text(notice, parse_mode=ParseMode.HTML)
        return True

    async def cleanup_state_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(3600)
                cutoff = time.monotonic() - 24 * 3600
                stale = [
                    chat_id for chat_id, last_seen in self.chat_last_seen.items()
                    if last_seen < cutoff
                    and chat_id not in self.reminder_tasks
                    and chat_id not in self.timer_tasks
                    and not (self.chat_locks.get(chat_id) and self.chat_locks[chat_id].locked())
                ]
                for chat_id in stale:
                    self.chat_last_seen.pop(chat_id, None)
                    self.chat_locks.pop(chat_id, None)
                    self.nav.pop(chat_id, None)
                    self.last_template_images.pop(chat_id, None)
                    self.last_motivation_videos.pop(chat_id, None)
                if stale:
                    logger.info("Cleaned %s inactive chat states", len(stale))
        except asyncio.CancelledError:
            return

    # ---------- Menus ----------
    @staticmethod
    @lru_cache(maxsize=1)
    def categories_keyboard() -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for index in range(0, len(CATEGORIES), 2):
            row = [
                InlineKeyboardButton(category[1], callback_data=f"cat:{category[0]}")
                for category in CATEGORIES[index:index + 2]
            ]
            rows.append(row)
        rows.append([InlineKeyboardButton("🎲 تصميم عشوائي", callback_data="random")])
        rows.append([
            InlineKeyboardButton("📊 إحصائياتي", callback_data="stats"),
            InlineKeyboardButton("🕘 سجل الجلسات", callback_data="history"),
        ])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    @lru_cache(maxsize=128)
    def template_nav_keyboard(category_id: int, template_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("◀️ السابق", callback_data=f"nav:{category_id}:prev"),
                InlineKeyboardButton("✅ اختيار هذا التصميم", callback_data=f"pick:{template_id}"),
                InlineKeyboardButton("التالي ▶️", callback_data=f"nav:{category_id}:next"),
            ],
            [
                InlineKeyboardButton("🔙 الأقسام", callback_data="start"),
                InlineKeyboardButton("🎲 تصميم عشوائي", callback_data="random"),
            ],
        ])

    @staticmethod
    @lru_cache(maxsize=1)
    def time_choice_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ الآن فورًا", callback_data="time:now")],
            [
                InlineKeyboardButton("🔄 تصميم مختلف", callback_data="start"),
                InlineKeyboardButton("❌ إلغاء", callback_data="cancel_flow"),
            ],
        ])

    @staticmethod
    @lru_cache(maxsize=1)
    def duration_keyboard() -> InlineKeyboardMarkup:
        # Grouped by rough length (short / medium / long) instead of a flat
        # chronological list, so the choices read as related clusters rather
        # than an arbitrary row of numbers.
        short = (15, 30, 45)
        medium = (60, 90, 120)
        long_ = (150, 180, 240)
        rows = [
            [InlineKeyboardButton(duration_label(m), callback_data=f"dur:{m}") for m in group]
            for group in (short, medium, long_)
        ]
        rows.append([
            InlineKeyboardButton("◀️ رجوع", callback_data="back_to_time"),
            InlineKeyboardButton("❌ إلغاء", callback_data="cancel_flow"),
        ])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    @lru_cache(maxsize=2)
    def task_keyboard(has_tasks: bool) -> InlineKeyboardMarkup:
        rows = [[InlineKeyboardButton("✅ انتهيت من إضافة المهام", callback_data="finalize")]]
        second_row = []
        if has_tasks:
            second_row.append(InlineKeyboardButton("↩️ حذف آخر مهمة", callback_data="del_last"))
        second_row.append(InlineKeyboardButton("❌ إلغاء الجلسة", callback_data="cancel_flow"))
        rows.append(second_row)
        return InlineKeyboardMarkup(rows)

    @staticmethod
    @lru_cache(maxsize=1)
    def final_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 جلسة جديدة", callback_data="new")],
            [
                InlineKeyboardButton("📊 إحصائياتي", callback_data="stats"),
                InlineKeyboardButton("🕘 سجل الجلسات", callback_data="history"),
            ],
        ])

    @staticmethod
    def timer_keyboard(share_url: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 مشاركة المؤقت", url=share_url)],
            *StudyBot.final_keyboard().inline_keyboard,
        ])

    @staticmethod
    def completed_tasks_keyboard(total: int) -> InlineKeyboardMarkup:
        total = max(1, min(int(total), 20))
        values = list(range(0, total + 1))
        rows = []
        for index in range(0, len(values), 5):
            rows.append([
                InlineKeyboardButton(f"{value}/{total}", callback_data=f"done_count:{value}")
                for value in values[index:index + 5]
            ])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    @lru_cache(maxsize=1)
    def rating_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(str(value), callback_data=f"rate:{value}") for value in range(1, 6)],
            [InlineKeyboardButton(str(value), callback_data=f"rate:{value}") for value in range(6, 11)],
        ])

    @staticmethod
    @lru_cache(maxsize=1)
    def admin_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 حالة البوت", callback_data="admin:status"), InlineKeyboardButton("📊 الإحصائيات", callback_data="admin:stats")],
            [InlineKeyboardButton("👥 كل الأعضاء", callback_data="admin:users"), InlineKeyboardButton("🆕 أعضاء جدد", callback_data="admin:new_users")],
            [InlineKeyboardButton("🔎 بحث عن عضو", callback_data="admin:lookup"), InlineKeyboardButton("🧾 السجلات", callback_data="admin:logs")],
            [InlineKeyboardButton("🚫 حظر عضو", callback_data="admin:block"), InlineKeyboardButton("✅ فك الحظر", callback_data="admin:unblock")],
            [InlineKeyboardButton("📢 إذاعة رسالة", callback_data="admin:broadcast"), InlineKeyboardButton("⏹ إيقاف الإذاعة", callback_data="admin:broadcast_cancel")],
            [InlineKeyboardButton("🔒 الاشتراك الإجباري", callback_data="admin:subscription")],
        ])

    @staticmethod
    def subscription_keyboard(channels: tuple[str, ...]) -> InlineKeyboardMarkup:
        rows = [[InlineKeyboardButton(f"📢 اشترك في {channel}", url=f"https://t.me/{channel.lstrip('@')}")] for channel in channels]
        rows.append([InlineKeyboardButton("✅ تحقّق من الاشتراك", callback_data="subscription:check")])
        return InlineKeyboardMarkup(rows)

    def is_owner(self, user_id: int | None) -> bool:
        return bool(user_id is not None and user_id in self.settings.owner_ids)

    async def has_required_subscriptions(self, user_id: int, bot) -> bool:
        channels = self.settings.force_subscription_channels
        if not channels or self.is_owner(user_id):
            return True
        cached = self.subscription_cache.get(user_id)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        allowed = True
        for channel in channels:
            try:
                member = await bot.get_chat_member(channel, user_id)
                valid = member.status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
                valid = valid or (member.status == ChatMemberStatus.RESTRICTED and bool(getattr(member, "is_member", False)))
                if not valid:
                    allowed = False
                    break
            except Exception:
                logger.warning("Could not verify subscription for %s in %s", user_id, channel, exc_info=True)
                allowed = False
                break
        self.subscription_cache[user_id] = (time.monotonic(), allowed)
        return allowed

    async def ensure_access(self, update: Update, context: ContextTypes.DEFAULT_TYPE, allow_subscription_callback: bool = False) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        message = update.effective_message
        if not user or not chat or not message:
            return False
        is_new_user = self.store.upsert_user(chat.id, user.id, user.username or "", user.first_name or "", user.last_name or "")
        if is_new_user:
            details = f"user_id={user.id},username={user.username or ''},name={user.first_name or ''}"
            self.store.log_event(chat.id, "new_user", details)
            await self.notify_owner_new_user(context.bot, user, chat.id)
        self.store.log_event(chat.id, "update", (update.callback_query.data if update.callback_query else message.text or "")[:120])
        if self.is_owner(user.id):
            return True
        if self.store.is_blocked(chat.id):
            await message.reply_text("🚫 تم إيقاف حسابك عن استخدام البوت.")
            return False
        if self.settings.force_subscription_channels and not (allow_subscription_callback and update.callback_query and update.callback_query.data == "subscription:check"):
            if not await self.has_required_subscriptions(user.id, context.bot):
                await message.reply_text(
                    "🔒 <b>الاشتراك مطلوب لاستخدام البوت</b>\n\nاشترك في القنوات المطلوبة ثم اضغط «تحقّق من الاشتراك».",
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.subscription_keyboard(self.settings.force_subscription_channels),
                )
                return False
        return True

    async def send_categories(self, message) -> None:
        await message.reply_text(
            "📚 <b>اختار قسم التصميمات</b>\n\n"
            f"عندك <b>{len(TEMPLATES)} تصميم</b> في <b>{len(CATEGORIES)} أقسام</b>.\n"
            "تقدر تتصفح التصميمات أو تختار تصميمًا عشوائيًا:",
            parse_mode=ParseMode.HTML,
            reply_markup=self.categories_keyboard(),
        )

    def _random_template_image(self, chat_id: int | None = None) -> Path | None:
        images = available_template_images()
        if not images:
            return None
        previous = self.last_template_images.get(chat_id) if chat_id is not None else None
        choices = tuple(path for path in images if path != previous) or images
        selected = random.choice(choices)
        if chat_id is not None:
            self.last_template_images[chat_id] = selected
        return selected

    def _template_image_path(self, template_id: int) -> Path | None:
        template = TEMPLATE_BY_ID.get(template_id)
        if not template:
            return None
        path = Path(__file__).resolve().parent / template.image_path
        return path if path.is_file() else None

    def _photo_source(self, template_id: int, image_path: Path | None = None):
        path = image_path or self._template_image_path(template_id)
        cache_key = str(path) if path else f"template:{template_id}"
        cached = self.template_photo_ids.get(cache_key)
        if cached:
            return cached
        cached = self.store.get_media_id(cache_key)
        if cached:
            self.template_photo_ids[cache_key] = cached
            return cached
        return path

    def _remember_photo_id(self, image_path: Path | None, template_id: int, sent_message) -> None:
        photos = getattr(sent_message, "photo", None) or []
        if photos:
            cache_key = str(image_path) if image_path else f"template:{template_id}"
            file_id = photos[-1].file_id
            self.template_photo_ids[cache_key] = file_id
            self.store.save_media_id(cache_key, file_id)

    def _random_motivation_video(self, chat_id: int | None = None) -> Path | None:
        videos = available_motivation_videos()
        if not videos:
            return None
        previous = self.last_motivation_videos.get(chat_id) if chat_id is not None else None
        choices = tuple(path for path in videos if path != previous) or videos
        selected = random.choice(choices)
        if chat_id is not None:
            self.last_motivation_videos[chat_id] = selected
        return selected

    def _motivation_video_source(self, video_path: Path | None):
        if video_path is None:
            return None
        cache_key = f"motivation_video:{video_path.name}"
        cached = self.motivation_video_ids.get(cache_key)
        if cached:
            return cached
        cached = self.store.get_media_id(cache_key)
        if cached:
            self.motivation_video_ids[cache_key] = cached
            return cached
        return video_path

    async def send_motivation_video(
        self,
        bot,
        chat_id: int,
        *,
        video_path: Path | None = None,
        retry: bool = False,
    ) -> None:
        if not self.settings.motivation_videos_enabled:
            return
        video_path = video_path or self._random_motivation_video(chat_id)
        source = self._motivation_video_source(video_path)
        if source is None:
            logger.info("No motivation videos available; skipping chat %s", chat_id)
            return
        caption = "🎉 <b>أحسنت! خلصت جلستك الدراسية</b>\n\nاستمر، كل جلسة صغيرة بتقرّبك من هدفك 💛📚"
        cache_key = f"motivation_video:{video_path.name}"
        try:
            if isinstance(source, str):
                sent = await bot.send_video(chat_id=chat_id, video=source, caption=caption, parse_mode=ParseMode.HTML)
            else:
                with source.open("rb") as video:
                    sent = await bot.send_video(chat_id=chat_id, video=video, caption=caption, parse_mode=ParseMode.HTML)
            telegram_video = getattr(sent, "video", None)
            file_id = getattr(telegram_video, "file_id", None)
            if file_id:
                self.motivation_video_ids[cache_key] = file_id
                self.store.save_media_id(cache_key, file_id)
        except RetryAfter as exc:
            if retry:
                logger.warning("Motivation video retry limit reached for %s", chat_id)
                return
            await asyncio.sleep(float(exc.retry_after))
            await self.send_motivation_video(bot, chat_id, video_path=video_path, retry=True)
        except Exception:
            logger.exception("Motivation video failed for %s", chat_id)

    @staticmethod
    def _format_remaining(seconds: float) -> str:
        total = max(0, int(seconds + 0.999))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    @classmethod
    def _timer_caption(cls, base_caption: str, seconds: float) -> str:
        status = "انتهت الجلسة" if seconds <= 0 else f"الوقت المتبقي: {cls._format_remaining(seconds)}"
        return f"{base_caption}\n\n<i>⏳ <b>{status}</b></i>"

    def schedule_live_timer(
        self,
        chat_id: int,
        message_id: int,
        base_caption: str,
        end_at: float,
        bot,
        reply_markup=None,
    ) -> None:
        old_task = self.timer_tasks.pop(chat_id, None)
        if old_task and not old_task.done():
            old_task.cancel()

        async def update_timer() -> None:
            # The initial caption was already sent with the photo; avoid an
            # immediate duplicate edit when its displayed second is unchanged.
            last_caption = self._timer_caption(base_caption, end_at - time.time())
            try:
                while True:
                    remaining = end_at - time.time()
                    caption = self._timer_caption(base_caption, remaining)
                    if caption != last_caption:
                        try:
                            await bot.edit_message_caption(
                                chat_id=chat_id,
                                message_id=message_id,
                                caption=caption,
                                parse_mode=ParseMode.HTML,
                                reply_markup=reply_markup,
                            )
                            last_caption = caption
                        except RetryAfter as exc:
                            await asyncio.sleep(float(exc.retry_after))
                            continue
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(
                        self.settings.timer_update_interval_seconds,
                        max(0.05, remaining),
                    ))
            except asyncio.CancelledError:
                return
            except Exception:
                # A deleted message or a transient Telegram error should stop
                # only this timer, not the main update loop or the reminder.
                logger.debug("Live timer stopped for %s", chat_id, exc_info=True)
            finally:
                current = asyncio.current_task()
                if self.timer_tasks.get(chat_id) is current:
                    self.timer_tasks.pop(chat_id, None)

        self.timer_tasks[chat_id] = asyncio.create_task(
            update_timer(), name=f"live-timer-{chat_id}"
        )

    async def _reply_with_template_photo(
        self,
        message,
        template_id: int,
        caption: str,
        reply_markup=None,
        image_path: Path | None = None,
    ):
        source = self._photo_source(template_id, image_path)
        if source is None:
            return await message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        if isinstance(source, str):
            sent = await message.reply_photo(
                photo=source,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        else:
            with source.open("rb") as photo:
                sent = await message.reply_photo(
                    photo=photo,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
        self._remember_photo_id(image_path, template_id, sent)
        return sent

    async def show_template(self, query, chat_id: int, category_id: int, index: int) -> None:
        category = CATEGORY_BY_ID.get(category_id)
        if not category:
            return
        template_ids = category[3]
        index %= len(template_ids)
        template_id = template_ids[index]
        self.nav[chat_id] = (category_id, index)
        keyboard = self.template_nav_keyboard(category_id, template_id)
        image_path = self._random_template_image(chat_id)
        caption = (
            f"<b>{html.escape(category[1])}</b> — {html.escape(category[2])}\n\n"
            f"{preview(template_id)}\n\n"
            f"<i>التصميم {index + 1} من {len(template_ids)}</i>"
        )
        source = self._photo_source(template_id, image_path)
        try:
            if query.message and query.message.photo and source is not None:
                if isinstance(source, str):
                    media = InputMediaPhoto(media=source, caption=caption, parse_mode=ParseMode.HTML)
                    await query.edit_message_media(media=media, reply_markup=keyboard)
                else:
                    with source.open("rb") as photo:
                        edited = await query.edit_message_media(
                            media=InputMediaPhoto(media=photo, caption=caption, parse_mode=ParseMode.HTML),
                            reply_markup=keyboard,
                        )
                    self._remember_photo_id(image_path, template_id, edited)
            else:
                await self._reply_with_template_photo(query.message, template_id, caption, keyboard, image_path)
        except Exception:
            # A stale text/media message or a Telegram edit limitation should
            # not prevent the user from seeing the next template.
            await self._reply_with_template_photo(query.message, template_id, caption, keyboard, image_path)

    async def get_bot_username(self, bot) -> str:
        if self.bot_username:
            return self.bot_username
        me = await bot.get_me()
        self.bot_username = me.username or ""
        return self.bot_username

    async def send_shared_timer_status(self, message, source_chat_id: int) -> None:
        session = self.store.get(source_chat_id)
        remaining = max(0.0, session.get("reminder_ends_at", 0) / 1000 - time.time())
        if session.get("step") != "done" or not session.get("reminder_ends_at"):
            await message.reply_text("⌛ هذا المؤقت انتهى أو لم يعد متاحًا. ابدأ جلسة جديدة من فضلك.")
            return
        status = "انتهت الجلسة" if remaining <= 0 else f"الوقت المتبقي: <b>{self._format_remaining(remaining)}</b>"
        await message.reply_text(
            "⏳ <b>مؤقت جلسة مذاكرة مشتركة</b>\n\n"
            f"{status}\n\n<i>يمكنك فتح الرابط مرة أخرى لمعرفة الوقت المتبقي.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📝 ابدأ جلستك", callback_data="start")]]),
        )

    # ---------- Commands ----------
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_chat:
            return
        if not await self.ensure_access(update, context):
            return
        chat_id = update.effective_chat.id
        async with self.chat_lock(chat_id):
            args = context.args or []
            if args and args[0].startswith("timer_"):
                try:
                    source_chat_id = int(args[0].split("_", 1)[1])
                except (TypeError, ValueError):
                    source_chat_id = 0
                if source_chat_id:
                    await self.send_shared_timer_status(update.message, source_chat_id)
                    return
            if await self.tell_session_is_locked(update.message, chat_id):
                return
            self.cancel_reminder(chat_id)
            reset_flow(self.store, chat_id)
            self.nav.pop(chat_id, None)
            name = html.escape(update.effective_user.first_name if update.effective_user else "طالب")
            today_sessions = self.store.daily_session_count(chat_id)
            await update.message.reply_text(
                f"أهلاً <b>{name}</b> 📚✨\n\n"
                "أنا <b>Study Bot Maker</b>، هساعدك تعمل بطاقة مذاكرة منظمة وجميلة في خطوات بسيطة:\n\n"
                "1️⃣ اختار تصميمًا مناسبًا\n"
                "2️⃣ حدد وقت البداية والمدة\n"
                "3️⃣ اكتب مهامك\n"
                "4️⃣ استلم بطاقتك وتذكيرك التلقائي\n\n"
                f"🎨 <b>{len(TEMPLATES)} تصميم</b>  •  📂 <b>{len(CATEGORIES)} أقسام</b>\n"
                f"📅 <b>Today: {today_sessions} completed sessions</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📝 ابدأ جلسة مذاكرة", callback_data="start")],
                    [
                        InlineKeyboardButton("📊 إحصائياتي", callback_data="stats"),
                        InlineKeyboardButton("❓ مساعدة", callback_data="help"),
                    ],
                ]),
            )

    async def study(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message and update.effective_chat:
            if not await self.ensure_access(update, context):
                return
            chat_id = update.effective_chat.id
            async with self.chat_lock(chat_id):
                if await self.tell_session_is_locked(update.message, chat_id):
                    return
                self.cancel_reminder(chat_id)
                reset_flow(self.store, chat_id)
                await self.send_categories(update.message)

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message and update.effective_chat:
            if not await self.ensure_access(update, context):
                return
            chat_id = update.effective_chat.id
            async with self.chat_lock(chat_id):
                was_running = self.store.get(chat_id).get("step") == "done" and self.store.get(chat_id).get("reminder_ends_at", 0) > time.time() * 1000
                self.cancel_reminder(chat_id)
                reset_flow(self.store, chat_id)
                notice = (
                    "✅ <b>تم إلغاء الجلسة الجارية.</b>\nلن يصلك تذكيرها، ولما تكون جاهزًا نبدأ جلسة جديدة 🤍"
                    if was_running else
                    "✅ <b>تم إلغاء التجهيز الحالي.</b>\nخذ وقتك، ولما تكون جاهزًا أرسل /study ونبدأ من جديد 🤍"
                )
                await update.message.reply_text(
                    notice,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📝 جلسة جديدة", callback_data="start")]]),
                )

    async def done(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message and update.effective_chat:
            if not await self.ensure_access(update, context):
                return
            chat_id = update.effective_chat.id
            async with self.chat_lock(chat_id):
                await self.finalize(update.message, chat_id)

    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message and update.effective_chat:
            if not await self.ensure_access(update, context):
                return
            chat_id = update.effective_chat.id
            async with self.chat_lock(chat_id):
                await update.message.reply_text(self.stats_text(chat_id, self.store.get(chat_id)), parse_mode=ParseMode.HTML, reply_markup=self.final_keyboard())

    async def history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_chat:
            return
        if not await self.ensure_access(update, context):
            return
        chat_id = update.effective_chat.id
        async with self.chat_lock(chat_id):
            await self.send_history(update.message, chat_id)

    async def send_history(self, message, chat_id: int) -> None:
        rows = self.store.recent_history(chat_id, 10)
        if not rows:
            await message.reply_text(
                "🕘 لا يوجد سجل جلسات حتى الآن.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📝 ابدأ أول جلسة", callback_data="start")]]),
            )
            return
        lines = ["🕘 <b>آخر جلساتك</b>", ""]
        for index, item in enumerate(rows, 1):
            lines.append(f"{index}. Session #{index} — {item['duration_minutes']} دقيقة — {item['task_count']} مهام — {html.escape(item['created_at'][:10])}")
        await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=self.final_keyboard())

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message and await self.ensure_access(update, context):
            await self.send_help(update.message)

    async def send_help(self, message) -> None:
        await message.reply_text(
            "📖 <b>طريقة الاستخدام</b>\n\n"
            "1️⃣ اختار قسمًا ثم تصفح القوالب\n"
            "2️⃣ اضغط اختيار وأدخل وقت البداية\n"
            "3️⃣ اختار المدة أو اكتبها\n"
            "4️⃣ أرسل المهام، كل مهمة في سطر\n"
            "5️⃣ اضغط انتهيت أو أرسل /done\n\n"
            "<b>الأوامر:</b> /study /menu /templates /done /cancel /stats /history /help",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📝 ابدأ جلسة", callback_data="start")]]),
        )

    # ---------- Owner administration ----------
    def owner_required(self, update: Update) -> bool:
        return self.is_owner(update.effective_user.id if update.effective_user else None)

    async def notify_owner_new_user(self, bot, user, chat_id: int) -> None:
        """Notify every other owner once when a chat is first registered."""
        name = " ".join(part for part in (user.first_name, user.last_name) if part).strip() or "بدون اسم"
        username = f"@{user.username}" if user.username else "بدون username"
        text = (
            "🆕 <b>عضو جديد دخل البوت</b>\n\n"
            f"👤 الاسم: <b>{html.escape(name[:80])}</b>\n"
            f"🔗 username: <code>{html.escape(username)}</code>\n"
            f"🆔 chat_id: <code>{chat_id}</code>\n"
            f"🕐 الوقت: <code>{html.escape(cairo_now().strftime('%Y-%m-%d %H:%M'))}</code>"
        )
        deliveries = [
            bot.send_message(owner_id, text, parse_mode=ParseMode.HTML)
            for owner_id in self.settings.owner_ids
            if owner_id != chat_id
        ]
        if deliveries:
            results = await asyncio.gather(*deliveries, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.debug("Could not notify owner about new chat %s", chat_id, exc_info=result)

    def admin_status_text(self) -> str:
        sqlite = "🟢 متصل" if self.store.connection is not None else "🔴 غير متصل — وضع الذاكرة"
        broadcast = "🟡 إذاعة قيد التنفيذ" if self.broadcast_task and not self.broadcast_task.done() else "🟢 لا توجد إذاعة قيد التنفيذ"
        uptime = max(0, int(time.time() - APP_STARTED_AT))
        hours, remainder = divmod(uptime, 3600)
        minutes, seconds = divmod(remainder, 60)
        return (
            "🟢 <b>حالة البوت الآن</b>\n\n"
            f"⚙️ العملية: <b>تعمل</b>\n"
            f"🗄 قاعدة البيانات: <b>{sqlite}</b>\n"
            f"⏱ مدة التشغيل: <b>{hours}س {minutes}د {seconds}ث</b>\n"
            f"🔄 جلسات نشطة أو قيد التنفيذ: <b>{self.store.active_session_count()}</b>\n"
            f"📢 الإذاعة: <b>{broadcast}</b>\n"
            f"🖼 الصور المتاحة: <b>{len(available_template_images())}</b>\n"
            f"🎬 الفيديوهات التحفيزية: <b>{len(available_motivation_videos())}</b>\n"
            f"🧮 أخطاء SQLite المسجلة: <b>{self.store.sqlite_failure_count}</b>\n"
            f"🕐 توقيت القاهرة: <code>{html.escape(cairo_now().strftime('%Y-%m-%d %H:%M:%S'))}</code>"
        )

    @staticmethod
    def _admin_user_line(index: int, item: dict) -> str:
        name = " ".join(part for part in (item.get("first_name", ""), item.get("last_name", "")) if part).strip() or "بدون اسم"
        username = f"@{item['username']}" if item.get("username") else "بدون username"
        status = "🚫" if item.get("is_blocked") else "✅"
        created = str(item.get("created_at", "")).replace("T", " ")[:16]
        seen = str(item.get("last_seen_at", "")).replace("T", " ")[:16]
        return (
            f"{index}. {status} <code>{item.get('chat_id')}</code> — {html.escape(name[:30])} — "
            f"{html.escape(username)}\n   <i>أول دخول: {html.escape(created)} | آخر نشاط: {html.escape(seen)}</i>"
        )

    def admin_new_users_text(self) -> str:
        users = self.store.new_users(24, 30)
        if not users:
            return "🆕 <b>الأعضاء الجدد</b>\n\nلا يوجد عضو جديد خلال آخر 24 ساعة."
        lines = [f"🆕 <b>الأعضاء الجدد خلال آخر 24 ساعة</b> ({len(users)})", ""]
        lines.extend(self._admin_user_line(index, item) for index, item in enumerate(users, 1))
        return "\n".join(lines)[:3900]

    def admin_user_profile_text(self, chat_id: int) -> str:
        profile = self.store.user_profile(chat_id)
        if not profile:
            return f"🔎 لا يوجد عضو مسجل بالمعرّف <code>{chat_id}</code>."
        name = " ".join(part for part in (profile.get("first_name", ""), profile.get("last_name", "")) if part).strip() or "بدون اسم"
        username = f"@{profile['username']}" if profile.get("username") else "بدون username"
        status = "🚫 محظور" if profile.get("is_blocked") else "✅ نشط"
        events = self.store.recent_events_for_chat(chat_id, 6)
        event_lines = []
        for event in events:
            timestamp = html.escape(str(event.get("created_at", "")).replace("T", " ")[-8:])
            details = html.escape(str(event.get("details", ""))[:60])
            event_lines.append(f"<code>{timestamp}</code> — <b>{html.escape(str(event.get('event_type', 'event')))}</b> {details}")
        recent_activity = "\n".join(event_lines) if event_lines else "لا توجد أحداث بعد."
        return (
            "🔎 <b>ملف العضو</b>\n\n"
            f"👤 الاسم: <b>{html.escape(name[:80])}</b>\n"
            f"🔗 username: <code>{html.escape(username)}</code>\n"
            f"🆔 chat_id: <code>{chat_id}</code>\n"
            f"📌 الحالة: <b>{status}</b>\n"
            f"🕐 أول دخول: <code>{html.escape(str(profile.get('created_at', '')).replace('T', ' ')[:19])}</code>\n"
            f"👀 آخر نشاط: <code>{html.escape(str(profile.get('last_seen_at', '')).replace('T', ' ')[:19])}</code>\n"
            f"📚 الجلسة الحالية: <b>{html.escape(str(profile.get('current_step', 'idle')))}</b>\n"
            f"✅ الجلسات المكتملة: <b>{profile.get('total_sessions', 0)}</b>\n"
            f"⏱ دقائق المذاكرة: <b>{profile.get('total_minutes', 0)}</b>\n"
            f"🕘 سجلات الجلسات: <b>{profile.get('history_count', 0)}</b>\n"
            f"🧾 الأحداث المسجلة: <b>{profile.get('event_count', 0)}</b>\n\n"
            f"<b>آخر نشاط:</b>\n{recent_activity}"
        )

    async def admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not self.owner_required(update):
            return
        self.store.log_event(update.effective_chat.id, "admin_panel")
        totals = self.store.bot_totals()
        await update.message.reply_text(
            "🛠 <b>لوحة مالك البوت</b>\n\n"
            "أهلًا يا مالك البوت، من هنا تقدر تتابع كل شيء بهدوء وتتحكم في الإعدادات بأمان.\n\n"
            f"👥 المستخدمون: <b>{totals['users']}</b>  •  🆕 آخر 24 ساعة: <b>{self.store.count_users_since(24, 'created_at')}</b>\n"
            f"🚫 المحظورون: <b>{totals['blocked']}</b>  •  🟢 النشطون اليوم: <b>{self.store.count_users_since(24)}</b>\n"
            f"✅ الجلسات: <b>{totals['sessions']}</b>  •  ⏱ الدقائق: <b>{totals['minutes']}</b>\n\n"
            "اختر القسم الذي تريد مراجعته:",
            parse_mode=ParseMode.HTML,
            reply_markup=self.admin_keyboard(),
        )

    def admin_stats_text(self) -> str:
        totals = self.store.bot_totals()
        recent = self.store.recent_events(10, 500)
        return (
            "📊 <b>إحصائيات البوت</b>\n\n"
            f"👥 إجمالي المستخدمين: <b>{totals['users']}</b>\n"
            f"🆕 أعضاء جدد آخر 24 ساعة: <b>{self.store.count_users_since(24, 'created_at')}</b>\n"
            f"👀 أعضاء نشطون آخر 24 ساعة: <b>{self.store.count_users_since(24)}</b>\n"
            f"🚫 المستخدمون المحظورون: <b>{totals['blocked']}</b>\n"
            f"✅ الجلسات المسجلة: <b>{totals['sessions']}</b>\n"
            f"🔄 جلسات نشطة أو قيد الإعداد: <b>{self.store.active_session_count()}</b>\n"
            f"⏱ إجمالي دقائق المذاكرة: <b>{totals['minutes']}</b>\n"
            f"🧾 أحداث آخر 10 دقائق: <b>{len(recent)}</b>\n"
            f"🗄 حالة SQLite: <b>{'متصل' if self.store.connection is not None else 'وضع الذاكرة'}</b>"
        )

    def admin_users_text(self) -> str:
        users = self.store.list_users(30)
        if not users:
            return "👥 لا توجد بيانات أعضاء مسجلة بعد."
        lines = ["👥 <b>آخر الأعضاء نشاطًا</b>", "", "اضغط «بحث عن عضو» لعرض ملف عضو محدد.", ""]
        lines.extend(self._admin_user_line(index, item) for index, item in enumerate(users, 1))
        return "\n".join(lines)[:3900]

    def admin_logs_text(self) -> str:
        events = self.store.recent_events(10, 80)
        if not events:
            return "🧾 لا توجد سجلات خلال آخر 10 دقائق."
        lines = ["🧾 <b>سجلات آخر 10 دقائق</b>", ""]
        for item in events:
            timestamp = html.escape(item["created_at"].replace("T", " ")[-8:])
            details = html.escape(item["details"][:80])
            lines.append(f"<code>{timestamp}</code> — <code>{item['chat_id']}</code> — <b>{html.escape(item['event_type'])}</b> {details}")
        return "\n".join(lines)[:3900]

    async def admin_subscription(self, message) -> None:
        channels = self.settings.force_subscription_channels
        current = "\n".join(f"• {html.escape(channel)}" for channel in channels) if channels else "لا توجد قنوات مفعّلة حاليًا."
        await message.reply_text(
            "🔒 <b>إدارة الاشتراك الإجباري</b>\n\n"
            f"القنوات الحالية:\n{current}\n\n"
            "اكتب القنوات مفصولة بفواصل، مثل: <code>@channel_one,@channel_two</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ تغيير القنوات", callback_data="admin:sub_set")],
                [InlineKeyboardButton("🗑 إيقاف الاشتراك الإجباري", callback_data="admin:sub_off")],
                [InlineKeyboardButton("🔙 لوحة الأدمن", callback_data="admin:back")],
            ]),
        )

    async def handle_admin_text(self, message, chat_id: int, text: str) -> None:
        action = self.admin_pending.pop(chat_id, "")
        if action in {"block", "unblock"}:
            try:
                target = int(text.strip())
            except ValueError:
                await message.reply_text("⚠️ أرسل رقم chat_id صحيحًا.")
                return
            if target in self.settings.owner_ids:
                await message.reply_text("⚠️ لا يمكن حظر مالك مسجل في OWNER_IDS.")
                return
            blocked = action == "block"
            self.store.set_blocked(target, blocked)
            self.store.log_event(chat_id, action, str(target))
            await message.reply_text(("🚫 تم حظر العضو." if blocked else "✅ تم فك حظر العضو.") + f"\nID: <code>{target}</code>", parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
            return
        if action == "lookup":
            try:
                target = int(text.strip())
            except ValueError:
                await message.reply_text("⚠️ أرسل chat_id رقميًا صحيحًا، مثل <code>123456789</code>.", parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
                return
            self.store.log_event(chat_id, "admin_lookup", str(target))
            await message.reply_text(self.admin_user_profile_text(target), parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
            return
        if action == "broadcast":
            if self.broadcast_task and not self.broadcast_task.done():
                await message.reply_text("⏳ توجد إذاعة قيد التنفيذ بالفعل.")
                return
            broadcast_text = text[:4000]
            self.store.log_event(chat_id, "broadcast_started", broadcast_text[:120])
            self.broadcast_task = asyncio.create_task(self.run_broadcast(message.get_bot(), chat_id, broadcast_text), name="admin-broadcast")
            await message.reply_text("📢 بدأت الإذاعة في الخلفية. سأرسل لك النتيجة عند الانتهاء.")
            return
        if action == "subscription":
            channels = tuple(item.strip() for item in text.split(",") if item.strip())
            self.store.set_setting("force_subscription_channels", json.dumps(channels, ensure_ascii=False))
            self.settings = self.settings.__class__(**{**self.settings.__dict__, "force_subscription_channels": channels})
            self.subscription_cache.clear()
            self.store.log_event(chat_id, "subscription_updated", ",".join(channels))
            await message.reply_text("✅ تم تحديث قنوات الاشتراك الإجباري.", reply_markup=self.admin_keyboard())
            return
        await message.reply_text("لا توجد عملية إدارية معلقة.")

    async def run_broadcast(self, bot, owner_chat_id: int, text: str) -> None:
        targets = self.store.broadcast_targets()
        semaphore = asyncio.Semaphore(8)
        success = 0
        failed = 0

        async def send_one(target: int) -> bool:
            async with semaphore:
                try:
                    await bot.send_message(target, text)
                    return True
                except RetryAfter as exc:
                    await asyncio.sleep(float(exc.retry_after))
                    try:
                        await bot.send_message(target, text)
                        return True
                    except Exception:
                        return False
                except Exception:
                    return False

        results = await asyncio.gather(*(send_one(target) for target in targets))
        success = sum(1 for result in results if result)
        failed = len(results) - success
        self.store.log_event(owner_chat_id, "broadcast_finished", f"success={success},failed={failed}")
        try:
            await bot.send_message(owner_chat_id, f"✅ انتهت الإذاعة.\nتم الإرسال: {success}\nفشل الإرسال: {failed}", reply_markup=self.admin_keyboard())
        except Exception:
            logger.exception("Could not report broadcast result")

    async def _admin_callback_locked(self, query, chat_id: int) -> bool:
        data = query.data or ""
        if not data.startswith("admin:"):
            return False
        if not self.is_owner(query.from_user.id if query.from_user else None):
            await query.message.reply_text("غير مصرح.")
            return True
        self.admin_pending.pop(chat_id, None)
        if data == "subscription:check":
            self.subscription_cache.clear()
            await query.message.reply_text("✅ حساب المالك مستثنى من الاشتراك الإجباري.", reply_markup=self.admin_keyboard())
            return True
        if data == "admin:status":
            await query.message.reply_text(self.admin_status_text(), parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
        elif data == "admin:stats":
            await query.message.reply_text(self.admin_stats_text(), parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
        elif data == "admin:users":
            await query.message.reply_text(self.admin_users_text(), parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
        elif data == "admin:new_users":
            await query.message.reply_text(self.admin_new_users_text(), parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
        elif data == "admin:lookup":
            self.admin_pending[chat_id] = "lookup"
            await query.message.reply_text("🔎 أرسل chat_id العضو، وسأعرض لك حالته وإحصاءاته وسجل نشاطه.", parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
        elif data == "admin:logs":
            await query.message.reply_text(self.admin_logs_text(), parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
        elif data == "admin:block":
            self.admin_pending[chat_id] = "block"
            await query.message.reply_text("🚫 أرسل chat_id العضو المراد حظره.", reply_markup=self.admin_keyboard())
        elif data == "admin:unblock":
            self.admin_pending[chat_id] = "unblock"
            await query.message.reply_text("✅ أرسل chat_id العضو المراد فك حظره.", reply_markup=self.admin_keyboard())
        elif data == "admin:broadcast":
            self.admin_pending[chat_id] = "broadcast"
            await query.message.reply_text("📢 أرسل نص الإذاعة الآن. سيتم الإرسال للمستخدمين غير المحظورين.", reply_markup=self.admin_keyboard())
        elif data == "admin:broadcast_cancel":
            if self.broadcast_task and not self.broadcast_task.done():
                self.broadcast_task.cancel()
                self.store.log_event(chat_id, "broadcast_cancelled")
                await query.message.reply_text("⏹ تم طلب إيقاف الإذاعة الحالية. لن تُرسل رسائل جديدة بعد إلغاء المهمة.", reply_markup=self.admin_keyboard())
            else:
                await query.message.reply_text("ℹ️ لا توجد إذاعة قيد التنفيذ حاليًا.", reply_markup=self.admin_keyboard())
        elif data == "admin:subscription":
            await self.admin_subscription(query.message)
        elif data == "admin:sub_set":
            self.admin_pending[chat_id] = "subscription"
            await query.message.reply_text("✏️ أرسل أسماء القنوات مفصولة بفواصل، ويجب أن يكون البوت عضوًا/مشرفًا فيها للتحقق.")
        elif data == "admin:sub_off":
            self.settings = self.settings.__class__(**{**self.settings.__dict__, "force_subscription_channels": ()})
            self.store.set_setting("force_subscription_channels", "[]")
            self.subscription_cache.clear()
            self.store.log_event(chat_id, "subscription_disabled")
            await query.message.reply_text("✅ تم إيقاف الاشتراك الإجباري.", reply_markup=self.admin_keyboard())
        elif data == "admin:back":
            await query.message.reply_text(self.admin_stats_text(), parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
        return True

    # ---------- Callback buttons ----------
    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        chat_id = query.message.chat_id if query and query.message else None
        if chat_id is None:
            return
        try:
            await query.answer()
        except Exception:
            logger.debug("Could not acknowledge callback", exc_info=True)
        if not await self.ensure_access(update, context, allow_subscription_callback=True):
            return
        async with self.chat_lock(chat_id):
            await self._callback_locked(update, context)

    async def _callback_locked(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        chat_id = query.message.chat_id
        data = query.data or ""

        if await self._admin_callback_locked(query, chat_id):
            return
        if data == "subscription:check":
            self.subscription_cache.pop(query.from_user.id, None)
            if await self.has_required_subscriptions(query.from_user.id, context.bot):
                await query.message.reply_text("✅ تم التحقق من اشتراكك. يمكنك استخدام البوت الآن.")
            else:
                await query.message.reply_text(
                    "⚠️ لم يكتمل الاشتراك بعد.",
                    reply_markup=self.subscription_keyboard(self.settings.force_subscription_channels),
                )
            return

        if data == "start":
            if await self.tell_session_is_locked(query.message, chat_id):
                return
            self.cancel_reminder(chat_id)
            reset_flow(self.store, chat_id)
            self.nav.pop(chat_id, None)
            await self.send_categories(query.message)
            return

        if data.startswith("cat:"):
            try:
                category_id = int(data.split(":", 1)[1])
            except (TypeError, ValueError):
                return
            await self.show_template(query, chat_id, category_id, 0)
            return

        if data.startswith("nav:"):
            parts = data.split(":")
            if len(parts) != 3 or parts[2] not in {"next", "prev"}:
                return
            try:
                category_id = int(parts[1])
            except (TypeError, ValueError):
                return
            old_category, old_index = self.nav.get(chat_id, (category_id, 0))
            if old_category != category_id:
                old_index = 0
            step = 1 if parts[2] == "next" else -1
            await self.show_template(query, chat_id, category_id, old_index + step)
            return

        if data == "random":
            template_id = random.choice(TEMPLATES).id
            await self.choose_template(query.message, chat_id, template_id)
            return

        if data.startswith("done_count:"):
            try:
                completed = int(data.split(":", 1)[1])
            except (TypeError, ValueError):
                return
            session = self.store.get(chat_id)
            planned = len(session.get("tasks") or [])
            if planned <= 0 or completed < 0 or completed > planned:
                return
            ratio = completed / planned
            self.store.log_event(chat_id, "tasks_completed", f"completed={completed},planned={planned}")
            if completed == planned:
                message_text = f"يا سكر 🍬 أنجزت كل مهامك <b>{completed}/{planned}</b>! أداء رائع جدًا، خليك فخور بنفسك 💛"
            elif ratio >= 0.75:
                message_text = f"برافو عليك 🌟 أنجزت <b>{completed}/{planned}</b> من مهامك، إنجاز جميل وقريب جدًا من الكمال!"
            elif ratio >= 0.5:
                message_text = f"شاطر يا بطل 💪 أنجزت <b>{completed}/{planned}</b> من مهامك. خطوة مهمة، وكمل الباقي بهدوء!"
            elif completed > 0:
                message_text = f"أحسنت إنك بدأت 🌱 أنجزت <b>{completed}/{planned}</b> من مهامك. القليل المستمر تقدم حقيقي!"
            else:
                message_text = "ولا يهمك 🤍 حتى لو لم تنجز مهمة اليوم، مجرد رجوعك للمذاكرة بداية جديدة. جرّب جلسة أقصر الآن."
            await query.message.reply_text(
                message_text + "\n\n<b>تقييمك للجلسة من 10؟</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=self.rating_keyboard(),
            )
            return

        if data.startswith("rate:"):
            try:
                rating = max(1, min(10, int(data.split(":", 1)[1])))
            except (TypeError, ValueError):
                return
            self.store.log_event(chat_id, "session_rating", str(rating))
            if rating >= 9:
                text = f"⭐ تقييمك <b>{rating}/10</b> — جلسة ممتازة! حافظ على نفس الروح يا بطل 💛"
            elif rating >= 7:
                text = f"⭐ تقييمك <b>{rating}/10</b> — جلسة جميلة جدًا، ومع خطوة صغيرة إضافية هتبقى أقوى! 🌷"
            elif rating >= 5:
                text = f"⭐ تقييمك <b>{rating}/10</b> — مش بطالة، المرة الجاية نخلّيها أهدى وأفضل معًا 🤍"
            else:
                text = f"⭐ تقييمك <b>{rating}/10</b> — شكرًا على صراحتك. خذ نفسًا وابدأ بخطوة صغيرة، وأنا معك 📚"
            await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=self.final_keyboard())
            return

        if data.startswith("pick:"):
            try:
                template_id = int(data.split(":", 1)[1])
            except (TypeError, ValueError):
                return
            if template_id not in TEMPLATE_BY_ID:
                await query.message.reply_text("⚠️ هذا التصميم لم يعد متاحًا. أرسل /study من فضلك.")
                return
            await self.choose_template(query.message, chat_id, template_id)
            return

        if data == "time:now":
            session = self.store.get(chat_id)
            if session["step"] == "waiting_start_time":
                session["start_time"] = format_time(cairo_now())
                session["step"] = "waiting_duration"
                self.store.save(chat_id, session)
                await self.ask_duration(query.message, session["start_time"])
            return

        if data == "back_to_time":
            session = self.store.get(chat_id)
            if session["step"] != "waiting_duration":
                return
            session["step"] = "waiting_start_time"
            session["duration"] = ""
            session["duration_minutes"] = 0
            self.store.save(chat_id, session)
            await query.message.reply_text(
                "🔁 رجعنا لاختيار وقت البداية للجلسة.\n\n"
                "🕐 <b>الساعة كام هتبدأ؟</b>\nمثال: <code>3 العصر</code> أو <code>9:30 ص</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=self.time_choice_keyboard(),
            )
            return

        if data == "cancel_flow":
            self.cancel_reminder(chat_id)
            reset_flow(self.store, chat_id)
            self.nav.pop(chat_id, None)
            await query.message.reply_text(
                "✅ <b>تم إلغاء الجلسة الحالية.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📝 جلسة جديدة", callback_data="start")]]),
            )
            return

        if data.startswith("dur:"):
            try:
                minutes = int(data.split(":", 1)[1])
            except (TypeError, ValueError):
                return
            if minutes <= 0 or minutes > self.settings.max_session_minutes:
                return
            session = self.store.get(chat_id)
            if session["step"] == "waiting_duration":
                session["duration"] = duration_label(minutes)
                session["duration_minutes"] = minutes
                session["step"] = "waiting_tasks"
                self.store.save(chat_id, session)
                await self.ask_tasks(query.message)
            return

        if data == "finalize":
            await self.finalize(query.message, chat_id)
            return

        if data == "del_last":
            session = self.store.get(chat_id)
            if session["step"] != "waiting_tasks":
                await query.message.reply_text("⚠️ لا توجد جلسة نشطة لتعديل مهامها. أرسل /study للبدء من جديد.")
                return
            if not session["tasks"]:
                await query.message.reply_text("⚠️ لا توجد مهام لحذفها.")
                return
            removed = session["tasks"].pop()
            self.store.save(chat_id, session)
            await query.message.reply_text(
                f"🗑 تم حذف: <i>{html.escape(str(removed))}</i>\n\n{self.task_list(session['tasks'])}",
                parse_mode=ParseMode.HTML,
                reply_markup=self.task_keyboard(has_tasks=bool(session["tasks"])),
            )
            return

        if data == "stats":
            await query.message.reply_text(self.stats_text(chat_id, self.store.get(chat_id)), parse_mode=ParseMode.HTML, reply_markup=self.final_keyboard())
            return

        if data == "history":
            await self.send_history(query.message, chat_id)
            return

        if data == "help":
            await self.send_help(query.message)
            return

        if data == "new":
            if await self.tell_session_is_locked(query.message, chat_id):
                return
            self.cancel_reminder(chat_id)
            reset_flow(self.store, chat_id)
            await self.send_categories(query.message)

    async def choose_template(self, message, chat_id: int, template_id: int) -> None:
        template = TEMPLATE_BY_ID.get(template_id)
        if not template:
            return
        if await self.tell_session_is_locked(message, chat_id):
            return
        session = self.store.get(chat_id)
        session.update({
            "template_id": template_id,
            "step": "waiting_start_time",
            "tasks": [],
            "duration": "",
            "duration_minutes": 0,
            "start_time": "",
        })
        self.store.save(chat_id, session)
        await message.reply_text(
            "✅ تم تجهيز جلسة المذاكرة.\n\n"
            "🕐 <b>الخطوة 1 من 3 — الساعة كام هتبدأ؟</b>\n"
            "مثال: <code>3 العصر</code> أو <code>9:30 ص</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=self.time_choice_keyboard(),
        )

    # ---------- Text input ----------
    async def text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_chat or not update.message.text:
            return
        if not await self.ensure_access(update, context):
            return
        if self.owner_required(update) and self.admin_pending.get(update.effective_chat.id):
            await self.handle_admin_text(update.message, update.effective_chat.id, update.message.text.strip())
            return
        async with self.chat_lock(update.effective_chat.id):
            await self._text_locked(update, context)

    async def _text_locked(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_chat or not update.message.text:
            return
        chat_id = update.effective_chat.id
        session = self.store.get(chat_id)
        text = update.message.text.strip()

        if session["step"] == "waiting_start_time":
            parsed = parse_time(text)
            if not parsed:
                await update.message.reply_text('⚠️ لم أفهم الوقت. جرّب <code>3 العصر</code> أو <code>9:30 ص</code> أو <code>الآن</code>.', parse_mode=ParseMode.HTML)
                return
            session["start_time"] = format_time(parsed)
            session["step"] = "waiting_duration"
            self.store.save(chat_id, session)
            await self.ask_duration(update.message, session["start_time"])
            return

        if session["step"] == "waiting_duration":
            minutes = parse_duration(text)
            if minutes <= 0:
                await update.message.reply_text('⚠️ لم أفهم المدة. جرّب <code>ساعتين</code> أو <code>90 دقيقة</code> أو <code>2h</code>.', parse_mode=ParseMode.HTML)
                return
            if minutes > self.settings.max_session_minutes:
                await update.message.reply_text(
                    f'⚠️ أقصى مدة مسموحة {self.settings.max_session_minutes} دقيقة. جرّب مدة أقصر.',
                    parse_mode=ParseMode.HTML,
                )
                return
            # Store the canonical label derived from the parsed minutes, not
            # the raw free-text the user typed, so the displayed duration on
            # the card always matches duration_minutes exactly.
            session["duration"] = duration_label(minutes)
            session["duration_minutes"] = minutes
            session["step"] = "waiting_tasks"
            self.store.save(chat_id, session)
            await self.ask_tasks(update.message)
            return

        if session["step"] == "waiting_tasks":
            new_tasks = [line.strip()[:self.settings.max_task_chars] for line in text.splitlines() if line.strip()]
            if not new_tasks:
                await update.message.reply_text("⚠️ اكتب مهمة واحدة على الأقل.")
                return
            remaining = max(0, self.settings.max_tasks - len(session["tasks"]))
            if remaining == 0:
                await update.message.reply_text(f"⚠️ وصلت للحد الأقصى وهو {self.settings.max_tasks} مهام.")
                return
            session["tasks"].extend(new_tasks[:remaining])
            session["tasks"] = session["tasks"][:self.settings.max_tasks]
            self.store.save(chat_id, session)
            await update.message.reply_text(
                f"✅ تمت الإضافة. إجمالي المهام: <b>{len(session['tasks'])}</b>\n\n{self.task_list(session['tasks'])}\n\nأضف المزيد أو اضغط انتهيت:",
                parse_mode=ParseMode.HTML,
                reply_markup=self.task_keyboard(has_tasks=True),
            )
            return

        await update.message.reply_text("أرسل /study لإنشاء جلسة مذاكرة جديدة 📚")

    async def ask_duration(self, message, start_time: str) -> None:
        await message.reply_text(
            f"🕐 البداية: <b>{html.escape(start_time)}</b>\n\n"
            "⏱️ <b>الخطوة 2 من 3 — اختار مدة الجلسة:</b>\nأو اكتب المدة بنفسك، مثلاً <code>ساعة ونص</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=self.duration_keyboard(),
        )

    async def ask_tasks(self, message) -> None:
        await message.reply_text(
            "📝 <b>الخطوة 3 من 3 — اكتب مهامك الآن</b>\n"
            f"كل مهمة في سطر، ويمكنك إرسال أكثر من رسالة.\nالحد الأقصى: {self.settings.max_tasks} مهام.\nبعدها اضغط «انتهيت».",
            parse_mode=ParseMode.HTML,
            reply_markup=self.task_keyboard(has_tasks=False),
        )

    @staticmethod
    def task_list(tasks: list[str]) -> str:
        if not tasks:
            return "لا توجد مهام بعد"
        return "\n".join(f"{i}. {html.escape(str(task))}" for i, task in enumerate(tasks, 1))

    def stats_text(self, chat_id: int, session: dict) -> str:
        hours, minutes = divmod(session["total_minutes"], 60)
        total_sessions = session["total_sessions"]
        if total_sessions >= 50:
            badge = "🏆 أسطورة التركيز"
        elif total_sessions >= 20:
            badge = "🥇 بطل المذاكرة"
        elif total_sessions >= 5:
            badge = "🌟 ملتزم ومتميز"
        else:
            badge = "🌱 بداية قوية"
        lines = [
            "📊 <b>إحصائياتك</b>",
            "",
            f"🎖️ الشارة: <b>{badge}</b>",
            f"✅ الجلسات المكتملة: <b>{total_sessions}</b>",
            f"⏱️ وقت المذاكرة: <b>{hours} ساعة و{minutes} دقيقة</b>",
            f"🔥 السلسلة الحالية: <b>{session['streak_days']} يوم</b>",
            f"📅 جلسات اليوم: <b>{self.store.daily_session_count(chat_id)}</b>",
        ]
        history = self.store.recent_history(chat_id, 3)
        if history:
            lines.extend(["", "🕘 <b>آخر الجلسات:</b>"])
            for index, item in enumerate(history, 1):
                lines.append(f"• Session #{index} — {item['duration_minutes']} دقيقة — {item['task_count']} مهام")

        return "\n".join(lines)

    # ---------- Completion and reminders ----------
    async def finalize(self, message, chat_id: int) -> None:
        session = self.store.get(chat_id)
        if session["step"] != "waiting_tasks":
            await message.reply_text("ابدأ جلسة جديدة بإرسال /study")
            return
        if not session["tasks"]:
            await message.reply_text("⚠️ أضف مهمة واحدة على الأقل قبل الإنهاء.")
            return

        start = parse_time(session["start_time"]) or cairo_now()
        end = start + timedelta(minutes=session["duration_minutes"])
        # parse_time always anchors to "today", so a session that starts late
        # at night and runs past midnight would otherwise compute an "end"
        # that looks earlier than "start" on the same calendar day.
        if end <= start:
            end += timedelta(days=1)
        today = cairo_now().date()
        if session["last_study_date"] != today.isoformat():
            previous = datetime.fromisoformat(session["last_study_date"]).date() if session["last_study_date"] else None
            session["streak_days"] = session["streak_days"] + 1 if previous and (today - previous).days == 1 else 1
            session["last_study_date"] = today.isoformat()
        session["total_sessions"] += 1
        session["total_minutes"] += session["duration_minutes"]
        session["step"] = "done"
        session["reminder_ends_at"] = end.timestamp() * 1000
        self.store.complete_session(chat_id, session, format_time(end))
        daily_session_number = self.store.daily_session_count(chat_id)
        dua = random_dua()

        image_path = self._random_template_image(chat_id)

        self.schedule_reminder(
            chat_id,
            max(1, (end - cairo_now()).total_seconds()),
            message.get_bot(),
            expected_ends_at=session["reminder_ends_at"],
        )
        card = render_card({**session, "dua": dua, "end_time": format_time(end), "session_number": daily_session_number})
        if len(card) > 4000:
            # Telegram messages are limited in size. Keep the card readable and
            # avoid paying for a network request that is guaranteed to fail.
            compact_tasks = [str(task)[:120] for task in session["tasks"][:14]]
            card = render_card({
                **session,
                "dua": dua,
                "tasks": compact_tasks,
                "end_time": format_time(end),
                "session_number": daily_session_number,
            })
        try:
            bot_username = await self.get_bot_username(message.get_bot())
        except Exception:
            bot_username = ""
        share_url = f"https://t.me/{bot_username}?start=timer_{chat_id}" if bot_username else ""
        final_markup = self.timer_keyboard(share_url) if share_url else self.final_keyboard()
        template_id = session["template_id"]
        template = TEMPLATE_BY_ID.get(template_id)
        short_caption = (
            f"<i>📚 <b>Study Session #{daily_session_number}</b></i>\n"
            f"<i>✦ {html.escape(template.quote if template else 'أحسنت، استمر بثبات.')} ✦</i>\n"
            f"<i>🤲 <b>دعاء للمذاكرة:</b> {html.escape(dua)}</i>"
        )
        base_caption = card if len(card) + 45 <= 1024 else short_caption
        sent_photo = await self._reply_with_template_photo(
            message,
            template_id,
            self._timer_caption(base_caption, (end - cairo_now()).total_seconds()),
            final_markup if base_caption == card else None,
            image_path,
        )
        self.schedule_live_timer(
            chat_id,
            sent_photo.message_id,
            base_caption,
            end.timestamp(),
            message.get_bot(),
            final_markup if base_caption == card else None,
        )
        if base_caption != card:
            await message.reply_text(card, parse_mode=ParseMode.HTML, reply_markup=final_markup)

    def cancel_reminder(self, chat_id: int) -> None:
        task = self.reminder_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
        timer = self.timer_tasks.pop(chat_id, None)
        if timer and not timer.done():
            timer.cancel()

    def schedule_reminder(
        self,
        chat_id: int,
        seconds: float,
        bot,
        expected_ends_at: float | None = None,
    ) -> None:
        self.cancel_reminder(chat_id)

        async def remind() -> None:
            try:
                await asyncio.sleep(seconds)
                async with self.chat_lock(chat_id):
                    session = self.store.get(chat_id)
                    # A newer session or a cancellation supersedes this task.
                    if expected_ends_at is not None and session["reminder_ends_at"] != expected_ends_at:
                        return
                    session["reminder_ends_at"] = 0
                    self.store.save(chat_id, session)
                await bot.send_message(
                    chat_id,
                    "⏰ <b>خلصت الجلسة يا سكر!</b> 🍬\n\n"
                    "قيّم إنجازك: كم مهمة أنجزت من مهامك؟",
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.completed_tasks_keyboard(len(session.get("tasks") or [])),
                )
                await self.send_motivation_video(bot, chat_id)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Reminder failed for %s", chat_id)
            finally:
                current = asyncio.current_task()
                if self.reminder_tasks.get(chat_id) is current:
                    self.reminder_tasks.pop(chat_id, None)

        self.reminder_tasks[chat_id] = asyncio.create_task(remind(), name=f"reminder-{chat_id}")

    async def restore_reminders(self, application: Application) -> None:
        # cairo_now() is timezone-aware; datetime.now() here would silently
        # assume the server's local clock is UTC, causing reminders restored
        # after a restart to fire at the wrong time whenever that assumption
        # doesn't hold (this was the other half of the timing bug).
        now_ms = cairo_now().timestamp() * 1000
        for chat_id, _session, ends in self.store.pending_reminders():
            self.schedule_reminder(
                chat_id,
                max(1, (ends - now_ms) / 1000),
                application.bot,
                expected_ends_at=ends,
            )


class ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def make_health_handler(store: Store) -> type[BaseHTTPRequestHandler]:
    # Note: returns HTTP 503 when SQLite is unavailable so hosting platforms
    # can detect persistence issues via the health check. The bot itself
    # keeps running on the in-memory fallback (sessions just won't survive a
    # restart), so if your platform auto-restarts on a failed health check,
    # weigh that against surfacing the degraded state — you may prefer to
    # always return 200 and rely on "sqlite_connected" in the body instead.
    class HealthHandler(BaseHTTPRequestHandler):
        def _health_body(self) -> bytes:
            sqlite_ok = store.connection is not None
            tz = timezone_diagnostics()
            payload = {
                "status": "ok" if sqlite_ok and tz["using_real_tzdata"] else "degraded",
                "service": "study-bot-python",
                "uptime_seconds": round(time.time() - APP_STARTED_AT, 2),
                "templates": len(TEMPLATES),
                "categories": len(CATEGORIES),
                "sqlite_connected": sqlite_ok,
                "sqlite_failure_count": store.sqlite_failure_count,
                "last_sqlite_failure_at": store.last_sqlite_failure_at,
                "cairo_time": tz["cairo_time"],
                "using_real_tzdata": tz["using_real_tzdata"],
                "clock_offset_minutes": tz["clock_offset_minutes"],
            }
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        def _send_health(self, include_body: bool = True) -> None:
            body = self._health_body()
            status_code = 200 if store.connection is not None else 503
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if include_body:
                self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path in {"/", "/api/healthz", "/healthz"}:
                self._send_health()
                return
            self.send_response(404)
            self.end_headers()

        def do_HEAD(self):  # noqa: N802
            if self.path in {"/", "/api/healthz", "/healthz"}:
                self._send_health(include_body=False)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            return

    return HealthHandler


def run_health_server(port: int, store: Store) -> None:
    handler = make_health_handler(store)
    server = ReusableHTTPServer(("0.0.0.0", port), handler)
    logger.info("Health server listening on port %s", port)
    server.serve_forever()


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.error:
        logger.error("Unhandled Telegram update error: %s", context.error, exc_info=context.error)
    else:
        logger.error("Unhandled Telegram update error without exception details")


def build_application(bot: StudyBot) -> Application:
    async def initialize(application: Application) -> None:
        try:
            await application.bot.set_my_commands([
                ("start", "بدء البوت"),
                ("study", "إنشاء جلسة مذاكرة"),
                ("stats", "عرض الإحصائيات"),
                ("history", "سجل الجلسات الأخيرة"),
                ("done", "إنهاء إدخال المهام"),
                ("cancel", "إلغاء الجلسة"),
                ("help", "طريقة الاستخدام"),
                ("admin", "لوحة المالك"),
            ])
        except Exception:
            logger.warning("Could not update Telegram command menu", exc_info=True)
        await bot.restore_reminders(application)
        bot.cleanup_task = asyncio.create_task(bot.cleanup_state_loop(), name="state-cleanup")

    async def shutdown(application: Application) -> None:
        if bot.cleanup_task and not bot.cleanup_task.done():
            bot.cleanup_task.cancel()
        for task in (*bot.reminder_tasks.values(), *bot.timer_tasks.values()):
            if not task.done():
                task.cancel()
        bot.reminder_tasks.clear()
        bot.timer_tasks.clear()
        bot.store.close()

    application = (
        ApplicationBuilder()
        .token(bot.settings.telegram_bot_token)
        # Handlers already serialize state per chat_id. This allows unrelated
        # chats to progress concurrently without removing the per-chat guard.
        .concurrent_updates(bot.settings.update_concurrency)
        .connection_pool_size(bot.settings.connection_pool_size)
        .pool_timeout(bot.settings.pool_timeout_seconds)
        .post_init(initialize)
        .post_shutdown(shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("study", bot.study))
    application.add_handler(CommandHandler("menu", bot.study))
    application.add_handler(CommandHandler("templates", bot.study))
    application.add_handler(CommandHandler("cancel", bot.cancel))
    application.add_handler(CommandHandler("stats", bot.stats))
    application.add_handler(CommandHandler("history", bot.history))
    application.add_handler(CommandHandler("help", bot.help))
    application.add_handler(CommandHandler("admin", bot.admin))
    application.add_handler(CommandHandler("done", bot.done))
    application.add_handler(CallbackQueryHandler(bot.callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.text))
    application.add_error_handler(handle_error)
    return application


def main() -> None:
    settings = load_settings()
    set_clock_offset_minutes(settings.clock_offset_minutes)
    diagnostics = timezone_diagnostics()
    logger.info(
        "Timezone check: using_real_tzdata=%s cairo_time=%s utc_offset=%s clock_offset_minutes=%s",
        diagnostics["using_real_tzdata"], diagnostics["cairo_time"], diagnostics["utc_offset"],
        diagnostics["clock_offset_minutes"],
    )
    if not diagnostics["using_real_tzdata"]:
        logger.warning(
            "Running on a fixed UTC+3 fallback offset, not the real Africa/Cairo tzdata. "
            "Times will be wrong for part of the year (Egypt observes DST). Install the "
            "'tzdata' Python package to fix this permanently."
        )
    bot = StudyBot(settings)
    Thread(target=run_health_server, args=(settings.port, bot.store), daemon=True).start()
    application = build_application(bot)
    logger.info("Starting Study Bot: %s templates / %s categories", len(TEMPLATES), len(CATEGORIES))
    application.run_polling(
        poll_interval=0.0,
        timeout=30,
        bootstrap_retries=-1,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
