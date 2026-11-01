"""Settings used by `manage.py test` and CI: fast, isolated, deterministic."""
from .base import *  # noqa: F401,F403

SECRET_KEY = "test-only-secret-key"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# Weak hasher makes tests much faster. Never use outside tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Test login pages never contact configured external identity providers.
GOOGLE_OAUTH_CLIENT_ID = ""
GOOGLE_OAUTH_CLIENT_SECRET = ""

# Never contact a paid provider during the ordinary test suite.
ASSISTANT_ENABLED = False
OPENAI_API_KEY = ""
