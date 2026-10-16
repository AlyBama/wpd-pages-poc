---
title: WhatsApp Preview Doctor
emoji: 🩺
colorFrom: green
colorTo: blue
sdk: gradio
app_file: server.py
app_port: 7860
pinned: false
---

# طبيب معاينة واتساب v2

## التشغيل (Termux)
```bash
cd /storage/emulated/0/b/whatsapp-preview-doctor-v2
pip install pillow
bash run.sh      # يعمل على http://127.0.0.1:8081
bash stop.sh     # للإيقاف
```

## العنوان العام (مطلوب لواتساب)
واتساب لا يستطيع الوصول إلى localhost. اربط التطبيق بنفق HTTPS:

```bash
pkg install cloudflared   # أو: npm i -g cloudflared
cloudflared tunnel --url http://127.0.0.1:8081
```

انسخ عنوان `https://...` الناتج والصقه في حقل «العنوان العام» داخل التطبيق ثم احفظ.
بدون ذلك تعمل روابط المشاركة محليًا فقط ولن تظهر معاينتها في واتساب.

ملاحظة: قد تحتفظ واتساب بنسخة مخزنة مؤقتًا؛ المعاينة ليست فورية ولا مضمونة.

## النشر الدائم المجاني (بلا بطاقة بنكية)

نفق cloudflared **مؤقت** (يتغير ويموت) — والمنصات التي تطلب بطاقة (Fly.io،
وRender حاليًا) مغلقة أمام من لا يملك بطاقة. المسار المجاني تمامًا:
**Hugging Face Spaces (تسجيل بالإيميل فقط، بلا بطاقة)**
+ تنبيه مجاني يمنع نوم الخادم. الرابط الناتج ثابت ودائم، مثل:
`https://<اسمك>-<المساحة>.hf.space`

### الخطوة 1: إنشاء المساحة (من المتصفح، مرة واحدة)

1. أنشئ حسابًا مجانيًا على `huggingface.co` (إيميل فقط).
2. New Space ← الاسم (مثل `wpd-shares`) ← اختر **Gradio** كنوع المساحة
   ← قالب Blank ← Create. (هذا المستودع جاهز: `server.py` + `requirements.txt` +
   بيانات `sdk: gradio` و`app_file: server.py` أعلى هذا الملف — لا حاجة لأي
   مكتبة عدا Pillow.)
3. من صفحة المساحة ← Settings ← Variables: أضف
   `PORT=7860` و `WPD_DATA_DIR=/data` ولاحقًا `PUBLIC_BASE_URL`
   بعنوان المساحة الكامل.

### الخطوة 2: دفع الكود إلى المساحة (من Termux)

من إعدادات حسابك: Access Tokens ← Create New Token ← دور **Write** ← انسخه.
(`HF_TOKEN` لمرة الدفع؛ يبقى مخزنًا في حسابك أنت فقط.)

```bash
cd /storage/emulated/0/b/whatsapp-preview-doctor-v2
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/storage/emulated/0/b/whatsapp-preview-doctor-v2
git remote add space https://huggingface.co/spaces/<اسمك>/<المساحة>
git push space main
```

سيطلب اسم المستخدم (اتركه فارغًا أو ضع اسمك) وكلمة المرور = **رمز HF**.
انتظر انتهاء البناء (أول بناء قد يستغرق عدة دقائق).

### الخطوة 3: التفعيل ومنع النوم

1. ضع `PUBLIC_BASE_URL` في Variables على عنوان المساحة ← ستُعاد
   العملية تلقائيًا.
2. حساب مجاني على `uptimerobot.com` (بلا بطاقة) ← منبّه `HTTP(s)` كل
   5 دقائق على `https://<مساحتك>.hf.space/api/health` (المساحات المجانية
   تنام بعد 48 ساعة بلا زيارات — المنبّه يمنع ذلك).
3. افتح `https://<مساحتك>.hf.space` من أي متصفح واستخدم التطبيق
   (نفس الواجهة العربية + نفس مسارات الروبوت) — كل روابط المشاركة
   **دائمة** بلا هاتف ولا نفق.

### قيود الصدق في المجانية

- لا بطاقة في أي خطوة (Hugging Face وUptimeRobot وGitHub كلها بالإيميل).
- إعادة البناء/الضبط المصنعي للمساحة تمسح البيانات (`/data`) — نادر،
  وعندها أعد توليد الروابط فقط.
- أول طلب بعد صمت طويل قد يتأخر (إقلاع بارد) — المنبّه الدوري يجعله نادرًا.
- بديل مركون: `render.yaml` يصلح لـ Render لكنها تطلب بطاقة حاليًا.

## واجهات API
- `GET /api/health`
- `GET /api/config` / `POST /api/config`
- `POST /api/analyze {"url"}`
- `POST /api/upload` (ملف `image` حتى 8MB — بديل عند حجب الموقع)
- `POST /api/generate {"image_url"|"upload_id","page_url","title","description","mode"}`
- `GET /api/sharecheck/<token>` (فحص ما يراه الروبوت + كشف العنوان القديم)
- `GET /share/<token>` (صفحة OG + تحويل)
- `GET /preview/<token>.jpg` (الصورة 1200×630)
- `GET /robots.txt`

## الأمان
- يقبل http/https فقط، ويمنع SSRF (localhost والشبكات الداخلية) قبل كل طلب وبعد كل تحويل.
- مهلات وحدود حجم، والتحقق من الصور عبر Pillow.
- لا إعادة محاولة لـ 404/410، ومحاولة واحدة مبررة لـ 403 مع Referer.
