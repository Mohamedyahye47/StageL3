from __future__ import annotations

from typing import Any

import requests
from django.conf import settings


class BackendUnavailable(Exception):
    """Raised when the FastAPI backend cannot be reached."""


class ApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _url(path: str) -> str:
    return f"{settings.FASTAPI_BASE_URL}{path}"


def _request(method: str, path: str, **kwargs) -> Any:
    timeout = kwargs.pop("timeout", settings.REQUEST_TIMEOUT_SECONDS)
    try:
        response = requests.request(method, _url(path), timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        raise BackendUnavailable("Le backend est indisponible. Vérifiez que l'API FastAPI est lancée.") from exc

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        raise ApiError(detail or "Une erreur API est survenue.", status_code=response.status_code)

    if response.status_code == 204:
        return None
    return response.json()


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
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"search": search, "limit": limit}
    if source_code:
        params["source_code"] = source_code
    if topic_id:
        params["topic_id"] = topic_id
    return _request("GET", "/api/indicators", params=params)


def get_countries(*, search: str = "", limit: int = 50) -> list[dict[str, Any]]:
    return _request("GET", "/api/countries", params={"search": search, "limit": limit})


def publish_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", "/api/publish-dataset", json=payload, timeout=max(settings.REQUEST_TIMEOUT_SECONDS, 90))


def preview_dataset(payload: dict, limit: int = 50) -> dict:
    """
    Builds a real data preview through FastAPI.
    This does not publish to Hugging Face.
    """
    return _request(
        "POST",
        f"/api/datasets/preview?limit={limit}",
        json=payload,
    )


def get_published_datasets() -> list[dict[str, Any]]:
    return _request("GET", "/api/published-datasets")


def get_dataset_detail(slug: str) -> dict[str, Any]:
    return _request("GET", f"/api/published-datasets/{slug}")


def get_dataset_versions(slug: str) -> list[dict[str, Any]]:
    return _request("GET", f"/api/published-datasets/{slug}/versions")


def get_dataset_preview(slug: str, version: int, *, limit: int = 25) -> dict[str, Any]:
    return _request("GET", f"/api/published-datasets/{slug}/versions/{version}/data-preview", params={"limit": limit})


def get_hf_health() -> dict[str, Any]:
    return _request("GET", "/api/hf/health")


def get_ai_dataset_recommendation(user_request: str) -> dict:
    """
    Calls FastAPI AI assistant endpoint.
    Django does not call Gemini directly.
    Gemini key stays only in FastAPI backend.
    """
    if not user_request or not user_request.strip():
        raise ValueError("La demande utilisateur est obligatoire.")

    return _request(
        "POST",
        "/api/ai/recommend-dataset",
        json={"user_request": user_request.strip()},
    )



def sync_published_datasets_with_hf() -> dict:
    return _request(
        "POST",
        "/api/published-datasets/sync-hf",
    )


def delete_published_dataset(slug: str) -> dict:
    return _request(
        "DELETE",
        f"/api/published-datasets/{slug}",
    )