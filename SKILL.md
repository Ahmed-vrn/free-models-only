---
name: Free Models Only
description: Discover, classify, and hide every paid, conditional, or unverified model across all OpenCode providers while keeping genuinely free and verified local ones; includes adapter-based discovery, loopback verification, multi-key default protection, smoke tests, and atomic JSONC updates.
metadata:
  opencode/autoinvoke: true
---
# Free Models Only v3.0

اجعل `OpenCode` يعرض ويشغّل النماذج المجانية فقط عبر **جميع المزوّدين المهيّئين**، انطلاقاً من **أدلة تسعير رسمية قابلة للتحقق الآلي** لا من اسم النموذج أو لاحقة `:free`.

المهارة لا تنشئ حسابات، ولا تستخرج مفاتيح، ولا تثبّت مزوّدين أو إضافات، ولا تستخدم بديلاً مدفوعاً (`paid fallback`) تحت أي ظرف.

هذا المجلد مستودع Git: التحديث عبر `git pull` ثم `bash scripts/install.sh`.

---

## ما الجديد في 3.0

1. **النقض أحادي الاتجاه** عبر كتالوج `models.dev`: يُستخدم لاستبعاد أي نموذج تظهر له تكلفة موجبة، ولا يُستخدم لمنح صفة «مجاني».
2. **حصص المزوّدين غير المعلنة** (Gemini / Groq / Cerebras / GitHub Models...): غياب الأسعار من `/v1/models` يعني `unknown` ويُعطّل، لا `quota_free`.
3. **التحقق الشبكي من المحلية**: لا يُصنّف مزوّد كـ `local` إلا إذا كان `baseURL` استرجاعاً محلياً مؤكداً (`127.0.0.1` / `::1` / `localhost` / `unix://`).
4. **حماية كل مفاتيح الاختيار**: `model` و`small_model` و`default_model` و`agent.<name>.model`.
5. **ذاكرة التعطيل (`state.json`):** لا يعود نموذج معطّل للظهور لمجرد اختفائه من `opencode models`، ولا تُمسح تعطيلات المستخدم اليدوية.
6. **تعدد أبعاد التسعير**: فحص النص، التفكير، الكاش، رسوم الطلب، البحث، والوسائط، ورفض الشرائح التصاعدية والتسعير الديناميكي `-1`.

---

## سياسة التصنيف (Zero-Trust Classification Policy)

وحدة التقييم هي **مسار النموذج الكامل** (`provider_id` + `model_id` + `endpoint`). يأخذ كل مسار تصنيفاً واحداً فقط:

| التصنيف | المعنى الفني الدقيق | ماذا يفعل `apply.py` |
| --- | --- | --- |
| `verified_zero` | نموذج محادثة/برمجة، وسعره الرسمي `0` صريحاً في جميع حقول التكلفة بلا شرائح سياق مشروطة | يبقى مفعّلاً |
| `quota_free` | السعر الرسمي `0` لكل الحقول، لكن يلزم حساب/مفتاح ويخضع لحصة استخدام (`RPM/RPD`) | يبقى مفعّلاً افتراضياً، ويُعطّل مع `--disable-quota-free` |
| `funded_account_required` | السعر الظاهري `0` لكن المزوّد يشترط شحن رصيد، أو بطاقة دفع مفعّلة، أو باقة مدفوعة | يُعطّل |
| `local` | تشغيل محلي مثبَت شبكياً (`127.0.0.1` / `localhost` / `::1` / `unix://`) بلا فوترة API | يبقى مفعّلاً |
| `paid` | أي حقل تكلفة رسمي `> 0`، أو يحتوي على شرائح تسعير تصاعدية (`tiers`) تنقلب مدفوعة | يُعطّل |
| `unknown` | السعر مفقود، أو غير رقمي، أو ديناميكي (`-1`)، أو المزوّد لا يوفّر API تسعير رسمي، أو النموذج غير مخصّص للمحادثة | يُعطّل |

