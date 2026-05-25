from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Header, HTTPException

from app.config import INTERNAL_API_TOKEN, is_configured_secret


def require_internal_token(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    if not is_configured_secret(INTERNAL_API_TOKEN):
        raise HTTPException(status_code=500, detail="Configuration de sécurité incomplète.")
    if not x_internal_token:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    if not hmac.compare_digest(x_internal_token, INTERNAL_API_TOKEN):
        raise HTTPException(status_code=403, detail="Accès refusé.")
