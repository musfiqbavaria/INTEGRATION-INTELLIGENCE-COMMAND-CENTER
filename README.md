# Emerald Rozalia Email Marketing Centre — Python 3.14.3

Production full-stack rebuild of the Laravel command centre using Django 6, ASGI, PostgreSQL, Redis and Celery. It targets `https://emeraldrozalia.ie` on Hetzner CX23 (`2.28.11.222`).

## Included

- Exact Emerald Rozalia dark command-centre visual system and logo
- Owner dashboard and 20 operational modules
- Leads, campaigns, email, content, AEO, finance, automation and WhatsApp data
- Email engagement tracking: opens, clicks, RFC 8058 one-click unsubscribe
- Revenue attribution, cohorts, lifetime value and a stated-confidence forecast
- Immediate critical alerts with signed one-click approve/resolve links
- Multi-entity foundation: organisations, multi-currency with dated FX rates
- Live OpenAI orchestration with structured decisions, confidence, risk and owner approval
- SMTP checks, WhatsApp webhook verification and GDPR unsubscribe endpoint
- Authentication, CSRF, secure cookies, CSP/HSTS, audit logs and admin
- PostgreSQL, Redis, Celery worker/scheduler, Gunicorn/Uvicorn and Docker Compose
- Caddy HTTPS configuration, health endpoint and backup script

## Engagement, attribution, alerting and entities

Four capabilities added on top of the original console. Each is covered by tests
in `core/tests.py`; run `python manage.py test core` to exercise all 179.

**Engagement tracking** — `EmailCampaign.opens` and `.clicks` were shown on the
dashboard as rates but nothing ever incremented them. Every campaign message now
carries a per-recipient tracking pixel, click redirects with a **signed**
destination (so the endpoint cannot be used as an open redirect), and
`List-Unsubscribe` headers that Gmail and Yahoo require of bulk senders. Opens
and clicks feed three new segments — `opened`, `clicked`, `not opened` — which is
what makes a resend-to-non-openers sequence possible, plus the `lead.engaged`
event and a behaviour component in lead scoring.

**Revenue attribution** — the new `Conversion` model links a payment to a lead
and to the campaign that last engaged them, within a configurable window.
A nightly roll-up folds conversions into `FinancialRecord`, so the Executive
Dashboard's reconciliation warnings resolve themselves. `/revenue/` reports
channel ROI, per-campaign revenue, cohorts, lifetime value and a straight-line
forecast that publishes its own r² and refuses to draw a line through too few
points. Revenue in a currency with no FX rate on file is **held out** of every
report rather than counted as euro, and the owner is told which rate is missing.

**Owner alerting** — critical items are emailed within two minutes instead of
waiting for the 07:00 digest, with signed links to resolve or dismiss. An
escalation ladder (`ESCALATION_STEPS`, default 4/24/72 hours past deadline)
raises severity for anything ignored, and reaching critical re-arms the alert.
Action links confirm before acting: a `GET` only ever renders a form, because
mail security appliances fetch every URL in an inbound message.

**Multi-entity foundation** — every business record now carries an
`organisation`. With one entity the tenant key is a no-op and nothing can be
hidden; adding a second turns strict isolation on everywhere at once and reveals
the sidebar entity switcher. Currency conversion uses the rate dated on or
before the record's own date, so loading today's rate never moves last month's
reported revenue.

## Local development

Runs on SQLite with no Docker, PostgreSQL or Redis required. `config/settings_local.py`
supplies the overrides; `config/settings.py` is left untouched.

```bash
python -m venv .venv
.venv\Scripts\activate                      # Windows
# source .venv/bin/activate                 # macOS / Linux
pip install -r requirements-local.txt
```

Create the database and an owner account:

```bash
python manage.py migrate --settings=config.settings_local
set OWNER_PASSWORD=ChooseAStrongLocalPassword       # Windows CMD
python manage.py seed_system --settings=config.settings_local
```

Run it:

```bash
python manage.py runserver --settings=config.settings_local
```

