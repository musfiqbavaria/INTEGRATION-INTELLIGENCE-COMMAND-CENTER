# নতুন ফিচার ও অটোমেশন — কার্যপদ্ধতি ও যাচাইকরণ

Emerald Rozalia Marketing ERP · হালনাগাদ ১৬ আগস্ট ২০২৬

এই নথিতে নতুন যুক্ত প্রতিটি বৈশিষ্ট্য, তার কাজ করার পদ্ধতি এবং কীভাবে নিজে
যাচাই করবেন তা দেওয়া আছে। মোট **৮৭টি স্বয়ংক্রিয় টেস্ট** আছে, সবগুলো পাস করে।


---

## সারণি — এক নজরে সব ফিচার

| # | ফিচার / অটোমেশন | কী করে | কার্যপদ্ধতি | যাচাই কমান্ড | টেস্ট |
|---|---|---|---|---|---|
| ১ | **রেকর্ড সম্পাদনা** | ১৭টি মডিউলেই edit (আগে শুধু create/delete) | `?edit=<id>` → পূরণ-করা ফর্ম → `full_clean()` → শুধু বদলানো ফিল্ড audit-এ | `test core.tests.RecordEditTests` | ৯ |
| ২ | **Bulk কাজ** | একসাথে delete / unsubscribe | checkbox + select-all → নির্বাচন হলেই bar দেখায় | ঐ একই ক্লাস | — |
| ৩ | **খোঁজা (Search)** | সব টেক্সট কলামে case-insensitive | `icontains` OR-যুক্ত query | `test core.tests.ModuleListingTests` | ৭ |
| ৪ | **Pagination** | পৃষ্ঠাপ্রতি ২৫ (আগে নীরবে ১০০-তে কাটা) | Django `Paginator`; `q` ও `page` URL-এ থাকে | ঐ একই ক্লাস | — |
| ৫ | **ইভেন্ট ট্রিগার** | ৭টি ঘটনায় workflow সঙ্গে সঙ্গে চালু | Django signals + `on_commit` + depth guard | `test core.tests.EventTriggerTests` | ৭ |
| ৬ | **দৈনিক সারসংক্ষেপ** | প্রতিদিন ০৭:০০ মালিকের ইমেইল | beat `crontab` → HTML render → send | `test core.tests.OwnerDigestTests` | ২ |
| ৭ | **সময়সীমা নজরদারি** | overdue হলে ইভেন্ট, একবারই | প্রতি ৫ মিনিট; `overdue_notified_at` পুনরাবৃত্তি ঠেকায় | `test core.tests.OverdueSweepTests` | ২ |
| ৮ | **Segment** | ক্যাম্পেইনের প্রাপক সত্যিই বাছে | `wholesale`, `market:X`, `score>=N` → queryset; অচেনা হলে **কেউ নয়** | `test core.tests.SegmentTests` | ৭ |
| ৯ | **নির্ধারিত ক্যাম্পেইন** | `scheduled_at` এখন কাজ করে | beat প্রতি মিনিট → `send_campaign` | `test core.tests.ScheduledCampaignTests` | ৩ |
| ১০ | **Wait ধাপ** | সত্যিকারের বিরতি (আগে উপেক্ষা হতো) | `apply_async(countdown=)`; run status `waiting` | `test core.tests.WaitStepTests` | ৪ |
| ১১ | **Bounce সুরক্ষা** | ৩ বার ব্যর্থে auto-unsubscribe | প্রতি ১৫ মিনিট; `ConsentEvent` + alert + `lead.bounced` | `test core.tests.DeliverabilityTests` | ৪ |
| ১২ | **সম্মতির মেয়াদ** | ২৪ মাসের পুরোনো consent বাতিল | প্রতিদিন ০৩:৩০; status `consent-expired` | `test core.tests.ConsentExpiryTests` | ২ |
| ১৩ | **নিষ্ক্রিয় লিড** | ৬ মাস চুপ থাকলে শনাক্ত | প্রতিদিন ০৪:০০ → `lead.dormant` ইভেন্ট | `test core.tests.DormantLeadTests` | ২ |
| ১৪ | **প্রকাশ্য AEO** | AI ও সার্চ ইঞ্জিনের জন্য উত্তর | `/answers/`, `/answers/<id>/`, `/sitemap.xml`, `/robots.txt` + FAQPage JSON-LD | `test core.tests.AeoTests` | ১ |
| ১৫ | **CSV রপ্তানি** | সব মডিউলে, সার্চ মেনে | secret `<hidden>`; audit-এ লেখা হয় | `test core.tests.CsvExportTests` | ৪ |
| ১৬ | **CSV আমদানি** | Leads-এ, dry-run সহ | `email` মিলিয়ে update; “Validate only” ডিফল্ট | `test core.tests.CsvImportTests` | ৭ |
| ১৭ | **নিরাপত্তা** | অনুমতি, rate limit, webhook স্বাক্ষর | superuser-only মডিউল; ১০/মিনিট IP; `X-Hub-Signature-256` | `test core.tests.PermissionTests` | ১০ |

