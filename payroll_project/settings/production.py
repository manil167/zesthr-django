from pathlib import Path
"""Production settings (default for wsgi.py).

Everything sensitive comes from real environment variables. If a critical value
is missing or unsafe, the app refuses to start instead of running insecurely.

Security hardening (HTTPS, cookies, headers, HSTS) is configured below and is
controlled by environment variables with safe defaults.
"""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .env import env, env_bool, env_list, parse_database_url, require_env

# --- Core -----------------------------------------------------------------
SECRET_KEY = require_env("DJANGO_SECRET_KEY")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# --- HTTPS and transport security / Render reverse proxy ------------------------------------------
# Redirect all HTTP requests to HTTPS. Turn off only for a first smoke test
# before your certificate exists.
SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)

# Behind a reverse proxy / load balancer (Render, Railway, Nginx, ...) the app
# only sees plain HTTP. Set DJANGO_TRUST_PROXY_SSL_HEADER=True ONLY if your proxy
# sets X-Forwarded-Proto itself and strips any value sent by the client;
# otherwise visitors could spoof it, and without it you get a redirect loop.
# Render terminates HTTPS at its proxy and forwards the request to Django.
if env_bool("DJANGO_TRUST_PROXY_SSL_HEADER", True):
    SECURE_PROXY_SSL_HEADER = (
        "HTTP_X_FORWARDED_PROTO",
        "https",
    )

# ---------------------------------------------------------------------------
# HSTS
# ---------------------------------------------------------------------------

# HSTS makes browsers refuse plain HTTP for the whole period, and is hard to
# undo. Leave at 0 until HTTPS works everywhere, then raise gradually:
# 3600 -> 86400 -> 2592000 -> 31536000 (one year).
SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
# Preload is a deliberate, near-permanent opt-in (browser preload lists), so the
# "preload not enabled" advisory is silenced instead of nudging anyone into it.
SILENCED_SYSTEM_CHECKS = ["security.W021"]

# --- Cookies ----------------------------------------------------------------
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# --- Browser protections ----------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# ---------------------------------------------------------------------------
# Database - Neon / hosted PostgreSQL
# ---------------------------------------------------------------------------

DATABASE_URL = env("DATABASE_URL", "").strip()

if DATABASE_URL:
    _db = parse_database_url(DATABASE_URL)

else:
    _db = {
        "NAME": require_env("DB_NAME"),
        "USER": require_env("DB_USER"),
        "PASSWORD": require_env("DB_PASSWORD"),
        "HOST": require_env("DB_HOST"),
        "PORT": env("DB_PORT", "5432"),
        "SSLMODE": None,
    }


# Encrypted connection by default; set DB_SSLMODE=disable only for a database on the
# same private network (e.g. a local Docker container).
_sslmode = env("DB_SSLMODE") or _db.pop("SSLMODE", None) or "require"
if _sslmode not in ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"):
    raise ImproperlyConfigured(f"DB_SSLMODE={_sslmode!r} is not a valid PostgreSQL sslmode.")
_db.pop("SSLMODE", None)

# DB_POOLED=True is the one switch for serverless hosting (Vercel) or any connection pooler
# (Neon's "pooled" address, Supabase pooler, pgbouncer in transaction mode): every request may run
# in a fresh process, so connections are not kept open, server-side cursors are off, and
# psycopg does not prepare statements (a pooler may hand the next query to a different server).
_pooled = env_bool("DB_POOLED", False)
_options = {"sslmode": _sslmode, "connect_timeout": int(env("DB_CONNECT_TIMEOUT", "10"))}
if _pooled:
    _options["prepare_threshold"] = None

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        **_db,
        "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "0" if _pooled else "60")),
        "CONN_HEALTH_CHECKS": True,  # drop a persistent connection the server has closed
        "DISABLE_SERVER_SIDE_CURSORS": env_bool("DB_DISABLE_SERVER_SIDE_CURSORS", _pooled),
        "OPTIONS": _options,
    }
}

if DATABASE_URL and _db['HOST'].endswith('.neon.tech'):
    from .env import postgres_database
    DATABASES = {'default': postgres_database(DATABASE_URL)}

# --- Static files -----------------------------------------------------------
# Behind gunicorn (Docker, a VM) WhiteNoise serves them. On Vercel the CDN serves the files that
# `collectstatic` produced during the build, and WhiteNoise stays idle.
# Not strict: a file missing from the build manifest falls back to its plain URL instead of
# turning every page into an error.
WHITENOISE_MANIFEST_STRICT = False
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
STORAGES = {
    **STORAGES,
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Email ----------------------------------------------------------------
# Password reset sends email, so production needs a real mail server.
EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
if EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend":
    EMAIL_HOST = require_env("DJANGO_EMAIL_HOST")
    EMAIL_PORT = int(env("DJANGO_EMAIL_PORT", "587"))
    EMAIL_HOST_USER = env("DJANGO_EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = env("DJANGO_EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("DJANGO_EMAIL_USE_TLS", True)
    EMAIL_TIMEOUT = 10
    DEFAULT_FROM_EMAIL = require_env("DJANGO_DEFAULT_FROM_EMAIL")

# --- Logging: stdout only; the platform collects it ------------------------
LOGGING["root"]["level"] = env("DJANGO_LOG_LEVEL", "INFO")  # noqa: F405
LOGGING["formatters"]["simple"]["format"] = (  # noqa: F405
    "%(asctime)s %(levelname)s %(name)s: %(message)s"
)

# --- Startup/Production validation ---------------------------------------------------
if DEBUG:
    raise ImproperlyConfigured("DJANGO_DEBUG must be False in production.")
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith("django-insecure") or "dev-only" in SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is too short or looks insecure.")
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list explicit host names (no '*').")
if any(not origin.startswith("https://") for origin in CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured("Every DJANGO_CSRF_TRUSTED_ORIGINS entry must start with https://.")
if SECURE_HSTS_PRELOAD and not (SECURE_HSTS_INCLUDE_SUBDOMAINS and SECURE_HSTS_SECONDS >= 31536000):
    raise ImproperlyConfigured(
        "HSTS preload requires DJANGO_HSTS_INCLUDE_SUBDOMAINS=True and DJANGO_HSTS_SECONDS >= 31536000."
    )

# Enable uploads only after a private durable storage location has been supplied.
DOCUMENT_UPLOADS_ENABLED = env_bool("DJANGO_DOCUMENT_UPLOADS_ENABLED", False)
if DOCUMENT_UPLOADS_ENABLED and env("DJANGO_DOCUMENT_STORAGE", "neon" if env("AWS_ENDPOINT_URL_S3") else "filesystem").lower() == "filesystem":
    _private_root = Path(require_env("DJANGO_PRIVATE_DOCUMENT_ROOT"))
    if not _private_root.is_absolute() or _private_root.resolve().is_relative_to(Path(STATIC_ROOT).resolve()):
        raise ImproperlyConfigured("Private document storage must be an absolute path outside static assets.")
