from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlparse, urlunparse, parse_qsl

import requests

from app.config import (
    ODS_API_KEY,
    ODS_DEFAULT_LICENSE,
    ODS_DEFAULT_THEME,
    ODS_DOMAIN,
    ODS_DRY_RUN,
    ODS_ENABLED,
    ODS_ORGANIZATION,
    ODS_PRODUCER,
    is_configured_secret,
)


ODS_TIMEOUT_SECONDS = 30


@dataclass
class ODSApiError(Exception):
    status_code: int | None
    message: str


def build_opendatasoft_metadata(dataset: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    dataset_id = _stable_dataset_id(getattr(dataset, "slug", None) or manifest.get("slug"))
    title = _first_text(getattr(dataset, "title", None), manifest.get("title"), manifest.get("titre"), dataset_id)
    country_name = _first_text(manifest.get("country_name"), manifest.get("nom_pays"), "")
    start_date = _first_text(manifest.get("start_date"), manifest.get("date_debut"), "")
    end_date = _first_text(manifest.get("end_date"), manifest.get("date_fin"), "")
    theme = _first_text(_first_list_item(manifest.get("topic_names")), ODS_DEFAULT_THEME)
    source = _first_text(_first_list_item(manifest.get("source_names")), _first_list_item(manifest.get("source_codes")), "Banque mondiale")
    csv_url = _with_query_params(_first_text(manifest.get("csv_url"), manifest.get("url_donnees"), manifest.get("data_url")), download="1")
    json_url = _first_text(manifest.get("json_url"), "")
    indicators = _as_text_list(manifest.get("indicator_names")) or _as_text_list(manifest.get("codes_indicateurs"))
    indicator_codes = _as_text_list(manifest.get("indicator_codes") or manifest.get("codes_indicateurs"))
    description = _build_public_description(
        title=title,
        description=_first_text(getattr(dataset, "description", None), manifest.get("description"), ""),
        country_name=country_name,
        start_date=start_date,
        end_date=end_date,
        source=source,
        indicators=indicators,
    )
    keywords = _deduplicate_keywords(
        [
            country_name,
            theme,
            source,
            "Banque mondiale",
            "World Bank",
            "World Development Indicators",
            "WDI",
            "Richat DataBridge",
            "Richat Data Hub",
            *indicator_codes,
            *indicators,
        ]
    )
    return {
        "dataset_id": dataset_id,
        "title": title,
        "description": description,
        "theme": theme,
        "keywords": keywords,
        "source": source,
        "producer": ODS_PRODUCER,
        "organization": ODS_ORGANIZATION,
        "license": ODS_DEFAULT_LICENSE,
        "period": {
            "start_date": start_date,
            "end_date": end_date,
        },
        "geographic_coverage": country_name,
        "csv_url": csv_url,
        "json_url": json_url,
        "public_url": get_opendatasoft_public_url(dataset_id),
    }


def build_opendatasoft_payload(dataset: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    metadata = build_opendatasoft_metadata(dataset, manifest)
    default_metadata: dict[str, Any] = {
        "title": _ods_value(metadata["title"]),
        "description": _ods_value(metadata["description"]),
        "keyword": _ods_value(metadata["keywords"]),
        "theme": _ods_value([metadata["theme"]]),
        "attributions": _ods_value([metadata["producer"], metadata["source"]]),
    }
    if metadata.get("license"):
        default_metadata["license"] = _ods_value(metadata["license"])

    resource_payload = {
        "type": "csvfile",
        "title": f"CSV distant - {metadata['title']}",
        "params": {
            "encoding": "utf-8",
            "headers_first_row": True,
            "separator": ",",
        },
        "datasource": {
            "type": "http",
            "url": metadata["csv_url"],
            "headers": [],
        },
    }

    return {
        "dataset_id": metadata["dataset_id"],
        "is_restricted": False,
        "metadata": {
            "default": default_metadata,
        },
        "resource": resource_payload,
        "databridge_metadata": metadata,
    }


def publish_to_opendatasoft(dataset: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    payload = build_opendatasoft_payload(dataset, manifest)
    metadata = payload["databridge_metadata"]
    safe_payload = _safe_payload(payload)

    if ODS_DRY_RUN:
        return {
            "status": "dry_run",
            "dry_run": True,
            "dataset_id": metadata["dataset_id"],
            "public_url": metadata["public_url"],
            "payload": safe_payload,
            "error": None,
        }

    if not ODS_ENABLED:
        return _error_result(metadata, safe_payload, "Publication OpenDataSoft désactivée par configuration.")
    if not ODS_DOMAIN:
        return _error_result(metadata, safe_payload, "ODS_DOMAIN n'est pas configuré.")
    if not is_configured_secret(ODS_API_KEY):
        return _error_result(metadata, safe_payload, "Clé API OpenDataSoft non configurée.")

    try:
        existing_uid = _find_dataset_uid(metadata["dataset_id"])
        dataset_payload = {key: value for key, value in payload.items() if key not in {"resource", "databridge_metadata"}}
        if existing_uid:
            dataset_uid = existing_uid
            _ods_request("PUT", f"/api/automation/v1.0/datasets/{quote(dataset_uid)}/", json_payload=dataset_payload)
            status = "updated"
        else:
            response = _ods_request("POST", "/api/automation/v1.0/datasets/", json_payload=dataset_payload)
            dataset_uid = _extract_uid(response) or metadata["dataset_id"]
            status = "published"

        _upsert_dataset_resource(dataset_uid, payload["resource"])
        _ods_request("POST", f"/api/automation/v1.0/datasets/{quote(dataset_uid)}/publish/", json_payload={})
        return {
            "status": status,
            "dry_run": False,
            "dataset_id": metadata["dataset_id"],
            "public_url": metadata["public_url"],
            "payload": safe_payload,
            "error": None,
        }
    except Exception as exc:
        return _error_result(metadata, safe_payload, sanitize_opendatasoft_error(exc))


def get_opendatasoft_public_url(dataset_id: str) -> str:
    return f"{ODS_DOMAIN.rstrip('/')}/explore/dataset/{quote(dataset_id)}/"


def sanitize_opendatasoft_error(error: Any) -> str:
    if isinstance(error, ODSApiError):
        status = error.status_code
        if status == 401:
            return "API key OpenDataSoft invalide."
        if status == 403:
            return "Permissions OpenDataSoft insuffisantes pour créer, modifier ou publier ce dataset."
        if status == 400:
            return "Payload OpenDataSoft invalide ou source HTTP/CSV non acceptée."
        if status == 404:
            return "Endpoint OpenDataSoft introuvable ou dataset non trouvé."
        if status and status >= 500:
            return "Erreur temporaire côté OpenDataSoft."
        return error.message
    message = str(error) if error else "Erreur OpenDataSoft inconnue."
    return _redact_secret(message)


def _upsert_dataset_resource(dataset_uid: str, resource_payload: dict[str, Any]) -> None:
    resource_uid = _find_resource_uid(dataset_uid, resource_payload)
    if resource_uid:
        _ods_request(
            "PUT",
            f"/api/automation/v1.0/datasets/{quote(dataset_uid)}/resources/{quote(resource_uid)}/",
            json_payload=resource_payload,
        )
    else:
        _ods_request(
            "POST",
            f"/api/automation/v1.0/datasets/{quote(dataset_uid)}/resources/",
            json_payload=resource_payload,
        )


def _find_dataset_uid(dataset_id: str) -> str | None:
    try:
        response = _ods_request("GET", f"/api/explore/v2.1/catalog/datasets/{quote(dataset_id)}")
    except ODSApiError as exc:
        if exc.status_code == 404:
            return None
        raise
    return _extract_uid(response) or dataset_id


def _find_resource_uid(dataset_uid: str, resource_payload: dict[str, Any]) -> str | None:
    try:
        response = _ods_request("GET", f"/api/automation/v1.0/datasets/{quote(dataset_uid)}/resources/")
    except ODSApiError as exc:
        if exc.status_code == 404:
            return None
        raise
    resources = response.get("results", response if isinstance(response, list) else [])
    if not isinstance(resources, list):
        return None
    expected_title = resource_payload.get("title")
    expected_url = ((resource_payload.get("datasource") or {}).get("url") or "").strip()
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        datasource = resource.get("datasource") or {}
        if resource.get("title") == expected_title or resource.get("display_name") == expected_url:
            return str(resource.get("uid") or "")
        if isinstance(datasource, dict) and datasource.get("url") == expected_url:
            return str(resource.get("uid") or "")
    return None


def _ods_request(method: str, path: str, *, json_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{ODS_DOMAIN.rstrip('/')}{path}"
    headers = {
        "Authorization": f"Apikey {ODS_API_KEY}",
        "Accept": "application/json",
    }
    if json_payload is not None:
        headers["Content-Type"] = "application/json"

    try:
        response = requests.request(
            method,
            url,
            json=json_payload,
            headers=headers,
            timeout=ODS_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise ODSApiError(None, "Connexion OpenDataSoft impossible.") from exc

    if response.status_code >= 400:
        raise ODSApiError(response.status_code, _response_message(response))
    if response.status_code == 204 or not response.content:
        return {}
    try:
        data = response.json()
    except ValueError:
        return {"message": response.text}
    return data if isinstance(data, dict) else {"results": data}


def _response_message(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return _redact_secret(response.text[:500])
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("message") or data.get("error") or data
        return _redact_secret(json.dumps(detail, ensure_ascii=False) if isinstance(detail, dict) else str(detail))
    return _redact_secret(str(data))


def _extract_uid(payload: dict[str, Any]) -> str | None:
    candidates = [
        payload.get("uid"),
        payload.get("dataset_uid"),
        payload.get("id"),
        (payload.get("dataset") or {}).get("uid") if isinstance(payload.get("dataset"), dict) else None,
    ]
    for value in candidates:
        if value:
            return str(value)
    return None


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))


def _error_result(metadata: dict[str, Any], payload: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "dry_run": ODS_DRY_RUN,
        "dataset_id": metadata["dataset_id"],
        "public_url": metadata["public_url"],
        "payload": payload,
        "error": _redact_secret(message),
    }


def _ods_value(value: Any) -> dict[str, Any]:
    return {
        "value": value,
        "remote_value": value,
        "override_remote_value": True,
    }


def _build_public_description(
    *,
    title: str,
    description: str,
    country_name: str,
    start_date: str,
    end_date: str,
    source: str,
    indicators: list[str],
) -> str:
    period = _period_label(start_date, end_date)
    indicator_text = ", ".join(indicators[:8])
    parts = [
        description.strip() or title,
        f"Ce jeu de données couvre {country_name or 'le pays sélectionné'} sur la période {period}.",
        f"Les données proviennent de {source or 'la source configurée'} et sont préparées par Richat DataBridge pour un usage analytique dans Richat Data Hub.",
    ]
    if indicator_text:
        parts.append(f"Indicateurs inclus : {indicator_text}.")
    return " ".join(part for part in parts if part)


def _period_label(start_date: str, end_date: str) -> str:
    start = str(start_date)[:4] if start_date else ""
    end = str(end_date)[:4] if end_date else ""
    if start and end:
        return f"{start}-{end}"
    return start or end or "non précisée"


def _stable_dataset_id(value: str | None) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", (value or "richat-databridge-dataset").strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "richat-databridge-dataset"


def _with_query_params(url: str, **params: str) -> str:
    if not url:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _first_list_item(value: Any) -> str:
    items = _as_text_list(value)
    return items[0] if items else ""


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_first_text(item) for item in value if _first_text(item)]
    text = _first_text(value)
    return [text] if text else []


def _deduplicate_keywords(values: list[str]) -> list[str]:
    seen: set[str] = set()
    keywords: list[str] = []
    for value in values:
        text = _first_text(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        keywords.append(text[:120])
    return keywords[:40]


def _redact_secret(value: str) -> str:
    text = value or ""
    if ODS_API_KEY:
        text = text.replace(ODS_API_KEY, "[secret]")
    text = re.sub(r"(?i)(authorization|apikey|api_key|token)[^,\n\r]{0,120}", r"\1=[secret]", text)
    return text