**মোট ৮৭টি টেস্ট** — একসাথে চালাতে: `python manage.py test core`

### স্বয়ংক্রিয় কাজের সময়সূচি

| কাজ | কখন চলে | ফল |
|---|---|---|
| `process_due_automations` | প্রতি ১ মিনিট | সময় হয়ে যাওয়া workflow সারিতে দেয় |
| `send_scheduled_campaigns` | প্রতি ১ মিনিট | নির্ধারিত ক্যাম্পেইন পাঠায় |
| `sweep_overdue_attention` | প্রতি ৫ মিনিট | `attention.overdue` ইভেন্ট তোলে |
| `process_bounces` | প্রতি ১৫ মিনিট | বারবার ব্যর্থ ঠিকানা unsubscribe করে |
| `expire_stale_consent` | প্রতিদিন ০৩:৩০ | পুরোনো consent বাতিল করে |
| `flag_dormant_leads` | প্রতিদিন ০৪:০০ | `lead.dormant` ইভেন্ট তোলে |
| `send_owner_digest` | প্রতিদিন ০৭:০০ | মালিকের দৈনিক ইমেইল |

### ইভেন্ট তালিকা

| ইভেন্ট | কখন ঘটে | সাধারণ ব্যবহার |
|---|---|---|
| `lead.created` | নতুন লিড | স্বাগত ইমেইল, স্কোরিং |
| `lead.consented` | প্রথমবার consent | onboarding সিকোয়েন্স |
| `campaign.sent` | ক্যাম্পেইন শেষ | ফলাফলের সারসংক্ষেপ |
| `delivery.failed` | পাঠানো ব্যর্থ | সতর্কতা, তালিকা পরিষ্কার |
| `attention.overdue` | সময়সীমা পার | escalation |
| `lead.dormant` | ৬ মাস নিষ্ক্রিয় | win-back ক্যাম্পেইন |
| `lead.bounced` | bounce-এর পর unsubscribe | তালিকা পরিচ্ছন্নতা |

---

## ০. যাচাই শুরু করার আগে

সব কমান্ড প্রকল্পের মূল ফোল্ডার (`g:\Projects\Automation`) থেকে চালাতে হবে।

```bash
.venv\Scripts\activate
set DJANGO_SETTINGS_MODULE=config.settings_local
```

**সব টেস্ট একবারে চালান:**

```bash
python manage.py test core
```

প্রত্যাশিত ফল: `Ran 87 tests ... OK`

**সার্ভার চালু করুন:**

```bash
python manage.py runserver
```

> **সতর্কতা:** `--noreload` ব্যবহার করবেন না। Django 5.1 থেকে template cache
> `DEBUG=True` অবস্থাতেও সক্রিয় থাকে, তাই `--noreload` দিলে HTML পরিবর্তন
> দেখা যাবে না।

ঠিকানা: `http://127.0.0.1:8000/login/` · ব্যবহারকারী: `urmos@rozalia.ie`

---

## ১. রেকর্ড সম্পাদনা (Edit) ও একসাথে একাধিক কাজ (Bulk)

### কাজ কী

আগে যেকোনো মডিউলে শুধু **তৈরি** ও **মুছে ফেলা** যেত। একটি ইমেইলে টাইপো
থাকলে রেকর্ডটি মুছে আবার নতুন করে লিখতে হতো। এখন ১৭টি মডিউলেই সম্পাদনা করা যায়।

### কার্যপদ্ধতি

