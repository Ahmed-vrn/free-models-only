---
name: Free Models Only
description: Discover, classify, and hide every paid or unknown OpenCode model while keeping genuinely free ones; includes dry-run, default protection, smoke tests, and safe JSONC updates.
metadata:
  opencode/autoinvoke: true
---

# Free Models Only v2.0

اجعل OpenCode يعرض النماذج المجانية فقط، انطلاقاً من **أسعار رسمية** لا من
الاسم أو كلمة `free`. المهارة لا تنشئ حسابات، ولا تستخرج مفاتيح، ولا تثبّت
مزوّدين أو إضافات، ولا تستخدم fallback مدفوعاً.

هذا المجلد مستودع Git: التحديث عبر `git pull` ثم `scripts/install.sh`.

## سياسة التصنيف

كل نموذج يأخذ تصنيفاً واحداً فقط:

| التصنيف | المعنى | ماذا يفعل `apply.py` |
| --- | --- | --- |
| `verified_zero` | سعر رسمي صفر لكل حقول التكلفة، ونموذج chat | يبقى مفعّلاً |
| `quota_free` | سعر صفر لكن يلزم حساب/مفتاح وحصة محدودة | يبقى افتراضياً، ويُعطّل مع `--disable-quota-free` |
| `funded_account_required` | سعر صفر لكن يلزم شحن أو رصيد | يُعطّل |
| `local` | تشغيل محلي بلا فاتورة API | يبقى مفعّلاً |
| `paid` | سعر رسمي موجب | يُعطّل |
| `unknown` | السعر أو النوع غير مؤكد | يُعطّل |

قواعد إلزامية:

- السعر المفقود = `unknown`، وليس صفراً.
- لا تُستنتج المجانية من الاسم أو لاحقة `:free`.
- لا يُنقل حكم السعر من مزوّد إلى آخر.
- لا يُستخدم نموذج مدفوع كبديل عند فشل المجاني.
- `unknown` يُعطّل في وضع المجاني فقط، حتى لا يظهر نموذج مجهول التكلفة.

## المصادر

- OpenCode: `https://opencode.ai/inference/v1/models`
  - السعر تحت `cost.text.input.price` و`cost.text.output.price`.
  - تُفحص أيضاً تكاليف `image` و`audio` و`video` و`cache`.
- OpenRouter: `https://openrouter.ai/api/v1/models`
  - الأسعار لكل token، وتُحوَّل إلى سعر لكل مليون token.
  - `-1` تعني سعراً ديناميكياً غير معروف، وليست مجانية.
- صفحة الوثائق `https://opencode.ai/docs/console/models` للتحقق البشري فقط،
  وليست مصدراً آلياً موثوقاً للتصنيف.

## سير العمل

من داخل مجلد المهارة:

### 1. الاكتشاف

```bash
python3 scripts/discover.py
```

يكتب `~/.cache/free-models-only/discovery.json` ويعرض ملخصاً. إذا فشلت كل
المصادر، لا يستبدل ملف الاكتشاف السابق.

يستعلم أيضاً `opencode models` لاكتشاف أي معرّف يظهر في التشغيل لكنه غائب عن
الكتالوج الرسمي؛ يُصنَّف هذا المعرّف `unknown` ويُعطَّل. استخدم `--no-runtime`
لتجاهل هذه الخطوة.

### 2. مراجعة النتيجة

```bash
python3 - <<'PY'
import json, pathlib
p = pathlib.Path.home() / ".cache/free-models-only/discovery.json"
d = json.loads(p.read_text())
print(d["summary"])
for m in d["models"]:
    if m["classification"] in ("verified_zero", "unknown"):
        print(m["classification"], m["provider_id"] + "/" + m["model_id"])
PY
```

### 3. معاينة التطبيق

```bash
python3 scripts/apply.py \
  --discovery "$HOME/.cache/free-models-only/discovery.json" \
  --dry-run
```

### 4. التطبيق

```bash
python3 scripts/apply.py \
  --discovery "$HOME/.cache/free-models-only/discovery.json" \
  --apply
```