### قواعد إلزامية صارمة:

1. **السعر المفقود = `unknown`، وليس صفراً:** غياب حقل السعر في استجابة المزوّد يعني عدم القدرة على إثبات المجانية، فيُعطّل النموذج.
2. **لا تُستنتج المجانية من الاسم:** اللواحق مثل `:free` أو `-free` أو `trial` لا تمنح أي استثناء من فحص الأرقام.
3. **استقلال المزوّدين:** لا يُنقل حكم السعر من مزوّد إلى آخر؛ النموذج المجاني على منصة قد يكون مدفوعاً على أخرى.
4. **مبدأ النقض أحادي الاتجاه (One-Way Veto):**
   - **للتعطيل (`paid`):** يكفي أن يُظهر أي مصدر موثوق (المزوّد الرسمي، أو `opencode models`، أو كتالوج `models.dev`) تكلفة `> 0` في أي بُعد ليُحكم على النموذج بأنه مدفوع ويُعطّل فوراً.
   - **للتفعيل (`verified_zero` / `quota_free`):** لا يكفي غياب السعر في كتالوج وسيط؛ بل يلزم دليل إيجابي صريح من محوّل التسعير الرسمي للمزوّد يثبت أن كل حقول التكلفة تساوي `0` (باستخدام حسابات `Decimal` الدقيقة).
5. **فحص جميع أبعاد التكلفة (Multi-Dimensional Cost Check):**
   - يُفحص كل حقل تسعير يقدمه المصدر: `input`/`prompt`، `output`/`completion`، `reasoning`/`internal_reasoning`، `cache_read`، `cache_write`، `request` (رسم الطلب الثابت)، `web_search`، ووسائط `image` و`audio` و`video`.
   - وجود تسعير شرائحي مشروط بطول السياق (`tiers`) يصنّف النموذج كـ `paid` لمنع الفوترة المفاجئة في الجلسات الطويلة.
   - القيمة `-1` (تسعير ديناميكي متغير) تُصنّف `unknown` وتُعطّل.
6. **التحقق من الهوية المحلية (`local`):**
   - لا يُصنّف المزوّد المحلي (`ollama`، `lmstudio`، `vllm`، `llamacpp`...) كـ `local` بمجرد اسمه؛ بل يجب أن يثبت الفحص أن نقطة الاتصال (`baseURL`) محلية (`Loopback`) وليست خادماً سحابياً بعيداً، إلا إذا مُرّر `--allow-lan-local` للشبكة المحلية الخاصة.
7. **حماية الخصوصية الاختيارية (`--require-zero-retention`):**
   - النماذج المجانية التي تشترط صراحةً استخدام المدخلات لتدريب النماذج (`data_collection: allow` / `training_opt_in`) تُوسم بالخاصية `privacy_retention: true`، ويمكن تعطيلها بتمرير `--require-zero-retention`.

---

## بنية المصادر والمحوّلات (Provider Adapter Architecture)

تعتمد `scripts/discover.py` على **سجل محوّلات قابل للتوسعة (Adapter Registry)** بدلاً من حصر الاكتشاف في مصدرين:

1. **محوّلات التسعير الرسمية المباشرة (Tier-1 Pricing Adapters):**
   - **OpenCode Inference (`opencode`):** `https://opencode.ai/inference/v1/models`
     - يفحص `cost.text.input.price` و`cost.text.output.price` وكافة كائنات `image` و`audio` و`video` و`cache` و`reasoning`.
   - **OpenRouter (`openrouter`):** `https://openrouter.ai/api/v1/models`
     - يفحص كائن `pricing` بالكامل (`prompt`، `completion`، `request`، `image`، `web_search`، `internal_reasoning`، `input_cache_read`، `input_cache_write`).
