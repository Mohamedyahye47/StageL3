from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from app.config import (
    ODS_DEFAULT_LICENSE,
    ODS_DEFAULT_THEME,
    ODS_DOMAIN,
    ODS_ORGANIZATION,
    ODS_PRODUCER,
)


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
    return {
        "mode": "manual_url",
        "dataset_id": metadata["dataset_id"],
        "metadata": metadata,
        "remote_resources": {
            "csv_url": metadata["csv_url"],
            "json_url": metadata["json_url"],
        },
        "manual_steps": [
            "Créer ou ouvrir le dataset dans OpenDataSoft / Richat Data Hub.",
            "Ajouter une source distante de type URL HTTP avec le lien CSV.",
            "Renseigner le titre, la description, le thème et les mots-clés fournis par DataBridge.",
            "Vérifier l'aperçu OpenDataSoft, puis publier manuellement.",
        ],
        "note": (
            "Publication manuelle assistée : l'Automation API OpenDataSoft n'est pas disponible "
            "sur le plan actuel du portail."
        ),
    }


def prepare_opendatasoft_package(dataset: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    payload = build_opendatasoft_payload(dataset, manifest)
    metadata = payload["metadata"]
    return {
        "status": "manual_package",
        "mode": "manual_url",
        "dry_run": False,
        "dataset_id": metadata["dataset_id"],
        "public_url": metadata["public_url"],
        "payload": _safe_payload(payload),
        "opendatasoft_metadata": metadata,
        "opendatasoft_last_error": None,
        "opendatasoft_last_steps": [
            {
                "action": "prepare_manual_package",
                "method": "LOCAL",
                "endpoint": "manual_url",
                "status_code": "ready",
                "response_text": "Paquet de publication manuelle préparé. Aucun appel Automation API n'a été exécuté.",
                "dataset_id": metadata["dataset_id"],
                "dry_run": False,
            }
        ],
        "error": None,
    }


def get_opendatasoft_public_url(dataset_id: str) -> str:
    return f"{ODS_DOMAIN.rstrip('/')}/explore/dataset/{quote(dataset_id)}/"


def sanitize_opendatasoft_error(error: Any) -> str:
    message = str(error) if error else "Erreur OpenDataSoft inconnue."
    return _redact_secret(message)


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))


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
    text = re.sub(r"(?i)(authorization|apikey|api_key|token)[^,\n\r]{0,120}", r"\1=[secret]", text)
    return text
