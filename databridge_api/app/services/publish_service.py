from __future__ import annotations

import csv
import io
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete
import requests
from requests.adapters import HTTPAdapter
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.exc import IntegrityError
from urllib3.util.retry import Retry

from app.config import HF_DATASET_VISIBILITY, REMOTE_PROVIDER
from app.models import (
    Country,
    Indicator,
    IndicatorTopic,
    PublishLog,
    PublishedDataset,
    PublishedDatasetVersion,
    PublishedDatasetVersionIndicator,
    Source,
    Topic,
)
from app.schemas import PublishDatasetIn
from app.services.hf_service import (
    build_repo_id,
    delete_remote_dataset,
    ensure_dataset_repo,
    get_hf_api,
    get_manifest_url,
    get_remote_dataset_url,
    get_repo_file_url,
    list_remote_datasets,
    upload_dataset_artifacts,
)


WORLD_BANK_DATA_API_BASE = os.getenv("WB_API_BASE", "https://api.worldbank.org/v2").rstrip("/")
WORLD_BANK_DATA_TIMEOUT = int(os.getenv("WB_DATA_TIMEOUT", os.getenv("WB_API_TIMEOUT", "60")))
WORLD_BANK_DATA_CONNECT_TIMEOUT = int(os.getenv("WB_DATA_CONNECT_TIMEOUT", os.getenv("WB_API_CONNECT_TIMEOUT", "15")))
WORLD_BANK_DATA_PAGE_SIZE = max(200, int(os.getenv("WB_DATA_PAGE_SIZE", "2000")))


class PublishError(Exception):
    status_code = 400


class ValidationError(PublishError):
    status_code = 400


class RemotePublishError(PublishError):
    status_code = 502


class DataBuildError(PublishError):
    status_code = 502


@dataclass
class PublishContext:
    source: Source
    country: Country
    indicators: list[Indicator]
    topics: list[Topic]
    dataset: PublishedDataset | None
    slug: str
    version: int
    remote_version: str


@dataclass
class DatasetDataBuild:
    csv_text: str
    json_text: str
    row_count: int
    non_null_value_count: int
    missing_indicator_codes: list[str]
    columns: list[str]