2. **محوّل الفحص المحلي (Local Loopback Adapter):**
   - يفحص المزوّدين المحليين المهيّئين في `opencode.jsonc` ويتحقق من أن العنوان `127.0.0.1` أو `localhost` أو `::1` أو مقبس `unix://` عبر `urllib.parse` و`ipaddress`.
3. **محوّل النقض والمطابقة المرجعية (`models.dev` Veto Adapter):**
   - يستعلم `https://models.dev/api.json` (الكتالوج المرجعي لـ OpenCode) لاكتشاف الأسعار الموجبة (`> 0`) عبر جميع المزوّدين (OpenAI, Anthropic, Google, Groq, Cerebras, Mistral, DeepSeek, Together, Fireworks, Bedrock, Vertex, إلخ) وإسقاطها فوراً إلى `paid`.
4. **محوّل استبطان بيئة التشغيل (Runtime Introspection Adapter):**
   - يشغّل `opencode models` لاكتشاف أي مزوّد أو نموذج مفعّل في بيئة المستخدم الفعلية.
   - أي نموذج يظهر في التشغيل ولا يملك إثبات مجانية من محوّل رسمي (أو إثبات تشغيل محلي) يُصنّف `paid` (إن ظهر له سعر موجب) أو `unknown` (إن غاب سعره، مثل المزوّدين السحابيين الذين لا تعرض واجهاتهم أسعاراً أو حالة فوترة الحساب) ويُعطّل.
5. **المصادر التوثيقية البشرية:**
   - صفحات الوثائق مثل `https://opencode.ai/docs/console/models` تُستخدم للمراجعة البشرية فقط، ولا تُعتمد مصدراً آلياً للتفعيل.

---

## سير العمل (Workflow)

يُنفّذ من داخل مجلد المهارة:

### 1. الاكتشاف الشامل
```bash
python3 scripts/discover.py
```
- يكتب `~/.cache/free-models-only/discovery.json` ذرياً ويعرض ملخصاً إحصائياً لكل مزوّد وتصنيف.
- إذا فشلت الشبكة أو المصادر، **لا يستبدل** ملف الاكتشاف الصالح السابق.
- الخيارات المتاحة:
  - `--no-runtime`: تجاهل استعلام `opencode models`.
  - `--offline`: الاعتماد على الكاش المحلي/القائمة الاحتياطية دون اتصال بالشبكة.
  - `--config /path/to/opencode.jsonc`: تمرير ملف إعداد محدد لفحص المزوّدين المحليين والمهيّئين فيه.
  - `--allow-lan-local`: السماح بعناوين LAN الخاصة كـ `local`.

### 2. مراجعة النتيجة والتدقيق
```bash
python3 - <<'PY'
import json, pathlib
p = pathlib.Path.home() / ".cache/free-models-only/discovery.json"
d = json.loads(p.read_text(encoding="utf-8"))
print("Summary:", json.dumps(d["summary"], ensure_ascii=False))
for m in d["models"]:
    if m["classification"] in ("verified_zero", "quota_free", "local", "unknown"):
        print(f"{m['classification']:<16} {m['provider_id']}/{m['model_id']} ({m.get('reason', '')})")
PY
```

### 3. معاينة التطبيق (`Dry-Run`)
```bash
python3 scripts/apply.py \
  --discovery "$HOME/.cache/free-models-only/discovery.json" \
  --dry-run
```
يعرض ملخص النماذج التي ستُعطّل أو تُفعّل، ويتحقق من سلامة النموذج الافتراضي وبنية `JSONC` دون كتابة أي تغيير على القرص.

### 4. التطبيق الفعلي الآمن (`Apply`)
```bash
python3 scripts/apply.py \
  --discovery "$HOME/.cache/free-models-only/discovery.json" \
  --apply
```
خيارات إضافية للتحكم الصارم:
- `--disable-quota-free`: تعطيل نماذج `quota_free` والاكتفاء بـ `verified_zero` و`local`.
- `--require-zero-retention`: تعطيل النماذج المجانية التي تسجّل البيانات للتدريب.
- `--include-unconfigured-providers`: كتابة قواعد التعطيل حتى للمزوّدين غير المذكورين مسبقاً في ملف الإعداد.

