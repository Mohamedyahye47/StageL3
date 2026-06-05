from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

for path in (PROJECT_ROOT, PROJECT_ROOT / "databridge_api", PROJECT_ROOT / "databridge_web"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _set_default_env() -> None:
    defaults = {
        "APP_ENV": "production",
        "DJANGO_DEBUG": "0",
        "DJANGO_SECRET_KEY": "smoke-test-django-secret-not-for-production",
        "DJANGO_ALLOWED_HOSTS": "stagel3.onrender.com",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "https://stagel3.onrender.com",
        "CORS_ALLOWED_ORIGINS": "https://stagel3.onrender.com",
        "PUBLIC_API_BASE_URL": "https://stagel3.onrender.com",
        "DJANGO_TURSO_DATABASE_URL": "libsql://smoke-test.turso.io",
        "DJANGO_TURSO_AUTH_TOKEN": "smoke-test-django-token",
        "INTERNAL_API_TOKEN": "smoke-test-internal-token",
        "DATABRIDGE_EXPORT_TOKEN": "smoke-test-export-token",
        "DATABRIDGE_DB_BACKEND": "sqlite",
        "AI_PROVIDER": "local",
        "AI_MODEL": "regles_metier_locales",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


def check_asgi_import() -> None:
    from asgi_root import application

    assert application is not None
    print("ASGI OK")


def check_fastapi_import_and_routes() -> None:
    from databridge_api.app.main import app

    paths = {route.path for route in app.routes}
    assert "/healthz" in paths
    assert "/docs" in paths
    print("FASTAPI OK")


def check_ai_registry() -> None:
    from databridge_api.app.ai.registry import AIProviderConfigError, validate_layer_config

    validate_layer_config("recommendation", "local", "regles_metier_locales")
    for provider, model in (
        ("unknown-provider", "regles_metier_locales"),
        ("gemini", "o3-mini"),
        ("openai", "gemini-2.5-flash-lite"),
    ):
        try:
            validate_layer_config("recommendation", provider, model)
        except AIProviderConfigError:
            continue
        raise AssertionError(f"Configuration IA invalide acceptée: {provider}/{model}")
    print("AI REGISTRY OK")


def check_export_tokens() -> None:
    from databridge_api.app.services import publish_service

    token_a = publish_service._build_export_token("dataset-a")
    token_b = publish_service._build_export_token("dataset-b")
    assert token_a != token_b

    for bad_token in (None, "", "bad-token"):
        try:
            publish_service._validate_export_token("dataset-a", bad_token)
        except publish_service.ExportTokenError:
            continue
        raise AssertionError("Token export absent/invalide accepté.")

    publish_service._validate_export_token("dataset-a", token_a)
    print("EXPORT TOKENS OK")


def main() -> None:
    _set_default_env()
    check_asgi_import()
    check_fastapi_import_and_routes()
    check_ai_registry()
    check_export_tokens()
    print("SMOKE CHECKS OK")


if __name__ == "__main__":
    main()