- প্রতিটি সারিতে **Edit** বোতাম → পাশের প্যানেল পূরণ-করা সম্পাদনা ফর্মে বদলে যায়
- সম্পাদনাধীন সারিটি সবুজ দাগ দিয়ে চিহ্নিত হয়
- **Cancel** চাপলে ঠিক আগের সার্চ ও পৃষ্ঠায় ফিরে আসে
- সংরক্ষণের আগে `full_clean()` চলে — ভুল ইমেইল হলে রেকর্ড অপরিবর্তিত থাকে
- **শুধু যে ফিল্ড আসলে বদলেছে** সেটিই audit log-এ লেখা হয়

**নিরাপত্তা:** ব্যবহারকারী সম্পাদনা করলেও `is_staff`/`is_superuser` দেওয়া যায় না,
পাসওয়ার্ড এখান থেকে বদলানো যায় না, এবং `is_secret` Setting ফাঁকা রাখলে আগের
গোপন মান অক্ষত থাকে।

### যাচাই

```bash
python manage.py test core.tests.RecordEditTests    # ৯টি টেস্ট
```

**হাতে-কলমে:**
1. `/leads/` খুলুন → যেকোনো সারিতে **Edit** চাপুন
2. Company বদলে **Save changes** দিন → “Record #N updated” বার্তা আসবে
3. `/audit/` খুলুন → `lead.updated` এন্ট্রিতে শুধু বদলানো ফিল্ড দেখবেন
4. কয়েকটি সারিতে টিক দিন → নিচে **Delete selected** বার আসবে

---

## ২. খোঁজা (Search) ও পৃষ্ঠা বিভাজন (Pagination)

### কাজ কী

আগে তালিকা নীরবে ১০০ সারিতে কেটে যেত — ১০১তম রেকর্ড কোনোভাবেই দেখা যেত না।

### কার্যপদ্ধতি

- মডেলের **সব টেক্সট কলামে** case-insensitive খোঁজা হয়
- পৃষ্ঠাপ্রতি ২৫টি রেকর্ড
- শিরোনামে প্রকৃত সংখ্যা: “৩০ matches of ৬৩”
- সার্চ ও পৃষ্ঠা URL-এ থাকে, তাই মুছে ফেলার পরেও একই জায়গায় ফিরে আসে

### যাচাই

```bash
python manage.py test core.tests.ModuleListingTests    # ৭টি টেস্ট
```

**হাতে-কলমে:** `/leads/` → সার্চ বাক্সে কোম্পানির নাম লিখুন → Next/Previous চাপুন।

---

## ৩. ইভেন্ট ট্রিগার — অটোমেশন ইঞ্জিনের অনুপস্থিত অর্ধেক

### কাজ কী

`Automation.trigger` আগে শুধু **টেক্সট হিসেবে সংরক্ষিত ও প্রদর্শিত** হতো, কখনো
মূল্যায়ন হতো না। “WHEN Lead created” লেখা থাকলেও workflow প্রতি ৬০ মিনিটে
টাইমারে চলত — লিড তৈরি হোক বা না হোক।

### কার্যপদ্ধতি

নতুন `trigger_event` ফিল্ড, Django signals দিয়ে চালিত:

| ইভেন্ট | কখন ঘটে |
|---|---|
| `lead.created` | নতুন লিড তৈরি হলে |
| `lead.consented` | লিড প্রথমবার consent দিলে |
| `campaign.sent` | ইমেইল ক্যাম্পেইন পাঠানো শেষ হলে |
| `delivery.failed` | কোনো বার্তা পাঠানো ব্যর্থ হলে |
| `attention.overdue` | কাজের সময়সীমা পেরোলে |
| `lead.dormant` | লিড দীর্ঘদিন নিষ্ক্রিয় থাকলে |
| `lead.bounced` | বারবার bounce-এর পর unsubscribe হলে |

**তিনটি সুরক্ষা:**

1. **শুধু পরিবর্তনের মুহূর্তে** — `pre_save` snapshot রাখে, তাই ইতিমধ্যে consent
   দেওয়া লিড সম্পাদনা করলে আবার ইভেন্ট চালু হয় না
2. **`transaction.on_commit`** — transaction কমিট হওয়ার আগে worker কখনো row পড়ে না
3. **লুপ প্রতিরোধ** — যে workflow-এর ইমেইল ব্যর্থ হয় সেটি `delivery.failed`
   তোলে, যা আবার একই workflow চালাতে পারত। `depth` ২-এ থেমে যায়