**الضمانات الهندسية لـ `apply.py`:**
1. **التعديل المحدود بالعلامات:** يستبدل الكتلة المحصورة بين علامتي `free-models-only` فقط، ويحتفظ بكل التعليقات (`//` و`/* */`) والفواصل والإعدادات خارجها. يتعرف على علامات v3 (`// BEGIN free-models-only` / `// END free-models-only`) وعلى علامات v2 القديمة، ويحافظ على نمط الملف الحالي.
2. **حماية جميع النماذج الافتراضية:** يفحص `model` و`small_model` و`default_model` ونماذج الوكلاء `agent.*.model` و`agents.*.model`؛ فإذا كان أيٌّ منها سيُعطّل، يرفض الكتابة ويعرض بدائل مجانية متاحة.
3. **ذاكرة النماذج المعطّلة (`state.json` Tombstone Retention):**
   - يحفظ في `~/.cache/free-models-only/state.json` قائمة المعرّفات التي عطّلتها المهارة.
   - إذا اختفى معرّف كان معطّلاً من مخرجات `opencode models` (لأن تعطيله أخفاه من وقت التشغيل)، **يبقى معطّلاً** في الإعدادات حتى لا يعود للظهور في التحديث التالي.
   - إذا ظهر مجدداً في الاكتشاف الرسمي كـ `verified_zero` (أو `quota_free` في الوضع الافتراضي)، يُفعّل تلقائياً.
   - لا يمسح أي نموذج عطّله المستخدم يدوياً خارج إدارة المهارة، ولا يحذف `state.json` عند الإزالة.
4. **الكتابة الذرية والنسخ الاحتياطي:** ينشئ نسخة احتياطية مؤرخة قبل أي تعديل، ويكتب في ملف مؤقت بنفس المجلد مع `fsync` ثم يستبدله ذرياً (`os.replace`)، ثم يعيد قراءة الملف والتحقق منه.

### 5. فحص الدخان (`Smoke Probe`)
```bash
python3 scripts/probe.py \
  --discovery "$HOME/.cache/free-models-only/discovery.json" \
  --limit 3
```
- يفحص فقط نماذج `verified_zero` افتراضياً، بحد أقصى 3 نماذج (و10 كحد أقصى مسموح)، عبر `opencode run --model provider/model`، بمهلة زمنية محددة وبلا إعادة محاولة (`No Retries`).
- لا يفحص `quota_free` إلا مع `--include-quota-free` للحفاظ على الحصص، ولا يفحص المدفوع مطلقاً إلا مع `--allow-nonfree`.
- مع `--require-zero-retention` يتخطى النماذج الموسومة `privacy_retention: true`.

### 6. التحقق النهائي
عبر الطرفية:
```bash
CLI="$(command -v opencode || find "$HOME/.config/ai.opencode.desktop" -name opencode-cli 2>/dev/null | head -n 1)"
"$CLI" run --model opencode/deepseek-v4.1-flash-free \
  "Reply with exactly: FREE_MODEL_TEST_OK"
```
ثم تحقق برمجياً عبر أداة `Code Mode` من خلوّ القائمة الفعّالة من أي نموذج مدفوع أو مجهول:
```javascript
const r = await tools.opencode.models({ limit: 200 })
const all = r.providers.flatMap(p => p.models.map(m => ({
  id: `${p.id}/${m.id}`,
  input: m.cost?.[0]?.input ?? null,
  output: m.cost?.[0]?.output ?? null,
})))
return {
  count: all.length,
  paidLeft: all.filter(m => (m.input ?? 0) > 0 || (m.output ?? 0) > 0),
  unknownLeft: all.filter(m => m.input === null || m.output === null),
}
```

