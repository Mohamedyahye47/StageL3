from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

from sqlalchemy import delete
import requests
from requests.adapters import HTTPAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.exc import IntegrityError
from urllib3.util.retry import Retry

from app.config import (
    DATABRIDGE_EXPORT_TOKEN,
    EXPORT_SCHEMA_MODE,
    PUBLIC_API_BASE_URL,
    REMOTE_PROVIDER,
    get_source_indicator_limit,
    get_source_label,
    is_configured_secret,
)
from app.models import (
    Country,
    ExportDataset,
    ExportDatasetIndicator,
    ExportLog,
    Indicator,
    IndicatorTopic,
    Source,
    Topic,
)
from app.schemas import PublishDatasetIn
from app.services.measure_service import enregistrer_mesure
from app.services.opendatasoft_service import (
    build_opendatasoft_metadata as _build_opendatasoft_metadata,
    prepare_opendatasoft_package,
)

WORLD_BANK_DATA_API_BASE = os.getenv("WB_API_BASE", "https://api.worldbank.org/v2").rstrip("/")
WORLD_BANK_DATA_TIMEOUT = int(os.getenv("WB_DATA_TIMEOUT", os.getenv("WB_API_TIMEOUT", "60")))
WORLD_BANK_DATA_CONNECT_TIMEOUT = int(os.getenv("WB_DATA_CONNECT_TIMEOUT", os.getenv("WB_API_CONNECT_TIMEOUT", "15")))
WORLD_BANK_DATA_PAGE_SIZE = max(200, int(os.getenv("WB_DATA_PAGE_SIZE", "2000")))
PUBLIC_EXPORT_COLUMNS = ["Pays", "Indicateur", "Date", "Valeur", "Unite", "Source"]
TECHNICAL_EXPORT_COLUMNS = [
    "id_pays",
    "code_pays",
    "nom_pays",
    "id_indicateur",
    "code_indicateur",
    "nom_indicateur",
    "date",
    "valeur",
]


class PublishError(Exception):
    status_code = 400


class ValidationError(PublishError):
    status_code = 400


class DataBuildError(PublishError):
    status_code = 502


class ExportTokenError(PublishError):
    status_code = 403


class ExportTokenConfigError(PublishError):
    status_code = 500


@dataclass
class ExportContext:
    source: Source
    country: Country
    indicators: list[Indicator]
    topics: list[Topic]
    dataset: ExportDataset | None
    slug: str
    version: int
    export_version: str


@dataclass
class DatasetDataBuild:
    csv_text: str
    json_text: str
    row_count: int
    non_null_value_count: int
    missing_indicator_codes: list[str]
    columns: list[str]
    indicator_measurements: list[dict[str, Any]] = field(default_factory=list)
    csv_duration_seconds: float = 0.0
    json_duration_seconds: float = 0.0


