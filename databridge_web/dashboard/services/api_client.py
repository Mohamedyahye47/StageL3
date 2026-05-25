from __future__ import annotations

from typing import Any

import requests
from django.conf import settings


class BackendUnavailable(Exception):
    """Raised when the FastAPI backend cannot be reached."""


class ApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


def _url(path: str) -> str:
    if path.startswith(("http://", "https://")):
        raise ValueError("Les appels API internes doivent utiliser un chemin relatif FastAPI.")
    return f"{settings.FASTAPI_BASE_URL}{path}"


def _headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(headers or {})
    token = getattr(settings, "INTERNAL_API_TOKEN", "")
    if token:
        merged["X-Internal-Token"] = token
    return merged


def _request(method: str, path: str, **kwargs) -> Any:
    timeout = kwargs.pop("timeout", settings.REQUEST_TIMEOUT_SECONDS)
    kwargs["headers"] = _headers(kwargs.pop("headers", None))
    try:
        response = requests.request(method, _url(path), timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        raise BackendUnavailable("API FastAPI indisponible. Vérifiez que le serveur backend est lancé.") from exc

    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = {"message": response.text}
        detail = body.get("detail", body) if isinstance(body, dict) else body
        if isinstance(detail, dict):
            message = detail.get("message") or detail.get("error") or "Une erreur API est survenue."
            payload = detail
        else:
            message = str(detail or "Une erreur API est survenue.")
            payload = body if isinstance(body, dict) else {}
        raise ApiError(message, status_code=response.status_code, payload=payload)

    if response.status_code == 204:
        return None
    return response.json()


def _request_raw(method: str, path: str, **kwargs) -> requests.Response:
    timeout = kwargs.pop("timeout", settings.REQUEST_TIMEOUT_SECONDS)
    kwargs["headers"] = _headers(kwargs.pop("headers", None))
    try:
        response = requests.request(method, _url(path), timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        raise BackendUnavailable("API FastAPI indisponible. Verifiez que le serveur backend est lance.") from exc

    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = {"message": response.text}
        detail = body.get("detail", body) if isinstance(body, dict) else body
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
        raise ApiError(message or "Une erreur API est survenue.", status_code=response.status_code, payload=body)
    return response


def get_sources() -> list[dict[str, Any]]:
    return _request("GET", "/api/sources")


def get_topics(*, source_code: str | None = None, source_id: int | None = None, search: str = "") -> list[dict[str, Any]]:
    params = {"search": search}
    if source_code:
        params["source_code"] = source_code
    if source_id:
        params["source_id"] = source_id
    return _request("GET", "/api/topics", params=params)


def get_indicators(
    *,
    source_code: str | None = None,
    topic_id: int | None = None,
    search: str = "",
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"search": search, "limit": limit, "offset": offset}
    if source_code:
        params["source_code"] = source_code
    if topic_id:
        params["topic_id"] = topic_id
    return _request("GET", "/api/indicators", params=params)


def get_source_limits() -> dict[str, Any]:
    return _request("GET", "/api/source-limits")


def get_countries(*, search: str = "", limit: int = 50) -> list[dict[str, Any]]:
    return _request("GET", "/api/countries", params={"search": search, "limit": limit})


def generate_export_links(payload: dict[str, Any]) -> dict[str, Any]:
    return _request(
        "POST",
        "/api/datasets/generate-export-links",
        json=payload,
        timeout=max(settings.REQUEST_TIMEOUT_SECONDS, 90),
    )


def preview_dataset(payload: dict, limit: int = 50) -> dict:
    """
    Builds a real data preview through FastAPI.
    This does not create an export configuration.
    """
    return _request(
        "POST",
        f"/api/datasets/preview?limit={limit}",
        json=payload,
    )


def get_export_datasets() -> list[dict[str, Any]]:
    return _request("GET", "/api/export-datasets")


def get_dataset_detail(slug: str) -> dict[str, Any]:
    return _request("GET", f"/api/export-datasets/{slug}")


def get_dataset_versions(slug: str) -> list[dict[str, Any]]:
    return _request("GET", f"/api/export-datasets/{slug}/versions")


def get_dataset_preview(slug: str, version: int, *, limit: int = 25) -> dict[str, Any]:
    return _request("GET", f"/api/export-datasets/{slug}/versions/{version}/data-preview", params={"limit": limit})


def get_export_health() -> dict[str, Any]:
    return _request("GET", "/api/export/health")


def get_ai_dataset_recommendation(user_request: str, *, local_only: bool = False) -> dict:
    """
    Calls FastAPI AI assistant endpoint.
    Django does not call AI providers directly.
    Provider keys stay only in the FastAPI backend.
    """
    if not user_request or not user_request.strip():
        raise ValueError("La demande utilisateur est obligatoire.")

    return _request(
        "POST",
        "/api/ai/recommend-dataset",
        params={"local_only": "true"} if local_only else None,
        json={"user_request": user_request.strip()},
    )


def get_ai_runtime_config() -> dict[str, Any]:
    return _request("GET", "/api/ai/runtime-config")


def update_ai_runtime_config(payload: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", "/api/ai/runtime-config", json=payload)


def check_export_mode() -> dict:
    return _request("POST", "/api/export-datasets/check-mode")


def delete_export_dataset(slug: str) -> dict:
    return _request(
        "DELETE",
        f"/api/export-datasets/{slug}",
    )


def get_export_chronology_chart() -> tuple[bytes, str]:
    response = _request_raw("GET", "/api/export/charts/chronology.png", timeout=settings.REQUEST_TIMEOUT_SECONDS)
    return response.content, response.headers.get("content-type", "image/png")
