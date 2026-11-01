"""Settings shared by every environment.

This module must stay safe: no secrets, no DEBUG=True, no host wildcards.
SECRET_KEY, DEBUG, ALLOWED_HOSTS and DATABASES are set per environment.
"""
from pathlib import Path

from dotenv import load_dotenv

from .env import env, env_bool
from .document_storage import private_document_storage

# settings/base.py -> settings -> payroll_project -> project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Reads <project root>/.env if it exists. override=False: real environment
# variables (e.g. set by the hosting platform) always win over the file.
if env_bool("DJANGO_LOAD_DOTENV", True):
    # Neon writes .env.local unless an existing .env is present. Process values win.
    load_dotenv(BASE_DIR / ".env.local", override=False)
    load_dotenv(BASE_DIR / ".env", override=False)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "rest_framework",
    "django_filters",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "apps.organizations",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.TenantScopeMiddleware",
    "core.middleware.ForcePasswordChangeMiddleware",
]

ROOT_URLCONF = "payroll_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.review_flags",
                "core.context_processors.notification_flags",
            ]
        },
    }
]

WSGI_APPLICATION = "payroll_project.wsgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
# Kept at the previous hardcoded value so behaviour does not change.
TIME_ZONE = env("DJANGO_TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"  # target of `collectstatic` (production)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Sessions: sliding idle timeout (each request renews it) -----------------
SESSION_IDLE_MINUTES = int(env("DJANGO_SESSION_IDLE_MINUTES", "60"))
SESSION_COOKIE_AGE = SESSION_IDLE_MINUTES * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# --- Attendance corrections: how far back an employee may ask for a fix ----
CORRECTION_WINDOW_DAYS = int(env("DJANGO_CORRECTION_WINDOW_DAYS", "30"))

# --- Password reset and sign-in protection ---------------------------------
PASSWORD_RESET_TIMEOUT = int(env("DJANGO_PASSWORD_RESET_MINUTES", "60")) * 60  # reset links expire
LOGIN_MAX_FAILURES = int(env("DJANGO_LOGIN_MAX_FAILURES", "5"))
LOGIN_FAILURE_WINDOW_MINUTES = int(env("DJANGO_LOGIN_FAILURE_WINDOW_MINUTES", "15"))
PASSWORD_RESET_MAX_REQUESTS = int(env("DJANGO_PASSWORD_RESET_MAX_REQUESTS", "5"))
PASSWORD_RESET_WINDOW_MINUTES = int(env("DJANGO_PASSWORD_RESET_WINDOW_MINUTES", "15"))
# Trust X-Forwarded-For for the audit log's IP address ONLY if your proxy overwrites it.
TRUST_X_FORWARDED_FOR = env_bool("DJANGO_TRUST_X_FORWARDED_FOR", False)
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", "HROS <noreply@localhost>")

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# --- Authentication and Google OAuth ----------------------------------------

SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

ACCOUNT_LOGIN_METHODS = {"username", "email"}
ACCOUNT_SIGNUP_FIELDS = [
    "username*",
    "email*",
    "password1*",
    "password2*",
]

# Google OAuth credentials come from environment variables.
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = env("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()

SOCIALACCOUNT_LOGIN_ON_GET = False
SOCIALACCOUNT_AUTO_SIGNUP = True

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"prompt": "select_account"},
    }
}

if GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET:
    SOCIALACCOUNT_PROVIDERS["google"]["APP"] = {
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "secret": GOOGLE_OAUTH_CLIENT_SECRET,
        "key": "",
    }

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}

# Every API list is bounded; session authentication retains CSRF protection.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

DOCUMENT_UPLOADS_ENABLED = env_bool("DJANGO_DOCUMENT_UPLOADS_ENABLED", True)
PRIVATE_DOCUMENT_ROOT = env("DJANGO_PRIVATE_DOCUMENT_ROOT", "").strip() or str(BASE_DIR / "private_documents")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    "private_documents": private_document_storage(BASE_DIR),
}

# Neon managed Better Auth. Empty URL keeps the existing local development login.
NEON_AUTH_BASE_URL = env('NEON_AUTH_BASE_URL', '').strip()
NEON_AUTH_APP_ORIGIN = env('NEON_AUTH_APP_ORIGIN', 'http://localhost:8000').strip()
if NEON_AUTH_BASE_URL:
    # Provider passwords never authenticate against local Django accounts.
    AUTHENTICATION_BACKENDS = ['core.auth_backends.NeonBackend']
    MIDDLEWARE.insert(MIDDLEWARE.index('core.middleware.ForcePasswordChangeMiddleware'),
                      'core.middleware.NeonSessionMiddleware')

WHATSAPP_VERIFY_TOKEN = env('WHATSAPP_VERIFY_TOKEN', '')
WHATSAPP_APP_SECRET = env('WHATSAPP_APP_SECRET', '')
WHATSAPP_ACCESS_TOKEN = env('WHATSAPP_ACCESS_TOKEN', '')
WHATSAPP_PHONE_NUMBER_ID = env('WHATSAPP_PHONE_NUMBER_ID', '')
WHATSAPP_DISPLAY_NUMBER = env('WHATSAPP_DISPLAY_NUMBER', '')
WHATSAPP_API_VERSION = env('WHATSAPP_API_VERSION', 'v23.0')

# AI is optional; credentials stay server-side. No employee records are sent.
OPENAI_API_KEY = env('OPENAI_API_KEY', '').strip()
ASSISTANT_ENABLED = env_bool('DJANGO_ASSISTANT_ENABLED', bool(OPENAI_API_KEY))
ASSISTANT_MODEL = env('DJANGO_ASSISTANT_MODEL', 'gpt-6-luna').strip()
ASSISTANT_MINUTE_LIMIT = max(1, int(env('DJANGO_ASSISTANT_MINUTE_LIMIT', '10')))
ASSISTANT_DAILY_LIMIT = max(1, int(env('DJANGO_ASSISTANT_DAILY_LIMIT', '100')))
