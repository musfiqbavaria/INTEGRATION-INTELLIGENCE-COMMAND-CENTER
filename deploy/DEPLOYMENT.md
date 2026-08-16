# Deploying the Emerald Rozalia Marketing Centre to Hetzner

Target: `emeraldrozalia.ie` on Hetzner CX23 (`2.28.11.222`), Ubuntu 24.04.

Stack: Caddy (HTTPS, port 443) → Gunicorn/Uvicorn on `127.0.0.1:8080` → Django 6 ·
PostgreSQL 18 · Redis 8.2 · Celery worker + beat, all under Docker Compose.

---

## 0. Pre-flight

Confirm these before touching the server.

| Check | Status at time of writing |
|---|---|
| `emeraldrozalia.ie` → `2.28.11.222` | ✅ resolves correctly |
| `www.emeraldrozalia.ie` → `2.28.11.222` | ✅ resolves correctly |
| `SECRET_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `OWNER_PASSWORD` | ✅ real values in `.env` |
| `OPENAI_API_KEY` | ✅ set |
| SPF for `rozalia.ie` | ✅ `v=spf1 include:zohomail.eu ~all` |
| DKIM for `rozalia.ie` | ✅ `zmail._domainkey.rozalia.ie` present |
| DMARC for `rozalia.ie` | ⚠️ **no record** — see 3.1 |
| `SMTP_HOST` | ⚠️ empty — the value is `smtp.zoho.eu`, see 3.1 |
| `SMTP_PASSWORD` | ❌ **needs a Zoho app password, see 3.1** |
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | ❌ **empty — WhatsApp will not send** |

You can deploy without SMTP and WhatsApp; those two features simply fail with a
clear message until the credentials are filled in. Everything else works.

**Read section 9 before going live.** There is a privilege-escalation defect that
matters once this is on a public domain.

---

## 1. Prepare the server

### 1.0 First access — getting SSH working

A fresh Hetzner Ubuntu image refuses **root password login over SSH**
(`PermitRootLogin prohibit-password`), even though the same password works fine
in the web console. sshd still advertises `password` as an available method, so
the failure looks like a wrong password when it is actually a policy refusal.
Retrying the password can never succeed.

Everything below is done once. After it, you log in with a key and no password.

**Step 1 — open the web console.** Hetzner Cloud Console → your project → the
server → **Console** button (top right). Log in as `root` with the password from
the Rescue → Reset root password step. This is a virtual monitor, not SSH, so the
policy above does not apply.

**Step 2 — see what sshd is actually enforcing.** This prints the *effective*
merged configuration, including cloud-init drop-ins, so there is no guesswork:

```bash
sshd -T | grep -Ei 'permitrootlogin|passwordauthentication'
```

Expect `permitrootlogin prohibit-password`, which is the cause.

Note that `password` still appears in the advertised method list. That list is
built from the **global** settings, so `PasswordAuthentication` is enabled — the
restriction applies to `root` alone. A normal user can therefore log in with a
password straight away, which is the shortest route in and avoids editing sshd
config at all.

**Step 3 — create an admin user, still on the console:**

```bash
adduser deploy
usermod -aG sudo deploy
```

`adduser` asks for a password twice, then name, room and phone — press **Enter**
through those and **Y** to confirm.

**Step 4 — log in from your own machine.** This works immediately, with no sshd
changes and no restart:

```bat
ssh deploy@2.28.11.222
```

**Step 5 — install your public key**, run on your PC, not on the server.

`ssh-copy-id` does **not** exist on Windows — it ships only with Git Bash — and
`~` does not expand in CMD. Use the form matching your terminal.

**Windows CMD:**

```bat
type %USERPROFILE%\.ssh\id_ed25519_hetzner.pub | ssh deploy@2.28.11.222 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

**PowerShell:**

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519_hetzner.pub | ssh deploy@2.28.11.222 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

**Git Bash** (here `ssh-copy-id` and `~` both work):

```bash
ssh-copy-id -i ~/.ssh/id_ed25519_hetzner.pub deploy@2.28.11.222
```

**Step 6 — confirm key login works:**