### যাচাই

```bash
python manage.py test core.tests.EventTriggerTests    # ৭টি টেস্ট
```

**হাতে-কলমে:**
1. `/automations/` → নতুন workflow তৈরি করুন
2. **Fires on** ড্রপডাউনে **Lead created** বাছুন
3. Actions-এ লিখুন: `Assign lead score`
4. তালিকায় **Activate** চাপুন
5. `/leads/` → নতুন লিড যোগ করুন
6. `/automations/` → Execution logs-এ সঙ্গে সঙ্গে নতুন run দেখবেন

---

## ৪. মালিকের দৈনিক সারসংক্ষেপ (Daily Digest)

### কাজ কী

এক-ব্যক্তির ব্যবসার জন্য সবচেয়ে গুরুত্বপূর্ণ অটোমেশন: সিস্টেম নিজে থেকে
জানায়, লগইন করে খুঁজতে হয় না।

### কার্যপদ্ধতি

প্রতিদিন সকাল **০৭:০০ (Europe/Dublin)** একটি ইমেইল যায় `OWNER_EMAIL` ঠিকানায়,
যাতে থাকে — critical ও overdue সংখ্যা, নতুন লিড, সিদ্ধান্তের অপেক্ষায় থাকা
বিষয়, পাঠানো ক্যাম্পেইন, ব্যর্থ ডেলিভারি এবং সমস্যাযুক্ত সংযোগ।

### যাচাই

```bash
python manage.py test core.tests.OwnerDigestTests    # ২টি টেস্ট
```

**এখনই একটি পাঠিয়ে দেখুন** (local-এ কনসোলে ছাপা হয়, বাইরে যায় না):

```bash
python manage.py shell -c "from core.tasks import send_owner_digest; print(send_owner_digest())"
```

---

## ৫. সময়সীমা পেরোনো কাজের নজরদারি

প্রতি ৫ মিনিটে `sweep_overdue_attention` চলে। `due_at` পেরোনো প্রতিটি item-এর
জন্য একবারই `attention.overdue` ইভেন্ট তোলে — `overdue_notified_at` ফিল্ড
পুনরাবৃত্তি ঠেকায়।

```bash
python manage.py test core.tests.OverdueSweepTests    # ২টি টেস্ট
```

---

## ৬. ড্রিপ সিকোয়েন্স — প্রকৃত মার্কেটিং অটোমেশন

### ৬.১ Segment এখন সত্যিই প্রাপক বাছে

আগে `segment` ফিল্ডে যাই লিখুন, **প্রতিটি ক্যাম্পেইন সব consent-দেওয়া লিডে**
যেত। “Wholesale partners” লিখেও পুরো retail তালিকায় মেইল চলে যেত।

**ব্যাকরণ** (কমা দিয়ে আলাদা, সবগুলো মিলতে হবে):

| লেখা | অর্থ |
|---|---|
| `all` | সব consent-দেওয়া লিড |
| `wholesale` | কোম্পানি আছে এমন |
| `retail` | কোম্পানি নেই এমন |
| `status:qualified` | নির্দিষ্ট status |
| `market:Ireland` | নির্দিষ্ট বাজার |
| `source:Website` | নির্দিষ্ট উৎস |
| `score>=70` | ন্যূনতম স্কোর |

**দুটি নিরাপত্তা নিয়ম:**
- consent বাধ্যতামূলক — কোনো নিয়মই তালিকা বড় করতে পারে না
- **অচেনা segment হলে কারও কাছে যায় না** (আগের ভুলের ঠিক উল্টো)

### ৬.২ নির্ধারিত সময়ে পাঠানো

`scheduled_at` ফিল্ড আগে কেউ পড়ত না — scheduled ক্যাম্পেইন কখনোই যেত না।
এখন প্রতি মিনিটে beat task সময় হয়ে যাওয়া ক্যাম্পেইন সারিতে দেয়।

### ৬.৩ Wait ধাপ এখন সত্যিকারের বিরতি

আগে “Wait 20 hours” কেবল **উপেক্ষা** করা হতো। আপনার seed করা
`Abandoned Basket Recovery` (ইমেইল → ২০ ঘণ্টা অপেক্ষা → WhatsApp) তাই কাজ
করত না — WhatsApp সঙ্গে সঙ্গে চলে যেত।

