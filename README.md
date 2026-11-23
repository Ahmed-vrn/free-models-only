# free-models-only v3.0

مهارة OpenCode تُبقي **النماذج المجانية فقط** في الكتالوج عبر جميع المزوّدين.
تكتشف الأسعار من واجهات رسمية، وتصنّف كل نموذج بسياسة **Zero-Trust**، ثم
تعطّل المدفوع وغير المعروف عبر
`providers.<provider>.models.<id>.disabled = true`.

لا تحتاج مفتاح API ولا رصيداً. لا تثبّت مزوّدين، ولا تكتب أسراراً، ولا
تستخدم نموذجاً مدفوعاً كبديل.

## الجديد في 3.0

- **One-Way Veto** من `models.dev`: أي سعر موجب يُعطّل النموذج فوراً، لكن غياب
  السعر لا يمنح صفة «مجاني».
- **حصص غير معلنة**: مزوّدون مثل Gemini/Groq/Cerebras/GitHub Models لا يعرضون
  تسعيراً في `/v1/models` ⇒ `unknown` ويُعطّلون إلا بإثبات رسمي.
- **Loopback Verification**: `local` فقط إذا كان `baseURL` محلياً فعلاً.
- **حماية جميع مفاتيح النماذج**: `model`, `small_model`, `default_model`,
  `agent.*.model`.
- **Tombstones** في `state.json` لمنع عودة النماذج المعطّلة.
- **فحص كل أبعاد التكلفة**: نص، تفكير، كاش، رسوم طلب، بحث، وسائط، شرائح، `-1`.

## التثبيت

```bash
git clone https://github.com/Ahmed-vrn/free-models-only.git \
  ~/.config/opencode/skills/free-models-only
bash ~/.config/opencode/skills/free-models-only/scripts/install.sh
```

OpenCode يكتشف المهارة تلقائياً من `SKILL.md`.

## الاستخدام

```bash
cd ~/.config/opencode/skills/free-models-only

# 1) اكتشاف وتصنيف
python3 scripts/discover.py

# 2) معاينة
python3 scripts/apply.py \
  --discovery "$HOME/.cache/free-models-only/discovery.json" --dry-run

# 3) تطبيق
python3 scripts/apply.py \
  --discovery "$HOME/.cache/free-models-only/discovery.json" --apply

# 4) فحص دخان محدود
python3 scripts/probe.py \
  --discovery "$HOME/.cache/free-models-only/discovery.json" --limit 3
```

### خيارات مفيدة

| الخيار | المعنى |
| --- | --- |
| `--scope project` | استخدم أقرب `.opencode/opencode.jsonc` بدل الإعداد العام |
| `--config PATH` | مسار صريح |
| `--no-runtime` | تجاهل `opencode models` |
| `--offline` | إعادة استخدام الكاش + القائمة الاحتياطية بلا شبكة |
| `--allow-lan-local` | اعتبار عناوين LAN الخاصة `local` |
| `--disable-quota-free` | تعطيل أيضاً النماذج المجانية المحدودة الحصة |
| `--require-zero-retention` | استبعاد النماذج التي تحتفظ بالمدخلات للتدريب |
| `--include-unconfigured-providers` | كتابة تعطيلات لمزوّدين غير موجودين في الإعداد |

## التصنيف

| التصنيف | المعنى |
| --- | --- |
| `verified_zero` | سعر رسمي صفر في كل الحقول، نموذج محادثة، بلا شرائح |
| `quota_free` | صفر لكن بحساب/مفتاح وحصة محدودة |
| `funded_account_required` | يحتاج رصيداً أو بطاقة |
| `local` | محلي مثبَت شبكياً بلا فاتورة API |
| `paid` | أي تكلفة موجبة أو شرائح تصاعدية |
| `unknown` | سعر مفقود/ديناميكي/غير مؤكد ⇒ يُعطّل |

السعر المفقود لا يُعامل أبداً كصفر.

## الملفات

| المسار | الوظيفة |
| --- | --- |
| `SKILL.md` | تعليمات المهارة وسياسة التصنيف. |
| `scripts/discover.py` | محوّلات OpenCode + OpenRouter + `models.dev` + runtime + loopback. |
| `scripts/apply.py` | تطبيق ذري مع حماية كل مفاتيح النماذج و`tombstones`. |
| `scripts/probe.py` | فحص دخان محدود عبر OpenCode CLI. |
| `scripts/opencode_cli.py` | تحديد مسار الـ CLI وقراءة `opencode models`. |
| `scripts/install.sh` | تثبيت/تطبيق مع `--dry-run` و`--offline`. |
| `scripts/update.sh` | `git pull` + إعادة التطبيق. |
| `scripts/uninstall.sh` | إزالة الكتلة المُدارة فقط مع الاحتفاظ بـ `state.json`. |
| `references/paid-model-ids.txt` | قائمة احتياطية للتشغيل دون شبكة. |
| `tests/` | اختبارات بلا شبكة. |
| `.github/workflows/validate.yml` | تحقق عند كل push/PR. |

## الأمان

- dry-run افتراضي؛ الكتابة تحتاج `--apply`.
- نسخة احتياطية مؤرخة قبل كل كتابة، وكتابة ذرية `tmp + fsync + rename`.
- رفض تعطيل أي نموذج افتراضي أو نموذج وكيل.
- الحفاظ على التعليقات وكل ما هو خارج العلامات؛ دعم علامات v2 القديمة.
- رفض الملفات الرمزية (`symlinks`) افتراضياً.
- لا مفاتيح API ولا حزم جديدة ولا مهام مجدولة تلقائياً.

## التحديث والإزالة

```bash
bash ~/.config/opencode/skills/free-models-only/scripts/update.sh
bash ~/.config/opencode/skills/free-models-only/scripts/uninstall.sh
```

## التحقق

```bash
python3 -m unittest discover -s tests -v
```

ثم في OpenCode، `/models` يجب أن يعرض المجاني فقط. الافتراضي المقترح:
`opencode/deepseek-v4.1-flash-free`.

## الترخيص

MIT — انظر `LICENSE`.
