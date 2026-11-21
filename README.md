# free-models-only

مهارة OpenCode V2 تُبقي **النماذج المجانية فقط** في الكتالوج. تكتشف النماذج
من واجهات رسمية، تصنّف تكلفتها، ثم تعطّل المدفوع وغير المعروف عبر
`providers.<provider>.models.<id>.disabled = true`، وهي الآلية الموثّقة في
[Providers](https://opencode.ai/docs/providers) و[Models](https://opencode.ai/docs/models).

لا تحتاج مفتاح API ولا رصيداً. لا تثبّت مزوّدين، ولا تكتب أسراراً، ولا
تستخدم نموذجاً مدفوعاً كبديل.

## لماذا

كتالوج OpenCode ديناميكي: تظهر معرّفات مدفوعة جديدة وتختفي أخرى. القائمة
الثابتة تتقادم بسرعة. هذه النسخة (2.0) تبني `discovery.json` من المصدر
الرسمي في كل تشغيل، وتصنّف كل نموذج، ثم تطبّق النتيجة على الإعداد بأمان.

## التثبيت

```bash
git clone https://github.com/Ahmed-vrn/free-models-only.git \
  ~/.config/opencode/skills/free-models-only
bash ~/.config/opencode/skills/free-models-only/scripts/install.sh
```

OpenCode يكتشف المهارة تلقائياً من `~/.config/opencode/skills/<id>/SKILL.md`.

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
| `--disable-quota-free` | عطّل أيضاً النماذج المجانية المحدودة الحصة |
| `--include-unconfigured-providers` | اكتب تعطيلات لمزوّدين غير موجودين في الإعداد |
| `--offline` | استخدم `references/paid-model-ids.txt` بلا شبكة |

## التصنيف

- `verified_zero`: سعر رسمي صفر لكل حقول التكلفة، ونموذج chat.
- `quota_free`: مجاني ضمن حصة/حساب، مثل OpenRouter `:free`.
- `funded_account_required`: يحتاج شحناً أو رصيداً.
- `local`: تشغيل محلي.
- `paid`: سعر موجب.
- `unknown`: سعر أو نوع غير مؤكد، ويُعطّل.

السعر المفقود لا يُعامل أبداً كصفر.

## الملفات

| المسار | الوظيفة |
| --- | --- |
| `SKILL.md` | تعليمات المهارة وسير العمل. |
| `scripts/discover.py` | يجلب OpenCode + OpenRouter ويصنّف ويكتب `discovery.json`. |
| `scripts/apply.py` | يطبّق التعطيلات داخل الكتلة المُدارة فقط، مع حماية الافتراضي. |
| `scripts/probe.py` | فحص دخان محدود عبر OpenCode CLI. |
| `scripts/install.sh` | تثبيت/تطبيق، مع `--dry-run` و`--offline`. |
| `scripts/update.sh` | `git pull` + إعادة التطبيق. |
| `scripts/uninstall.sh` | إزالة الكتلة المُدارة فقط. |
| `references/paid-model-ids.txt` | قائمة احتياطية مولّدة من الأسعار الرسمية. |
| `tests/` | اختبارات بلا شبكة. |
| `.github/workflows/validate.yml` | تشغيل الاختبارات عند كل push/PR. |

## الأمان

- dry-run افتراضي؛ الكتابة تحتاج `--apply`.
- نسخة احتياطية قبل كل كتابة، وكتابة ذرية `tmp + rename`.
- رفض تعطيل النموذج الافتراضي.
- الحفاظ على التعليقات وكل ما هو خارج العلامات.
- رفض الملفات الرمزية (symlinks) افتراضياً.
- لا مفاتيح API ولا حزم جديدة ولا مهام مجدولة تلقائياً.

## التحديث

```bash
bash ~/.config/opencode/skills/free-models-only/scripts/update.sh
```

## الإزالة

```bash
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