এখন workflow বিরতিতে থামে, run-এর status হয় `waiting`, এবং বাকি ধাপগুলো
`countdown` দিয়ে পরে চালানোর জন্য সারিতে যায়।

### যাচাই

```bash
python manage.py test core.tests.SegmentTests           # ৭টি টেস্ট
python manage.py test core.tests.ScheduledCampaignTests # ৩টি টেস্ট
python manage.py test core.tests.WaitStepTests          # ৪টি টেস্ট
```

**হাতে-কলমে:** `/email-marketing/` → নতুন ক্যাম্পেইনে Segment লিখুন `wholesale`
→ তালিকায় দেখবেন “N matches — wholesale (has a company)”। ভুল কিছু লিখলে
হলুদ রঙে “unrecognised” দেখাবে।

---

## ৭. ডেলিভারি ও সম্মতি সুরক্ষা

### ৭.১ Bounce হলে স্বয়ংক্রিয় unsubscribe

একই ঠিকানায় **৩ বার** ব্যর্থ হলে লিডটি unsubscribe হয়, `ConsentEvent` লেখা হয়
এবং মালিকের জন্য attention item তৈরি হয়। বারবার bounce হওয়া ঠিকানায় মেইল
পাঠাতে থাকলে প্রেরকের সুনাম নষ্ট হয় এবং বৈধ মেইলও স্প্যামে যায়।

### ৭.২ পুরোনো সম্মতির মেয়াদ শেষ

**২৪ মাসের** বেশি পুরোনো consent প্রতি রাতে বাতিল হয় (status হয়
`consent-expired`), `ConsentEvent` লেখা হয় এবং governance alert ওঠে। GDPR-এর
দিক থেকে বহু বছরের পুরোনো সম্মতি রক্ষা করা কঠিন।

### ৭.৩ নিষ্ক্রিয় লিড শনাক্তকরণ

**৬ মাস** যোগাযোগ না হলে `lead.dormant` ইভেন্ট ওঠে, যাতে win-back workflow চালু
করা যায়।

তিনটি সীমাই `.env` দিয়ে বদলানো যায়: `BOUNCE_LIMIT`, `CONSENT_EXPIRY_MONTHS`,
`DORMANT_MONTHS`।

### যাচাই

```bash
python manage.py test core.tests.DeliverabilityTests   # ৪টি টেস্ট
python manage.py test core.tests.ConsentExpiryTests    # ২টি টেস্ট
python manage.py test core.tests.DormantLeadTests      # ২টি টেস্ট
```

---

## ৮. প্রকাশ্য AEO পাতা ও JSON-LD

### কাজ কী

AEO মডিউলের **পুরো উদ্দেশ্যই** ছিল AI সহকারী ও সার্চ ইঞ্জিন যেন এই ব্যবসাকে
উদ্ধৃত করে। কিন্তু `/aeo/` লগইন-সুরক্ষিত ছিল এবং কোথাও JSON-LD ছিল না —
অনুমোদিত উত্তর কখনো বাইরে যেত না।

### কার্যপদ্ধতি

লগইন ছাড়াই খোলা চারটি ঠিকানা:

| ঠিকানা | কাজ |
|---|---|
| `/answers/` | সব প্রকাশিত উত্তর + FAQPage JSON-LD |
| `/answers/<id>/` | একক উত্তরের পাতা |
| `/sitemap.xml` | ক্রলারের জন্য তালিকা |
| `/robots.txt` | `/answers/` খোলা, `/login/`, `/api/`, `/leads/`, `/settings/`, `/users/`, `/audit/` বন্ধ |

**শুধুমাত্র `status="published"` উত্তর দেখানো হয়** — draft/review ৪০৪ দেয় এবং
কোথাও ফাঁস হয় না। পাতাগুলো আলাদা হালকা রঙের stylesheet ব্যবহার করে, কারণ
গ্রাহক ও ক্রলারের জন্য কালো কমান্ড কনসোল উপযুক্ত নয়।

### যাচাই

```bash
python manage.py test core.tests.AeoTests
```

**হাতে-কলমে (লগ আউট অবস্থায়):**
1. `http://127.0.0.1:8000/answers/` খুলুন
2. পাতার সোর্স দেখুন → `<script type="application/ld+json">` ব্লকে
   `"@type": "FAQPage"` থাকবে
