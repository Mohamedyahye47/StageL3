from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent

# Preferred location: C:\Users\medya\OneDrive\Desktop\.env
# Backward-compatible fallback: C:\Users\medya\OneDrive\Desktop\Stage\.env
load_dotenv(WORKSPACE_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        not lowered
        or lowered.startswith("replace-with")
        or lowered.startswith("your-")
        or lowered in {"changeme", "change-me", "todo", "none", "null"}
    )


def _is_local_url(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        lowered.startswith("http://127.0.0.1")
        or lowered.startswith("https://127.0.0.1")
        or lowered.startswith("http://localhost")
        or lowered.startswith("https://localhost")
    )


def _resolve_path(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"


# SECURITY
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
if IS_PRODUCTION and _is_placeholder(SECRET_KEY):
    raise RuntimeError("DJANGO_SECRET_KEY doit être configurée avec une vraie valeur en production.")
if not SECRET_KEY:
    SECRET_KEY = "dev-richat-databridge-secret"

DEBUG = _env_bool("DJANGO_DEBUG", default=not IS_PRODUCTION)

ALLOWED_HOSTS = _split_csv(os.getenv("DJANGO_ALLOWED_HOSTS"))
if IS_PRODUCTION and not ALLOWED_HOSTS:
    raise RuntimeError("DJANGO_ALLOWED_HOSTS est obligatoire en production.")
if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

CSRF_TRUSTED_ORIGINS = _split_csv(os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS"))
if IS_PRODUCTION and not CSRF_TRUSTED_ORIGINS:
    raise RuntimeError("DJANGO_CSRF_TRUSTED_ORIGINS est obligatoire en production.")

if DEBUG and "testserver" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("testserver")

try:
    import whitenoise  # noqa: F401

    WHITENOISE_AVAILABLE = True
except ImportError:
    WHITENOISE_AVAILABLE = False

if IS_PRODUCTION and not WHITENOISE_AVAILABLE:
    raise RuntimeError("whitenoise doit être installé pour servir les fichiers statiques en production.")


# APPLICATIONS
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "dashboard",
]


# MIDDLEWARE
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    *(["whitenoise.middleware.WhiteNoiseMiddleware"] if WHITENOISE_AVAILABLE else []),
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "dashboard.middleware.RequireSuperuserLoginMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


ROOT_URLCONF = "databridge_web.urls"


# TEMPLATES
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


WSGI_APPLICATION = "databridge_web.wsgi.application"
ASGI_APPLICATION = "databridge_web.asgi.application"


# DATABASE
# This DB is only for Django UI features like sessions.
# Your main DataBridge metadata remains in the main databridge.db / FastAPI side.
DJANGO_DB_PATH = os.getenv("DJANGO_DB_PATH", "").strip()
if IS_PRODUCTION and not DJANGO_DB_PATH:
    raise RuntimeError("DJANGO_DB_PATH est obligatoire en production pour une base Django persistante.")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": _resolve_path(DJANGO_DB_PATH, BASE_DIR / "django_ui.sqlite3"),
    }
}


# MESSAGES
MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"


# INTERNATIONALIZATION
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Nouakchott"
USE_I18N = True
USE_TZ = True


# STATIC FILES
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = _resolve_path(os.getenv("DJANGO_STATIC_ROOT"), BASE_DIR / "staticfiles")
if WHITENOISE_AVAILABLE:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", default=IS_PRODUCTION)
SESSION_COOKIE_SECURE = IS_PRODUCTION
CSRF_COOKIE_SECURE = IS_PRODUCTION


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"


# FASTAPI BACKEND
FASTAPI_BASE_URL = os.getenv("FASTAPI_BASE_URL", "").strip().rstrip("/")
if IS_PRODUCTION:
    if not FASTAPI_BASE_URL:
        raise RuntimeError("FASTAPI_BASE_URL est obligatoire en production.")
    if _is_local_url(FASTAPI_BASE_URL):
        raise RuntimeError("FASTAPI_BASE_URL ne doit pas pointer vers localhost en production.")
    if not FASTAPI_BASE_URL.lower().startswith("https://"):
        raise RuntimeError("FASTAPI_BASE_URL doit commencer par https:// en production.")
if not FASTAPI_BASE_URL:
    FASTAPI_BASE_URL = "http://127.0.0.1:8001"
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("DATABRIDGE_WEB_TIMEOUT", "18"))