def generate_export_links(db: Session, payload: PublishDatasetIn) -> dict[str, Any]:
    mesure_debut = time.perf_counter()
    etapes: dict[str, float] = {}
    title = payload.title.strip()
    description = payload.description.strip()
    _validate_dataset_business_rules(payload)
    validation_debut = time.perf_counter()
    if not title:
        raise ValidationError("Le titre est obligatoire.")
    etapes["validation_titre"] = round(time.perf_counter() - validation_debut, 4)
    validation_debut = time.perf_counter()
    if not description:
        raise ValidationError("La description est obligatoire.")
    etapes["validation_description"] = round(time.perf_counter() - validation_debut, 4)
    validation_debut = time.perf_counter()
    if not payload.indicator_ids:
        raise ValidationError("Au moins un indicateur doit etre selectionne.")
    etapes["validation_indicateurs"] = round(time.perf_counter() - validation_debut, 4)

    validation_debut = time.perf_counter()
    start_date = _parse_date(payload.start_date, "start_date")
    end_date = _parse_date(payload.end_date, "end_date")
    if start_date > end_date:
        raise ValidationError("La date de debut doit etre inferieure ou egale a la date de fin.")
    etapes["validation_dates"] = round(time.perf_counter() - validation_debut, 4)

    contexte_debut = time.perf_counter()
    context = _load_publish_context(db, payload)
    etapes["chargement_contexte"] = round(time.perf_counter() - contexte_debut, 4)
    now = _utc_now()
    donnees_debut = time.perf_counter()
    data_build = _build_dataset_data(
        context=context,
        start_date=start_date,
        end_date=end_date,
    )
    etapes["construction_donnees"] = round(time.perf_counter() - donnees_debut, 4)

    manifeste_debut = time.perf_counter()
    manifest = _build_manifest(
        payload=payload,
        context=context,
        title=title,
        description=description,
        created_at=now,
        generated_at=now,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        data_build=data_build,
    )
    manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2)
    etapes["construction_manifeste"] = round(time.perf_counter() - manifeste_debut, 4)
    liens_debut = time.perf_counter()
    csv_url = _build_export_url(context.slug, "csv")
    json_url = _build_export_url(context.slug, "json")
    etapes["generation_liens"] = round(time.perf_counter() - liens_debut, 4)

    sauvegarde_debut = time.perf_counter()
    dataset = context.dataset
    if dataset is None:
        dataset = ExportDataset(
            slug=context.slug,
            title=title,
            description=description,
            source_id=context.source.id,
            topic_id=payload.topic_id,
            country_id=context.country.id,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            status="export_links_ready",
            provider=REMOTE_PROVIDER,
            csv_export_url=csv_url,
            json_export_url=json_url,
            latest_version=context.version,
            format=manifest["format"],
            frequency=_manifest_get(manifest, "frequence", "frequency"),
            build_json=manifest_json,
            created_at=now,
            updated_at=now,
        )
        db.add(dataset)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise ValidationError(
                "Un dataset avec ce slug existe deja. Choisissez un autre titre ou utilisez le mode de nouvelle version."
            ) from exc
    else:
        dataset.title = title
        dataset.description = description
        dataset.latest_version = context.version
        dataset.source_id = context.source.id
        dataset.topic_id = payload.topic_id
        dataset.country_id = context.country.id
        dataset.start_date = start_date.isoformat()
        dataset.end_date = end_date.isoformat()
        dataset.status = "export_links_ready"
        dataset.provider = REMOTE_PROVIDER
        dataset.csv_export_url = csv_url
        dataset.json_export_url = json_url
        dataset.format = manifest["format"]
        dataset.frequency = _manifest_get(manifest, "frequence", "frequency")
        dataset.build_json = manifest_json
        dataset.updated_at = now
        db.flush()

    db.execute(
        delete(ExportDatasetIndicator).where(
            ExportDatasetIndicator.export_dataset_id == dataset.id
        )
    )
    for indicator in context.indicators:
        db.add(
            ExportDatasetIndicator(
                export_dataset_id=dataset.id,
                indicator_id=indicator.id,
            )
        )

    manifest["opendatasoft_metadata"] = build_opendatasoft_metadata(dataset, manifest)
    manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2)
    dataset.build_json = manifest_json

    db.add(
        ExportLog(
            export_dataset_id=dataset.id,
            action="generation_liens",
            row_count=data_build.row_count,
            non_null_value_count=data_build.non_null_value_count,
            status="success",
            error_message=None,
            duration_seconds=round(time.perf_counter() - mesure_debut, 4),
            created_at=now,
        )
    )
    db.commit()
    etapes["sauvegarde_configuration_locale"] = round(time.perf_counter() - sauvegarde_debut, 4)

    result = {
        "slug": dataset.slug,
        "title": dataset.title,
        "description": dataset.description,
        "csv_url": csv_url,
        "json_url": json_url,
        "row_count": data_build.row_count,
        "non_null_value_count": data_build.non_null_value_count,
        "indicator_count": len(context.indicators),
        "status": dataset.status,
        "opendatasoft_metadata": manifest.get("opendatasoft_metadata"),
        "opendatasoft_status": manifest.get("opendatasoft_status"),
        "opendatasoft_public_url": manifest.get("opendatasoft_public_url"),
    }
    _enregistrer_mesure_dataset(
        type_mesure="generation_liens_dataset",
        payload=payload,
        context=context,
        start_date=start_date,
        end_date=end_date,
        data_build=data_build,
        etapes=etapes,
        duree_totale=time.perf_counter() - mesure_debut,
        etat="Réussi",
    )
    return result



def preview_dataset(db: Session, payload: PublishDatasetIn, *, limit: int = 50) -> dict[str, Any]:
    """
    Builds the real dataset rows for preview only.

    It validates the same payload used for export generation.
    It uses the same data-building logic as generate_export_links().
    It does not write export metadata.
    """

    mesure_debut = time.perf_counter()
    etapes: dict[str, float] = {}
    title = payload.title.strip()
    description = payload.description.strip()
    _validate_dataset_business_rules(payload)

    validation_debut = time.perf_counter()
    if not title:
        raise ValidationError("Le titre est obligatoire.")
    etapes["validation_titre"] = round(time.perf_counter() - validation_debut, 4)
    validation_debut = time.perf_counter()
    if not description:
        raise ValidationError("La description est obligatoire.")
    etapes["validation_description"] = round(time.perf_counter() - validation_debut, 4)
    validation_debut = time.perf_counter()
    if not payload.indicator_ids:
        raise ValidationError("Au moins un indicateur doit etre selectionne.")
    etapes["validation_indicateurs"] = round(time.perf_counter() - validation_debut, 4)

    validation_debut = time.perf_counter()
    start_date = _parse_date(payload.start_date, "start_date")
    end_date = _parse_date(payload.end_date, "end_date")

    if start_date > end_date:
        raise ValidationError("La date de debut doit etre inferieure ou egale a la date de fin.")
    etapes["validation_dates"] = round(time.perf_counter() - validation_debut, 4)

    contexte_debut = time.perf_counter()
    context = _load_publish_context(db, payload)
    etapes["chargement_contexte"] = round(time.perf_counter() - contexte_debut, 4)

    donnees_debut = time.perf_counter()
    data_build = _build_dataset_data(
        context=context,
        start_date=start_date,
        end_date=end_date,
    )
    etapes["construction_donnees"] = round(time.perf_counter() - donnees_debut, 4)

    lecture_debut = time.perf_counter()
    try:
        all_rows = json.loads(data_build.json_text)
    except json.JSONDecodeError as exc:
        raise DataBuildError("Impossible de lire les donnees generees pour l'apercu.") from exc
    etapes["lecture_json_apercu"] = round(time.perf_counter() - lecture_debut, 4)

    preview_rows = all_rows[:limit]
    _enregistrer_mesure_dataset(
        type_mesure="apercu_dataset",
        payload=payload,
        context=context,
        start_date=start_date,
        end_date=end_date,
        data_build=data_build,
        etapes=etapes,
        duree_totale=time.perf_counter() - mesure_debut,
        etat="Réussi",
    )

    return {
        "columns": data_build.columns,
        "rows": preview_rows,
        "preview_count": len(preview_rows),
        "row_count": data_build.row_count,
        "non_null_value_count": data_build.non_null_value_count,
        "missing_indicator_codes": data_build.missing_indicator_codes,
        "country": context.country,
        "indicators": context.indicators,
    }