3. `http://127.0.0.1:8000/robots.txt` ও `/sitemap.xml` খুলুন

**Google-এর যাচাই যন্ত্র:** পাতার HTML কপি করে
`https://validator.schema.org` এ পরীক্ষা করতে পারেন।

---

## ৯. CSV আমদানি ও রপ্তানি

### রপ্তানি (সব মডিউলে)

**Export CSV** বোতাম বর্তমান সার্চ অনুযায়ী রপ্তানি করে এবং audit log-এ লেখা হয়।
গোপন Setting-এর মান `<hidden>` হিসেবে যায় — ফাঁস হয় না।

### আমদানি (শুধু Leads)

- `email` কলাম বাধ্যতামূলক
- **“Validate only” ডিফল্টভাবে টিক দেওয়া** — আগে যাচাই, পরে সংরক্ষণ
- ইমেইল মিলিয়ে পুরোনো লিড হালনাগাদ হয়, নকল তৈরি হয় না
- নাম ছাড়া সারি, ভুল স্কোর বা ভুল তারিখ — প্রতিটি আলাদা করে জানানো হয়

### যাচাই

```bash
python manage.py test core.tests.CsvExportTests    # ৪টি টেস্ট
python manage.py test core.tests.CsvImportTests    # ৭টি টেস্ট
```

**হাতে-কলমে:** `/leads/` → **Export CSV** চাপুন। তারপর একটি ছোট CSV বানিয়ে
(`first_name,last_name,email`) “Validate only” টিক রেখে Import করুন — কিছুই
সংরক্ষিত হবে না, শুধু ফল দেখাবে।

---

## ১০. নিরাপত্তা (পূর্বে যুক্ত, এখানে স্মরণ করিয়ে দেওয়া)

- `/users/` দিয়ে superuser বানানো বন্ধ; পাসওয়ার্ড hash হয়
- Audit log শুধু পড়া যায়, মোছা যায় না
- `is_secret` Setting-এর মান লুকানো থাকে
- Login-এ rate limit: প্রতি IP ১০/মিনিট, প্রতি ইমেইল ৫/মিনিট
- WhatsApp webhook-এ `X-Hub-Signature-256` যাচাই (`WHATSAPP_APP_SECRET` লাগবে)

```bash
python manage.py test core.tests.PermissionTests core.tests.LoginRateLimitTests core.tests.WhatsAppWebhookTests
```

---

## ১১. Celery সময়সূচি (সার্ভারে)

| কাজ | কখন |
|---|---|
| `process_due_automations` | প্রতি ১ মিনিট |
| `send_scheduled_campaigns` | প্রতি ১ মিনিট |
| `sweep_overdue_attention` | প্রতি ৫ মিনিট |
| `process_bounces` | প্রতি ১৫ মিনিট |
| `expire_stale_consent` | প্রতিদিন ০৩:৩০ |
| `flag_dormant_leads` | প্রতিদিন ০৪:০০ |
| `send_owner_digest` | প্রতিদিন ০৭:০০ |

> **মনে রাখবেন:** এই কাজগুলো চলার জন্য সার্ভারে `worker` ও `scheduler` দুটিই
> চালু থাকতে হবে (`docker compose ps` দিয়ে দেখুন)। Local-এ Celery
> `CELERY_TASK_ALWAYS_EAGER` মোডে চলে, তাই কাজগুলো সঙ্গে সঙ্গেই সম্পন্ন হয়।

---

## ১২. সার্ভারে নেওয়ার সময়

নতুন migration আছে — `0003`, `0004`, `0005`। `deploy/DEPLOYMENT.md`-এর
সেকশন ৯ অনুসরণ করুন। `.env`-এ নতুন ঐচ্ছিক মান যোগ করা যায়:

```
WHATSAPP_APP_SECRET=
BOUNCE_LIMIT=3
CONSENT_EXPIRY_MONTHS=24
DORMANT_MONTHS=6
```

---

## সংক্ষিপ্ত যাচাই তালিকা

```bash
python manage.py test core          # ৮৭টি টেস্ট, সব পাস করা উচিত
python manage.py check              # কোনো সমস্যা থাকা উচিত নয়
python manage.py runserver          # ১৯টি পাতা + ৪টি প্রকাশ্য ঠিকানা
```