---

## النطاق (Scope)

- **الافتراضي:** الإعداد العام `~/.config/opencode/opencode.jsonc` (أو داخل `$XDG_CONFIG_HOME/opencode/`).
- **مشروع محدد:** `--scope project`، ويبحث عن `.opencode/opencode.jsonc` أو `.opencode/opencode.json` أو `opencode.jsonc` صعوداً من مجلد العمل الحالي.
- **مسار صريح:** `--config /path/to/opencode.jsonc`.

> **قاعدة صارمة:** لا تُعدّل إعدادات مشروع لم يطلب المستخدم تعديله صراحةً.

---

## الأمان (Security Guardrails)

- لا تُكتب مفاتيح API أو أسرار داخل `opencode.jsonc`.
- لا تُضاف تعريفات مزوّدين جديدة تلقائياً؛ تُكتب فقط مدخلات التعطيل ضمن المزوّدين الموجودين، إلا عند طلب `--include-unconfigured-providers`.
- لا تُثبّت حزم أو إضافات (`plugins`) من نتائج الاكتشاف.
- لا تُنشأ مهام مجدولة (`cron` / `systemd`) تلقائياً؛ الجدولة قرار مستقل بيد المستخدم بعد مراجعة السكربت.
- إذا كان ملف الإعداد تالفاً، أو تغيّر النموذج الافتراضي إلى نموذج غير مجاني، أو اختفت علامات `free-models-only`، يتوقف `apply.py` فوراً دون كتابة.
- تُرفض الكتابة عبر روابط رمزية (`symlink`) ما لم يُمرَّر `--allow-symlink`.

---

## التثبيت، التحديث، والإزالة

```bash
# تثبيت وتطبيق كامل
bash scripts/install.sh

# معاينة فقط دون تعديل الملفات
bash scripts/install.sh --dry-run

# بدون شبكة (الاعتماد على الكاش/القائمة الاحتياطية)
bash scripts/install.sh --offline

# تحديث المستودع من GitHub وإعادة الاكتشاف والتطبيق
bash scripts/update.sh

# إزالة الكتلة المُدارة فقط مع الاحتفاظ ببقية الإعدادات ونسخة احتياطية
bash scripts/uninstall.sh
```
- `install.sh` يضيف علامتي `free-models-only` إذا لم تكونا موجودتين، ثم يشغّل الاكتشاف والتطبيق.
- إذا كان ملف الإعداد يحتوي على مفتاح `providers` (أو `provider`) خارج العلامات، يتوقف بأمان ويوضح للمستخدم كيفية إحاطته بالعلامتين يدوياً لتجنّب تكرار المفاتيح أو إتلاف التعليقات.
- `uninstall.sh` يحتفظ بـ `state.json` حتى لا تعود النماذج المدفوعة عند إعادة التثبيت.

---

## ملاحظات تشغيلية هامة

- **ديناميكية الكتالوجات:** كتالوجات المزوّدين و`OpenCode` تتغير باستمرار؛ أعد تشغيل الاكتشاف والتطبيق بعد كل تحديث.
- **حقيقة `:free` على OpenRouter:** معظم نماذج `:free` هي `quota_free` (مقيدة بحصة طلبات ومفتاح API)، وبعضها يفرض سياسة الاحتفاظ بالمدخلات للتدريب؛ استخدم `--disable-quota-free` أو `--require-zero-retention` عند الحاجة.
- **حماية الجلسة النشطة:** لا تعطّل النموذج المستخدم في الجلسة الحالية أثناء العمل؛ التغييرات تُطبَّق على الطلبات والجلسات الجديدة.
- **تحذير متغير البيئة:** المتغير `OPENCODE_DISABLE_FREE_MODELS=1` يفعل العكس تماماً (يعطّل النماذج المجانية)؛ لا تضبطه في بيئتك.
