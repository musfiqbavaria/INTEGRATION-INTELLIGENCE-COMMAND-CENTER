# নতুন ফিচার ও অটোমেশন — কার্যপদ্ধতি ও যাচাইকরণ

Emerald Rozalia Marketing ERP · হালনাগাদ ১৭ আগস্ট ২০২৬

এই নথিতে নতুন যুক্ত প্রতিটি বৈশিষ্ট্য, তার কাজ করার পদ্ধতি এবং কীভাবে নিজে
যাচাই করবেন তা দেওয়া আছে। মোট **১৭৯টি স্বয়ংক্রিয় টেস্ট** আছে, সবগুলো পাস করে।


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
| ১৮ | **ইমেইল ট্র্যাকিং** | open ও click সত্যিই মাপা হয় | pixel + স্বাক্ষরিত click redirect; RFC 8058 unsubscribe | `test core.tests.EngagementTrackingTests` | ৯ |
| ১৯ | **আয়ের হিসাব (Attribution)** | কোন ক্যাম্পেইন টাকা এনেছে | last-touch; রাতে ledger-এ যোগ; forecast ও cohort | `test core.tests.AttributionTests` | ৫ |
| ২০ | **তাৎক্ষণিক সতর্কতা** | critical বিষয় সঙ্গে সঙ্গে জানায় | ২ মিনিটে ইমেইল/WhatsApp; স্বাক্ষরিত approve লিংক | `test core.tests.CriticalAlertTests` | ৪ |
| ২১ | **বহু প্রতিষ্ঠান** | একাধিক কোম্পানি ও মুদ্রা | `Organisation` + `FxRate`; দ্বিতীয় প্রতিষ্ঠান যোগ করলেই আলাদা হয়ে যায় | `test core.tests.TenancyTests` | ১১ |

**মোট ১৭৯টি টেস্ট** — একসাথে চালাতে: `python manage.py test core`

### স্বয়ংক্রিয় কাজের সময়সূচি

| কাজ | কখন চলে | ফল |
|---|---|---|
| `process_due_automations` | প্রতি ১ মিনিট | সময় হয়ে যাওয়া workflow সারিতে দেয় |
| `send_scheduled_campaigns` | প্রতি ১ মিনিট | নির্ধারিত ক্যাম্পেইন পাঠায় |
| `dispatch_critical_alerts` | প্রতি ২ মিনিট | critical বিষয় সঙ্গে সঙ্গে মালিককে জানায় |
| `sweep_overdue_attention` | প্রতি ৫ মিনিট | `attention.overdue` ইভেন্ট তোলে |
| `attribute_conversions` | প্রতি ১০ মিনিট | আয়কে ক্যাম্পেইনের সঙ্গে যুক্ত করে |
| `process_bounces` | প্রতি ১৫ মিনিট | বারবার ব্যর্থ ঠিকানা unsubscribe করে |
| `escalate_attention` | প্রতি ১৫ মিনিট | উপেক্ষিত কাজের গুরুত্ব বাড়ায় |
| `roll_up_attribution` | প্রতিদিন ০২:১৫ | conversion থেকে আর্থিক রেকর্ড বানায় |
| `expire_stale_consent` | প্রতিদিন ০৩:৩০ | পুরোনো consent বাতিল করে |
| `flag_dormant_leads` | প্রতিদিন ০৪:০০ | `lead.dormant` ইভেন্ট তোলে |
| `send_owner_digest` | প্রতিদিন ০৭:০০ | মালিকের দৈনিক ইমেইল |
| `review_campaign_engagement` | প্রতিদিন ০৯:৩০ | ক্যাম্পেইনের ফল যাচাই করে, একবারই |
| `send_weekly_review` | সোমবার ০৭:৩০ | সাপ্তাহিক ব্যবসায়িক পর্যালোচনা |

### ইভেন্ট তালিকা

