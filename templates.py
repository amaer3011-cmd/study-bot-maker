from __future__ import annotations

from dataclasses import dataclass
import random
from functools import lru_cache
from html import escape


@dataclass(frozen=True)
class Template:
    id: int
    name: str
    icon: str
    quote: str
    divider: str
    bullet: str
    category_id: int
    dua: str
    image_path: str


_RAW_TEMPLATES = [
    ("🐰 Classic Bunny", "🐰", "Stay focused, you got this!", "━━━━━━━━━━━━━━━━━━━━", "🌿"),
    ("🌙 Night Study", "🌙✨", "The night is yours — make it count", "━━━━━━━━━━━━━━━━━━━━", "🕯️"),
    ("🍵 Aesthetic Coffee", "🍵☕", "Small steps, big progress", "⋆｡°✩ ───────── ✩°｡⋆", "☕"),
    ("🍅 Pomodoro", "🍅⏱️", "Focus. Break. Repeat.", "🍅 ───────────── 🍅", "⏱️"),
    ("🌟 Star Academy", "🌟⭐", "Your effort is your superpower", "✦ ───────────── ✦", "⭐"),
    ("🖤 Dark Mode", "🖤⬛", "Quiet focus, powerful results", "▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪", "▸"),
    ("🌷 Garden Study", "🌷🌿", "Let your knowledge bloom", "🌷 ─────────── 🌷", "🌱"),
    ("🚀 Space Explorer", "🚀🌌", "Explore beyond your limits", "🚀 · · · · · · · 🚀", "🪐"),
    ("👑 Royal Study", "👑💎", "Study like royalty", "♛════════════♛", "♜"),
    ("🐻 Cute Bear", "🐻🍯", "One task at a time", "ʕ•ᴥ•ʔ ─────── ʕ•ᴥ•ʔ", "🍯"),
    ("📋 Study Log", "📋🖊️", "Progress is a daily habit", "┌──────────────────┐", "☐"),
    ("🌊 Flow State", "🌊〰️", "Find your flow", "〰️〰️〰️〰️〰️〰️〰️", "🌊"),
    ("🏆 Champion Mode", "🏆🥇", "Champions keep going", "🏆 ━━━━━━━━━ 🏆", "🥇"),
    ("🌸 Soft Study", "🌸🤍", "Be patient with your progress", "♡ ───────────── ♡", "🌸"),
    ("⚙️ Engineer Mode", "⚙️🔧", "Build your future", "⚙️ ═══════════ ⚙️", "🔧"),
    ("🌅 Morning Session", "🌅☀️", "Start strong, finish proud", "☀️ ───────────── ☀️", "🌞"),
    ("🔮 Mystic Realm", "🔮✨", "Unlock your potential", "✧･ﾟ: *✧･ﾟ:*", "🔮"),
    ("🎧 Music Mode", "🎧🎶", "Tune in and focus", "♫ ───────────── ♫", "🎵"),
    ("🏕️ Forest Camp", "🏕️🌲", "Deep work, fresh mind", "🌲 ─────────── 🌲", "🌲"),
    ("💎 Diamond Focus", "💎✨", "Pressure makes progress", "◇◆◇◆◇◆◇◆◇", "◆"),
    ("🎮 Gamer Mode", "🎮🕹️", "New level unlocked", "🎮 ─────────── 🎮", "🎯"),
    ("🌴 Tropical Vibes", "🌴🌺", "Keep the good energy", "🌴 ~~~~~~~~~ 🌴", "🌺"),
    ("🦁 Beast Mode", "🦁🔥", "Make it happen", "🔥 ━━━━━━━━━ 🔥", "💪"),
    ("❄️ Ice Crystal", "❄️🧊", "Cool mind, clear goals", "❄️ ───────── ❄️", "🔹"),
    ("🔬 Science Lab", "🔬🧪", "Question, learn, discover", "🧪 ────────── 🧪", "🔬"),
    ("🌙 Oriental Dream", "🌙🪷", "Knowledge is a beautiful journey", "☾ ───────── ☽", "🪷"),
    ("🌈 Rainbow Mode", "🌈💫", "Every color, every possibility", "🌈 ───────── 🌈", "🌈"),
    ("🏠 Cozy Corner", "🏠🧸", "Make learning feel like home", "⌂ ─────────── ⌂", "🧸"),
    ("💜 Purple Dream", "💜🪻", "Dream it, study it, achieve it", "💜 ────────── 💜", "🪻"),
    ("🎸 Rock Study", "🎸🤘", "Rock your goals", "🤘 ━━━━━━━━━ 🤘", "🎸"),
    ("🌵 Desert Focus", "🌵☀️", "Grow through every challenge", "🌵 ─────────── 🌵", "☀️"),
    ("🦋 Transform Mode", "🦋🌱", "Become a stronger version of you", "🦋 · · · · · 🦋", "🌱"),
    ("⚡ Lightning Fast", "⚡🚀", "Energy into action", "⚡ ━━━━━━━━━ ⚡", "⚡"),
    ("🎋 Zen Mode", "🎋🧘", "Breathe, focus, learn", "🎋 ─────────── 🎋", "◦"),
    ("🌊 Deep Ocean", "🌊🐚", "Go deeper, go further", "🌊 〰〰〰〰〰 🌊", "🐠"),
    ("📖 Bookworm", "📖🪱", "One page closer to your goal", "📖 ─────────── 📖", "📚"),
    ("🧠 Brain Gym", "🧠💡", "Train your mind every day", "🧠 ═══════════ 🧠", "💡"),
    ("✍️ Writer's Desk", "✍️📜", "Ideas become progress", "✍️ ─────────── ✍️", "📝"),
    ("🧮 Math Master", "🧮➗", "Solve it step by step", "∑ ─────────── ∑", "➤"),
    ("🧑‍💻 Code Sprint", "🧑‍💻💻", "Compile your best day", "</> ───────── </>", "▹"),
    ("🧭 Explorer Notes", "🧭🗺️", "Every lesson is a new path", "🧭 ────────── 🧭", "📍"),
    ("◻️ Minimal White", "◻️▫️", "Less noise, more focus", "────────────────────", "•"),
    ("◼️ Minimal Black", "◼️▪️", "Clear desk, clear mind", "────────────────────", "—"),
    ("📐 Grid Focus", "📐▦", "Put your plan on the map", "┼ ┼ ┼ ┼ ┼ ┼ ┼", "□"),
    ("🗓️ Daily Planner", "🗓️✅", "Make today count", "┌────── TODAY ──────┐", "☑"),
    ("⏳ Deep Work", "⏳🎯", "Protect your focus", "⌛ ─────────── ⌛", "→"),
    ("🇬🇧 English Focus", "🇬🇧📚", "Learn something useful today", "UK ─────────── UK", "✓"),
    ("💬 Language Lab", "💬🗣️", "Practice makes progress", "A B C ─────────", "🔤"),
    ("🌍 Global Learner", "🌍✈️", "Your knowledge has no borders", "🌍 ─────────── 🌍", "📌"),
    ("🎓 Exam Season", "🎓📝", "Prepare with confidence", "🎓 ═══════════ 🎓", "☑"),
    ("🧪 Revision Lab", "🧪📒", "Review, remember, master", "🧪 ─────────── 🧪", "↻"),
    ("🎨 Creative Desk", "🎨🖌️", "Create your own path", "🎨 ~~~~~~~~~ 🎨", "🖌️"),
    ("📸 Vision Board", "📸🌠", "See it, plan it, do it", "📸 ─────────── 📸", "🌠"),
    ("🎬 Director Mode", "🎬🎞️", "Direct your day with purpose", "🎬 ━━━━━━━━━ 🎬", "▶️"),
    ("🪄 Magic Focus", "🪄✨", "A little focus creates magic", "✧ ───────── ✧", "✦"),
    ("🎭 Stage Study", "🎭🎟️", "Show up for your goals", "🎭 ────────── 🎭", "🎟️"),
    ("🌤️ Calm Day", "🌤️☁️", "Steady progress is still progress", "☁️ ───────── ☁️", "☁️"),
    ("🫖 Slow Learning", "🫖🌿", "Learn deeply, not hurriedly", "🫖 ───────── 🫖", "🍃"),
    ("🧘 Mindful Study", "🧘🌾", "One breath, one task", "· · · ─── · · ·", "○"),
    ("🌼 Fresh Start", "🌼🌱", "You can begin again today", "🌼 ───────── 🌼", "🌱"),
    ("📌 Priority Mode", "📌🚦", "Do what matters first", "📌 ═════════ 📌", "🔴"),
    ("🚦 Focus Signal", "🚦🎯", "Green light for your goals", "🚦 ───────── 🚦", "🟢"),
    ("🛡️ Study Shield", "🛡️⚔️", "Guard your time", "🛡️ ━━━━━━━━━ 🛡️", "⚔️"),
    ("🔥 30-Day Challenge", "🔥📅", "Consistency changes everything", "🔥 ───────── 🔥", "✅"),
    ("🏁 Finish Line", "🏁🏃", "Keep moving to the finish", "🏁 ───────── 🏁", "🏃"),
    ("💯 Perfect Score", "💯🏅", "Aim high, improve daily", "💯 ═════════ 💯", "🏅"),
    ("🎯 Target Lock", "🎯🔒", "One goal, full attention", "🎯 ───────── 🎯", "⊙"),
    ("🧱 Build Habit", "🧱🔨", "Small bricks build big results", "🧱 ───────── 🧱", "▪"),
    ("🌌 Cosmic Focus", "🌌🛰️", "Your goals are worth the journey", "✦ · · · · · ✦", "🛰️"),
    ("🧿 Lucky Focus", "🧿🍀", "Good habits, good outcomes", "🍀 ───────── 🍀", "☘️"),
    # Quiet and elegant collection: restrained icons, calm dividers, and low visual noise.
    ("Quiet Linen", "◦", "خذ وقتك، التقدم الهادئ ما زال تقدمًا", "────────────────", "·"),
    ("Calm Morning", "☁️", "ابدأ بخطوة صغيرة وواضحة", "·  ·  ·  ·  ·  ·  ·", "○"),
    ("Still Water", "⌁", "الوضوح يأتي مع الهدوء", "⌁  ─────────  ⌁", "—"),
    ("Soft Balance", "○", "وازن بين التركيز والراحة", "○  ─────────  ○", "◦"),
    ("Simple Notes", "✎", "اكتب ما يهم واترك الباقي", "┄┄┄┄┄┄┄┄┄┄", "·"),
    ("Pastel Focus", "◌", "مساحة هادئة لعقل أكثر صفاءً", "◌  ─────────  ◌", "○"),
    ("Lavender Page", "❧", "كل صفحة تقربك من هدفك", "❧  ─────────  ❧", "·"),
    ("Rose Paper", "⌑", "تعلم بلطف وثبات", "⌑  ─────────  ⌑", "◦"),
    ("Sage Desk", "⌇", "رتب يومك بهدوء", "⌇  ─────────  ⌇", "—"),
    ("Ivory Plan", "□", "خطة واضحة ليوم أخف", "□  ─────────  □", "□"),
    ("Paper & Tea", "☕", "جلسة بسيطة، أثر كبير", "☕  ─────────  ☕", "·"),
    ("Quiet Library", "▱", "بين السطور تنمو الأفكار", "▱  ─────────  ▱", "›"),
    ("Clean Horizon", "—", "اترك أمامك مساحة للإنجاز", "──────────────", "·"),
    ("Cloud Notes", "☁", "فكرة واحدة في كل مرة", "☁  ─────────  ☁", "○"),
    ("Warm Sand", "◇", "ثبات بسيط كل يوم", "◇  ─────────  ◇", "·"),
    ("Morning Paper", "⌂", "اجعل صباحك مرتبًا وهادئًا", "⌂  ─────────  ⌂", "—"),
    ("Silent Bloom", "❀", "دع عاداتك تنمو بلا استعجال", "❀  ─────────  ❀", "◦"),
    ("Minimal Moon", "☾", "هدوء الليل يساعد على التركيز", "☾  ─────────  ☽", "·"),
    ("Clear Mind", "◍", "أقل تشتيتًا، أكثر حضورًا", "◍  ─────────  ◍", "○"),
    ("Elegant Finish", "✧", "أنهِ يومك وأنت راضٍ عن خطوتك", "✧  ─────────  ✧", "·"),
]