`apply.py` يستبدل الكتلة بين علامتي `free-models-only` فقط، ويحتفظ بكل
التعليقات والإعدادات خارجها. ينشئ نسخة احتياطية، ويكتب ذرياً، ويرفض تعطيل
النموذج الافتراضي.

### 5. فحص الدخان

```bash
python3 scripts/probe.py \
  --discovery "$HOME/.cache/free-models-only/discovery.json" \
  --limit 3
```

يفحص فقط `verified_zero` افتراضياً، بحد أقصى 3 نماذج (10 كحد أقصى)، عبر
`opencode run --model provider/model`، وبلا إعادة محاولة. لا يفحص `quota_free`
إلا مع `--include-quota-free`، ولا المدفوع إلا مع `--allow-nonfree`.

### 6. التحقق النهائي

```bash
CLI="$(command -v opencode || find "$HOME/.config/ai.opencode.desktop" -name opencode-cli 2>/dev/null | head -n 1)"
"$CLI" run --model opencode/deepseek-v4.1-flash-free \
  "Reply with exactly: FREE_MODEL_TEST_OK"
```

ثم تحقق أن قائمة النماذج لا تحتوي مدفوعاً. يمكن استخدام أداة Code Mode:

```javascript
const r = await tools.opencode.models({ limit: 100 })
const all = r.providers.flatMap(p => p.models.map(m => ({
  id: m.id,
  input: m.cost?.[0]?.input ?? null,
  output: m.cost?.[0]?.output ?? null,
})))
return {
  count: all.length,
  paidLeft: all.filter(m => (m.input ?? 0) > 0 || (m.output ?? 0) > 0),
  unknownLeft: all.filter(m => m.input === null || m.output === null),
}
```

## النطاق

- الافتراضي: الإعداد العام `~/.config/opencode/opencode.jsonc`.
- مشروع محدد: `--scope project`، ويبحث عن `.opencode/opencode.jsonc` أو
  `.opencode/opencode.json` صعوداً من مجلد العمل.
- مسار صريح: `--config /path/to/opencode.jsonc`.

لا تُعدّل إعدادات مشروع لم يُطلب تعديله.

## الأمان

- لا تُكتب مفاتيح API في `opencode.jsonc`.
- لا تُضاف تعريفات مزوّدين جديدة تلقائياً؛ تُكتب فقط مدخلات `disabled` ضمن
  المزوّدين الموجودين، إلا مع `--include-unconfigured-providers`.
- لا تُثبّت حزم أو plugins من نتائج البحث.
- لا تُنشئ مهمة مجدولة تلقائياً. الجدولة قرار منفصل بعد مراجعة السكربت.
- إذا كان الملف غير صالح، أو تغيّر النموذج الافتراضي، أو اختفت العلامات،
  يتوقف `apply.py` دون كتابة.

## التثبيت والتحديث

```bash
# تثبيت/تطبيق
bash scripts/install.sh

# معاينة فقط
bash scripts/install.sh --dry-run

# بدون شبكة (القائمة الاحتياطية)
bash scripts/install.sh --offline

# تحديث من GitHub
bash scripts/update.sh
```

`install.sh` يضيف العلامات إذا لم تكن موجودة، ثم يشغّل الاكتشاف والتطبيق.
إذا كان الإعداد يحتوي مفتاح `providers` بلا علامات، يتوقف ويطلب تغليفه
بالعلامات يدوياً لتجنّب مفتاح مكرر.

## الإزالة

```bash
bash scripts/uninstall.sh
```

يزيل الكتلة المُدارة فقط، ويحتفظ ببقية الإعدادات وبنسخة احتياطية.

## ملاحظات

- كتالوج OpenCode ديناميكي ويبدّل المعرّفات؛ أعد الاكتشاف بعد كل تحديث.
- OpenRouter `:free` غالباً `quota_free` وليس مجانياً بلا حدود.
- لا تعطّل النموذج المستخدم في الجلسة الحالية أثناء العمل؛ التغيير يُحمَّل
  للطلبات الجديدة.
- `OPENCODE_DISABLE_FREE_MODELS=1` يفعل العكس؛ لا تضبطه هنا.
