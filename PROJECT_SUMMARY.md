# SPHEREx Explorer: ملخص المشروع الكامل

> آخر تحديث: الأحد 15 نوفمبر 2026 (يوم التحكيم). شوف **القسم 0**.

---

## 0. وين وقفنا (اقرأ هذا أول)

**آخر تحديث: الأحد 15 نوفمبر 2026، يوم التحكيم.** الشغل على فرع `judging-features` (ما انرفع push).

**وش انضاف اليوم (كله مختبر بتشغيل فعلي):**
1. **TAP بـ timeout:** IRSA TAP كان طايح (502) طول الجلسة. `/observations` صار يرجع للكاش بعد 15 إلى 40 ثانية بدل ما يعلّق، ويقبل أقرب بحث محفوظ في حدود 0.1°.
2. **منع الـ right-click في Aladin** (brightness/contrast). الـ pan والـ zoom والضغط شغالين.
3. **`/astrometry`:** الـ presets الخمسة تجي من IRSA (عن طريق Gator، لأن TAP طايح): Barnard و RW Aur A و R Dra و ε Indi B من **Gaia DR3** (ε Indi B موجود في Gaia DR3: parallax 270.7 mas)، و WISE 0855 من **CatWISE2020**. موقع R Dra صار من Gaia بدل "من الذاكرة".
4. **Timelapse علمي:** stretch مشترك لكل باند (بعد طرح median الخلفية لأن الـ zodi مو مطروح)، وكل فريم متمركز على موقع الهدف عند تاريخه (proper motion عن طريق `apply_space_motion`).
5. **Motion track:** 2MASS و AllWISE و CatWISE2020 و Gaia DR3، وكل مسح يتطابق عند تاريخه هو. خط متصل للمرصود، ومتقطع للمحسوب، وجدول تحت الخريطة. Barnard: 10.39″/سنة.
6. **Difference imaging:** كل فريم ناقص median الفريمات الثانية اللي طولها الموجي في حدود ±3% منه (بسبب الـ LVF). ولو أقل من 3 فريمات، يقول السبب وما يطرح.
7. **الخريطة:** طبقة SPHEREx DR1 **صارت تنرسم** (السيرفر يقدّم الـ cube حق IRSA كصورة 2D من الـ PNG tiles حقته)، وطبقة Gaia DR3.
8. **Quick wins:** زر WiseView، و badge للـ flags قرب الهدف على كل فريم، وسطر يوضح إن الطيف بالطول الموجي لكل بكسل (102 قناة).

**`web/vendor/aladin-next.js` (3.9.2-beta):** جربناه، وما رسم الـ cube بعد. ما نحتاجه، ولا هو في git. احذفه لو تبي.

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
| `GET /observations?ra=&dec=&limit=` | الصور اللي تغطي الموقع (IRSA TAP). ولو TAP طايح: الكاش، أو أقرب بحث محفوظ في حدود 0.1° |
| `GET /cutout?url=&ra=&dec=&size=[&pmra=&pmdec=&epoch=&mjd=][&lo=&hi=]` | PNG بعد طرح الخلفية. الهيدرات: `X-Tx/Ty/Nx/Ny` (بكسل الهدف)، `X-Lo/Hi` (الـ stretch حق الفريم)، `X-Ra/Dec` (الموقع المستخدم)، `X-Flags` (الـ flags في حدود 3 px من الهدف) |
| `GET /photometry?...[&pmra=&pmdec=&epoch=&mjd=]` | `flux_uJy` و `err_uJy` و `snr` و `wavelength` و `warnings`... |
| `GET /astrometry[?target=]` | `ra, dec, ref_epoch, pmra, pmdec, parallax, source_table, source_id`، وقاعدة الاختيار. الأهداف: `barnard, rwaur, rdra, epsindib, wise0855` |
| `GET /catalog?catalog=&ra=&dec=&radius=` | cone search في `gaia_dr3_source` أو `catwise_2020` أو `allwise_p3as_psd` أو `fp_psc` (2MASS) |
| `GET /motion?target=` أو `?ra=&dec=&pmra=&pmdec=&epoch=` | `observed` (لكل مسح) و `not_found` و `computed` (كل سنة لين اليوم) |
| `POST /diffstack` ثم `GET /diff?id=&url=` | صورة الفرق. وإذا أقل من 3 فريمات في حدود ±3% من λ يرجع 422 مع السبب. الهيدرات: `X-Wl` و `X-RefLo/Hi` و `X-NRef` |
| `GET /hips/spherex/...` | properties الـ SPHEREx HiPS بصيغة 2D، والـ tiles redirect لـ IRSA |
| `GET /health` و `GET /docs` | |

**متغيرات البيئة:** `SPHEREX_SOURCES` (الافتراضي `s3,irsa`)، و `SPHEREX_S3_BASE`، و `SPHEREX_CACHE_MAX_GB` (2)، و `SPHEREX_TAP_TIMEOUT` (40).

**الملفات الجديدة:** `astrometry.py` (propagation)، و `catalogs.py` (Gator والأهداف والـ motion track)، و `diff.py` (الـ resampling والطرح).

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

## 7. الأهداف: الأرقام من IRSA (Gaia DR3 / CatWISE2020)، تاريخ الاستعلام 15 نوفمبر 2026