# Refined, calm study quotes. They rotate deterministically to keep cards
# varied while preserving a consistent Arabic visual identity.
STUDY_QUOTES = (
    "خطوة هادئة اليوم تصنع فرقًا كبيرًا غدًا.",
    "ركّز على ما بين يديك؛ فالإتقان يُبنى من التفاصيل.",
    "ليس المطلوب أن تنجز كل شيء؛ ابدأ بالأهم.",
    "كل صفحة تفهمها تقرّبك من هدفك.",
    "استمر بهدوء؛ فالنتائج الجميلة تحتاج وقتًا.",
    "أنت لا تتسابق مع أحد؛ أنت تبني نسختك الأفضل.",
    "اجعل وقتك مساحة للإنجاز لا ساحة للقلق.",
    "قليلٌ ثابت خيرٌ من كثيرٍ منقطع.",
    "حين يهدأ ذهنك، يظهر طريقك.",
    "التقدم لا يحتاج ضجيجًا؛ يحتاج حضورًا.",
    "ابدأ بما تستطيع، وسيأتي الباقي خطوةً خطوة.",
    "كل جلسة تركيز هي هدية تقدمها لمستقبلك.",
)

# Short, respectful supplications suitable for the bottom of every study card.
# They are rotated deterministically so all templates receive a dua without
# duplicating a large value in every raw template tuple.
STUDY_DUAS = (
    "رَبِّ زِدْنِي عِلْمًا",
    "اللهم افتح عليَّ أبواب الفهم والعلم النافع",
    "اللهم علّمني ما ينفعني، وانفعني بما علّمتني، وزدني علمًا",
    "اللهم ارزقني فهمًا حاضرًا، وحفظًا ثابتًا، وتركيزًا هادئًا",
    "اللهم يسّر لي مذاكرتي، وبارك لي في وقتي وجهدي",
    "اللهم أبعد عني التشتت والكسل في المذاكرة، وقرّب مني النشاط والهمة",
    "اللهم اجعل كل صفحة أقرأها خطوةً نحو النجاح والإتقان",
    "اللهم ثبّت المعلومات في ذهني، وردّها إليّ وقت الحاجة",
    "اللهم ارزقني حسن الفهم وسرعة الاستيعاب وقوة المراجعة",
    "اللهم اجعل وقت المذاكرة مباركًا، ونتيجته طيبةً نافعة",
    "اللهم أعنّي على تنظيم وقتي وترتيب أولوياتي وإنجاز مهامي",
    "اللهم اجعل الصعب سهلًا، والمعلومة واضحة، والحفظ ميسّرًا",
    "اللهم ارزقني صفاء الذهن وهدوء النفس وحسن التركيز",
    "اللهم وفّقني في دراستي، وبلّغني ما أرجو من النجاح",
    "اللهم اجعل بداية مذاكرتي حماسًا، واستمراري ثباتًا، ونهايتي إنجازًا",
    "اللهم بارك في ذاكرتي الدراسية، ونظّم أفكاري، ووسّع مداركي",
    "اللهم اجعل مراجعتي الدراسية نافعة، وإجابتي موفّقة، ونتيجتي مفرحة",
    "اللهم قوِّ عزيمتي على طلب العلم، واصرف عني التسويف",
    "اللهم اجعلني أتعلم بوعي، وأراجع بثبات، وأطبق بإتقان",
    "اللهم ذكّرني ما نسيت، وعلّمني ما جهلت، وزدني فهمًا",
    "اللهم ارزقني طاقةً تكفيني، وهمةً تعينني، ووقتًا يسعني",
    "اللهم اجعل كل دقيقة تركيز سببًا في تقدم حقيقي",
    "اللهم وفّقني لفهم دروسي وربط المعلومات ببعضها",
    "اللهم اجعل علمي نافعًا، وسعيي مباركًا، وطريقي واضحًا",
    "اللهم أعنّي على حل المسائل وفهم القواعد وتثبيت الخطوات",
    "اللهم ارزقني الثقة دون غرور، والاجتهاد دون قلق، والنجاح بتوفيقك",
    "اللهم اجعلني أبدأ مذاكرتي الآن، وأستمر حتى أتمّ مهامي",
    "اللهم خفّف عني رهبة الاختبار، وارزقني إجابةً حاضرة",
    "اللهم اجعل ما أدرسه نورًا في عقلي ونفعًا في مستقبلي",
    "اللهم ارزقني حُسن الاستماع، ودقة الملاحظة، وسرعة الفهم",
    "اللهم اجعل خطتي الدراسية واضحةً وواقعيةً ومثمرةً",
    "اللهم أعنّي على إنهاء مهامي واحدةً تلو الأخرى دون تشتت",
    "اللهم ارزقني الصبر على التعلم، والفرح بكل تقدم صغير",
    "اللهم اجعل تعبي في المذاكرة طريقًا إلى ثمرةٍ جميلة",
    "اللهم افتح لي في كل درس بابًا جديدًا من الفهم",
    "اللهم اجعل وقتي منظمًا، وذهني حاضرًا، وقلبي مطمئنًا",
    "اللهم وفّقني لمراجعة ما ينفعني وترك ما يشتتني",
    "اللهم اجعلني أستفيد من أخطائي في الدراسة وأتقدم كل يوم",
    "اللهم ارزقني إنجازًا دراسيًا يرضيني، ونتيجةً تفرح قلبي",
    "اللهم اجعل نهاية جلسة مذاكرتي فخرًا بما أنجزت، ودافعًا لما هو قادم",
    "اللهم أعنّي على الاستمرار في المذاكرة حين يقل الحماس، وثبّتني حين أتعب",
    "اللهم اجعل علمي حجةً لي، ونجاحي بابًا لنفع من حولي",
    "اللهم بارك في كل محاولة، وكل مراجعة، وكل خطوة نحو هدفي",
    "اللهم ارزقني عقلًا مرتبًا لفهم دروسي، وقلبًا مطمئنًا، وعملًا متقنًا",
    "اللهم اجعلني أستثمر وقتي فيما يقربني من حلمي",
    "اللهم اجعل دراستي خفيفةً على نفسي، عظيمةَ الأثر في مستقبلي",
    "اللهم وفّقني للإجابة الصحيحة والفهم العميق والتطبيق السليم",
    "اللهم اجعل لكل جهد بذلته ثمرةً أراها في وقتها الجميل",
    "اللهم ارزقني مراجعةً ذكية، وتركيزًا طويلًا، وذاكرةً قوية",
    "اللهم اجعل كل إنجاز صغير دافعًا لي لإكمال طريق العلم",
)