| ইভেন্ট | কখন ঘটে | সাধারণ ব্যবহার |
|---|---|---|
| `lead.created` | নতুন লিড | স্বাগত ইমেইল, স্কোরিং |
| `lead.consented` | প্রথমবার consent | onboarding সিকোয়েন্স |
| `lead.engaged` | প্রথমবার লিংকে ক্লিক | আগ্রহীদের follow-up |
| `campaign.sent` | ক্যাম্পেইন শেষ | ফলাফলের সারসংক্ষেপ |
| `campaign.underperforming` | লক্ষ্যের নিচে ফল | subject বদলে পুনরায় পাঠানো |
| `conversion.recorded` | আয় নথিভুক্ত হলে | ধন্যবাদ বার্তা, upsell |
| `delivery.failed` | পাঠানো ব্যর্থ | সতর্কতা, তালিকা পরিষ্কার |
| `attention.overdue` | সময়সীমা পার | escalation |
| `attention.escalated` | বারবার উপেক্ষিত | মালিককে পুনরায় জানানো |
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

প্রত্যাশিত ফল: `Ran 179 tests ... OK` (প্রায় ২ সেকেন্ড)

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

---

## ১৩. ইমেইল ট্র্যাকিং — open ও click এখন সত্যিই মাপা হয়

### সমস্যা কী ছিল

Executive Dashboard-এ "open rate" ও "click rate" দেখানো হতো
`EmailCampaign.opens` ও `.clicks` ফিল্ড থেকে। কিন্তু **কোথাও এই দুটি ফিল্ড
বাড়ানো হতো না** — মান সবসময় শূন্য ছিল, তাই প্রতিটি শতাংশ আসলে শূন্যের ভাগ।

### কার্যপদ্ধতি

প্রতিটি ক্যাম্পেইন বার্তায় তিনটি জিনিস যোগ হয়:

১. **Tracking pixel** — ১x১ ছবি, যার ঠিকানায় সেই প্রাপকের নিজস্ব token থাকে
২. **Click redirect** — প্রতিটি `href` আমাদের ঠিকানা দিয়ে ঘুরিয়ে দেওয়া হয়
৩. **Unsubscribe** — `List-Unsubscribe` header ও ফুটারে লিংক

**গুরুত্বপূর্ণ নিরাপত্তা:** গন্তব্যের ঠিকানা **স্বাক্ষরিত** (signed)। স্বাক্ষর
ছাড়া কেউ URL বদলে দিলে ৪০০ ফেরত যায়। এটি না থাকলে আমাদের ডোমেইন ব্যবহার করে
যে কেউ যেকোনো জায়গায় পাঠাতে পারত (open redirect)।

**Token-এ ইমেইল ঠিকানা থাকে না** — কেউ বার্তাটি অন্যকে forward করলেও প্রাপকের
ঠিকানা ফাঁস হয় না।

**Scanner ছাঁকা:** Barracuda, Proofpoint, Mimecast-এর মতো নিরাপত্তা যন্ত্র
প্রতিটি লিংক নিজে খুলে দেখে। এদের হিট সংরক্ষণ করা হয় কিন্তু **গোনা হয় না** —
নইলে একটি স্ক্যান-করা বার্তা পুরোপুরি পঠিত হিসেবে দেখাত।

### নতুন Segment

| লেখা | অর্থ |
|---|---|
| `opened` | আগের ক্যাম্পেইন খুলেছে |
| `clicked` | আগের ক্যাম্পেইনে ক্লিক করেছে |
| `not opened` | খোলেনি — নতুন subject দিয়ে আবার পাঠানোর তালিকা |

### যাচাই

```bash
python manage.py test core.tests.EngagementTrackingTests core.tests.OneClickUnsubscribeTests
python manage.py test core.tests.CampaignTrackingTests core.tests.EngagementSegmentTests
```

