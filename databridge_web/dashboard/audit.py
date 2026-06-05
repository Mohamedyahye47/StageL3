from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .models import AuditLog


SENSITIVE_MARKERS = ("password", "token", "secret", "api_key", "apikey", "key")
SENSITIVE_EXACT_KEYS = {
    "DJANGO_SECRET_KEY",
    "TURSO_AUTH_TOKEN",
    "DJANGO_TURSO_AUTH_TOKEN",
    "INTERNAL_API_TOKEN",
    "DATABRIDGE_EXPORT_TOKEN",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "MISTRAL_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
    "XAI_API_KEY",
    "PERPLEXITY_API_KEY",
    "OPENROUTER_API_KEY",
    "FIREWORKS_API_KEY",
    "CEREBRAS_API_KEY",
    "NVIDIA_API_KEY",
    "SAMBANOVA_API_KEY",
    "AI_API_KEY",
}


def record_audit_event(
    request=None,
    *,
    action: str,
    object_type: str = "",
    object_id: str = "",
    extra: Mapping[str, Any] | None = None,
    user=None,
) -> None:
    """Best-effort audit logging that must never break the user workflow."""

    try:
        actor = user if user is not None else getattr(request, "user", None)
        if not getattr(actor, "is_authenticated", False):
            actor = None
        AuditLog.objects.create(
            user=actor,
            username_snapshot=getattr(actor, "get_username", lambda: "")() if actor else "",
            action=action[:80],
            object_type=object_type[:80],
            object_id=str(object_id or "")[:180],
            method=str(getattr(request, "method", "") or "")[:12],
            path=str(getattr(request, "path", "") or "")[:512],
            ip_address=_client_ip(request),
            user_agent=str(getattr(request, "META", {}).get("HTTP_USER_AGENT", "") or ""),
            extra=_sanitize(extra or {}),
        )
    except Exception:
        # Audit is important, but it must never expose secrets or block exports/login.
        return


def _client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.upper() in SENSITIVE_EXACT_KEYS or any(marker in key_text.lower() for marker in SENSITIVE_MARKERS):
                clean[key_text] = "[masque]"
            else:
                clean[key_text] = _sanitize(item)
        return clean
    if isinstance(value, str):
        return _sanitize_text(value)[:1000]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize(item) for item in list(value)[:50]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1000]


def _sanitize_text(value: str) -> str:
    text = _mask_sensitive_query_params(value)
    return re.sub(
        r"(?i)\b(password|token|secret|api[_-]?key|apikey|key)\s*[:=]\s*[^,\s;&]+",
        r"\1=[masque]",
        text,
    )


def _mask_sensitive_query_params(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.query:
        return value
    changed = False
    query_items: list[tuple[str, str]] = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in {"token", "api_key", "apikey", "key", "secret", "password"}:
            query_items.append((key, "[masque]"))
            changed = True
        else:
            query_items.append((key, item))
    if not changed:
        return value
    return urlunparse(parsed._replace(query=urlencode(query_items)))
