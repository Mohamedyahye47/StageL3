from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from core import db as core_db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = core_db.DB_PATH

BACKUP_PATH = PROJECT_ROOT / "databridge_backup_before_opendatasoft_cleanup.db"


EXPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS export_datasets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    source_id       INTEGER NOT NULL,
    topic_id        INTEGER NOT NULL,
    country_id      INTEGER NOT NULL,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    status          TEXT NOT NULL,
    provider        TEXT NOT NULL DEFAULT 'opendatasoft_url',
    csv_export_url  TEXT NOT NULL,
    json_export_url TEXT NOT NULL,
    latest_version  INTEGER NOT NULL DEFAULT 1,
    format          TEXT NOT NULL DEFAULT 'csv',
    frequency       TEXT NOT NULL DEFAULT 'non precisee',
    build_json      TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_id) REFERENCES sources(id),
    FOREIGN KEY (topic_id) REFERENCES topics(id),
    FOREIGN KEY (country_id) REFERENCES countries(id)
);

CREATE TABLE IF NOT EXISTS export_dataset_indicators (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    export_dataset_id INTEGER NOT NULL,
    indicator_id      INTEGER NOT NULL,
    FOREIGN KEY (export_dataset_id) REFERENCES export_datasets(id),
    FOREIGN KEY (indicator_id) REFERENCES indicators(id),
    UNIQUE (export_dataset_id, indicator_id)
);

CREATE TABLE IF NOT EXISTS export_logs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    export_dataset_id      INTEGER,
    action                TEXT NOT NULL,
    row_count             INTEGER,
    non_null_value_count  INTEGER,
    status                TEXT NOT NULL,
    error_message         TEXT,
    duration_seconds      REAL,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (export_dataset_id) REFERENCES export_datasets(id)
);

CREATE INDEX IF NOT EXISTS idx_export_datasets_updated_at
    ON export_datasets (updated_at);

CREATE INDEX IF NOT EXISTS idx_export_logs_dataset_created
    ON export_logs (export_dataset_id, created_at);