**হাতে-কলমে:** `/email-marketing/` → নিজের ঠিকানায় একটি ক্যাম্পেইন পাঠান →
ইমেইল খুলুন → `/revenue/` এ open rate শূন্যের বদলে সংখ্যা দেখবেন।

---

## ১৪. আয়ের হিসাব (Attribution) ও পূর্বাভাস

### সমস্যা কী ছিল

`Campaign` আর `FinancialRecord`-এর মধ্যে কোনো সংযোগ ছিল না। আয় ছিল একটি মোট
সংখ্যা, যা কোন কাজের ফল তা কেউ বলতে পারত না — এই কারণেই Executive Dashboard
"DATA RECONCILIATION" সতর্কতা দেখাত।

### কার্যপদ্ধতি

নতুন `Conversion` রেকর্ড — একটি টাকা, একটি লিড, একটি ক্যাম্পেইন।

**Last-touch নিয়ম:** টাকা আসার আগে ৩০ দিনের মধ্যে লিড যে ক্যাম্পেইনে শেষবার
সাড়া দিয়েছে, কৃতিত্ব সেটির। অগ্রাধিকার: **click > open > পাঠানো**। ক্লিক
ইচ্ছাকৃত কাজ, open হতে পারে শুধু ছবি লোড হওয়া।

রাত ০২:১৫-তে `roll_up_attribution` conversion-গুলোকে `FinancialRecord`-এ
রূপান্তর করে — তাই dashboard-এর পুরোনো সতর্কতা নিজে থেকেই মিটে যায়।
**হাতে লেখা রেকর্ড কখনো ছোঁয়া হয় না।**

### নতুন পাতা `/revenue/`

চ্যানেলভিত্তিক ROI · ক্যাম্পেইনভিত্তিক আয় · মাসভিত্তিক cohort · lifetime value ·
পুনরায় কেনার হার · এবং ৩০ দিনের পূর্বাভাস।

**পূর্বাভাস সৎ থাকে:** ৭ দিনের কম তথ্য থাকলে সংখ্যা দেখায় **না**, এবং সবসময়
নিজের r² (fit) জানায়। fit কম হলে পাতাতেই লেখা থাকে "সংখ্যা নয়, দিক দেখুন"।

### মুদ্রা — সবচেয়ে গুরুত্বপূর্ণ সুরক্ষা

কোনো মুদ্রার FX rate না থাকলে সেই আয় **কোনো প্রতিবেদনে ঢোকে না**, এবং মালিককে
জানানো হয় কোন rate লাগবে। ৫০০ ডলারকে ৫০০ ইউরো হিসেবে গোনা — এই ভুলটিই ঠেকানো
হয়েছে।

### যাচাই

```bash
python manage.py test core.tests.AttributionTests core.tests.RollUpTests
python manage.py test core.tests.CurrencyTests core.tests.ForecastTests
python manage.py test core.tests.RevenueReportingTests core.tests.WeeklyReviewTests
```

---

## ১৫. তাৎক্ষণিক সতর্কতা ও এক-ক্লিকে অনুমোদন

### সমস্যা কী ছিল

বাইরে যাওয়ার একমাত্র সংকেত ছিল সকাল ০৭:০০-এর digest। সকাল ০৭:০৫-এ ওঠা critical
বিষয় প্রায় পুরো দিন অপেক্ষা করত।

### কার্যপদ্ধতি

- **২ মিনিটের মধ্যে** critical বিষয় ইমেইলে যায়, `OWNER_WHATSAPP` দেওয়া থাকলে
  WhatsApp-এও
- ইমেইলেই **Mark resolved / Dismiss** বোতাম — স্বাক্ষরিত, ৭ দিনে মেয়াদ শেষ
- **Escalation ladder:** সময়সীমার ৪ / ২৪ / ৭২ ঘণ্টা পার হলে প্রতিবার গুরুত্ব এক
  ধাপ বাড়ে; critical-এ পৌঁছালে আবার সতর্কতা যায়

