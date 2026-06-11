from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT.parent

# Preferred location: C:\Users\medya\OneDrive\Desktop\.env
# Backward-compatible fallback: C:\Users\medya\OneDrive\Desktop\Stage\.env
load_dotenv(WORKSPACE_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _env_text(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _is_placeholder(value: str | None) -> bool:
    text = (value or "").strip().lower()
    return (
        not text
        or text.startswith("replace-with")
        or text.startswith("your-")
        or text in {"changeme", "change-me", "todo", "none", "null"}
    )


APP_ENV = _env_text("APP_ENV", "development").lower()
IS_PRODUCTION = APP_ENV == "production"


def _resolve_project_path(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


DATABRIDGE_DB_BACKEND = _env_text("DATABRIDGE_DB_BACKEND", "sqlite").lower()
if DATABRIDGE_DB_BACKEND not in {"sqlite", "turso"}:
    raise RuntimeError("DATABRIDGE_DB_BACKEND doit valoir 'sqlite' ou 'turso'.")

DB_PATH = _resolve_project_path(
    os.getenv("DATABRIDGE_DB_PATH") or os.getenv("DB_PATH"),
    PROJECT_ROOT / "databridge.db",
)
TURSO_DATABASE_URL = _env_text("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = _env_text("TURSO_AUTH_TOKEN")
if DATABRIDGE_DB_BACKEND == "turso":
    if _is_placeholder(TURSO_DATABASE_URL):
        raise RuntimeError("TURSO_DATABASE_URL est obligatoire quand DATABRIDGE_DB_BACKEND=turso.")
    if _is_placeholder(TURSO_AUTH_TOKEN):
        raise RuntimeError("TURSO_AUTH_TOKEN est obligatoire quand DATABRIDGE_DB_BACKEND=turso.")


def _is_local_url(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        lowered.startswith("http://127.0.0.1")
        or lowered.startswith("https://127.0.0.1")
        or lowered.startswith("http://localhost")
        or lowered.startswith("https://localhost")
    )


def _resolve_public_api_base_url() -> str:
    value = _env_text("PUBLIC_API_BASE_URL")
    if not value and not IS_PRODUCTION:
        value = "http://127.0.0.1:8001"

    if IS_PRODUCTION:
        if not value:
            raise RuntimeError("PUBLIC_API_BASE_URL est obligatoire en production.")
        if _is_local_url(value):
            raise RuntimeError("PUBLIC_API_BASE_URL doit être une URL HTTPS publique en production.")
        if not value.lower().startswith("https://"):
            raise RuntimeError("PUBLIC_API_BASE_URL doit commencer par https:// en production.")

    return value.rstrip("/")


REMOTE_PROVIDER = (os.getenv("REMOTE_PROVIDER") or "opendatasoft_url").strip()
PUBLISH_MODE = (os.getenv("PUBLISH_MODE") or "opendatasoft_url").strip()
PUBLIC_API_BASE_URL = _resolve_public_api_base_url()
CORS_ALLOWED_ORIGINS = _split_csv(os.getenv("CORS_ALLOWED_ORIGINS"))
if IS_PRODUCTION and "*" in CORS_ALLOWED_ORIGINS:
    raise RuntimeError("CORS_ALLOWED_ORIGINS ne doit pas contenir '*' en production.")
EXPORT_SCHEMA_MODE = (os.getenv("EXPORT_SCHEMA_MODE") or "public_human_readable").strip().lower()
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
DATABRIDGE_EXPORT_TOKEN = os.getenv("DATABRIDGE_EXPORT_TOKEN", "")
ODS_PUBLISH_MODE = _env_text("ODS_PUBLISH_MODE", "manual_url")
ODS_DOMAIN = _env_text("ODS_DOMAIN", "https://richat.opendatasoft.com").rstrip("/")
ODS_DEFAULT_THEME = _env_text("ODS_DEFAULT_THEME", "Économie, finances, affaires, finances publiques")
ODS_DEFAULT_LICENSE = _env_text("ODS_DEFAULT_LICENSE", "")
ODS_PRODUCER = _env_text("ODS_PRODUCER", "Richat DataBridge")
ODS_ORGANIZATION = _env_text("ODS_ORGANIZATION", "Richat Data Hub")
DATABRIDGE_SEED_METADATA_ON_STARTUP = _env_bool("DATABRIDGE_SEED_METADATA_ON_STARTUP", "0")

AI_PROVIDER = (os.getenv("AI_PROVIDER") or "local").strip().lower()
AI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "60"))
AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", "0"))
AI_MAX_CANDIDATES = int(os.getenv("AI_MAX_CANDIDATES", "40"))
AI_TARGET_INDICATORS = int(os.getenv("AI_TARGET_INDICATORS", "5"))
AI_MODEL = os.getenv("AI_MODEL", "regles_metier_locales" if AI_PROVIDER == "local" else "").strip()
AI_ENABLE_BUSINESS_RULES = os.getenv("AI_ENABLE_BUSINESS_RULES", "1").strip().lower() in {"1", "true", "yes", "on"}
AI_LOG_DECISIONS = os.getenv("AI_LOG_DECISIONS", "1").strip().lower() in {"1", "true", "yes", "on"}
AI_MIN_DIRECT_MATCHES = int(os.getenv("AI_MIN_DIRECT_MATCHES", "1"))

SOURCE_LIMITS = {
    "WB": {
        "max_indicators_per_dataset": int(os.getenv("WB_MAX_INDICATORS_PER_DATASET", "60")),
        "label": os.getenv("WB_SOURCE_LABEL", "Banque mondiale"),
    },
}

API_TITLE = "Richat DataBridge API"


def is_configured_secret(value: str | None) -> bool:
    """Return True for a real local secret, not for empty example placeholders."""
    if value is None:
        return False
    text = value.strip()
    if not text:
        return False
    lowered = text.lower()
    return not (
        lowered.startswith("replace-with")
        or lowered.startswith("your-")
        or lowered in {"changeme", "change-me", "todo", "none", "null"}
    )


def export_api_is_local() -> bool:
    lowered = PUBLIC_API_BASE_URL.lower()
    return "127.0.0.1" in lowered or "localhost" in lowered


def get_source_limits(source_code: str | None) -> dict[str, object]:
    code = (source_code or "").strip().upper()
    return SOURCE_LIMITS.get(
        code,
        {
            "max_indicators_per_dataset": int(os.getenv("DEFAULT_MAX_INDICATORS_PER_DATASET", "60")),
            "label": code or "Source",
        },
    )


def get_source_indicator_limit(source_code: str | None) -> int:
    limits = get_source_limits(source_code)
    return int(limits["max_indicators_per_dataset"])


def get_source_label(source_code: str | None) -> str:
    limits = get_source_limits(source_code)
    return str(limits["label"])
