from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .models import AuditLog


SENSITIVE_MARKERS = ("password", "token", "secret", "api_key", "apikey", "key")


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
            if any(marker in key_text.lower() for marker in SENSITIVE_MARKERS):
                clean[key_text] = "[masque]"
            else:
                clean[key_text] = _sanitize(item)
        return clean
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize(item) for item in list(value)[:50]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1000]
