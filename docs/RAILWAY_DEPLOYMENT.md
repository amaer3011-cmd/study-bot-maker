# نشر Study Bot على Railway

## المتطلبات

يحتاج المشروع إلى Telegram bot token صالح، ومعرّف Telegram رقمي واحد على الأقل داخل `OWNER_IDS` إذا كانت لوحة المالك مطلوبة. لا تضع هذه القيم داخل Git.

## إنشاء الخدمة

اربط مستودع GitHub بالمشروع وأنشئ Service واحدة من مجلد المشروع. يجب أن يكون الملف `Dockerfile` موجودًا في جذر المستودع وبالاسم نفسه مع حرف D كبير؛ Railway يبحث عن هذا الاسم تلقائيًا.

المشروع يحتوي على `railway.json` للتوافق مع خدمات Railway التي ما زالت تستخدم Config as Code القديم. توثيق Railway الحالي يوصي بـInfrastructure as Code عبر `.railway/railway.ts` أو `.railway/railway.py` للمشاريع الجديدة، لذلك اضبط Start Command وHealthcheck يدويًا من إعدادات الخدمة إن لم يقرأ Railway `railway.json`.

أمر التشغيل هو:

```bash
python main.py
```

ونقطة الفحص هي:

```text
/api/healthz
```

يستمع خادم الصحة على قيمة `PORT` التي توفرها Railway. لا تضبط `PORT` يدويًا في الإنتاج إلا إذا كانت إعدادات الخدمة تتطلب ذلك.

## Volume وقاعدة البيانات

أنشئ Volume للخدمة واجعل Mount Path:

```text
/app/data
```

ثم اضبط:

```text
DATA_DIR=/app/data
DATABASE_PATH=/app/data/study_bot.sqlite3
```

Volume يحافظ على ملفات SQLite بين عمليات إعادة التشغيل، لكنه ليس Backup. احتفظ بنسخ خارجية منتظمة من قاعدة البيانات، ويفضل تنفيذ backup بعد إيقاف الخدمة أو باستخدام SQLite backup API.

## Variables

أضف المتغيرات التالية في Railway:

```text
TELEGRAM_BOT_TOKEN=توكن_حقيقي
OWNER_IDS=123456789
FORCE_SUBSCRIPTION_CHANNELS=
DATA_DIR=/app/data
DATABASE_PATH=/app/data/study_bot.sqlite3
MAX_TASKS=20
MAX_TASK_CHARS=180
MAX_SESSION_MINUTES=720
UPDATE_CONCURRENCY=8
CONNECTION_POOL_SIZE=16
POOL_TIMEOUT_SECONDS=5
TIMER_UPDATE_SECONDS=1
MOTIVATION_VIDEOS_ENABLED=true
```

اترك `PORT` لمتغير Railway في الإنتاج. استخدم `PORT=3000` محليًا فقط.

## التحقق بعد النشر

في سجل البناء ابحث عن استخدام Dockerfile بنجاح، ثم تحقق من أن السجل يعرض بدء خادم الصحة وبدء Telegram polling. افتح عنوان الخدمة على `/api/healthz` وتأكد من استجابة HTTP 200 وقيمة `sqlite_connected: true`.

اختبر `/start` و`/study` من Telegram، ثم أنشئ جلسة قصيرة وتأكد من وصول التذكير والفيديو التحفيزي وحفظ السجل. بعد ذلك أعد تشغيل الخدمة وتحقق من بقاء قاعدة البيانات والإعدادات وعدم تكرار الفيديو نفسه مباشرةً.

## ملاحظات تشغيل مهمة

استخدم replica واحدة مع polling والتوكن نفسه. لا تشغل نسختين من الخدمة بالتوكن ذاته. إذا زاد عدد المستخدمين، انقل البيانات إلى PostgreSQL وأضف queue خارجية قبل تشغيل أكثر من replica.

لا تُرسل ملفات `study_bot.sqlite3-wal` أو `study_bot.sqlite3-shm` وحدها كنسخة احتياطية؛ يجب أن تكون مرتبطة بملف قاعدة البيانات الرئيسي، أو استخدم Backup صحيحًا عبر SQLite.

هذا المشروع لا يشغّل كودًا مرفوعًا من المستخدمين. إذا أُضيفت هذه الوظيفة، يجب فصل Executor عن الخدمة وعدم استخدام `docker.sock` أو `privileged: true`.