```bat
ssh -i %USERPROFILE%\.ssh\id_ed25519_hetzner deploy@2.28.11.222
```

No password prompt means the key is in place.

**Step 7 — save yourself the flags.** In `~/.ssh/config` on your PC
(`C:\Users\<you>\.ssh\config` on Windows):

```
Host emeraldrozalia
  HostName 2.28.11.222
  User deploy
  IdentityFile ~/.ssh/id_ed25519_hetzner
  IdentitiesOnly yes
```

From then on it is just `ssh emeraldrozalia`. `IdentitiesOnly yes` stops SSH
offering other keys first — which matters here, because the older `id_ed25519` on
this machine is a PuTTY-format file that OpenSSH cannot read.

**Step 8 — harden, once key login is confirmed working.** On the server:

```bash
sudo sh -c 'printf "PasswordAuthentication no\n" > /etc/ssh/sshd_config.d/01-hardening.conf'
sudo sshd -t && sudo systemctl restart ssh
```

The `01-` prefix is essential: Ubuntu ships `/etc/ssh/sshd_config.d/50-cloud-init.conf`,
and sshd takes the **first** value it finds for each keyword, so a `99-` file
would be overridden by the `50-` one and silently do nothing. Run `sudo sshd -t`
before restarting — it catches syntax errors that would otherwise lock you out.

> **Everything from here on assumes a root shell.** As `deploy` you are a normal
> user, so plain `apt`, `docker` and file writes under `/opt` will fail with
> "Permission denied". Run `sudo -i` once after logging in and the rest of this
> guide works verbatim, or prefix each command with `sudo`.

### 1.1 Install the runtime

Install Docker and Compose:

```bash
apt update && apt upgrade -y
apt install -y docker.io docker-compose-v2 git curl ufw
systemctl enable --now docker
```

Install Caddy. The `caddy` package is **not** in Ubuntu's default repositories —
the README's `apt install caddy` will fail without this step:

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
```

Firewall. The application binds to `127.0.0.1:8080` only, so it is never exposed
directly; just SSH and the web ports:

```bash
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp
ufw --force enable
ufw status
```

Optional but recommended on a 4 GB box — the image build plus Postgres, Redis,
three Gunicorn workers and two Celery processes leaves little headroom:

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

---

## 2. Get the code onto the server

```bash
mkdir -p /opt/emerald-rozalia-email-centre-python
cd /opt/emerald-rozalia-email-centre-python
```

**Option A — clone from GitHub** (requires the repository to be pushed first):

```bash
git clone https://github.com/musfiqbavaria/INTEGRATION-INTELLIGENCE-COMMAND-CENTER.git .
```

**Option B — copy from your workstation** (no GitHub round-trip). Run this from
`g:\Projects\Automation` in Git Bash:

```bash
rsync -avz --exclude='.git' --exclude='__pycache__' --exclude='.env' \
  ./ root@2.28.11.222:/opt/emerald-rozalia-email-centre-python/