| الهدف | الكتالوج | source_id | RA | Dec | Epoch | pmRA* | pmDec (mas/yr) | parallax (mas) |
|---|---|---|---|---|---|---|---|---|
| Barnard's Star | Gaia DR3 | 4472832130942575872 | 269.4485025 | +4.7394201 | 2016.0 | −801.551 | 10362.394 | 546.976 |
| RW Aurigae A | Gaia DR3 | 156430822114424576 | 76.9560812 | +30.4012046 | 2016.0 | 2.641 | −27.398 | 6.406 |
| R Draconis | Gaia DR3 | 1648328016354496384 | 248.1673784 | +66.7550067 | 2016.0 | −20.643 | 6.149 | 1.251 |
| ε Indi Ba/Bb | Gaia DR3 | 6412596012146801152 | 331.0764526 | −56.7938121 | 2016.0 | 3981.977 | −2466.832 | 270.658 |
| WISE 0855−0714 | CatWISE2020 | 1342m076_b0-058037 | 133.7831722 | −7.2442516 | 2015.863 (meanobsmjd) | −8021.2 | 1035.7 | — |

**ملاحظات:** وحدة الـ pm في CatWISE2020 هي arcsec/yr، وحولناها لـ mas/yr. وفيه WISE 0855 له أكثر من صف في CatWISE لأنه سريع، والقاعدة تختار الصف اللي uncertainty حقه أقل.
**Motion track:** Barnard من 2MASS (2000) لين Gaia (2016) = 10.33″/سنة، والفروقات أقل من 0.6″. WISE 0855: "not found" في 2MASS و Gaia (طبيعي). R Dra: ما انلقى في CatWISE (ساطع مرة). RW Aur: في 2MASS الفرق 1.1″ لأن A و B (بينهم 1.4″) طلعوا نجم واحد.
**الطيف (قديم، من 14 نوفمبر):** RW Aur A: D1 و D2 و D3 بـ S/N بين 228 و 321. Barnard: saturated. ε Indi B: طيف T dwarf.

## 8. وش للحين ما تأكدنا منه

1. ~~تشغيل كامل من الواجهة بعد إصلاحات الأمان~~ ✅ اختبرناه: 24 من 24 فريم، و24 نقطة طيف على النجم التجريبي. **بس فيه نقطة شاذة:** D4 عند 3.174 µm طلعت 210,591 µJy، يعني أعلى بحوالي 20 مرة. نحتاج sigma-clipping لكل باند.
2. فتح الأداة من جهاز ثاني على الشبكة (`--host 0.0.0.0`).
3. CORS من origin ثاني (يعني مع `?api=`).
4. أوامر Debian و macOS في الـ README: مكتوبة بس ما انجربت على جهاز فعلي.
5. الـ flux من S3 مقابل IRSA على نفس الفريم الحقيقي: كل الفريمات المقارنة كانت saturated.
6. هل AWS يقطع الاتصال كثير؟ مرة وحدة فشلت فيها IRSA و AWS مع بعض.
7. `_trim_cache()` ما انختبر.

---

## 9. المشاكل والحدود المعروفة

- **IRSA TAP طايح (502) يوم 15 نوفمبر.** الكتالوجات تشتغل عن طريق Gator. والبحث في الأرشيف يرجع للكاش بعد 15 ثانية تقريبًا، ويشتغل بس للأهداف اللي بحثنا عنها قبل (الخمسة والنجم التجريبي).
- **الفريمات بموقع جديد** (بعد تصحيح الـ pm) تنقص من AWS أول مرة، وكل فريم ياخذ ثواني. 35 فريم = 4 دقايق تقريبًا. حمّل الهدف **قبل** العرض.
- **Difference:** الـ PSF مو sampled كفاية (6″/px)، فالنجوم الساطعة تخلي residual (+/−). ومع فريمات قليلة (مثل النجم التجريبي) أغلب الفريمات ترفض ("only N frames within ±3%").
- **طبقة SPHEREx في الخريطة:** هذي tiles حقت preview بصيغة PNG. الـ properties ما تقول أي طول موجي. والـ slider حق الـ 102 قناة ما يشتغل.
- **Barnard saturated** في أغلب الفريمات، فالـ photometry ترفضها (422). وهذا طبيعي.
- الـ parallax ما ينطبق على المسار (barycentric). أقل من 1″، وما يبين على بكسل 6″.
- الأخطاء statistical بس. ونقطة D4 3.174 µm حقت النجم التجريبي شاذة (×20). معروفة وما انحلت.

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
- **IRSA Gator** (`/cgi-bin/Gator/nph-query`) شغّال حتى لو TAP طايح. نفس الجداول: `gaia_dr3_source`، `catwise_2020`، `allwise_p3as_psd`، `fp_psc`.
- **AllWISE** `ra_pm/dec_pm` عند MJD 55400.0 (2010.5589)، حسب الـ data dictionary حق IRSA.
- **SPHEREx HiPS:** الـ cube slices موجودة FITS بس (`fits-cube`)، أما الـ PNG فهي 2D (`NpixN.png`).
- **WiseView:** `http://byw.tools/wiseview#ra=..&dec=..` (http بس).

---

## 11. الخطوات الجاية

1. Merge فرع `judging-features` على `main` بعد العرض، وبعدها push (`git push loom main && git push origin main`).
2. (اختياري) centroid لـ SPHEREx في كل فريم كنقاط "SPHEREx centroid" في الـ motion track.
3. (اختياري) تصدير الـ timelapse WebM.
4. sigma-clipping لكل باند في الطيف (عشان نقطة D4 الشاذة).

## 12. أفكار ما تنفذت

aperture correction، و error budget كامل، والبحث بالاسم في الخريطة، و centroid لكل فريم SPHEREx، وتصدير WebM.
(اللي خلص اليوم: Gaia في الخريطة، ورابط WiseView، و proper motion، و difference imaging.)