def generate_unique_slug(db: Session, title: str) -> str:
    base_slug = slugify(title)
    candidate = base_slug
    suffix = 2

    while db.scalar(
        select(ExportDataset.id).where(ExportDataset.slug == candidate)
    ) is not None:
        candidate = f"{base_slug}-{suffix}"
        suffix += 1

    return candidate


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value.lower()).strip("-")
    return slug or "dataset"


def get_dashboard_datasets(db: Session) -> list[dict[str, Any]]:
    datasets = db.scalars(
        select(ExportDataset)
        .options(selectinload(ExportDataset.country))
        .order_by(ExportDataset.updated_at.desc())
    ).all()
    rows: list[dict[str, Any]] = []
    for dataset in datasets:
        last_status = db.execute(
            select(ExportLog.status)
            .where(ExportLog.export_dataset_id == dataset.id)
            .order_by(ExportLog.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        rows.append(
            {
                "id": dataset.id,
                "slug": dataset.slug,
                "title": dataset.title,
                "description": dataset.description,
                "country": dataset.country,
                "latest_version": dataset.latest_version,
                "updated_at": dataset.updated_at,
                "provider": dataset.provider,
                "export_id": _build_export_id(dataset.slug),
                "csv_url": dataset.csv_export_url,
                "json_url": dataset.json_export_url,
                "last_export_status": last_status,
            }
        )
    return rows

def _delete_local_dataset_records(db: Session, dataset: ExportDataset) -> None:
    """
    Deletes a local export configuration from SQLite.

    We delete manually because the current SQLAlchemy relationships do not define
    cascade delete behavior.
    """

    db.execute(
        delete(ExportDatasetIndicator).where(
            ExportDatasetIndicator.export_dataset_id == dataset.id
        )
    )
    db.execute(
        delete(ExportLog).where(
            ExportLog.export_dataset_id == dataset.id
        )
    )

    db.delete(dataset)


def check_export_mode(db: Session) -> dict[str, Any]:
    local_datasets = db.scalars(select(ExportDataset)).all()
    return {
        "ok": True,
        "provider": REMOTE_PROVIDER,
        "dataset_count": len(local_datasets),
        "message": "Mode API d'export Opendatasoft actif. Aucune synchronisation externe n'est necessaire.",
    }


def delete_export_dataset(db: Session, slug: str) -> dict[str, Any]:
    """
    Deletes the local export configuration.
    """

    dataset = db.scalar(
        select(ExportDataset).where(ExportDataset.slug == slug)
    )

    if dataset is None:
        raise ValidationError(f"Dataset '{slug}' introuvable.")

    _delete_local_dataset_records(db, dataset)
    db.commit()

    return {
        "slug": slug,
        "deleted": True,
        "message": "Configuration d'export locale supprimee.",
    }


def get_dataset_detail(db: Session, slug: str) -> dict[str, Any] | None:
    dataset = db.scalar(
        select(ExportDataset)
        .options(
            selectinload(ExportDataset.country),
            selectinload(ExportDataset.indicator_links)
            .selectinload(ExportDatasetIndicator.indicator)
            .selectinload(Indicator.topic_links),
        )
        .where(ExportDataset.slug == slug)
    )
    if dataset is None:
        return None

    versions = list_dataset_versions(db, slug)
    if not versions:
        return None

    latest_version = next(
        (version for version in versions if version["version"] == dataset.latest_version),
        versions[0],
    )
    manifest = latest_version.get("manifest") or {}
    return {
        "id": dataset.id,
        "slug": dataset.slug,
        "title": dataset.title,
        "description": dataset.description,
        "provider": dataset.provider,
        "export_id": _build_export_id(dataset.slug),
        "csv_url": dataset.csv_export_url,
        "json_url": dataset.json_export_url,
        "status": dataset.status,
        "latest_version": dataset.latest_version,
        "created_at": dataset.created_at,
        "updated_at": dataset.updated_at,
        "latest_version_detail": latest_version,
        "versions": versions,
        "opendatasoft_metadata": manifest.get("opendatasoft_metadata"),
        "opendatasoft_status": manifest.get("opendatasoft_status"),
        "opendatasoft_public_url": manifest.get("opendatasoft_public_url"),
        "opendatasoft_last_error": manifest.get("opendatasoft_last_error"),
        "opendatasoft_last_steps": manifest.get("opendatasoft_last_steps") or [],
        "opendatasoft_last_result": manifest.get("opendatasoft_last_result"),
    }


def build_opendatasoft_metadata(dataset: ExportDataset, manifest: dict[str, Any]) -> dict[str, Any]:
    return _build_opendatasoft_metadata(dataset, manifest)


def get_opendatasoft_metadata(db: Session, slug: str) -> dict[str, Any]:
    dataset = _load_export_dataset_for_opendatasoft(db, slug)
    manifest = _load_dataset_manifest(dataset)
    metadata = build_opendatasoft_metadata(dataset, manifest)
    return {
        "slug": dataset.slug,
        "opendatasoft_metadata": metadata,
        "opendatasoft_status": manifest.get("opendatasoft_status"),
        "opendatasoft_public_url": manifest.get("opendatasoft_public_url") or metadata.get("public_url"),
        "opendatasoft_last_error": manifest.get("opendatasoft_last_error"),
        "opendatasoft_last_steps": manifest.get("opendatasoft_last_steps") or [],
        "opendatasoft_last_result": manifest.get("opendatasoft_last_result"),
    }


def prepare_dataset_for_opendatasoft(db: Session, slug: str) -> dict[str, Any]:
    dataset = _load_export_dataset_for_opendatasoft(db, slug)
    manifest = _load_dataset_manifest(dataset)
    metadata = build_opendatasoft_metadata(dataset, manifest)
    manifest["opendatasoft_metadata"] = metadata

    result = prepare_opendatasoft_package(dataset, manifest)
    last_steps = result.get("opendatasoft_last_steps") or []
    manifest["opendatasoft_status"] = result.get("status")
    manifest["opendatasoft_public_url"] = result.get("public_url")
    manifest["opendatasoft_last_error"] = result.get("error")
    manifest["opendatasoft_last_steps"] = last_steps
    manifest["opendatasoft_last_result"] = result
    manifest.pop("opendatasoft_published_at", None)

    dataset.build_json = json.dumps(manifest, ensure_ascii=False, indent=2)
    dataset.updated_at = _utc_now()
    db.add(
        ExportLog(
            export_dataset_id=dataset.id,
            action="preparation_opendatasoft",
            row_count=None,
            non_null_value_count=None,
            status="success" if result.get("status") == "manual_package" else "error",
            error_message=result.get("error"),
            duration_seconds=None,
            created_at=dataset.updated_at,
        )
    )
    db.commit()
    return result

def _load_export_dataset_for_opendatasoft(db: Session, slug: str) -> ExportDataset:
    dataset = db.scalar(
        select(ExportDataset)
        .options(
            selectinload(ExportDataset.country),
            selectinload(ExportDataset.source),
            selectinload(ExportDataset.topic),
            selectinload(ExportDataset.indicator_links).selectinload(ExportDatasetIndicator.indicator),
        )
        .where(ExportDataset.slug == slug)
    )
    if dataset is None:
        raise ValidationError(f"Dataset '{slug}' introuvable.")
    return dataset


def _load_dataset_manifest(dataset: ExportDataset) -> dict[str, Any]:
    try:
        manifest = json.loads(dataset.build_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValidationError("Manifest d'export illisible pour la publication OpenDataSoft.") from exc
    if not isinstance(manifest, dict):
        raise ValidationError("Manifest d'export invalide pour la publication OpenDataSoft.")
    return manifest


def list_dataset_versions(db: Session, slug: str) -> list[dict[str, Any]]:
    dataset = db.scalar(
        select(ExportDataset)
        .options(
            selectinload(ExportDataset.country),
            selectinload(ExportDataset.indicator_links)
            .selectinload(ExportDatasetIndicator.indicator)
            .selectinload(Indicator.topic_links),
        )
        .where(ExportDataset.slug == slug)
    )
    if dataset is None:
        return []

    return [_serialize_export_dataset(dataset)]


def get_dataset_version_data_preview(
    db: Session,
    slug: str,
    version_number: int,
    *,
    limit: int = 25,
) -> dict[str, Any] | None:
    dataset = db.scalar(
        select(ExportDataset)
        .options(
            selectinload(ExportDataset.country),
            selectinload(ExportDataset.indicator_links)
            .selectinload(ExportDatasetIndicator.indicator)
            .selectinload(Indicator.topic_links),
        )
        .where(ExportDataset.slug == slug)
    )
    if dataset is None:
        return None

    if version_number != dataset.latest_version:
        return None

    try:
        manifest, data_build = _build_saved_export_data(db, dataset)
        rows = json.loads(data_build.json_text)[:limit]
    except json.JSONDecodeError as exc:
        raise DataBuildError("Impossible de lire les donnees generees pour l'apercu.") from exc

    return {
        "data_url": dataset.csv_export_url,
        "columns": data_build.columns,
        "rows": rows,
        "preview_count": len(rows),
        "row_count": data_build.row_count,
        "non_null_value_count": data_build.non_null_value_count,
        "missing_indicator_codes": data_build.missing_indicator_codes,
        "manifest": manifest,
    }


def get_export_dataset_build(db: Session, slug: str) -> dict[str, Any] | None:
    dataset = db.scalar(
        select(ExportDataset)
        .options(
            selectinload(ExportDataset.country),
            selectinload(ExportDataset.indicator_links)
            .selectinload(ExportDatasetIndicator.indicator)
            .selectinload(Indicator.topic_links),
        )
        .where(ExportDataset.slug == slug)
    )
    if dataset is None:
        return None

    manifest, data_build = _build_saved_export_data(db, dataset)
    return {
        "dataset": dataset,
        "manifest": manifest,
        "data_build": data_build,
    }


def export_dataset_csv(db: Session, slug: str, token: str | None) -> str:
    _validate_export_token(slug, token)
    export_build = get_export_dataset_build(db, slug)
    if export_build is None:
        raise ValidationError(f"Dataset '{slug}' introuvable.")
    return export_build["data_build"].csv_text


def export_dataset_json(db: Session, slug: str, token: str | None) -> list[dict[str, Any]]:
    _validate_export_token(slug, token)
    export_build = get_export_dataset_build(db, slug)
    if export_build is None:
        raise ValidationError(f"Dataset '{slug}' introuvable.")
    try:
        rows = json.loads(export_build["data_build"].json_text)
    except json.JSONDecodeError as exc:
        raise DataBuildError("Impossible de lire les donnees JSON exportees.") from exc
    return rows


def record_export_access(
    db: Session,
    *,
    slug: str,
    export_format: str,
    request: Any = None,
    status: str,
    error_message: str | None = None,
) -> None:
    """Persist a sanitized export access trace without storing URL tokens."""

    try:
        dataset_id = db.scalar(select(ExportDataset.id).where(ExportDataset.slug == slug))
        headers = getattr(request, "headers", {}) or {}
        forwarded_for = headers.get("x-forwarded-for", "") if hasattr(headers, "get") else ""
        client_ip = forwarded_for.split(",", 1)[0].strip()
        if not client_ip and getattr(request, "client", None) is not None:
            client_ip = getattr(request.client, "host", "")
        payload = {
            "slug": slug,
            "format": export_format,
            "ip": client_ip,
            "user_agent": headers.get("user-agent", "") if hasattr(headers, "get") else "",
        }
        if error_message:
            payload["error"] = error_message
        db.add(
            ExportLog(
                export_dataset_id=dataset_id,
                action=f"access_export_{export_format}",
                row_count=None,
                non_null_value_count=None,
                status=status,
                error_message=json.dumps(payload, ensure_ascii=False),
                duration_seconds=None,
                created_at=_utc_now(),
            )
        )
        db.commit()
    except Exception:
        db.rollback()


def _build_saved_export_data(
    db: Session,
    dataset: ExportDataset,
) -> tuple[dict[str, Any], DatasetDataBuild]:
    try:
        manifest = json.loads(dataset.build_json)
    except json.JSONDecodeError as exc:
        raise DataBuildError("La configuration sauvegardee du dataset est illisible.") from exc

    source = dataset.source or db.scalar(select(Source).where(Source.id == dataset.source_id))
    if source is None:
        raise ValidationError("Source introuvable pour cet export.")

    country = dataset.country or db.scalar(
        select(Country).where(Country.id == dataset.country_id, Country.enabled.is_(True))
    )
    if country is None:
        raise ValidationError("Le pays sauvegarde est invalide ou desactive.")

    indicator_ids = db.scalars(
        select(ExportDatasetIndicator.indicator_id).where(
            ExportDatasetIndicator.export_dataset_id == dataset.id
        )
    ).all()

    if not indicator_ids:
        raise ValidationError("Aucun indicateur sauvegarde pour cet export.")

    indicators = db.scalars(
        select(Indicator)
        .options(selectinload(Indicator.topic_links).selectinload(IndicatorTopic.topic))
        .where(Indicator.id.in_(indicator_ids))
    ).all()
    indicators_by_id = {indicator.id: indicator for indicator in indicators}
    missing_ids = [identifier for identifier in indicator_ids if identifier not in indicators_by_id]
    if missing_ids:
        raise ValidationError(f"Indicateurs sauvegardes introuvables: {', '.join(map(str, missing_ids))}.")

    ordered_indicators = [indicators_by_id[identifier] for identifier in indicator_ids]
    foreign_source_ids = {indicator.source_id for indicator in ordered_indicators if indicator.source_id != source.id}
    if foreign_source_ids:
        raise ValidationError("La configuration sauvegardee contient des indicateurs d'une autre source.")

    topic_map: dict[int, Topic] = {}
    if dataset.topic is not None:
        topic_map[dataset.topic.id] = dataset.topic
    for indicator in ordered_indicators:
        for link in indicator.topic_links:
            if link.topic is not None:
                topic_map[link.topic.id] = link.topic

    context = ExportContext(
        source=source,
        country=country,
        indicators=ordered_indicators,
        topics=sorted(topic_map.values(), key=lambda topic: topic.name.lower()),
        dataset=dataset,
        slug=dataset.slug,
        version=dataset.latest_version,
        export_version=f"v{dataset.latest_version}",
    )
    start_date = _parse_date(
        _manifest_get(manifest, "date_debut", "start_date", default=dataset.start_date),
        "start_date",
    )
    end_date = _parse_date(
        _manifest_get(manifest, "date_fin", "end_date", default=dataset.end_date),
        "end_date",
    )
    if start_date > end_date:
        raise ValidationError("La plage de dates sauvegardee est invalide.")

    return manifest, _build_dataset_data(
        context=context,
        start_date=start_date,
        end_date=end_date,
    )


def _validate_dataset_business_rules(payload: PublishDatasetIn) -> None:
    if payload.topic_id is None:
        raise ValidationError("Le thème est obligatoire pour créer un jeu de données.")

    source_code = (payload.source_code or "").strip().upper()
    max_indicators = get_source_indicator_limit(source_code)
    selected_count = len(payload.indicator_ids or [])
    if selected_count > max_indicators:
        source_label = get_source_label(source_code)
        raise ValidationError(
            f"La source {source_label} autorise au maximum {max_indicators} indicateurs par dataset."
        )


def _load_publish_context(db: Session, payload: PublishDatasetIn) -> ExportContext:
    source = db.scalar(select(Source).where(Source.code == payload.source_code.upper()))
    if source is None:
        raise ValidationError(f"Source '{payload.source_code}' introuvable.")

    selected_topic = db.scalar(
        select(Topic).where(Topic.id == payload.topic_id, Topic.source_id == source.id)
    )
    if selected_topic is None:
        raise ValidationError("Le thème sélectionné est invalide pour cette source.")

    country = db.scalar(
        select(Country).where(Country.id == payload.country_id, Country.enabled.is_(True))
    )
    if country is None:
        raise ValidationError("Le pays selectionne est invalide ou desactive.")

    indicators = db.scalars(
        select(Indicator)
        .options(selectinload(Indicator.topic_links).selectinload(IndicatorTopic.topic))
        .where(Indicator.id.in_(payload.indicator_ids))
    ).all()
    indicators_by_id = {indicator.id: indicator for indicator in indicators}
    missing_ids = [identifier for identifier in payload.indicator_ids if identifier not in indicators_by_id]
    if missing_ids:
        raise ValidationError(f"Indicateurs introuvables: {', '.join(map(str, missing_ids))}.")

    ordered_indicators = [indicators_by_id[identifier] for identifier in payload.indicator_ids]
    foreign_source_ids = {indicator.source_id for indicator in ordered_indicators if indicator.source_id != source.id}
    if foreign_source_ids:
        raise ValidationError("Tous les indicateurs doivent appartenir a la source selectionnee.")

    topic_map: dict[int, Topic] = {selected_topic.id: selected_topic}
    for indicator in ordered_indicators:
        for link in indicator.topic_links:
            if link.topic is not None:
                topic_map[link.topic.id] = link.topic

    dataset = None
    slug = payload.existing_slug.strip() if payload.existing_slug else None
    if slug:
        dataset = db.scalar(select(ExportDataset).where(ExportDataset.slug == slug))
        if dataset is None:
            raise ValidationError(f"Dataset '{slug}' introuvable pour creer une nouvelle version.")
        version_number = dataset.latest_version + 1
    else:
        slug = generate_unique_slug(db, title=payload.title)
        version_number = 1

    return ExportContext(
        source=source,
        country=country,
        indicators=ordered_indicators,
        topics=sorted(topic_map.values(), key=lambda topic: topic.name.lower()),
        dataset=dataset,
        slug=slug,
        version=version_number,
        export_version=f"v{version_number}",
    )


def _build_manifest(
    *,
    payload: PublishDatasetIn,
    context: ExportContext,
    title: str,
    description: str,
    created_at: str,
    generated_at: str,
    start_date: str,
    end_date: str,
    data_build: DatasetDataBuild,
) -> dict[str, Any]:
    export_id = _build_export_id(context.slug)
    csv_url = _build_export_url(context.slug, "csv")
    json_url = _build_export_url(context.slug, "json")
    indicator_codes = [indicator.code for indicator in context.indicators]
    indicator_names = [indicator.name for indicator in context.indicators]
    source_ids = sorted({indicator.source_id for indicator in context.indicators})
    source_codes = sorted({context.source.code for _ in context.indicators})
    source_names = sorted({context.source.name for _ in context.indicators})
    topic_ids = sorted({topic.id for topic in context.topics})
    topic_names = sorted({topic.name for topic in context.topics})
    periodicities = [indicator.periodicity for indicator in context.indicators if indicator.periodicity]
    frequency = payload.frequency or _derive_frequency(periodicities)
    csv_path = f"/api/opendata/exports/{context.slug}.csv"
    json_path = f"/api/opendata/exports/{context.slug}.json"

    return {
        "titre": title,
        "title": title,
        "description": description,
        "slug": context.slug,
        "fournisseur_export": REMOTE_PROVIDER,
        "provider": REMOTE_PROVIDER,
        "identifiant_export": export_id,
        "export_id": export_id,
        "csv_url": csv_url,
        "json_url": json_url,
        "export_token_required": True,
        "version": context.version,
        "version_export": context.export_version,
        "export_version": context.export_version,
        "id_pays": context.country.id,
        "country_id": context.country.id,
        "code_pays": context.country.wb_code,
        "country_code": context.country.wb_code,
        "code_pays_iso3": context.country.code_iso3,
        "country_code_iso3": context.country.code_iso3,
        "nom_pays": context.country.name,
        "country_name": context.country.name,
        "ids_sources": source_ids,
        "source_ids": source_ids,
        "codes_sources": source_codes,
        "source_codes": source_codes,
        "noms_sources": source_names,
        "source_names": source_names,
        "ids_themes": topic_ids,
        "topic_ids": topic_ids,
        "noms_themes": topic_names,
        "topic_names": topic_names,
        "ids_indicateurs": [indicator.id for indicator in context.indicators],
        "indicator_ids": [indicator.id for indicator in context.indicators],
        "codes_indicateurs": indicator_codes,
        "indicator_codes": indicator_codes,
        "noms_indicateurs": indicator_names,
        "indicator_names": indicator_names,
        "date_debut": start_date,
        "start_date": start_date,
        "date_fin": end_date,
        "end_date": end_date,
        "format": payload.format or "csv",
        "mode_schema_export": EXPORT_SCHEMA_MODE,
        "export_schema_mode": EXPORT_SCHEMA_MODE,
        "frequence": frequency,
        "frequency": frequency,
        "nombre_lignes": data_build.row_count,
        "rows_count": data_build.row_count,
        "nombre_valeurs_non_nulles": data_build.non_null_value_count,
        "non_null_value_count": data_build.non_null_value_count,
        "codes_indicateurs_manquants": data_build.missing_indicator_codes,
        "missing_indicator_codes": data_build.missing_indicator_codes,
        "colonnes_donnees": data_build.columns,
        "data_columns": data_build.columns,
        "fichiers_donnees": [
            {
                "type": "csv",
                "chemin": csv_path,
                "url": csv_url,
            },
            {
                "type": "json",
                "chemin": json_path,
                "url": json_url,
            },
        ],
        "data_files": [
            {
                "kind": "csv",
                "path": csv_path,
                "url": csv_url,
            },
            {
                "kind": "json",
                "path": json_path,
                "url": json_url,
            },
        ],
        "url_donnees": csv_url,
        "data_url": csv_url,
        "cree_le": created_at,
        "created_at": created_at,
        "genere_le": generated_at,
        "generated_at": generated_at,
    }


def _build_dataset_data(
    *,
    context: ExportContext,
    start_date: date,
    end_date: date,
) -> DatasetDataBuild:
    session = _build_world_bank_session()
    rows: list[dict[str, Any]] = []
    missing_indicator_codes: list[str] = []
    indicator_measurements: list[dict[str, Any]] = []

    for indicator in context.indicators:
        indicateur_debut = time.perf_counter()
        indicator_rows = _fetch_indicator_rows(
            session=session,
            source=context.source,
            country=context.country,
            indicator=indicator,
            start_date=start_date,
            end_date=end_date,
        )
        indicateur_duree = time.perf_counter() - indicateur_debut
        indicator_measurements.append(
            {
                "code_indicateur": indicator.code,
                "nom_indicateur": indicator.name,
                "duree_secondes": round(indicateur_duree, 4),
                "nombre_lignes": len(indicator_rows),
                "valeurs_non_nulles": sum(1 for row in indicator_rows if row.get("valeur") is not None),
                "etat": "Réussi" if indicator_rows else "Sans données",
            }
        )
        if not indicator_rows:
            missing_indicator_codes.append(indicator.code)
        rows.extend(indicator_rows)

    if not rows:
        raise DataBuildError(
            "Aucune donnee reelle n'a ete trouvee pour cette combinaison pays, indicateurs et plage de dates."
        )

    columns = _export_columns()
    export_rows = _project_export_rows(rows, columns)
    csv_debut = time.perf_counter()
    csv_text = _rows_to_csv(export_rows, columns)
    csv_duration_seconds = round(time.perf_counter() - csv_debut, 4)
    json_debut = time.perf_counter()
    json_text = json.dumps(export_rows, ensure_ascii=False, indent=2)
    json_duration_seconds = round(time.perf_counter() - json_debut, 4)
    non_null_value_count = sum(1 for row in rows if row["valeur"] is not None)
    return DatasetDataBuild(
        csv_text=csv_text,
        json_text=json_text,
        row_count=len(rows),
        non_null_value_count=non_null_value_count,
        missing_indicator_codes=missing_indicator_codes,
        columns=columns,
        indicator_measurements=indicator_measurements,
        csv_duration_seconds=csv_duration_seconds,
        json_duration_seconds=json_duration_seconds,
    )


def _build_world_bank_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        read=5,
        connect=5,
        status=5,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "RichatDataBridge/2.0 export"})
    return session


def _enregistrer_mesure_dataset(
    *,
    type_mesure: str,
    payload: PublishDatasetIn,
    context: ExportContext,
    start_date: date,
    end_date: date,
    data_build: DatasetDataBuild,
    etapes: dict[str, float],
    duree_totale: float,
    etat: str,
) -> None:
    nombre_indicateurs = len(context.indicators)
    nombre_annees = max(0, end_date.year - start_date.year + 1)
    durees_indicateurs = [
        float(item.get("duree_secondes") or 0)
        for item in data_build.indicator_measurements
    ]
    enregistrer_mesure(
        "datasets",
        {
            "type": type_mesure,
            "etat": etat,
            "pays": context.country.name,
            "code_pays": context.country.wb_code,
            "nombre_indicateurs": nombre_indicateurs,
            "annee_debut": start_date.year,
            "annee_fin": end_date.year,
            "nombre_lignes_theorique": nombre_indicateurs * nombre_annees,
            "nombre_lignes": data_build.row_count,
            "valeurs_non_nulles": data_build.non_null_value_count,
            "indicateurs_sans_donnees": data_build.missing_indicator_codes,
            "duree_totale_secondes": round(duree_totale, 4),
            "duree_moyenne_par_indicateur": round(sum(durees_indicateurs) / len(durees_indicateurs), 4)
            if durees_indicateurs
            else 0,
            "duree_creation_csv_secondes": data_build.csv_duration_seconds,
            "duree_creation_json_secondes": data_build.json_duration_seconds,
            "etapes": etapes,
            "appels_banque_mondiale": data_build.indicator_measurements,
        },
    )


def _fetch_indicator_rows(
    *,
    session: requests.Session,
    source: Source,
    country: Country,
    indicator: Indicator,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    path = f"country/{country.wb_code}/indicator/{indicator.code}"
    params = {
        "format": "json",
        "per_page": WORLD_BANK_DATA_PAGE_SIZE,
        "date": f"{start_date.year}:{end_date.year}",
    }
    payload = _fetch_world_bank_paginated(session, path, params=params)
    rows: list[dict[str, Any]] = []
    for item in payload:
        rows.append(
            {
                "Pays": country.name,
                "Indicateur": indicator.name,
                "Date": str(item.get("date") or ""),
                "Valeur": item.get("value"),
                "Unite": indicator.unit or "",
                "Source": source.name,
                "id_pays": country.id,
                "code_pays": country.wb_code,
                "nom_pays": country.name,
                "id_indicateur": indicator.id,
                "code_indicateur": indicator.code,
                "nom_indicateur": indicator.name,
                "date": str(item.get("date") or ""),
                "valeur": item.get("value"),
            }
        )
    return rows


def _export_columns() -> list[str]:
    if EXPORT_SCHEMA_MODE == "technical_debug":
        return TECHNICAL_EXPORT_COLUMNS
    return PUBLIC_EXPORT_COLUMNS


def _project_export_rows(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    return [
        {column: row.get(column) for column in columns}
        for row in rows
    ]


def _fetch_world_bank_paginated(
    session: requests.Session,
    path: str,
    *,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    total_pages = 1

    while page <= total_pages:
        page_params = dict(params)
        page_params["page"] = page
        response = session.get(
            f"{WORLD_BANK_DATA_API_BASE}/{path.lstrip('/')}",
            params=page_params,
            timeout=(WORLD_BANK_DATA_CONNECT_TIMEOUT, WORLD_BANK_DATA_TIMEOUT),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or len(payload) < 2:
            raise DataBuildError(f"Reponse World Bank invalide pour {path}.")
        meta = payload[0] or {}
        items.extend(payload[1] or [])
        total_pages = int(meta.get("pages", 1) or 1)
        page += 1

    return items


def _rows_to_csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column) for column in columns})
    return buffer.getvalue()


def _build_export_url(slug: str, extension: str) -> str:
    signed_token = _build_export_token(slug)
    safe_slug = quote(slug.strip(), safe="")
    safe_token = quote(signed_token, safe="")
    return f"{PUBLIC_API_BASE_URL}/api/opendata/exports/{safe_slug}.{extension}?token={safe_token}"


def _build_export_id(slug: str) -> str:
    return f"{REMOTE_PROVIDER}:{slug}"


def _validate_export_token(slug: str, token: str | None) -> None:
    expected_token = _build_export_token(slug)
    if not token:
        raise ExportTokenError("Token d’export manquant.")
    if not hmac.compare_digest(str(token), expected_token):
        raise ExportTokenError("Token d’export invalide.")


def _build_export_token(slug: str) -> str:
    configured_token = _configured_export_token()
    return hmac.new(
        configured_token.encode("utf-8"),
        slug.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _configured_export_token() -> str:
    if not is_configured_secret(DATABRIDGE_EXPORT_TOKEN):
        raise ExportTokenConfigError("Configuration de sécurité incomplète.")
    return DATABRIDGE_EXPORT_TOKEN


def _manifest_get(manifest: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in manifest:
            return manifest[key]
    return default


def _derive_frequency(periodicities: list[str]) -> str:
    normalized = sorted({value.strip().lower() for value in periodicities if value and value.strip()})
    if not normalized:
        return "non precisee"
    if len(normalized) == 1:
        return normalized[0]
    return "mixte"


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{field_name} doit etre une date ISO valide (YYYY-MM-DD).") from exc


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _serialize_export_dataset(dataset: ExportDataset) -> dict[str, Any]:
    try:
        manifest = json.loads(dataset.build_json)
    except json.JSONDecodeError:
        manifest = {}
    indicators = [link.indicator for link in dataset.indicator_links if link.indicator is not None]
    return {
        "id": dataset.id,
        "version": dataset.latest_version,
        "export_version": f"v{dataset.latest_version}",
        "start_date": dataset.start_date,
        "end_date": dataset.end_date,
        "format": dataset.format,
        "frequency": dataset.frequency,
        "csv_url": dataset.csv_export_url,
        "json_url": dataset.json_export_url,
        "generated_at": dataset.updated_at,
        "country": dataset.country,
        "indicators": indicators,
        "manifest": manifest,
    }
