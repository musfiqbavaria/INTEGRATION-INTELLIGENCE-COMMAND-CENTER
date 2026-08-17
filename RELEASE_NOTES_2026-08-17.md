# Emerald Rozalia Marketing Centre — engagement, attribution, alerting, entities

Four capabilities added on top of the 2026-08-16 correction build. The existing
visual system, integrations, deployment topology and database architecture are
unchanged; nothing that worked before behaves differently.

## 1. Email engagement tracking

The Executive Dashboard rendered open and click rates from `EmailCampaign.opens`
and `.clicks`, and nothing in the codebase ever incremented either field. Every
percentage shown was a division of a permanent zero.

- Per-recipient tracking pixel and click redirects. The click destination is
  **signed**, so the endpoint cannot be edited into an open redirect trading on
  the company domain's reputation.
- `List-Unsubscribe` and `List-Unsubscribe-Post` headers plus a footer link on
  every campaign and workflow email. Gmail and Yahoo require one-click
  unsubscribe from bulk senders and the console had neither.
- Known mail security scanners are recorded but not counted, so one scanned
  message does not read as a fully engaged recipient.
- New segments `opened`, `clicked`, `not opened` — the resend-to-non-openers
  audience that makes a real sequence possible.
- New event `lead.engaged`, fired on first click. Clicks only: image proxies
  open everything, nothing clicks a link on its own.
- Lead scoring gains a behaviour component on top of the unchanged attribute
  weights, still capped at 100.
- `review_campaign_engagement` judges each campaign once, a day after sending,
  and raises `campaign.underperforming` when it misses target.

## 2. Revenue attribution and forecasting

`Campaign` and `FinancialRecord` were unconnected, which is why the Executive
Dashboard printed reconciliation warnings instead of an answer.

- New `Conversion` model: one payment, one lead, one campaign credited.
- Last-touch attribution within a configurable window, preferring a click over
  an open over a plain delivery.
- Nightly roll-up folds conversions into `FinancialRecord`, idempotently, and
  never touches a row entered by hand.
- New `/revenue/` page: channel ROI, per-campaign revenue, cohorts by acquisition
  month, lifetime value, repeat rate and attribution coverage.
- Forecast publishes its own r² and refuses to project from fewer than seven
  days of history rather than drawing a line through noise.
- Multi-currency revenue with no FX rate on file is **held out** of every report
  rather than counted as euro, and the missing currency is named to the owner.
- Weekly business review email, Mondays at 07:30.

## 3. Owner alerting and one-click approvals

The only outbound signal was the 07:00 digest, so an item raised at 07:05 waited
most of a day.

- `dispatch_critical_alerts` emails critical items within two minutes, and over
  WhatsApp when `OWNER_WHATSAPP` and Meta credentials are configured.
- Signed, time-limited action links resolve an item or approve a decision from
  the message. `GET` renders a confirmation; only `POST` acts — mail appliances
  fetch every URL in an inbound message, and a mutating `GET` would let a
  scanner decide. Every use is written to the audit log with the requesting IP.
- Escalation ladder: each configured step past a deadline raises the item one
  severity, and reaching critical re-arms the alert.
- `AttentionItem.sla_hours` turns a policy into a real deadline on save.

## 4. Multi-entity foundation

- New `Organisation` and `FxRate` models; every business record carries an
  `organisation`.
- With one entity, scoping is a deliberate no-op, so no record can be hidden by
  a tenant key that does not matter yet. Adding a second turns strict isolation
  on everywhere at once and reveals the sidebar switcher.
- Currency conversion uses the rate dated on or before the record's own date,
  so loading a new rate cannot move a historical total.
- The tenant key is never user-editable; it is set from the entity being viewed,
  and background work falls back to the default entity so nothing is orphaned.

## Defects fixed along the way

- `Referrer-Policy` was set as `REFERRER_POLICY`, not a Django setting, so it
  was never sent. Renamed to `SECURE_REFERRER_POLICY`.
- The dashboard's "Auto-executed safe" figure read the governance card's count.
- WhatsApp counters were read-then-written and lost concurrent increments; they
  now use `F()` expressions.
- `/api/consent/unsubscribe` was unauthenticated, unthrottled, and confirmed
  whether an address was on the list. Now rate limited to 30/min/IP.
- `/robots.txt` now disallows `/e/` and `/act/`.
- The duplicated display-field computation in the module view was merged.

## Validation

- `python manage.py test core` — **179 tests, all passing** (was 105).
- `python manage.py check` — no issues.
- `python manage.py makemigrations --check` — no missing migrations.
- Every one of the 24 routes exercised against a running server: all render.
- End-to-end probe: campaign sent → pixel and click recorded → counters correct
  → tampered redirect refused with 400 → conversion attributed last-touch →
  rolled up into the ledger → 100% attribution coverage reported.

The local test suite now runs in about two seconds rather than three minutes;
`config/settings_local.py` overrides `PASSWORD_HASHERS` with MD5, which applies
to local work and tests only.

## Deployment

Two migrations, `0006` and `0007`. `0006` builds sixteen indexes and will lock
`messagedelivery`, `auditlog` and `lead` while it does — run it in a quiet
window on a large database. `0007` creates the founding organisation and is
reversible.

**`SITE_URL` must be set in `.env`** or every tracked link in an outgoing
campaign points at the wrong host. All other new variables have safe defaults.
See section 11 of `deploy/DEPLOYMENT.md`.