Do **not** add `--noreload`. Since Django 5.1 the cached template loader is
active even when `DEBUG=True`, and template reloading depends on the
autoreloader. With `--noreload` the process serves the templates it cached at
startup, so edits to any `.html` file appear to have no effect until you
restart the server.

Then sign in at `http://127.0.0.1:8000/login/` as `urmos@rozalia.ie` with the
`OWNER_PASSWORD` you just used.

Run the tests:

```bash
python manage.py test --settings=config.settings_local
```

The local settings override `PASSWORD_HASHERS` with MD5. The suite creates a
superuser in most `setUp` methods and the production hasher is deliberately
slow, which was three minutes of a three-minute run; the override brings the
full 179 tests to about two seconds. It applies to local work and tests only —
`config/settings.py` keeps Django's default hasher.

To avoid repeating `--settings`, export it once per shell:

```bash
set DJANGO_SETTINGS_MODULE=config.settings_local    # Windows CMD
export DJANGO_SETTINGS_MODULE=config.settings_local # bash
```

What the local settings change, and why:

- **SQLite and an in-memory cache**, so no database or broker services are needed.
- **`SECURE_SSL_REDIRECT` and the secure-cookie flags off.** `config/settings.py`
  derives them from `not DEBUG`. Left enabled they make the dev server answer 301
  to every request and refuse to set a session cookie — and they are the reason
  `manage.py test` fails with `301 != 200` under the production settings.
- **Email printed to the console.** `.env` holds live SMTP credentials; without
  this override, testing a campaign would send real mail to real leads.
- **Celery runs inline** (`CELERY_TASK_ALWAYS_EAGER`), so `.delay()` executes
  immediately and no Redis broker is required.

`requirements-local.txt` omits `psycopg` and `gunicorn` deliberately: the first
is unnecessary on SQLite and lacks a wheel for the newest Python on Windows, and
the second cannot run on Windows at all.

`local.sqlite3` is gitignored, so the local database never reaches the repository.

## Hetzner installation

```bash
apt update && apt install -y docker.io docker-compose-v2 unzip caddy
mkdir -p /opt/emerald-rozalia-email-centre-python
unzip emerald-rozalia-email-centre-python3143.zip -d /opt/emerald-rozalia-email-centre-python
cd /opt/emerald-rozalia-email-centre-python
cp .env.example .env
```

Generate secrets and edit `.env` privately:

```bash
openssl rand -hex 48
openssl rand -hex 32
openssl rand -hex 24
nano .env
```

The password inside `DATABASE_URL` must match `POSTGRES_PASSWORD`. The password inside `REDIS_URL` must match `REDIS_PASSWORD`.

Start and seed:

```bash
docker compose build
docker compose up -d
docker compose exec app python manage.py seed_system
docker compose ps
curl -fsS http://127.0.0.1:8080/up
```

Install HTTPS proxy:

```bash
cp Caddyfile /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
curl -fsS https://emeraldrozalia.ie/up
```

Login: `https://emeraldrozalia.ie/login/` with username `urmos@rozalia.ie` and the private `OWNER_PASSWORD` used during seeding. Never commit `.env`.

## Operations

```bash
docker compose logs -f app worker scheduler
docker compose exec app python manage.py check --deploy
docker compose exec app python manage.py migrate
docker compose exec app python manage.py createsuperuser
```

## Upgrading an earlier Python package

Back up the database, replace the application files while preserving the private `.env`, then run:

```bash
docker compose build --no-cache
docker compose up -d
docker compose exec app python manage.py migrate
docker compose exec app python manage.py seed_system
docker compose exec app python manage.py check --deploy
```

The idempotent seed command adds missing Email, WhatsApp, Integration, Automation and AEO operational records without deleting existing records. Delivery counters are system controlled and no longer appear as editable form fields.

Webhook: `https://emeraldrozalia.ie/api/webhooks/whatsapp`

Health: `https://emeraldrozalia.ie/up`
"# INTEGRATION-INTELLIGENCE-COMMAND-CENTER" 