def publish_dataset(db: Session, payload: PublishDatasetIn) -> dict[str, Any]:
    title = payload.title.strip()
    description = payload.description.strip()
    if not title:
        raise ValidationError("Le titre est obligatoire.")
    if not description:
        raise ValidationError("La description est obligatoire.")
    if not payload.indicator_ids:
        raise ValidationError("Au moins un indicateur doit etre selectionne.")

    start_date = _parse_date(payload.start_date, "start_date")
    end_date = _parse_date(payload.end_date, "end_date")
    if start_date > end_date:
        raise ValidationError("La date de debut doit etre inferieure ou egale a la date de fin.")

    context = _load_publish_context(db, payload)
    now = _utc_now()
    data_build = _build_dataset_data(
        context=context,
        start_date=start_date,
        end_date=end_date,
    )

    manifest = _build_manifest(
        payload=payload,
        context=context,
        title=title,
        description=description,
        created_at=now,
        published_at=now,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        data_build=data_build,
    )
    manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2)
    root_readme = _build_root_readme(manifest, context)
    version_readme = _build_version_readme(manifest, context)
    repo_id = build_repo_id(context.slug)

    artifacts = {
        "README.md": root_readme,
        "manifest.json": manifest_json,
        "data/latest.csv": data_build.csv_text,
        "data/latest.json": data_build.json_text,
        f"versions/{context.remote_version}/README.md": version_readme,
        f"versions/{context.remote_version}/manifest.json": manifest_json,
        f"versions/{context.remote_version}/data.csv": data_build.csv_text,
        f"versions/{context.remote_version}/data.json": data_build.json_text,
    }

    try:
        api = get_hf_api()
        ensure_dataset_repo(api, repo_id, HF_DATASET_VISIBILITY)
        upload_dataset_artifacts(
            api,
            repo_id,
            artifacts,
            commit_message=f"Publication {context.remote_version} depuis Richat DataBridge",
        )
    except Exception as exc:
        _log_failure(
            db,
            message=f"Echec de publication Hugging Face: {exc}",
            remote_id=repo_id,
            remote_version=context.remote_version,
            dataset_id=context.dataset.id if context.dataset else None,
        )
        raise RemotePublishError(f"Echec de publication vers Hugging Face: {exc}") from exc

    dataset = context.dataset
    if dataset is None:
        dataset = PublishedDataset(
            slug=context.slug,
            title=title,
            description=description,
            remote_provider=REMOTE_PROVIDER,
            remote_id=repo_id,
            visibility=HF_DATASET_VISIBILITY,
            status="published",
            latest_version=context.version,
            created_at=now,
            published_at=now,
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
        dataset.visibility = HF_DATASET_VISIBILITY
        dataset.status = "published"
        dataset.published_at = now
        dataset.updated_at = now
        db.flush()

    version = PublishedDatasetVersion(
        dataset_id=dataset.id,
        version=context.version,
        remote_version=context.remote_version,
        country_id=context.country.id,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        format=manifest["format"],
        frequency=_manifest_get(manifest, "frequence", "frequency"),
        manifest_url=_manifest_get(manifest, "url_manifeste", "manifest_url"),
        build_json=manifest_json,
        created_at=now,
        published_at=now,
    )
    db.add(version)
    db.flush()

    for indicator in context.indicators:
        db.add(
            PublishedDatasetVersionIndicator(
                dataset_version_id=version.id,
                indicator_id=indicator.id,
            )
        )

    db.add(
        PublishLog(
            dataset_id=dataset.id,
            version_id=version.id,
            remote_provider=REMOTE_PROVIDER,
            remote_id=repo_id,
            remote_version=context.remote_version,
            status="success",
            message=f"Publication {context.remote_version} reussie.",
            created_at=now,
        )
    )
    db.commit()

    return {
        "dataset_id": dataset.id,
        "version_id": version.id,
        "slug": dataset.slug,
        "title": dataset.title,
        "description": dataset.description,
        "version": version.version,
        "remote_version": version.remote_version,
        "remote_id": dataset.remote_id,
        "remote_url": get_remote_dataset_url(dataset.remote_id),
        "manifest_url": version.manifest_url,
        "published_at": version.published_at,
        "country": context.country,
        "indicators": context.indicators,
        "manifest": manifest,
    }



def preview_dataset(db: Session, payload: PublishDatasetIn, *, limit: int = 50) -> dict[str, Any]:
    """
    Builds the real dataset rows for preview only.

    It validates the same payload used for publication.
    It uses the same data-building logic as publish_dataset().
    It does NOT upload to Hugging Face.
    It does NOT write publication metadata.
    """

    title = payload.title.strip()
    description = payload.description.strip()

    if not title:
        raise ValidationError("Le titre est obligatoire.")
    if not description:
        raise ValidationError("La description est obligatoire.")
    if not payload.indicator_ids:
        raise ValidationError("Au moins un indicateur doit etre selectionne.")

    start_date = _parse_date(payload.start_date, "start_date")
    end_date = _parse_date(payload.end_date, "end_date")

    if start_date > end_date:
        raise ValidationError("La date de debut doit etre inferieure ou egale a la date de fin.")

    context = _load_publish_context(db, payload)

    data_build = _build_dataset_data(
        context=context,
        start_date=start_date,
        end_date=end_date,
    )

    try:
        all_rows = json.loads(data_build.json_text)
    except json.JSONDecodeError as exc:
        raise DataBuildError("Impossible de lire les donnees generees pour l'apercu.") from exc

    preview_rows = all_rows[:limit]

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
        select(PublishedDataset.id).where(
            or_(
                PublishedDataset.slug == candidate,
                PublishedDataset.remote_id == build_repo_id(candidate),
            )
        )
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

    try:
       sync_datasets_with_huggingface(db)
    except PublishError:
       pass

    datasets = db.scalars(
        select(PublishedDataset).order_by(PublishedDataset.published_at.desc())
    ).all()
    rows: list[dict[str, Any]] = []
    for dataset in datasets:
        version = _get_version_by_number(db, dataset.id, dataset.latest_version)
        if version is None:
            continue
        last_status = db.execute(
            select(PublishLog.status)
            .where(PublishLog.dataset_id == dataset.id)
            .order_by(PublishLog.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        rows.append(
            {
                "id": dataset.id,
                "slug": dataset.slug,
                "title": dataset.title,
                "description": dataset.description,
                "country": version.country,
                "latest_version": dataset.latest_version,
                "published_at": dataset.published_at,
                "remote_provider": dataset.remote_provider,
                "remote_id": dataset.remote_id,
                "remote_url": get_remote_dataset_url(dataset.remote_id),
                "last_publish_status": last_status,
            }
        )
    return rows

def _delete_local_dataset_records(db: Session, dataset: PublishedDataset) -> None:
    """
    Deletes a published dataset mirror from local SQLite.

    We delete manually because the current SQLAlchemy relationships do not define
    cascade delete behavior.
    """

    versions = db.scalars(
        select(PublishedDatasetVersion).where(
            PublishedDatasetVersion.dataset_id == dataset.id
        )
    ).all()

    version_ids = [version.id for version in versions]

    if version_ids:
        db.execute(
            delete(PublishedDatasetVersionIndicator).where(
                PublishedDatasetVersionIndicator.dataset_version_id.in_(version_ids)
            )
        )

        db.execute(
            delete(PublishLog).where(
                PublishLog.version_id.in_(version_ids)
            )
        )

    db.execute(
        delete(PublishLog).where(
            PublishLog.dataset_id == dataset.id
        )
    )

    db.execute(
        delete(PublishedDatasetVersion).where(
            PublishedDatasetVersion.dataset_id == dataset.id
        )
    )

    db.delete(dataset)


def sync_datasets_with_huggingface(db: Session) -> dict[str, Any]:
    """
    Synchronizes local published dataset mirror with Hugging Face.

    If a dataset exists locally but its repo no longer exists on Hugging Face,
    the local mirror is removed.
    """

    try:
        remote_ids = set(list_remote_datasets())
    except Exception as exc:
        raise RemotePublishError(
            f"Impossible de synchroniser avec Hugging Face: {exc}"
        ) from exc

    local_datasets = db.scalars(select(PublishedDataset)).all()

    removed: list[str] = []
    kept: list[str] = []

    for dataset in local_datasets:
        if dataset.remote_id not in remote_ids:
            removed.append(dataset.slug)
            _delete_local_dataset_records(db, dataset)
        else:
            kept.append(dataset.slug)

    db.commit()

    return {
        "removed_count": len(removed),
        "kept_count": len(kept),
        "removed": removed,
        "kept": kept,
    }


def delete_published_dataset(db: Session, slug: str) -> dict[str, Any]:
    """
    Deletes a dataset from Hugging Face and removes its local mirror.
    """

    dataset = db.scalar(
        select(PublishedDataset).where(PublishedDataset.slug == slug)
    )

    if dataset is None:
        raise ValidationError(f"Dataset '{slug}' introuvable.")

    remote_id = dataset.remote_id

    try:
        delete_remote_dataset(remote_id)
    except Exception as exc:
        raise RemotePublishError(
            f"Impossible de supprimer le dataset sur Hugging Face: {exc}"
        ) from exc

    _delete_local_dataset_records(db, dataset)
    db.commit()

    return {
        "slug": slug,
        "remote_id": remote_id,
        "deleted": True,
    }


def get_dataset_detail(db: Session, slug: str) -> dict[str, Any] | None:
    dataset = db.scalar(select(PublishedDataset).where(PublishedDataset.slug == slug))
    if dataset is None:
        return None

    versions = list_dataset_versions(db, slug)
    if not versions:
        return None

    latest_version = next(
        (version for version in versions if version["version"] == dataset.latest_version),
        versions[0],
    )
    return {
        "id": dataset.id,
        "slug": dataset.slug,
        "title": dataset.title,
        "description": dataset.description,
        "remote_provider": dataset.remote_provider,
        "remote_id": dataset.remote_id,
        "remote_url": get_remote_dataset_url(dataset.remote_id),
        "visibility": dataset.visibility,
        "status": dataset.status,
        "latest_version": dataset.latest_version,
        "created_at": dataset.created_at,
        "published_at": dataset.published_at,
        "updated_at": dataset.updated_at,
        "latest_version_detail": latest_version,
        "versions": versions,
    }


def list_dataset_versions(db: Session, slug: str) -> list[dict[str, Any]]:
    dataset = db.scalar(select(PublishedDataset).where(PublishedDataset.slug == slug))
    if dataset is None:
        return []

    versions = db.scalars(
        select(PublishedDatasetVersion)
        .options(
            selectinload(PublishedDatasetVersion.country),
            selectinload(PublishedDatasetVersion.indicator_links)
            .selectinload(PublishedDatasetVersionIndicator.indicator)
            .selectinload(Indicator.topic_links),
        )
        .where(PublishedDatasetVersion.dataset_id == dataset.id)
        .order_by(PublishedDatasetVersion.version.desc())
    ).all()
    return [_serialize_version(version) for version in versions]


def get_dataset_version_data_preview(
    db: Session,
    slug: str,
    version_number: int,
    *,
    limit: int = 25,
) -> dict[str, Any] | None:
    dataset = db.scalar(select(PublishedDataset).where(PublishedDataset.slug == slug))
    if dataset is None:
        return None

    version = db.scalar(
        select(PublishedDatasetVersion).where(
            PublishedDatasetVersion.dataset_id == dataset.id,
            PublishedDatasetVersion.version == version_number,
        )
    )
    if version is None:
        return None

    manifest = json.loads(version.build_json)
    data_url = _extract_version_data_url(manifest)
    if not data_url:
        raise RemotePublishError("Aucun fichier de donnees n'est reference pour cette version.")
    try:
        return _fetch_remote_csv_preview(data_url, limit=limit)
    except requests.RequestException as exc:
        raise RemotePublishError(_format_remote_preview_error(exc)) from exc


def _load_publish_context(db: Session, payload: PublishDatasetIn) -> PublishContext:
    source = db.scalar(select(Source).where(Source.code == payload.source_code.upper()))
    if source is None:
        raise ValidationError(f"Source '{payload.source_code}' introuvable.")

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

    topic_map: dict[int, Topic] = {}
    for indicator in ordered_indicators:
        for link in indicator.topic_links:
            if link.topic is not None:
                topic_map[link.topic.id] = link.topic

    dataset = None
    slug = payload.existing_slug.strip() if payload.existing_slug else None
    if slug:
        dataset = db.scalar(select(PublishedDataset).where(PublishedDataset.slug == slug))
        if dataset is None:
            raise ValidationError(f"Dataset '{slug}' introuvable pour creer une nouvelle version.")
        version_number = dataset.latest_version + 1
    else:
        slug = generate_unique_slug(db, title=payload.title)
        version_number = 1

    return PublishContext(
        source=source,
        country=country,
        indicators=ordered_indicators,
        topics=sorted(topic_map.values(), key=lambda topic: topic.name.lower()),
        dataset=dataset,
        slug=slug,
        version=version_number,
        remote_version=f"v{version_number}",
    )


def _build_manifest(
    *,
    payload: PublishDatasetIn,
    context: PublishContext,
    title: str,
    description: str,
    created_at: str,
    published_at: str,
    start_date: str,
    end_date: str,
    data_build: DatasetDataBuild,
) -> dict[str, Any]:
    repo_id = build_repo_id(context.slug)
    indicator_codes = [indicator.code for indicator in context.indicators]
    source_ids = sorted({indicator.source_id for indicator in context.indicators})
    source_codes = sorted({context.source.code for _ in context.indicators})
    topic_ids = sorted({topic.id for topic in context.topics})
    periodicities = [indicator.periodicity for indicator in context.indicators if indicator.periodicity]
    frequency = payload.frequency or _derive_frequency(periodicities)
    csv_path = f"versions/{context.remote_version}/data.csv"
    json_path = f"versions/{context.remote_version}/data.json"

    return {
        "titre": title,
        "description": description,
        "slug": context.slug,
        "fournisseur_distant": REMOTE_PROVIDER,
        "identifiant_distant": repo_id,
        "url_distante": get_remote_dataset_url(repo_id),
        "url_manifeste": get_manifest_url(repo_id, context.remote_version),
        "version": context.version,
        "version_distante": context.remote_version,
        "id_pays": context.country.id,
        "code_pays": context.country.wb_code,
        "code_pays_iso3": context.country.code_iso3,
        "nom_pays": context.country.name,
        "ids_sources": source_ids,
        "codes_sources": source_codes,
        "ids_themes": topic_ids,
        "ids_indicateurs": [indicator.id for indicator in context.indicators],
        "codes_indicateurs": indicator_codes,
        "date_debut": start_date,
        "date_fin": end_date,
        "format": payload.format or "csv",
        "frequence": frequency,
        "nombre_lignes": data_build.row_count,
        "nombre_valeurs_non_nulles": data_build.non_null_value_count,
        "codes_indicateurs_manquants": data_build.missing_indicator_codes,
        "colonnes_donnees": data_build.columns,
        "fichiers_donnees": [
            {
                "type": "csv",
                "chemin": csv_path,
                "url": get_repo_file_url(repo_id, csv_path),
            },
            {
                "type": "json",
                "chemin": json_path,
                "url": get_repo_file_url(repo_id, json_path),
            },
        ],
        "url_donnees": get_repo_file_url(repo_id, csv_path),
        "cree_le": created_at,
        "publie_le": published_at,
    }


def _build_root_readme(manifest: dict[str, Any], context: PublishContext) -> str:
    topic_names = ", ".join(topic.name for topic in context.topics) or "Sans theme"
    indicator_lines = _format_indicator_lines(context.indicators)
    return f"""---
language:
- fr
pretty_name: {_manifest_get(manifest, "titre", "title")}
configs:
- config_name: default
  data_files:
  - split: train
    path: data/latest.csv
---

# {_manifest_get(manifest, "titre", "title")}

## Description
{_manifest_get(manifest, "description")}

## Resume
- Slug : `{_manifest_get(manifest, "slug")}`
- Version courante : `{_manifest_get(manifest, "version_distante", "remote_version")}`
- Pays : {context.country.name} (`{context.country.code_iso3}`)
- Source : {context.source.name}
- Themes : {topic_names}
- Periode : {_manifest_get(manifest, "date_debut", "start_date")} a {_manifest_get(manifest, "date_fin", "end_date")}
- Nombre d'indicateurs : {len(context.indicators)}
- Format : {_manifest_get(manifest, "format")}
- Frequence : {_manifest_get(manifest, "frequence", "frequency")}
- Lignes de donnees : {_manifest_get(manifest, "nombre_lignes", "rows_count")}
- Valeurs non nulles : {_manifest_get(manifest, "nombre_valeurs_non_nulles", "non_null_value_count")}

## Indicateurs
{indicator_lines}

## Fichiers de donnees
- CSV courant : `data/latest.csv`
- JSON courant : `data/latest.json`

## Note
Ce depot stocke les metadonnees de publication et les donnees reelles construites par Richat DataBridge.
"""


def _build_version_readme(manifest: dict[str, Any], context: PublishContext) -> str:
    return f"""# {_manifest_get(manifest, "titre", "title")} - {_manifest_get(manifest, "version_distante", "remote_version")}

## Version publiee
- Version : `{_manifest_get(manifest, "version_distante", "remote_version")}`
- Pays : {context.country.name} (`{context.country.wb_code}`)
- Periode : {_manifest_get(manifest, "date_debut", "start_date")} a {_manifest_get(manifest, "date_fin", "end_date")}
- Nombre d'indicateurs : {len(context.indicators)}
- Lignes de donnees : {_manifest_get(manifest, "nombre_lignes", "rows_count")}

## Description
{_manifest_get(manifest, "description")}

## Indicateurs
{_format_indicator_lines(context.indicators)}

## Donnees
- CSV : `versions/{_manifest_get(manifest, "version_distante", "remote_version")}/data.csv`
- JSON : `versions/{_manifest_get(manifest, "version_distante", "remote_version")}/data.json`

## Note
Ce dossier versionne contient les metadonnees et les donnees reelles publiees par Richat DataBridge.
"""


def _format_indicator_lines(indicators: list[Indicator], limit: int = 25) -> str:
    visible = indicators[:limit]
    lines = [f"- `{indicator.code}` - {indicator.name}" for indicator in visible]
    if len(indicators) > limit:
        lines.append(f"- ... et {len(indicators) - limit} autre(s) indicateur(s).")
    return "\n".join(lines) if lines else "- Aucun indicateur."


def _build_dataset_data(
    *,
    context: PublishContext,
    start_date: date,
    end_date: date,
) -> DatasetDataBuild:
    session = _build_world_bank_session()
    rows: list[dict[str, Any]] = []
    missing_indicator_codes: list[str] = []

    for indicator in context.indicators:
        indicator_rows = _fetch_indicator_rows(
            session=session,
            country=context.country,
            indicator=indicator,
            start_date=start_date,
            end_date=end_date,
        )
        if not indicator_rows:
            missing_indicator_codes.append(indicator.code)
        rows.extend(indicator_rows)

    if not rows:
        raise DataBuildError(
            "Aucune donnee reelle n'a ete trouvee pour cette combinaison pays, indicateurs et plage de dates."
        )

    columns = [
        "id_pays",
        "code_pays",
        "nom_pays",
        "id_indicateur",
        "code_indicateur",
        "nom_indicateur",
        "date",
        "valeur",
        "unite",
        "statut_observation",
        "decimales",
    ]
    csv_text = _rows_to_csv(rows, columns)
    json_text = json.dumps(rows, ensure_ascii=False, indent=2)
    non_null_value_count = sum(1 for row in rows if row["valeur"] is not None)
    return DatasetDataBuild(
        csv_text=csv_text,
        json_text=json_text,
        row_count=len(rows),
        non_null_value_count=non_null_value_count,
        missing_indicator_codes=missing_indicator_codes,
        columns=columns,
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
    session.headers.update({"User-Agent": "RichatDataBridge/2.0 publish"})
    return session


def _fetch_indicator_rows(
    *,
    session: requests.Session,
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
                "id_pays": country.id,
                "code_pays": country.wb_code,
                "nom_pays": country.name,
                "id_indicateur": indicator.id,
                "code_indicateur": indicator.code,
                "nom_indicateur": indicator.name,
                "date": str(item.get("date") or ""),
                "valeur": item.get("value"),
                "unite": indicator.unit,
                "statut_observation": item.get("obs_status") or None,
                "decimales": item.get("decimal"),
            }
        )
    return rows


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


def _extract_version_data_url(manifest: dict[str, Any]) -> str | None:
    data_url = manifest.get("url_donnees") or manifest.get("data_url")
    if isinstance(data_url, str) and data_url.strip():
        return data_url.strip()

    data_files = manifest.get("fichiers_donnees") or manifest.get("data_files")
    if not isinstance(data_files, list):
        return None

    for item in data_files:
        item_type = item.get("type") if isinstance(item, dict) else None
        if not item_type and isinstance(item, dict):
            item_type = item.get("kind")
        if isinstance(item, dict) and item_type == "csv" and item.get("url"):
            return str(item["url"]).strip()
    return None


def _manifest_get(manifest: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in manifest:
            return manifest[key]
    return default


def _fetch_remote_csv_preview(data_url: str, *, limit: int) -> dict[str, Any]:
    response = requests.get(
        data_url,
        timeout=(WORLD_BANK_DATA_CONNECT_TIMEOUT, WORLD_BANK_DATA_TIMEOUT),
        headers={"User-Agent": "RichatDataBridge/2.0 preview"},
    )
    response.raise_for_status()

    reader = csv.DictReader(io.StringIO(response.text))
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(reader):
        if index >= limit:
            break
        rows.append(dict(row))

    return {
        "data_url": data_url,
        "columns": list(reader.fieldnames or []),
        "rows": rows,
        "preview_count": len(rows),
    }


def _format_remote_preview_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "getaddrinfo failed" in lowered:
        return "Impossible de charger l'apercu : le serveur distant est introuvable pour le moment."
    if "timed out" in lowered or "timeout" in lowered:
        return "Impossible de charger l'apercu : le serveur distant a mis trop de temps a repondre."
    return f"Impossible de charger l'apercu distant : {message}"


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


def _serialize_version(version: PublishedDatasetVersion) -> dict[str, Any]:
    manifest = json.loads(version.build_json)
    indicators = [link.indicator for link in version.indicator_links if link.indicator is not None]
    return {
        "id": version.id,
        "version": version.version,
        "remote_version": version.remote_version,
        "start_date": version.start_date,
        "end_date": version.end_date,
        "format": version.format,
        "frequency": version.frequency,
        "manifest_url": version.manifest_url,
        "published_at": version.published_at,
        "country": version.country,
        "indicators": indicators,
        "manifest": manifest,
    }


def _get_version_by_number(db: Session, dataset_id: int, version_number: int) -> PublishedDatasetVersion | None:
    return db.scalar(
        select(PublishedDatasetVersion)
        .options(selectinload(PublishedDatasetVersion.country))
        .where(
            PublishedDatasetVersion.dataset_id == dataset_id,
            PublishedDatasetVersion.version == version_number,
        )
    )


def _log_failure(
    db: Session,
    *,
    message: str,
    remote_id: str | None,
    remote_version: str | None,
    dataset_id: int | None = None,
) -> None:
    db.rollback()
    db.add(
        PublishLog(
            dataset_id=dataset_id,
            version_id=None,
            remote_provider=REMOTE_PROVIDER,
            remote_id=remote_id,
            remote_version=remote_version,
            status="error",
            message=message,
            created_at=_utc_now(),
        )
    )
    db.commit()