def random_dua() -> str:
    return random.choice(STUDY_DUAS)


def _build_templates() -> list[Template]:
    result: list[Template] = []
    for index, item in enumerate(_RAW_TEMPLATES, 1):
        category_id = ((index - 1) // 9) + 1
        name, icon, _raw_quote, divider, bullet = item
        quote = STUDY_QUOTES[(index - 1) % len(STUDY_QUOTES)]
        dua = STUDY_DUAS[(index - 1) % len(STUDY_DUAS)]
        # Template image paths remain stable for compatibility; the runtime
        # selects from the combined image pool randomly for each chat/session.
        image_number = ((index - 1) % 59) + 1
        image_path = f"assets/templates/template_{image_number:03d}.jpg"
        result.append(Template(index, name, icon, quote, divider, bullet, category_id, dua, image_path))
    return result


TEMPLATES = _build_templates()
TEMPLATE_BY_ID = {template.id: template for template in TEMPLATES}

CATEGORIES = [
    (1, "🌸 جمالي وناعم", "ألوان مريحة وتفاصيل لطيفة", list(range(1, 10))),
    (2, "🌙 ليلي وهادئ", "جلسات تركيز في أجواء دافئة", list(range(10, 19))),
    (3, "🎯 تركيز وإنجاز", "خطط واضحة ومهام متقدمة", list(range(19, 28))),
    (4, "🔥 تحفيزي وتحديات", "طاقة وثبات حتى خط النهاية", list(range(28, 37))),
    (5, "🌿 طبيعة واسترخاء", "هدوء وانتعاش بدون تشتيت", list(range(37, 46))),
    (6, "🎓 أكاديمي ومراجعة", "تعلم ومراجعة واستعداد", list(range(46, 55))),
    (7, "🎨 إبداعي ومميز", "أفكار مختلفة ولمسة شخصية", list(range(55, 64))),
    (8, "📚 قراءة وكتابة", "دفاتر وملاحظات وتعلم متدرج", list(range(64, 73))),
    (9, "◻️ مينيمال وورقي", "بساطة وأناقة ومساحة للتركيز", list(range(73, 82))),
    (10, "✨ هدوء واستمرارية", "جلسات طويلة بعادات ثابتة", list(range(82, 91))),
]
CATEGORY_BY_ID = {category[0]: category for category in CATEGORIES}


def render_card(data: dict, preview: bool = False) -> str:
    template = TEMPLATE_BY_ID.get(data.get("template_id", 1), TEMPLATES[0])
    tasks = data.get("tasks") or ["لا توجد مهام مضافة"]
    timing = data.get("duration", "")
    if data.get("start_time"):
        timing += f"  •  {data['start_time']} → {data.get('end_time', '')}"
    divider = escape(str(template.divider))
    safe_timing = escape(str(timing))
    safe_tasks = "\n".join(
        f"<i>{escape(str(template.bullet))} {i}. {escape(str(task))}</i>"
        for i, task in enumerate(tasks, 1)
    )
    quote = f"<i>❝ {escape(template.quote)} ❞</i>"
    dua = data.get("dua") or template.dua
    dua_html = (
        f"{divider}\n"
        f"<i>🤲 <b>دعاء للمذاكرة</b></i>\n"
        f"<i>❝ {escape(str(dua))} ❞</i>"
    )
    session_number = data.get("session_number")
    session_label = f"Session #{int(session_number)}" if session_number else "Study Session"
    style = int(template.id) % 4

    if style == 0:
        return (
            "<i>(\\_/)</i>\n<i>( •ᴗ•)</i>\n<i>/づ 📚</i>\n\n"
            f"<i><b>𝑺𝒕𝒖𝒅𝒚 𝑻𝒊𝒎𝒆𝒓</b> ⏳</i>\n\n"
            f"{quote}\n{divider}\n"
            f"<i>📌 <b>{session_label}</b></i>\n"
            f"<i>⏱️ {safe_timing}</i>\n\n"
            f"<i>📝 <b>مهامي 🌿:</b></i>\n{safe_tasks}\n\n"
            f"{dua_html}"
        )
    if style == 1:
        return (
            "<pre>┌─────────────────────────────┐</pre>"
            f"\n<i>│  🍅🍅  <b>STUDY SESSION</b> · {session_label}  │</i>"
            "\n<pre>├─────────────────────────────┤</pre>\n"
            f"<i>│  💬 {quote}</i>\n"
            f"<i>│  ⏱️ {safe_timing}</i>\n"
            f"<i>│  ⏳ {escape(str(data.get('duration', '')))}</i>\n"
            "<pre>├─────────────────────────────┤</pre>\n"
            f"<i>│  📋 <b>المهام:</b></i>\n{safe_tasks}\n"
            f"<pre>├─────────────────────────────┤</pre>\n{dua_html}\n"
            "<pre>└─────────────────────────────┘</pre>"
        )
    if style == 2:
        return (
            f"<i>{escape(template.icon)} <b>Deep Focus</b> · {session_label}</i>\n"
            f"<i>✦ {quote} ✦</i>\n{divider}\n"
            f"<i>⏱️ <b>{safe_timing}</b></i>\n"
            f"<i>📋 المهام:</i>\n{safe_tasks}\n\n"
            f"<i>🔥 خطوة واحدة في كل مرة · لا تستسلم</i>\n\n{dua_html}"
        )
    return (
        f"<i>✦ <b>FOCUS MODE</b> · {session_label} ✦</i>\n"
        f"{quote}\n{divider}\n"
        f"<i>🕐 {safe_timing}</i>\n\n"
        f"<i>🌿 <b>مهامي:</b></i>\n{safe_tasks}\n\n"
        f"{dua_html}"
    )


@lru_cache(maxsize=128)
def preview(template_id: int) -> str:
    return render_card({
        "template_id": template_id,
        "duration": "ساعتين",
        "start_time": "04:00 م",
        "end_time": "06:00 م",
        "tasks": ["مراجعة الفصل الأول", "حل تمارين الرياضيات"],
    }, preview=True)
