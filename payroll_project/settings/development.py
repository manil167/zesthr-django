"""Local development settings (default for manage.py)."""
from .base import *  # noqa: F401,F403
from .base import BASE_DIR
from .env import env, env_bool, env_list

# Insecure on purpose and ONLY allowed here. Override with DJANGO_SECRET_KEY in .env.
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key-do-not-use-in-production")

DEBUG = env_bool("DJANGO_DEBUG", True)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": env("DJANGO_SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}

# Emails are printed in the terminal instead of being sent.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Optional Neon PostgreSQL in development; an unset URL retains isolated SQLite.
if env('DATABASE_URL', '').strip():
    from .env import postgres_database
    DATABASES = {'default': postgres_database(env('DATABASE_URL'))}