```

Note `.env` is excluded deliberately in both options — it is gitignored, and
secrets should be placed on the server by hand (next step).

---

## 3. Configure `.env` on the server

Copy your local `.env` across separately, or create it from the example:

```bash
cd /opt/emerald-rozalia-email-centre-python
cp .env.example .env
nano .env
```

**Convert the file to Unix line endings.** Your local `.env` is CRLF. Docker
Compose interpolates `${POSTGRES_PASSWORD}` and `${REDIS_PASSWORD}` from this
file to configure the database and Redis containers, while Django reads the same
file through django-environ. If a stray carriage return survives into one path
and not the other, Postgres and Redis will reject the application's password with
a confusing authentication error:

```bash
sed -i 's/\r$//' .env
chmod 600 .env
```

Two consistency rules the stack depends on:

- the password inside `DATABASE_URL` must equal `POSTGRES_PASSWORD`
- the password inside `REDIS_URL` must equal `REDIS_PASSWORD`

To generate fresh secrets if needed:

```bash
openssl rand -hex 48   # SECRET_KEY
openssl rand -hex 32   # POSTGRES_PASSWORD / REDIS_PASSWORD
openssl rand -hex 24   # WHATSAPP_VERIFY_TOKEN
```

Also confirm `DEBUG=false` and that `ALLOWED_HOSTS` contains
`emeraldrozalia.ie,www.emeraldrozalia.ie`.

### 3.1 Mail — Zoho (EU data centre)

The owner mailbox `urmos@rozalia.ie` is Zoho, hosted in the **EU** region. This is
not a guess: the domain's MX records are `mx.zoho.eu` / `mx2.zoho.eu` /
`mx3.zoho.eu`, and its TXT records include
`zoho-verification=zb49057943.zmverify.zoho.eu`.

```bash
SMTP_HOST=smtp.zoho.eu
SMTP_PORT=587
SMTP_USERNAME=urmos@rozalia.ie
SMTP_USE_TLS=true
SMTP_PASSWORD=<app-specific password, see below>
MAIL_FROM=Emerald Rozalia Limited <urmos@rozalia.ie>
```

**Use `smtp.zoho.eu`, not `smtp.zoho.com`.** Pointing an EU account at the `.com`
host fails authentication even with correct credentials, and is the most common
cause of "Zoho SMTP will not connect".

`SMTP_PASSWORD` is **not** the mailbox login password. Zoho requires an
application-specific password for SMTP, and mandates it once two-factor auth is
enabled:

1. Open **https://accounts.zoho.eu/home#security/device** — the `.eu` accounts
   domain, since the `.com` one will not list an EU account.
2. **Security → App Passwords → Generate New Password**.
3. Name it identifiably, for example `Emerald Rozalia ERP`.
4. Copy the password immediately; Zoho displays it only once.
5. Paste it into `.env` unquoted, then restart the app container — django-environ
   reads `.env` once at import, so a running process keeps the old value.

If authentication still fails, check **Zoho Mail → Settings → Mail Accounts →
IMAP/SMTP Access** and confirm SMTP is enabled for the account.

Verify end to end from the running site: **Connection Health → Send verification**
mails `urmos@rozalia.ie` and records the outcome in the connection audit.

#### Mail authentication

SPF and DKIM are already correct for Zoho EU. DMARC is missing. Add it in monitor
mode before the first campaign, so you collect reports without affecting delivery:

```
_dmarc.rozalia.ie   TXT   "v=DMARC1; p=none; rua=mailto:urmos@rozalia.ie"
```

#### Do not run bulk campaigns through this mailbox

Zoho **Mail** is a mailbox service with per-day sending limits, and Zoho's terms
restrict marketing and bulk mail sent over mailbox SMTP. That matters here because
`core/tasks.py` iterates every consented lead and sends one message per recipient
with no pacing, so a campaign will reach the account's limit as fast as the loop
runs, risking throttling or suspension of the mailbox the business depends on.

Use this SMTP configuration for transactional mail only — the verification check,
single notifications. For actual campaigns move the sender to **ZeptoMail**
(Zoho's transactional service, EU endpoint `smtp.zeptomail.eu`) or **Zoho
Campaigns**, which keeps bulk reputation separate from the owner mailbox. Confirm
your plan's limits in the Zoho admin console before the first real send.

---

## 4. Build and start

```bash
docker compose build
docker compose up -d
docker compose ps
```

Expect five services: `app`, `worker`, `scheduler`, `postgres`, `redis`.

The container entrypoint runs `migrate` and `collectstatic` automatically for the
`app` service and skips both for the Celery services, so no manual migration step
is needed on a first boot.

Watch the logs until the app reports it is serving:

```bash
docker compose logs -f app worker scheduler
```

---

## 5. Seed the owner account and operational data

```bash
docker compose exec app python manage.py seed_system
```

This is idempotent — it creates the owner account and demonstration records
without deleting anything, so it is safe to re-run after upgrades. The owner
account is `urmos@rozalia.ie` with the password from `OWNER_PASSWORD`.

Verify the application answers locally before involving Caddy:

```bash
curl -fsS http://127.0.0.1:8080/up
```

Expected: `{"status": "ok", "service": "Emerald Rozalia Marketing Centre", ...}`

---

## 6. HTTPS with Caddy

```bash
cp Caddyfile /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
systemctl status caddy --no-pager
```

Caddy obtains a Let's Encrypt certificate automatically on first request. DNS is
already correct, so this should succeed within seconds. If it does not, check
`journalctl -u caddy -n 50` — the usual cause is port 80 being blocked, which
breaks the ACME challenge.

Caddy sets `X-Forwarded-Proto`, which Django is configured to trust via
`SECURE_PROXY_SSL_HEADER`. That is what stops `SECURE_SSL_REDIRECT` from causing
an infinite redirect loop behind the proxy.

---

## 7. Verify the deployment

```bash
curl -fsS https://emeraldrozalia.ie/up
curl -sI https://www.emeraldrozalia.ie | head -3     # expect 301 to the apex domain
docker compose exec app python manage.py check --deploy
docker compose ps                                    # all services "Up"
```

Then in a browser:

1. Sign in at `https://emeraldrozalia.ie/login/`.
2. Confirm the dashboard shows live figures (revenue, leads, decision count).
3. Open **Integration Center** and run the **Database** test — it should log
   "Database connection successful" in the connection audit.
