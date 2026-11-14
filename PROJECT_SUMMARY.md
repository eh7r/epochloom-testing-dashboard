# SPHEREx Explorer: ملخص المشروع الكامل

> آخر تحديث: السبت 14 نوفمبر 2026، الساعة 14:30 (توقيت الرياض)
> الفريق: **EpochLoom** · NASA Space Apps Challenge 2026 · KFUPM الظهران (14 و 15 نوفمبر)
> التحدي: **Planet X and SPHEREx**

---

## 1. وش المشروع؟

أداة ويب تستكشف بيانات تلسكوب **SPHEREx** (مسح طيفي لكل السما، من 0.75 لين 5 µm، في 6 باندات D1 لين D6).
تكتب موقع في السما، والأداة:

1. تجيب كل صور SPHEREx اللي تغطي الموقع من أرشيف **IRSA**،
2. تقص cutouts صغيرة وتسوي لها **blink** عبر الزمن، باند باند،
3. تبني **طيف الجسم بالـ aperture photometry حقتنا** (بدون أداة forced photometry حقت IRSA)،
4. تعرض المكان على **خريطة سما تفاعلية** (Aladin Lite).

الهدف الشخصي: نبني الطيف بأنفسنا من الـ metadata حقت الأرشيف، مو نعتمد على أداة جاهزة.

---

## 2. هيكل المشروع

```
~/Project/spherex/
├── server.py           FastAPI: الـ API ويقدّم مجلد web/
├── photometry.py       الفوتومتري: measure(path, ra, dec, r_ap, r_in, r_out)
├── requirements.txt    fastapi uvicorn astropy numpy matplotlib pyvo fsspec requests
├── README.md           للفريق (بالإنجليزي): تشغيل على Debian و macOS و Arch والشبكة
├── PROJECT_SUMMARY.md  هذا الملف
├── .gitignore          .venv cache/ cutouts/ *.fits HANDOFF.md والملفات التجريبية
├── web/
│   ├── index.html      الداشبورد (ملف واحد، vanilla JS، بدون build)
│   └── vendor/aladin.js  Aladin Lite 3.8.2 (ES module، 2.39MB، رخصة GPL v3)
└── cache/              الـ cutouts المحفوظة (ما تنرفع)
```

**ملفات قديمة ما تنرفع:** `fetch.py` و `test_api.py` و `server_step3_backup.py` و `cutouts/` و `test.png` و `spherex/` و `HANDOFF.md`.

---

## 3. التشغيل (Arch + fish)

```fish
cd ~/Project/spherex && source .venv/bin/activate.fish
```

```fish
uvicorn server:app --reload --port 8000
```

افتح http://127.0.0.1:8000/

**من الجوال أو جهاز ثاني على نفس الواي فاي:**

```fish
uvicorn server:app --host 0.0.0.0 --port 8000
```

وبعدها افتح `http://<IP-اللابتوب>:8000/`. عشان تعرف الـ IP: `ip -4 addr` على Arch.

**سيرفر زميلك:** `http://127.0.0.1:8000/?api=http://<IP>:8000`

**البيئة:** Python 3.14، astropy 8.0.2، numpy 2.5.4، fastapi 0.162.7، uvicorn 0.54.0، pyvo 1.10.0، fsspec 2026.9.0.

---

## 4. الـ API

| Endpoint | وش يرجّع |
|---|---|
| `GET /observations?ra=&dec=&limit=` | الصور اللي تغطي الموقع (IRSA TAP، والحد الأقصى 200) |
| `GET /cutout?url=&ra=&dec=&size=` | صورة PNG، وموقع الهدف في الهيدرات `X-Tx` و `X-Ty` و `X-Nx` و `X-Ny` |
| `GET /photometry?url=&ra=&dec=&size=&r_ap=&r_in=&r_out=` | `flux_uJy` و `err_uJy` و `snr` و `wavelength` و `warnings` و `x` و `y` |
| `GET /health` | `{"ok":true,"sources":["irsa","s3"]}` |
| `GET /docs` | توثيق تفاعلي |

**متغيرات البيئة:** `SPHEREX_SOURCES` (الافتراضي `irsa,s3`)، و `SPHEREX_S3_BASE`، و `SPHEREX_CACHE_MAX_GB` (الافتراضي 2).

---

## 5. وش اشتغل اليوم (14 نوفمبر)

