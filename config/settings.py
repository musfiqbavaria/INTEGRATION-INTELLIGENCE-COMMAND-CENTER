from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env(DEBUG=(bool, False))
if (BASE_DIR / ".env").exists(): env.read_env(BASE_DIR / ".env")
SECRET_KEY = env("SECRET_KEY", default="unsafe-change-me")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["emeraldrozalia.ie", "www.emeraldrozalia.ie", "localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=["https://emeraldrozalia.ie"])
INSTALLED_APPS = ["django.contrib.admin","django.contrib.auth","django.contrib.contenttypes","django.contrib.sessions","django.contrib.messages","django.contrib.staticfiles","core"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware","whitenoise.middleware.WhiteNoiseMiddleware","django.contrib.sessions.middleware.SessionMiddleware","django.middleware.common.CommonMiddleware","django.middleware.csrf.CsrfViewMiddleware","django.contrib.auth.middleware.AuthenticationMiddleware","django.contrib.messages.middleware.MessageMiddleware","django.middleware.clickjacking.XFrameOptionsMiddleware","core.middleware.SecurityHeadersMiddleware","core.middleware.AuditMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND":"django.template.backends.django.DjangoTemplates","DIRS":[BASE_DIR/"templates"],"APP_DIRS":True,"OPTIONS":{"context_processors":["django.template.context_processors.request","django.contrib.auth.context_processors.auth","django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DATABASES = {"default": env.db("DATABASE_URL", default="postgresql://emerald_rozalia:change-me@postgres:5432/emerald_rozalia_marketing")}
CACHES = {"default":{"BACKEND":"django.core.cache.backends.redis.RedisCache","LOCATION":env("REDIS_URL",default="redis://redis:6379/1")}}
CELERY_BROKER_URL = env("REDIS_URL", default="redis://redis:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
from celery.schedules import crontab
CELERY_BEAT_SCHEDULE = {
    "process-automations-every-minute": {"task": "core.tasks.process_due_automations", "schedule": 60.0},
    # Raise attention.overdue once an item passes its deadline.
    "sweep-overdue-attention": {"task": "core.tasks.sweep_overdue_attention", "schedule": 300.0},
    # 07:00 Europe/Dublin, so the brief is waiting at the start of the day.
    "owner-daily-digest": {"task": "core.tasks.send_owner_digest", "schedule": crontab(hour=7, minute=0)},
    # Campaigns whose scheduled time has arrived.
    "send-scheduled-campaigns": {"task": "core.tasks.send_scheduled_campaigns", "schedule": 60.0},
    # Deliverability and consent hygiene, checked a few times a day.
    "process-bounces": {"task": "core.tasks.process_bounces", "schedule": 900.0},
    "expire-stale-consent": {"task": "core.tasks.expire_stale_consent", "schedule": crontab(hour=3, minute=30)},
    "flag-dormant-leads": {"task": "core.tasks.flag_dormant_leads", "schedule": crontab(hour=4, minute=0)},
}
AUTH_PASSWORD_VALIDATORS = [{"NAME":"django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},{"NAME":"django.contrib.auth.password_validation.MinimumLengthValidator","OPTIONS":{"min_length":12}},{"NAME":"django.contrib.auth.password_validation.CommonPasswordValidator"}]
LANGUAGE_CODE="en-ie"; TIME_ZONE="Europe/Dublin"; USE_I18N=True; USE_TZ=True
STATIC_URL="/static/"; STATIC_ROOT=BASE_DIR/"staticfiles"; STATICFILES_DIRS=[BASE_DIR/"static"]
STORAGES={"staticfiles":{"BACKEND":"django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG else "whitenoise.storage.CompressedManifestStaticFilesStorage"},"default":{"BACKEND":"django.core.files.storage.FileSystemStorage"}}
DEFAULT_AUTO_FIELD="django.db.models.BigAutoField"; LOGIN_URL="login"; LOGIN_REDIRECT_URL="dashboard"; LOGOUT_REDIRECT_URL="login"
SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO","https"); SECURE_SSL_REDIRECT=not DEBUG; SESSION_COOKIE_SECURE=not DEBUG; CSRF_COOKIE_SECURE=not DEBUG; SESSION_COOKIE_HTTPONLY=True; SESSION_COOKIE_SAMESITE="Lax"; SECURE_HSTS_SECONDS=31536000 if not DEBUG else 0; SECURE_HSTS_INCLUDE_SUBDOMAINS=True; SECURE_HSTS_PRELOAD=True; SECURE_CONTENT_TYPE_NOSNIFF=True; X_FRAME_OPTIONS="DENY"; REFERRER_POLICY="strict-origin-when-cross-origin"
EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"; EMAIL_HOST=env("SMTP_HOST",default=""); EMAIL_PORT=env.int("SMTP_PORT",default=587); EMAIL_HOST_USER=env("SMTP_USERNAME",default=""); EMAIL_HOST_PASSWORD=env("SMTP_PASSWORD",default=""); EMAIL_USE_TLS=env.bool("SMTP_USE_TLS",default=True); DEFAULT_FROM_EMAIL=env("MAIL_FROM",default="Emerald Rozalia Limited <urmos@rozalia.ie>"); OWNER_EMAIL=env("OWNER_EMAIL",default="urmos@rozalia.ie")
# Deliverability and consent policy.
BOUNCE_LIMIT=env.int("BOUNCE_LIMIT",default=3)
CONSENT_EXPIRY_MONTHS=env.int("CONSENT_EXPIRY_MONTHS",default=24)
DORMANT_MONTHS=env.int("DORMANT_MONTHS",default=6)
OPENAI_API_KEY=env("OPENAI_API_KEY",default=""); OPENAI_MODEL=env("OPENAI_MODEL",default="gpt-5.6")
WHATSAPP_ACCESS_TOKEN=env("WHATSAPP_ACCESS_TOKEN",default=""); WHATSAPP_PHONE_NUMBER_ID=env("WHATSAPP_PHONE_NUMBER_ID",default=""); WHATSAPP_BUSINESS_ACCOUNT_ID=env("WHATSAPP_BUSINESS_ACCOUNT_ID",default=""); WHATSAPP_VERIFY_TOKEN=env("WHATSAPP_VERIFY_TOKEN",default=""); WHATSAPP_APP_SECRET=env("WHATSAPP_APP_SECRET",default="")
