from __future__ import annotations

import sqlite3


TARGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    base_url    TEXT,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_topics_source_name
    ON topics (source_id, name);

CREATE TABLE IF NOT EXISTS indicators (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER NOT NULL,
    code         TEXT NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT,
    unit         TEXT,
    periodicity  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_id) REFERENCES sources(id),
    UNIQUE (source_id, code)
);

CREATE INDEX IF NOT EXISTS idx_indicators_source_code
    ON indicators (source_id, code);

CREATE INDEX IF NOT EXISTS idx_indicators_name
    ON indicators (name);

CREATE TABLE IF NOT EXISTS indicator_topics (
    indicator_id INTEGER NOT NULL,
    topic_id     INTEGER NOT NULL,
    PRIMARY KEY (indicator_id, topic_id),
    FOREIGN KEY (indicator_id) REFERENCES indicators(id),
    FOREIGN KEY (topic_id) REFERENCES topics(id)
);

CREATE TABLE IF NOT EXISTS countries (
    id         INTEGER PRIMARY KEY,
    code_iso3  TEXT NOT NULL UNIQUE,
    code_iso2  TEXT NOT NULL UNIQUE,
    wb_code    TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    region     TEXT,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_countries_enabled_name
    ON countries (enabled, name);

CREATE TABLE IF NOT EXISTS published_datasets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    remote_provider TEXT NOT NULL,
    remote_id       TEXT NOT NULL UNIQUE,
    visibility      TEXT NOT NULL,
    status          TEXT NOT NULL,
    latest_version  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    published_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS published_dataset_versions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id     INTEGER NOT NULL,
    version        INTEGER NOT NULL,
    remote_version TEXT NOT NULL,
    country_id     INTEGER NOT NULL,
    start_date     TEXT NOT NULL,
    end_date       TEXT NOT NULL,
    format         TEXT NOT NULL,
    frequency      TEXT NOT NULL,
    manifest_url   TEXT NOT NULL,
    build_json     TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    published_at   TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (dataset_id) REFERENCES published_datasets(id),
    FOREIGN KEY (country_id) REFERENCES countries(id),
    UNIQUE (dataset_id, version)
);

CREATE TABLE IF NOT EXISTS published_dataset_version_indicators (
    dataset_version_id INTEGER NOT NULL,
    indicator_id       INTEGER NOT NULL,
    PRIMARY KEY (dataset_version_id, indicator_id),
    FOREIGN KEY (dataset_version_id) REFERENCES published_dataset_versions(id),
    FOREIGN KEY (indicator_id) REFERENCES indicators(id)
);