| # | المشكلة | السبب الحقيقي | الإصلاح |
|---|---|---|---|
| 1 | الداشبورد في مجلد لحاله | طلب | `dashboard.html` صار `web/index.html`، والسيرفر يقدمه بـ `StaticFiles` |
| 2 | الصور مبكسلة | CSS `image-rendering:pixelated` على cutout حجمه 58×58 | صار `auto` (عرض بس، الفوتومتري ما تأثر) |
| 3 | الخريطة ما تتحمل أبد | رابط `aladin.cds.unistra.fr/.../latest/aladin.js` يرجّع **404** | نسخة محلية في `web/vendor/` من unpkg (3.8.2) |
| 4 | المكتبة ما تشتغل بعد التنزيل | نسخة npm هي **ES module** (`export default`) | تتحمل بـ `<script type="module">` و `import`، وبعدها `mapInit()` |
| 5 | الخريطة سودا | سيرفرات CDS (alasky) ترجّع 404 أو timeout من شبكتنا | الـ surveys صارت روابط كاملة لمرايا **IRSA** (`irsa.ipac.caltech.edu/data/hips/CDS/...`) |
| 6 | الخريطة فاضية بعد ما تحمّلت | قاعدة CSS عامة `canvas{background:...}` غطّت طبقة النجوم | القاعدة صارت لـ `#plot` بس |
| 7 | فريمات كثيرة تفشل بـ 502 | `fsspec` **مو منزّل**، فالـ S3 fallback ما اشتغل ولا مرة | `pip install fsspec` |
| 8 | `aiohttp` ما ينزل | ما فيه نسخة منه تدعم Python 3.14 | السيرفر يستخدم `fsspec.implementations.http_sync` (على `requests`) لو `aiohttp` مو موجود |
| 9 | ثغرات أمنية | انظر القسم 6 | انظر القسم 6 |
| 10 | README قديم | | كتبت واحد جديد للفريق |

**Pan-STARRS** انشال من الخريطة لأنه مو موجود على مرايا IRSA، وصار مكانه **SDSS DR9**.

---

## 6. الأمان (اللي اتصلح)

| الثغرة | الإصلاح | الاختبار |
|---|---|---|
| فحص `url` بالبداية بس (يمر `..` و `#` و `@` ودومين ثاني) | regex: مسار SPHEREx فيه `[A-Za-z0-9_./-]` بس، وينتهي بـ `.fits`، وبدون `..` أو `//` | 9 حالات نجحت، وعلى السيرفر: `..` رجّع 400 ودومين ثاني رجّع 400 ✅ |
| `ra` و `dec` بدون تحقق في `/cutout` و `/photometry` | `ge/le` (0 لين 360، و −90 لين 90) | `ra=999` رجّع 422 ✅ |
| `cache/` بدون حد للحجم | `_trim_cache()` يحذف الأقدم لما يتجاوز `SPHEREX_CACHE_MAX_GB` | ما انختبر |
| XSS: الـ `warnings` تدخل `innerHTML` بدون تنظيف | `esc()`، وزدت عليه `'` | فحص syntax بس |
| CORS يسمح بكل الـ methods | `GET` بس، و `expose_headers` لـ X-Tx و X-Ty و X-Nx و X-Ny | من نفس الـ origin ✅ |

`?api=` يقبل `http` و `https` بس، ويستخدم الـ origin فقط.

---

## 7. نتايج اختبار الأهداف (على جهاز يزيد)

المواقع **محسوبة** لسنة 2025.6 من J2000 + الـ proper motion (مو مأخوذة من مصدر جاهز):

| الهدف | RA | Dec | الحركة | صور الأرشيف | النتيجة |
|---|---|---|---|---|---|
| RW Aurigae A | 76.956108 | +30.401151 | أقل من 1″ | أكثر من 200 | ✅ طيف: D1 و D2 و D3 بـ S/N بين 228 و 321، وبعض الفريمات saturated |
| Barnard's Star | 269.446357 | +4.767053 | ≈266″ | أكثر من 200 | ⚠️ كل الفريمات **saturated** (حقيقي)، وهذا يأكد إن الموقع صحيح |
| ε Indi Ba/Bb | 331.096267 | −56.801090 | ≈123″ | أكثر من 200 | ❓ ما انعاد بعد إصلاح S3 |
| WISE 0855−0714 | 133.760417 | −7.242292 | ≈124″ | أكثر من 200 | ❓ ما انعاد. متوقع يكون خافت جدًا، وفرصته في D5 و D6 بس |
| R Draconis | 248.1675 | +66.755 | | أكثر من 200 | ❓ ما انختبر، **والموقع من الذاكرة مو من مصدر** |
| النجم التجريبي | 304.69253 | +42.45383 | | 24 | ✅ فريمات وblink شغالة |

**S3 fallback على ملفات حقيقية: ✅ مثبت.** 4 cutouts حقت Barnard جات من AWS (`CUTSRC='aws-s3'`)، وفيها IMAGE و FLAGS و VARIANCE و ZODI، و`WAVETGT` صحيح (1.09 µm، و 2.31 لين 2.44 µm).

