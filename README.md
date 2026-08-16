# Emerald Rozalia Email Marketing Centre — Python 3.14.3

Production full-stack rebuild of the Laravel command centre using Django 6, ASGI, PostgreSQL, Redis and Celery. It targets `https://emeraldrozalia.ie` on Hetzner CX23 (`2.28.11.222`).

## Included

- Exact Emerald Rozalia dark command-centre visual system and logo
- Owner dashboard and 17 operational modules
- Leads, campaigns, email, content, AEO, finance, automation and WhatsApp data
- Live OpenAI orchestration with structured decisions, confidence, risk and owner approval
- SMTP checks, WhatsApp webhook verification and GDPR unsubscribe endpoint
- Authentication, CSRF, secure cookies, CSP/HSTS, audit logs and admin
- PostgreSQL, Redis, Celery worker/scheduler, Gunicorn/Uvicorn and Docker Compose
- Caddy HTTPS configuration, health endpoint and backup script

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

Then sign in at `http://127.0.0.1:8000/login/` as `urmos@rozalia.ie` with the
`OWNER_PASSWORD` you just used.

Run the tests:

```bash
python manage.py test --settings=config.settings_local
```

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
