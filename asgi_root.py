from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
DJANGO_ROOT = PROJECT_ROOT / "databridge_web"
FASTAPI_ROOT = PROJECT_ROOT / "databridge_api"

for path in (PROJECT_ROOT, DJANGO_ROOT, FASTAPI_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from django.core.asgi import get_asgi_application  # noqa: E402


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "databridge_web.settings")

django_application = get_asgi_application()

from app.main import app as fastapi_application  # noqa: E402


FASTAPI_PREFIXES = ("/api", "/docs", "/redoc", "/openapi.json", "/healthz")


class UnifiedASGIApplication:
    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if scope.get("type") == "http" and _is_fastapi_path(path):
            await fastapi_application(scope, receive, send)
            return
        await django_application(scope, receive, send)


def _is_fastapi_path(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in FASTAPI_PREFIXES)


application = UnifiedASGIApplication()
