"""Integration-test settings: the same tests, run against a real PostgreSQL.

    TEST_DB_NAME / TEST_DB_USER / TEST_DB_PASSWORD / TEST_DB_HOST / TEST_DB_PORT

Django creates and drops its own `test_<name>` database, so the role needs CREATEDB.
"""
from .env import env
from .test import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("TEST_DB_NAME", "hros"),
        "USER": env("TEST_DB_USER", "hros"),
        "PASSWORD": env("TEST_DB_PASSWORD", "hros"),
        "HOST": env("TEST_DB_HOST", "127.0.0.1"),
        "PORT": env("TEST_DB_PORT", "5432"),
        "OPTIONS": {"connect_timeout": 10},
    }
}
