"""Small typed helpers for reading configuration from environment variables.

.env file parsing is done by python-dotenv (see base.py).
"""
import os

from django.core.exceptions import ImproperlyConfigured

_TRUE = {"1", "true", "yes", "on"}


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in _TRUE


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"Required environment variable {name} is not set.")
    return value


def parse_database_url(url):
    """Turn postgres://user:pass@host:5432/dbname?sslmode=require into Django settings.

    Hosted providers (Neon, Supabase, Render, Railway...) hand out a single URL.
    """
    from urllib.parse import parse_qs, unquote, urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise ImproperlyConfigured("DATABASE_URL must start with postgres:// or postgresql://")
    name = unquote(parsed.path.lstrip("/"))
    if not (name and parsed.hostname):
        raise ImproperlyConfigured("DATABASE_URL must include a host and a database name.")
    return {
        "NAME": name,
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname,
        "PORT": str(parsed.port or 5432),
        "SSLMODE": (parse_qs(parsed.query).get("sslmode") or [None])[0],
    }


def postgres_database(url):
    """Neon pooled endpoints need no persistent connections/prepared statements."""
    config = parse_database_url(url)
    mode = config.pop('SSLMODE') or 'verify-full'
    if mode not in ('require', 'verify-ca', 'verify-full'):
        raise ImproperlyConfigured('Hosted PostgreSQL requires TLS.')
    pooled = env_bool('DB_POOLED', '-pooler.' in config['HOST'])
    options = {'sslmode': mode, 'connect_timeout': 10}
    if pooled:
        options['prepare_threshold'] = None
    return {'ENGINE': 'django.db.backends.postgresql', **config,
            'CONN_MAX_AGE': int(env('DB_CONN_MAX_AGE', '0' if pooled else '60')), 'CONN_HEALTH_CHECKS': True,
            'DISABLE_SERVER_SIDE_CURSORS': env_bool('DB_DISABLE_SERVER_SIDE_CURSORS', pooled), 'OPTIONS': options}