4. Open **Automation & Workflows**, press **Run test** on a workflow, and confirm
   a row appears in the execution log within a few seconds. That proves Redis,
   the Celery worker and the scheduler are all wired up correctly.
5. Once the Zoho app password is in place, open **Connection Health → Send
   verification** and check that `urmos@rozalia.ie` receives the message.

> When testing SMTP or OpenAI from the **Integration Center**, the card keeps
> showing "Pending" even on success — that is the known status-mapping defect in
> section 9, not a failed test. Read the result from the connection audit table
> below the cards, which reports the true outcome and latency.

Register the WhatsApp webhook in Meta only after filling in the credentials:

```
https://emeraldrozalia.ie/api/webhooks/whatsapp
```

---

## 8. Routine operations

```bash
# Logs
docker compose logs -f app
docker compose logs -f worker scheduler

# Restart one service
docker compose restart app

# Django shell / admin user
docker compose exec app python manage.py createsuperuser

# Nightly backup at 02:30 (deploy/backup.sh keeps 14 days)
chmod +x deploy/backup.sh
crontab -e
#   30 2 * * * /opt/emerald-rozalia-email-centre-python/deploy/backup.sh >> /var/log/er-backup.log 2>&1
```

Restoring a dump:

```bash
gunzip -c backups/database-YYYYMMDD-HHMMSS.sql.gz \
  | docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
```

---

## 9. Shipping an update

### The rule that keeps this simple

**Change code on your workstation, never on the server.** The only file that
lives solely on the server is `.env`. Everything else is a copy of your working
tree, so any edit made directly on the server is silently destroyed by the next
update — and until then it hides the fact that your local copy is wrong.

If you ever do fix something on the server in an emergency, copy that change back
into your local tree the same day.

### Why every update needs a rebuild

`Dockerfile` does `COPY . .`, so the application code lives **inside the image**,
not in a mounted volume. Changing a template, a stylesheet or a `.py` file has no
effect until the image is rebuilt. There is no "just restart" shortcut.

Rebuilds are fast in practice: Docker caches the `pip install` layer and only
re-runs it when `requirements.txt` changes. A code-only change rebuilds in
seconds.

### Route A — via Git (recommended)

Requires the repository to be pushed. Gives you version history on the server and
a real rollback path.

On your workstation:

```bash
git add .
git commit -m "Describe the change"
git push
```

On the server:

```bash
cd /opt/emerald-rozalia-email-centre-python
./deploy/backup.sh          # always, before anything else
git pull
docker compose build
docker compose up -d
```

### Route B — via rsync (no GitHub round-trip)

From `g:\Projects\Automation` in Git Bash:

```bash
rsync -avz --delete \
  --exclude='.git' --exclude='__pycache__' --exclude='.env' \
  --exclude='staticfiles' --exclude='backups' \
  ./ root@2.28.11.222:/opt/emerald-rozalia-email-centre-python/
```

