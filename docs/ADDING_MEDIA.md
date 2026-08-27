# إضافة فيديوهات التحفيز إلى Study Bot

هذا الدليل يشرح المسار الآمن لإضافة فيديوهات تحفيزية جديدة إلى المستودع العام. يجب أن تكون كل إضافة ملف MP4 صالحًا، وأن يكون مصدرها واضحًا ويمنح الاستخدام المقصود. لا ترفع فيديوهات من Pinterest أو YouTube أو أي صفحة لا توضح صراحةً حقوق التنزيل وإعادة الاستخدام.

## 1. تجهيز الملفات محليًا

انسخ الفيديوهات إلى مجلد مؤقت على جهازك. يدعم السكربت ملفات `.mp4` فقط، ويتحقق من وجود فيديو H.264، ومن حجم الملف ومدته. الحد الافتراضي للحجم هو 95 MB لكل ملف، والمدى الافتراضي للمدة من ثانية واحدة إلى 180 ثانية.

```bash
mkdir -p /tmp/study-bot-videos
cp /path/to/your/video.mp4 /tmp/study-bot-videos/
```

لا تضع التوكن أو قاعدة SQLite أو أي أسرار داخل مجلد الأصول. ملفات البيئة وقاعدة البيانات المحلية مستبعدة من Git عبر `.gitignore`.

## 2. تسجيل المصدر والترخيص

أنشئ ملف JSON اختياريًا، مفتاحه اسم الملف الأصلي، وضع فيه بيانات صفحة المقطع وصفحة الترخيص. مثال:

```json
{
  "my-study-clip.mp4": {
    "title": "Study desk and notebook",
    "source_type": "Mixkit stock video",
    "source_url": "https://example.com/clip-page",
    "license_url": "https://example.com/license",
    "license_reviewed_at": "2026-08-27"
  }
}
```

بالنسبة إلى Mixkit، راجع صفحة المقطع نفسها وصفحة [Mixkit Stock Video Free License][1]. بعض العناصر قد تكون تحت ترخيص مقيّد، لذلك لا يكفي الاعتماد على اسم الموقع وحده. المقاطع الخمسة التي أضيفت في هذه التحديثة سُجلت بروابط صفحاتها الأصلية ورابط الترخيص في `assets/motivation_videos_manifest.json`.

## 3. تشغيل أداة الإضافة

من جذر المشروع شغّل:

```bash
python3 scripts/add_motivation_videos.py \
  --input-dir /tmp/study-bot-videos \
  --metadata-file /path/to/metadata.json
```

يمكن تمرير بيانات افتراضية بدل ملف metadata:

```bash
python3 scripts/add_motivation_videos.py \
  --input-dir /tmp/study-bot-videos \
  --source-type "user-provided" \
  --source-url "https://your-source.example/video" \
  --license-url "https://your-source.example/license" \
  --license-reviewed-at "2026-08-27"
```

تتحقق الأداة من كل ملف قبل نسخه، وتحسب SHA-256، وتتجاهل الملفات المكررة، وتستمر في الترقيم من آخر `video_###.mp4`. كما تحدث الـmanifest بطريقة ذرية، وتضيف `source_url` و`license_url` و`source_type` و`license_reviewed_at` عندما تتوفر.

## 4. الاختبار ثم الدفع إلى GitHub

```bash
pytest -q
ruff check .
git status --short
git add assets/motivation_videos assets/motivation_videos_manifest.json docs/ADDING_MEDIA.md scripts/add_motivation_videos.py tests README.md
git commit -m "Add licensed motivation videos"
git push origin main
```

بعد الدفع، تحقق من التطابق:

```bash
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

يجب أن يكون المعرّفان متساويين. لا ترفع ملفات `.env` أو `.sqlite3` أو أسرار Telegram؛ املأها فقط في متغيرات البيئة على Railway.

## 5. اختيار استراتيجية التخزين

المكتبة الحالية صغيرة بما يكفي للإبقاء على الملفات في Git. عند إضافة عدد كبير من المقاطع، راجع الحجم قبل الدفع لأن كل clone وbuild سيحملان الوسائط. لا تتجاوز حد GitHub البالغ 100 MB للملف المفرد؛ للمكتبات الأكبر استخدم أحد المسارات التالية بدل رفع كل الملفات الخام إلى Git.

| الخيار | مناسب عندما | المقابل |
|---|---|---|
| Git العادي | مكتبة صغيرة وإصدارات الوسائط جزء من إصدارات الكود | يزيد حجم المستودع مع كل ملف جديد |
| Git LFS | تريد بقاء الملفات مرتبطة بإصدارات Git مع تقليل حجم clone العادي | يحتاج إعداد LFS في كل بيئة بناء ورفع |
| Object Storage | مكتبة كبيرة أو تحديثات متكررة أو تريد build خفيفًا | يحتاج bucket وروابط موقعة أو آلية تنزيل، وقد توجد تكلفة تخزين/نقل |
| Telegram `file_id` بعد الرفع الأول | نفس المقاطع ترسل مرارًا إلى Telegram | يلزم رفع أولي موثوق وحفظ المعرّفات في SQLite/قاعدة دائمة |

## ملاحظات قانونية

الترخيص لا يعني أن كل عنصر مرئي داخل الفيديو خالٍ من حقوق مستقلة؛ راجع القيود المتعلقة بالعلامات التجارية والأشخاص والموسيقى إن وُجدت. استخدم فقط ما يطابق مشروع البوت، واحتفظ بروابط المصدر والترخيص في الـmanifest حتى يمكن تدقيق كل إضافة لاحقًا.

## مراجع

[1]: https://mixkit.co/license/#videoFree "Mixkit Stock Video Free License"
[2]: https://github.com/git-lfs/git-lfs "Git Large File Storage"