### একটি সূক্ষ্ম কিন্তু জরুরি সিদ্ধান্ত

লিংকে ক্লিক করলে **সঙ্গে সঙ্গে কিছু হয় না** — একটি নিশ্চিতকরণ পাতা আসে।
কারণ নিরাপত্তা যন্ত্র ইমেইলের প্রতিটি লিংক নিজে খুলে দেখে; সঙ্গে সঙ্গে কাজ হলে
সফটওয়্যারই আপনার সিদ্ধান্ত নিয়ে ফেলত। প্রতিটি ব্যবহার IP সহ audit log-এ লেখা হয়।

### যাচাই

```bash
python manage.py test core.tests.CriticalAlertTests core.tests.OwnerActionLinkTests
python manage.py test core.tests.EscalationTests core.tests.SlaDeadlineTests
```

---

## ১৬. বহু প্রতিষ্ঠান ও বহু মুদ্রার ভিত্তি

### কার্যপদ্ধতি

প্রতিটি ব্যবসায়িক রেকর্ডে এখন `organisation` আছে।

**একটি প্রতিষ্ঠান থাকলে কিছুই বদলায় না** — scoping সম্পূর্ণ নিষ্ক্রিয়, তাই কোনো
রেকর্ড লুকিয়ে যাওয়ার আশঙ্কা নেই। **দ্বিতীয় প্রতিষ্ঠান যোগ করার সঙ্গে সঙ্গে**
সব জায়গায় একসাথে আলাদাকরণ চালু হয় এবং sidebar-এ প্রতিষ্ঠান বদলানোর তালিকা দেখা
যায়। এটি ইচ্ছাকৃত নকশা — মডিউল ধরে ধরে চালু করলে কোথাও বাদ পড়ার ঝুঁকি থাকত।

**মুদ্রা রূপান্তর** রেকর্ডের নিজের তারিখের rate ব্যবহার করে, আজকের rate নয় —
তাই নতুন rate যোগ করলে গত মাসের হিসাব বদলে যায় না।

`organisation` কখনো হাতে লেখা যায় না; যে প্রতিষ্ঠান দেখা হচ্ছে সেখান থেকে
স্বয়ংক্রিয়ভাবে বসে। Celery-র কাজে request থাকে না, তাই সেখানে ডিফল্ট
প্রতিষ্ঠান ব্যবহৃত হয় — কোনো রেকর্ড অভিভাবকহীন থাকে না।

### যাচাই

```bash
python manage.py test core.tests.TenancyTests
```

**হাতে-কলমে:** `/organisations/` → দ্বিতীয় একটি প্রতিষ্ঠান যোগ করুন →
sidebar-এ ENTITY তালিকা দেখা যাবে → বদলে দেখুন, তালিকাও বদলে যাবে।

---

## ১৭. সার্ভারে নেওয়ার সময় (১৭ আগস্ট সংস্করণ)

নতুন migration: `0006` ও `0007`।

`.env`-এ **`SITE_URL` অবশ্যই দিতে হবে** — না দিলে পাঠানো ক্যাম্পেইনের প্রতিটি
লিংক ভুল ঠিকানায় যাবে।

```
SITE_URL=https://emeraldrozalia.ie
OPEN_RATE_TARGET=15.0
CLICK_RATE_TARGET=2.0
ATTRIBUTION_WINDOW_DAYS=30
ESCALATION_STEPS=4,24,72
OWNER_WHATSAPP=
```

বিস্তারিত `deploy/DEPLOYMENT.md`-এর সেকশন ১১-এ।

---

## সংক্ষিপ্ত যাচাই তালিকা

```bash
python manage.py test core          # ১৭৯টি টেস্ট, সব পাস করা উচিত
python manage.py check              # কোনো সমস্যা থাকা উচিত নয়
python manage.py runserver          # ২২টি পাতা + ৭টি প্রকাশ্য ঠিকানা
```