---

## 8. وش للحين ما تأكدنا منه

1. تشغيل كامل من الواجهة (بحث، ثم فريمات، ثم طيف) **بعد** إصلاحات الأمان. انقطع الاتصال في النص.
2. فتح الأداة من جهاز ثاني على الشبكة (`--host 0.0.0.0`).
3. CORS من origin ثاني (يعني مع `?api=`).
4. أوامر Debian و macOS في الـ README: مكتوبة بس ما انجربت على جهاز فعلي.
5. الـ flux من S3 مقابل IRSA على نفس الفريم الحقيقي: كل الفريمات المقارنة كانت saturated.
6. هل AWS يقطع الاتصال كثير؟ مرة وحدة فشلت فيها IRSA و AWS مع بعض.
7. `_trim_cache()` ما انختبر.

---

## 9. المشاكل والحدود المعروفة

- **بطء:** كل فريم ياخذ من 15 لين 60 ثانية من IRSA أول مرة، وبعدها من الكاش على طول. الداشبورد يطلب 3 بالتوازي.
- **IRSA يرجّع 503 كثير.** عندنا circuit breaker يتجاوز IRSA 45 ثانية بعد ما يفشل، وهنا الـ S3 مهم.
- **ما فيه تصحيح للـ proper motion.** الأهداف السريعة لازم ندخل موقعها لسنة 2025 بإيدنا.
- **الأخطاء statistical بس:** ما فيه aperture correction ولا deblending.
- **النجوم الساطعة saturated** (flags 0 و 1 و 2 و 26) وتنرفض.
- **أول رسم للخريطة** ياخذ حوالي 20 ثانية (كل tile حوالي 7 ثواني من IRSA).
- **GitHub Pages ما ينفع:** صفحة HTTPS ما تقدر تكلم سيرفر HTTP على لابتوب (mixed content). لازم باك اند منشور على HTTPS.

---

## 10. حقائق تقنية مثبتة

- **TAP:** `https://irsa.ipac.caltech.edu/TAP`، والجداول `spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid`، والفلتر `1 = CONTAINS(POINT('ICRS', ra, dec), p.poly)`.
- **رابط الملف** = `https://irsa.ipac.caltech.edu/` + `a.uri`. والـ cutout = الرابط + `?center={ra},{dec}d&size={deg}`.
- **cutout حجمه 0.1°** يطلع تقريبًا 58×58 px، والبكسل الواحد تقريبًا 6.15″.
- **الطول الموجي لكل بكسل:** `WCS(hdr, fobj=hdul, key="W")`، ثم `.sip=None`، ثم `pixel_to_world(x, y)`.
- **الباندات (µm):** D1 0.75–1.11، D2 1.11–1.64، D3 1.64–2.42، D4 2.42–3.82، D5 3.82–4.42، D6 4.42–5.0.
- **AWS:** bucket اسمه `nasa-irsa-spherex` في us-east-1، دخول بدون حساب، والمسار = كل اللي بعد `/ibe/data/spherex/`.
- **HiPS على IRSA:** القائمة في `https://irsa.ipac.caltech.edu/data/hips/list`. ومن ضمنها **`SPHEREx/spherex_allsky_cube`**، وهذي فكرة ممكن نحطها طبقة في الخريطة.
- **IRSA TAP** ما يقبل طلبات مباشرة من المتصفح (CORS)، فلازم تمر عبر السيرفر.
- **CDS** (aladin.cds و alasky و simbad) ما يوصل من شبكتنا.

---

## 11. الخطوات الجاية (بالترتيب)

1. **الرفع على GitHub:**
   ```fish
   cd ~/Project/spherex && git add . && git status
   ```
   بعدها commit، وبعدها نحدد الـ repo (للفريق ولا تحت `eh7r`)، وبعدها push.
2. **تست كامل من الواجهة** بعد إصلاحات الأمان.
3. **نعيد التست** على ε Indi B و WISE 0855 و R Dra (ونأكد موقع R Dra من مصدر).
4. **تصحيح الـ proper motion تلقائيًا** (pmra و pmdec، وكل فريم بتاريخه): أهم ميزة علمية للأهداف السريعة.
5. **centroiding لكل صورة:** لاكتشاف الأجسام المتحركة (مرشحين Planet X).
6. **طبقة SPHEREx HiPS** في الخريطة.
7. **مقارنة epoch** جنب بعض، وتصدير الطيف PNG.

---

## 12. أفكار ما تنفذت

Gaia cross-match، و aperture correction، و error budget كامل، والبحث بالاسم في الخريطة، ورابط WiseView لنفس الموقع.
