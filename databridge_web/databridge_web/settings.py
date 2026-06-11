from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} est obligatoire sur Render.")
    return value


def _is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        not lowered
        or lowered.startswith("replace-with")
        or lowered.startswith("your-")
        or lowered in {"changeme", "change-me", "todo", "none", "null"}
    )


def _is_local_value(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        lowered in {"localhost", "127.0.0.1", "0.0.0.0"}
        or lowered.startswith("localhost:")
        or lowered.startswith("127.0.0.1:")
        or lowered.startswith("0.0.0.0:")
        or lowered.startswith("http://localhost")
        or lowered.startswith("https://localhost")
        or lowered.startswith("http://127.0.0.1")
        or lowered.startswith("https://127.0.0.1")
        or lowered.startswith("http://0.0.0.0")
        or lowered.startswith("https://0.0.0.0")
    )


def _require_no_local_values(name: str, values: list[str]) -> None:
    local_values = [value for value in values if _is_local_value(value)]
    if local_values:
        raise RuntimeError(
            f"{name} ne doit pas contenir localhost/127.0.0.1/0.0.0.0 sur Render. "
            f"Valeurs interdites: {local_values}"
        )



def _turso_http_url(database_url: str) -> str:
    database_url = database_url.strip().rstrip("/")

    if database_url.startswith("libsql://"):
        return "https://" + database_url.removeprefix("libsql://")

    if database_url.startswith("https://"):
        return database_url

    raise RuntimeError(
        "DJANGO_TURSO_DATABASE_URL doit commencer par libsql:// ou https://"
    )


# ---------------------------------------------------------------------
# RENDER-ONLY MODE
# ---------------------------------------------------------------------

APP_ENV = _require_env("APP_ENV").lower()

if APP_ENV != "production":
    raise RuntimeError(
        "Cette configuration supporte seulement Render production: "
        "APP_ENV=production est obligatoire."
    )

if os.getenv("DJANGO_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}:
    raise RuntimeError("DJANGO_DEBUG doit être 0/false sur Render.")

DEBUG = False


# ---------------------------------------------------------------------
# SECURITY
# ---------------------------------------------------------------------

SECRET_KEY = _require_env("DJANGO_SECRET_KEY")

if _is_placeholder(SECRET_KEY):
    raise RuntimeError("DJANGO_SECRET_KEY doit être une vraie valeur secrète, pas un placeholder.")


ALLOWED_HOSTS = _split_csv(_require_env("DJANGO_ALLOWED_HOSTS"))
_require_no_local_values("DJANGO_ALLOWED_HOSTS", ALLOWED_HOSTS)

CSRF_TRUSTED_ORIGINS = _split_csv(_require_env("DJANGO_CSRF_TRUSTED_ORIGINS"))
_require_no_local_values("DJANGO_CSRF_TRUSTED_ORIGINS", CSRF_TRUSTED_ORIGINS)

for origin in CSRF_TRUSTED_ORIGINS:
    if not origin.startswith("https://"):
        raise RuntimeError(
            "Chaque valeur de DJANGO_CSRF_TRUSTED_ORIGINS doit commencer par https://"
        )

# En production, Django exige une configuration correcte de ALLOWED_HOSTS quand DEBUG=False.
# CSRF_TRUSTED_ORIGINS doit aussi contenir les origines HTTPS autorisées.
# Voir docs Django.


# ---------------------------------------------------------------------
# APPLICATIONS
# ---------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "dashboard",
]


# ---------------------------------------------------------------------
# MIDDLEWARE
# ---------------------------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "dashboard.middleware.RequireSuperuserLoginMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


ROOT_URLCONF = "databridge_web.urls"


# ---------------------------------------------------------------------
# TEMPLATES
# ---------------------------------------------------------------------

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# ---------------------------------------------------------------------
# DATABASE — DJANGO SUR TURSO UNIQUEMENT
# ---------------------------------------------------------------------

DJANGO_TURSO_DATABASE_URL = _require_env("DJANGO_TURSO_DATABASE_URL")
DJANGO_TURSO_AUTH_TOKEN = _require_env("DJANGO_TURSO_AUTH_TOKEN")

DATABASES = {
    "default": {
        "ENGINE": "django_libsql",
        "NAME": _turso_http_url(DJANGO_TURSO_DATABASE_URL),
        "AUTH_TOKEN": DJANGO_TURSO_AUTH_TOKEN,
        "OPTIONS": {
            "timeout": 30,
        },
    }
}


# ---------------------------------------------------------------------
# MESSAGES
# ---------------------------------------------------------------------

MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"


# ---------------------------------------------------------------------
# INTERNATIONALIZATION
# ---------------------------------------------------------------------

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Nouakchott"
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------
# STATIC FILES
# ---------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STATIC_SOURCE_DIR = BASE_DIR / "static"
STATICFILES_DIRS = [STATIC_SOURCE_DIR] if STATIC_SOURCE_DIR.exists() else []

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}


# ---------------------------------------------------------------------
# HTTPS / COOKIES / PROXY RENDER
# ---------------------------------------------------------------------

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = False

SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL_REDIRECT", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_SAVE_EVERY_REQUEST = False
X_FRAME_OPTIONS = "DENY"


# ---------------------------------------------------------------------
# DEFAULTS
# ---------------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"


INTERNAL_API_TOKEN = _require_env("INTERNAL_API_TOKEN")
SETUP_ADMIN_TOKEN = os.getenv("SETUP_ADMIN_TOKEN", "").strip()


# ---------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "dashboard": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}