`--delete` removes server files you have deleted locally, which keeps the two
trees identical. The `--exclude` list is what protects `.env`, the collected
static files and your database backups from being wiped — do not drop it.

Then on the server:

```bash
cd /opt/emerald-rozalia-email-centre-python
./deploy/backup.sh
docker compose build
docker compose up -d
```

### Migrations and static files

Both run automatically. The container entrypoint executes `migrate` and
`collectstatic` for the `app` service on every start, and skips them for the
Celery services. You only need to run them by hand if you are debugging:

```bash
docker compose exec app python manage.py migrate
docker compose exec app python manage.py collectstatic --noinput
```

Run `seed_system` again only when the update adds new operational records. It is
idempotent and never deletes anything:

```bash
docker compose exec app python manage.py seed_system
```

### Verify, every time

```bash
docker compose ps                                    # all services Up / healthy
curl -fsS https://emeraldrozalia.ie/up
docker compose logs --tail 40 app worker scheduler   # no tracebacks
docker compose exec app python manage.py check --deploy
```

Then load the dashboard in a browser and confirm the figures still render. A
successful `/up` only proves the process started, not that the pages work.

### When to use `--no-cache`

Almost never. Plain `docker compose build` is correct for ordinary changes.
Reach for `docker compose build --no-cache` only when you suspect a stale layer —
for example a dependency changed without `requirements.txt` changing. It rebuilds
everything from scratch and takes several minutes.

### Rolling back

With Git, roll the code back and rebuild:

```bash
git log --oneline -5
git checkout <previous-commit-sha>
docker compose build && docker compose up -d
```

If a migration has already altered the schema, restore the database from the
backup taken before the update:

```bash
gunzip -c backups/database-YYYYMMDD-HHMMSS.sql.gz \
  | docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
```

This is the reason `./deploy/backup.sh` runs first in every update above.

### Updating `.env`

`.env` is never transferred by either route. Edit it on the server, then restart
so django-environ re-reads it — it only loads the file at import, so a running
container keeps the old values:

```bash
nano .env
sed -i 's/\r$//' .env     # if edited on Windows and pasted in
docker compose up -d --force-recreate app worker scheduler
```

---

## 10. Known defects — review before go-live

These were confirmed by testing this codebase, not inferred. None of them block
the deployment mechanically, but the first one changes the security posture the
moment this is on a public domain.

**Privilege escalation via `/users/` — highest priority.** Any signed-in user can
POST to `/users/` and create an account with `is_superuser=True`, and can delete
the owner account. The generic module view has no permission layer beyond
`login_required`. Today the owner is the only account, which limits exposure, but
there is also no rate limiting on the login form (`django-ratelimit` is listed in
`requirements.txt` and never used), so a weak password is the only barrier.
Mitigate now by keeping `OWNER_PASSWORD` long and unique, and treat adding a
permission check as the first post-deploy task.

**Unsubscribe silently fails on mixed-case email addresses.** `/api/consent/unsubscribe`
lowercases the submitted address then performs a case-sensitive lookup, so a
request for `Name@Example.ie` returns HTTP 200 `{"status":"unsubscribed"}` while
the lead stays subscribed. This is a GDPR exposure and should be fixed before the
unsubscribe link reaches real recipients.

**The WhatsApp webhook accepts unauthenticated POSTs** and discards the payload.
There is no `X-Hub-Signature-256` verification, and delivery/read receipts are
never processed, so message statuses will never advance past `sent`.

**Integration cards show the wrong status.** The SMTP and OpenAI health checks
succeed but never update their card, which stays "Pending" — the view matches the
service slug (`smtp`, `openai`) against the seeded categories (`Email`, `AI`).
The connection audit table below the cards shows the true result.

**Some dashboard figures are hardcoded** in the template rather than queried:
active automations (26), WhatsApp volume (24,850), one-man-team mode (98%), the
risk/impact heatmap and the five attention-card counts. Revenue, profit, leads
and pending decisions are real.

**`process_due_automations` inflates success metrics.** The scheduled task
increments `runs` and `successes` for every active automation once a minute
without evaluating triggers or executing actions — about 1,440 fabricated
successful runs per automation per day.