CREATE TABLE IF NOT EXISTS publish_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id     INTEGER,
    version_id     INTEGER,
    remote_provider TEXT NOT NULL,
    remote_id      TEXT,
    remote_version TEXT,
    status         TEXT NOT NULL,
    message        TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (dataset_id) REFERENCES published_datasets(id),
    FOREIGN KEY (version_id) REFERENCES published_dataset_versions(id)
);
"""

LOCAL_FIRST_TABLE_RENAMES = {
    "datasets": "legacy_datasets",
    "dataset_indicators": "legacy_dataset_indicators",
    "push_logs": "legacy_push_logs",
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    _drop_retired_tables(conn)
    _archive_local_first_tables(conn)
    _migrate_topics_table(conn)
    _migrate_indicators_table(conn)
    _migrate_indicator_topics_table(conn)
    conn.executescript(TARGET_SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")


def _drop_retired_tables(conn: sqlite3.Connection) -> None:
    # Authentication is intentionally disabled for now.
    conn.execute("DROP TABLE IF EXISTS api_keys")


def _archive_local_first_tables(conn: sqlite3.Connection) -> None:
    for old_name, new_name in LOCAL_FIRST_TABLE_RENAMES.items():
        if _table_exists(conn, old_name) and not _table_exists(conn, new_name):
            conn.execute(f"ALTER TABLE {old_name} RENAME TO {new_name}")


def _migrate_topics_table(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "topics"):
        return

    columns = set(_table_columns(conn, "topics"))
    if {"id", "source_id", "name", "description"}.issubset(columns):
        return

    if not _table_exists(conn, "topics_legacy"):
        conn.execute("ALTER TABLE topics RENAME TO topics_legacy")
    conn.executescript(TARGET_SCHEMA)

    legacy_columns = set(_table_columns(conn, "topics_legacy"))
    conn.execute(
        f"""
        INSERT OR IGNORE INTO topics (id, source_id, name, description)
        SELECT
            {_coalesce_expr(legacy_columns, "id", default="rowid")},
            {_coalesce_expr(legacy_columns, "source_id", default="1")},
            {_coalesce_expr(legacy_columns, "name", "name_fr", "name_en", "topic_key", default="'Sans nom'")},
            NULLIF({_coalesce_expr(legacy_columns, "description", "description_fr", "description_en", default="''")}, '')
        FROM topics_legacy
        """
    )


def _migrate_indicators_table(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "indicators"):
        return

    columns = set(_table_columns(conn, "indicators"))
    expected_columns = {
        "id",
        "source_id",
        "code",
        "name",
        "description",
        "unit",
        "periodicity",
        "created_at",
    }
    if columns == expected_columns:
        return

    if not _table_exists(conn, "indicators_legacy"):
        conn.execute("ALTER TABLE indicators RENAME TO indicators_legacy")
    conn.executescript(TARGET_SCHEMA)

    legacy_columns = set(_table_columns(conn, "indicators_legacy"))
    conn.execute(
        f"""
        INSERT OR IGNORE INTO indicators (
            id,
            source_id,
            code,
            name,
            description,
            unit,
            periodicity,
            created_at
        )
        SELECT
            {_coalesce_expr(legacy_columns, "id", default="rowid")},
            {_coalesce_expr(legacy_columns, "source_id", default="1")},
            {_coalesce_expr(legacy_columns, "code", default="'UNKNOWN'")},
            {_coalesce_expr(legacy_columns, "name", "label_fr", "label_en", "label", default="code")},
            NULLIF({_coalesce_expr(legacy_columns, "description", "description_fr", "description_en", "long_definition", "short_definition", "notes_from_source", default="''")}, ''),
            NULLIF({_coalesce_expr(legacy_columns, "unit", default="''")}, ''),
            NULLIF({_coalesce_expr(legacy_columns, "periodicity", default="''")}, ''),
            {_coalesce_expr(legacy_columns, "created_at", "last_updated", default="datetime('now')")}
        FROM indicators_legacy
        """
    )

    if "topic_id" in legacy_columns:
        conn.execute(
            """
            INSERT OR IGNORE INTO indicator_topics (indicator_id, topic_id)
            SELECT id, topic_id
            FROM indicators_legacy
            WHERE topic_id IS NOT NULL
            """
        )


def _migrate_indicator_topics_table(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "indicator_topics"):
        return

    columns = set(_table_columns(conn, "indicator_topics"))
    if columns == {"indicator_id", "topic_id"}:
        return

    if not _table_exists(conn, "indicator_topics_legacy"):
        conn.execute("ALTER TABLE indicator_topics RENAME TO indicator_topics_legacy")
    conn.executescript(TARGET_SCHEMA)

    legacy_columns = set(_table_columns(conn, "indicator_topics_legacy"))
    if {"indicator_id", "topic_id"}.issubset(legacy_columns):
        conn.execute(
            """
            INSERT OR IGNORE INTO indicator_topics (indicator_id, topic_id)
            SELECT indicator_id, topic_id
            FROM indicator_topics_legacy
            WHERE indicator_id IS NOT NULL AND topic_id IS NOT NULL
            """
        )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [row[1] for row in rows]


def _coalesce_expr(columns: set[str], *names: str, default: str) -> str:
    available = [name for name in names if name in columns]
    if not available:
        return default
    if len(available) == 1:
        return available[0]
    return f"COALESCE({', '.join(available)})"