"""


LEGACY_TABLES = [
    "publish_logs",
    "published_dataset_version_indicators",
    "published_dataset_versions",
    "published_datasets",
]


def main() -> None:
    if core_db.DB_BACKEND == "turso":
        raise SystemExit(
            "Cette migration legacy supprime des tables et nécessite une sauvegarde locale. "
            "Elle n'est pas exécutable directement sur Turso."
        )
    if not DB_PATH.exists():
        raise SystemExit(f"Base introuvable : {DB_PATH}")

    _backup_database()
    with core_db.get_connection() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(EXPORT_SCHEMA)
        summary = _migrate_legacy_rows(conn)
        _drop_legacy_tables(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

    print("Migration Opendatasoft terminée.")
    print(f"Sauvegarde créée : {BACKUP_PATH}")
    print(f"Datasets migrés : {summary['datasets']}")
    print(f"Indicateurs liés migrés : {summary['indicators']}")
    print(f"Journaux migrés : {summary['logs']}")
    print("Anciennes tables published_*/publish_logs supprimées après sauvegarde.")


def _backup_database() -> None:
    if BACKUP_PATH.exists():
        timestamped = PROJECT_ROOT / f"databridge_backup_before_opendatasoft_cleanup_{_safe_timestamp()}.db"
        shutil.copy2(DB_PATH, timestamped)
        shutil.copy2(DB_PATH, BACKUP_PATH)
        print(f"Sauvegarde existante conservée aussi en copie horodatée : {timestamped}")
    else:
        shutil.copy2(DB_PATH, BACKUP_PATH)


def _safe_timestamp() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _migrate_legacy_rows(conn: Any) -> dict[str, int]:
    if not _table_exists(conn, "published_datasets"):
        return {"datasets": 0, "indicators": 0, "logs": 0}

    migrated_datasets = 0
    migrated_indicators = 0
    migrated_logs = 0

    rows = conn.execute(
        """
        SELECT
            d.*,
            v.id AS legacy_version_id,
            v.version AS legacy_version,
            v.country_id,
            v.start_date,
            v.end_date,
            v.format,
            v.frequency,
            v.build_json
        FROM published_datasets d
        LEFT JOIN published_dataset_versions v
            ON v.dataset_id = d.id
           AND v.version = d.latest_version
        ORDER BY d.id
        """
    ).fetchall()

    for row in rows:
        manifest = _load_json(row["build_json"])
        source_id = _first_existing_int(manifest, "source_ids", "ids_sources") or _lookup_default_source(conn)
        topic_id = _first_existing_int(manifest, "topic_ids", "ids_themes") or _lookup_default_topic(conn, source_id)
        country_id = row["country_id"] or _first_existing_int(manifest, "country_id", "id_pays")
        if not (source_id and topic_id and country_id):
            continue

        csv_url = _manifest_value(manifest, "csv_url", "url_donnees", "data_url") or ""
        json_url = _manifest_value(manifest, "json_url", "url_manifeste") or ""
        conn.execute(
            """
            INSERT OR IGNORE INTO export_datasets (
                slug, title, description, source_id, topic_id, country_id,
                start_date, end_date, status, provider, csv_export_url,
                json_export_url, latest_version, format, frequency, build_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["slug"],
                row["title"],
                row["description"],
                source_id,
                topic_id,
                country_id,
                row["start_date"] or _manifest_value(manifest, "start_date", "date_debut") or "1900-01-01",
                row["end_date"] or _manifest_value(manifest, "end_date", "date_fin") or "1900-01-01",
                row["status"] or "export_links_ready",
                "opendatasoft_url",
                csv_url,
                json_url,
                row["latest_version"] or row["legacy_version"] or 1,
                row["format"] or "csv",
                row["frequency"] or "non precisee",
                json.dumps(_clean_manifest(manifest), ensure_ascii=False),
                row["created_at"],
                row["updated_at"],
            ),
        )
        export_id = conn.execute(
            "SELECT id FROM export_datasets WHERE slug = ?",
            (row["slug"],),
        ).fetchone()["id"]
        migrated_datasets += 1

        indicator_ids = _manifest_list_int(manifest, "indicator_ids", "ids_indicateurs")
        if row["legacy_version_id"]:
            indicator_ids.extend(
                item["indicator_id"]
                for item in conn.execute(
                    """
                    SELECT indicator_id
                    FROM published_dataset_version_indicators
                    WHERE dataset_version_id = ?
                    """,
                    (row["legacy_version_id"],),
                ).fetchall()
            )

        for indicator_id in sorted(set(indicator_ids)):
            conn.execute(
                """
                INSERT OR IGNORE INTO export_dataset_indicators (export_dataset_id, indicator_id)
                VALUES (?, ?)
                """,
                (export_id, indicator_id),
            )
            migrated_indicators += 1

    if _table_exists(conn, "publish_logs"):
        for log in conn.execute("SELECT * FROM publish_logs ORDER BY id").fetchall():
            export_id = None
            if log["dataset_id"]:
                row = conn.execute(
                    """
                    SELECT e.id
                    FROM export_datasets e
                    JOIN published_datasets d ON d.slug = e.slug
                    WHERE d.id = ?
                    """,
                    (log["dataset_id"],),
                ).fetchone()
                export_id = row["id"] if row else None
            conn.execute(
                """
                INSERT INTO export_logs (
                    export_dataset_id, action, row_count, non_null_value_count,
                    status, error_message, duration_seconds, created_at
                )
                VALUES (?, ?, NULL, NULL, ?, ?, NULL, ?)
                """,
                (
                    export_id,
                    "generation_liens",
                    log["status"],
                    log["message"],
                    log["created_at"],
                ),
            )
            migrated_logs += 1

    return {"datasets": migrated_datasets, "indicators": migrated_indicators, "logs": migrated_logs}


def _drop_legacy_tables(conn: Any) -> None:
    for table in LEGACY_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")


def _table_exists(conn: Any, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _load_json(raw_value: str | None) -> dict[str, Any]:
    if not raw_value:
        return {}
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _clean_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    legacy_keys = {
        "remote_provider",
        "remote_id",
        "remote_url",
        "manifest_url",
        "fournisseur_distant",
        "identifiant_distant",
        "url_distante",
        "url_manifeste",
    }
    return {key: value for key, value in manifest.items() if key not in legacy_keys}


def _manifest_value(manifest: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in manifest:
            return manifest[key]
    return None


def _manifest_list_int(manifest: dict[str, Any], *keys: str) -> list[int]:
    for key in keys:
        value = manifest.get(key)
        if isinstance(value, list):
            result: list[int] = []
            for item in value:
                try:
                    result.append(int(item))
                except (TypeError, ValueError):
                    continue
            return result
    return []


def _first_existing_int(manifest: dict[str, Any], *keys: str) -> int | None:
    values = _manifest_list_int(manifest, *keys)
    return values[0] if values else None


def _lookup_default_source(conn: Any) -> int | None:
    row = conn.execute("SELECT id FROM sources WHERE code = 'WB' LIMIT 1").fetchone()
    return row["id"] if row else None


def _lookup_default_topic(conn: Any, source_id: int | None) -> int | None:
    if not source_id:
        return None
    row = conn.execute(
        "SELECT id FROM topics WHERE source_id = ? ORDER BY name LIMIT 1",
        (source_id,),
    ).fetchone()
    return row["id"] if row else None


if __name__ == "__main__":
    main()
