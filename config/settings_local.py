"""Local development settings — never use these in production.

Run with:
    python manage.py runserver --settings=config.settings_local

Three groups of overrides:

1. Infrastructure — SQLite and an in-memory cache, so PostgreSQL, Redis and
   Docker are not needed to work on the application.
2. Plain-http access — config/settings.py derives SECURE_SSL_REDIRECT and the
   secure-cookie flags from `not DEBUG`. Left on, the dev server answers 301 to
   every request and refuses to set a session cookie, which also makes the whole
   test suite fail with "301 != 200".
3. Safety — outbound email is printed to the console instead of being handed to
   the live SMTP relay whose credentials sit in .env, and Celery tasks run inline
   so no broker is required.
"""
from .settings import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:8000", "http://localhost:8000"]

# 1. Infrastructure
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "local.sqlite3",  # noqa: F405
    }
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
STORAGES = {
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
}

# 2. Plain-http access. DEBUG=True already relaxes these, but state them so the
#    file keeps working if someone turns DEBUG off to reproduce a bug.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
SECURE_PROXY_SSL_HEADER = None

# 3. Safety
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Tracking pixels and unsubscribe links must point at this machine, not at the
# live site, or a locally sent test would record engagement in production.
SITE_URL = "http://127.0.0.1:8000"

# 4. Speed. The suite creates a superuser in most setUp methods, and the
#    production hasher makes that deliberately slow — it was three minutes of a
#    three-and-a-bit minute run. Only ever correct for tests and local work.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
