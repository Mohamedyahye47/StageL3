from __future__ import annotations

import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.migrations import ensure_schema

DB_PATH = Path(os.getenv("DB_PATH", str(PROJECT_ROOT / "databridge.db"))).resolve()

WB_SOURCE = {
    "code": "WB",
    "name": "Banque mondiale",
    "base_url": "https://api.worldbank.org/v2",
    "description": "Catalogue metadata des indicateurs de la Banque mondiale pour Richat DataBridge.",
}
WORLD_BANK_SOURCE = WB_SOURCE

FALLBACK_COUNTRIES = [
    {"code_iso3": "DZA", "code_iso2": "DZ", "wb_code": "DZA", "name": "Algerie", "region": "Afrique", "enabled": 1},
    {"code_iso3": "BEL", "code_iso2": "BE", "wb_code": "BEL", "name": "Belgique", "region": "Europe", "enabled": 1},
    {"code_iso3": "BRA", "code_iso2": "BR", "wb_code": "BRA", "name": "Bresil", "region": "Ameriques", "enabled": 1},
    {"code_iso3": "CAN", "code_iso2": "CA", "wb_code": "CAN", "name": "Canada", "region": "Ameriques", "enabled": 1},
    {"code_iso3": "CHN", "code_iso2": "CN", "wb_code": "CHN", "name": "Chine", "region": "Asie", "enabled": 1},
    {"code_iso3": "CIV", "code_iso2": "CI", "wb_code": "CIV", "name": "Cote d'Ivoire", "region": "Afrique", "enabled": 1},
    {"code_iso3": "DEU", "code_iso2": "DE", "wb_code": "DEU", "name": "Allemagne", "region": "Europe", "enabled": 1},
    {"code_iso3": "ESP", "code_iso2": "ES", "wb_code": "ESP", "name": "Espagne", "region": "Europe", "enabled": 1},
    {"code_iso3": "FRA", "code_iso2": "FR", "wb_code": "FRA", "name": "France", "region": "Europe", "enabled": 1},
    {"code_iso3": "GBR", "code_iso2": "GB", "wb_code": "GBR", "name": "Royaume-Uni", "region": "Europe", "enabled": 1},
    {"code_iso3": "IND", "code_iso2": "IN", "wb_code": "IND", "name": "Inde", "region": "Asie", "enabled": 1},
    {"code_iso3": "ITA", "code_iso2": "IT", "wb_code": "ITA", "name": "Italie", "region": "Europe", "enabled": 1},
    {"code_iso3": "JPN", "code_iso2": "JP", "wb_code": "JPN", "name": "Japon", "region": "Asie", "enabled": 1},
    {"code_iso3": "KEN", "code_iso2": "KE", "wb_code": "KEN", "name": "Kenya", "region": "Afrique", "enabled": 1},
    {"code_iso3": "MAR", "code_iso2": "MA", "wb_code": "MAR", "name": "Maroc", "region": "Afrique", "enabled": 1},
    {"code_iso3": "MLI", "code_iso2": "ML", "wb_code": "MLI", "name": "Mali", "region": "Afrique", "enabled": 1},
    {"code_iso3": "MRT", "code_iso2": "MR", "wb_code": "MRT", "name": "Mauritanie", "region": "Afrique", "enabled": 1},
    {"code_iso3": "NER", "code_iso2": "NE", "wb_code": "NER", "name": "Niger", "region": "Afrique", "enabled": 1},
    {"code_iso3": "NGA", "code_iso2": "NG", "wb_code": "NGA", "name": "Nigeria", "region": "Afrique", "enabled": 1},
    {"code_iso3": "SEN", "code_iso2": "SN", "wb_code": "SEN", "name": "Senegal", "region": "Afrique", "enabled": 1},
    {"code_iso3": "TUN", "code_iso2": "TN", "wb_code": "TUN", "name": "Tunisie", "region": "Afrique", "enabled": 1},
    {"code_iso3": "USA", "code_iso2": "US", "wb_code": "USA", "name": "Etats-Unis", "region": "Ameriques", "enabled": 1},
    {"code_iso3": "ZAF", "code_iso2": "ZA", "wb_code": "ZAF", "name": "Afrique du Sud", "region": "Afrique", "enabled": 1},
]


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        ensure_schema(conn)
        seed_countries(conn)
        conn.commit()


def seed_metadata(force: bool = False, limit: int | None = None) -> dict[str, int]:
    from core import wb_metadata

    return wb_metadata.sync_world_bank_metadata(force=force, limit=limit)


def register_source(
    code: str,
    name: str,
    base_url: str | None = None,
    description: str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> int:
    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO sources (code, name, base_url, description)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                base_url = excluded.base_url,
                description = excluded.description
            """,
            (code, name, base_url, description),
        )
        row = conn.execute(
            "SELECT id FROM sources WHERE code = ?",
            (code,),
        ).fetchone()
        if owns_connection:
            conn.commit()
        return int(row["id"])
    finally:
        if owns_connection:
            conn.close()


def get_source_id(source_code: str, *, conn: sqlite3.Connection | None = None) -> int | None:
    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM sources WHERE code = ?",
            (source_code,),
        ).fetchone()
        return int(row["id"]) if row else None
    finally:
        if owns_connection:
            conn.close()


def source_has_metadata(source_id: int, *, conn: sqlite3.Connection | None = None) -> bool:
    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM indicators WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        return bool(row and row["count"] > 0)
    finally:
        if owns_connection:
            conn.close()


def upsert_topic(payload: dict[str, Any], *, conn: sqlite3.Connection | None = None) -> int:
    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO topics (id, source_id, name, description)
            VALUES (:id, :source_id, :name, :description)
            ON CONFLICT(id) DO UPDATE SET
                source_id = excluded.source_id,
                name = excluded.name,
                description = excluded.description
            """,
            {
                "id": payload["id"],
                "source_id": payload["source_id"],
                "name": payload["name"],
                "description": payload.get("description"),
            },
        )
        if owns_connection:
            conn.commit()
        return int(payload["id"])
    finally:
        if owns_connection:
            conn.close()


def upsert_indicator(payload: dict[str, Any], *, conn: sqlite3.Connection | None = None) -> int:
    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        indicator_id = _resolve_indicator_id(payload, conn)
        values = {
            "source_id": payload["source_id"],
            "code": payload["code"],
            "name": payload["name"],
            "description": payload.get("description"),
            "unit": payload.get("unit"),
            "periodicity": payload.get("periodicity"),
            "created_at": payload.get("created_at") or _utc_now(),
        }

        if indicator_id is None:
            cursor = conn.execute(
                """
                INSERT INTO indicators (
                    source_id,
                    code,
                    name,
                    description,
                    unit,
                    periodicity,
                    created_at
                )
                VALUES (
                    :source_id,
                    :code,
                    :name,
                    :description,
                    :unit,
                    :periodicity,
                    :created_at
                )
                """,
                values,
            )
            indicator_id = int(cursor.lastrowid)
        else:
            values["id"] = indicator_id
            conn.execute(
                """
                UPDATE indicators
                SET
                    source_id = :source_id,
                    code = :code,
                    name = :name,
                    description = :description,
                    unit = :unit,
                    periodicity = :periodicity,
                    created_at = :created_at
                WHERE id = :id
                """,
                values,
            )

        if owns_connection:
            conn.commit()
        return indicator_id
    finally:
        if owns_connection:
            conn.close()


def replace_indicator_topics(
    indicator_id: int,
    topic_ids: list[int],
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM indicator_topics WHERE indicator_id = ?",
            (indicator_id,),
        )
        for topic_id in sorted(set(topic_ids)):
            conn.execute(
                """
                INSERT OR IGNORE INTO indicator_topics (indicator_id, topic_id)
                VALUES (?, ?)
                """,
                (indicator_id, topic_id),
            )
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()


def seed_countries(conn: sqlite3.Connection | None = None) -> None:
    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        for payload in _build_country_seed_rows():
            upsert_country(payload, conn=conn)
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()


def upsert_country(payload: dict[str, Any], *, conn: sqlite3.Connection | None = None) -> int:
    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        existing = conn.execute(
            """
            SELECT id
            FROM countries
            WHERE code_iso3 = :code_iso3 OR wb_code = :wb_code OR code_iso2 = :code_iso2
            LIMIT 1
            """,
            {
                "code_iso3": payload["code_iso3"],
                "wb_code": payload["wb_code"],
                "code_iso2": payload["code_iso2"],
            },
        ).fetchone()

        values = {
            "code_iso3": payload["code_iso3"],
            "code_iso2": payload["code_iso2"],
            "wb_code": payload["wb_code"],
            "name": payload["name"],
            "region": payload.get("region"),
            "enabled": int(payload.get("enabled", 1)),
        }

        if existing:
            values["id"] = int(existing["id"])
            conn.execute(
                """
                UPDATE countries
                SET
                    code_iso3 = :code_iso3,
                    code_iso2 = :code_iso2,
                    wb_code = :wb_code,
                    name = :name,
                    region = :region,
                    enabled = :enabled
                WHERE id = :id
                """,
                values,
            )
            country_id = int(existing["id"])
        else:
            cursor = conn.execute(
                """
                INSERT INTO countries (
                    code_iso3,
                    code_iso2,
                    wb_code,
                    name,
                    region,
                    enabled
                )
                VALUES (
                    :code_iso3,
                    :code_iso2,
                    :wb_code,
                    :name,
                    :region,
                    :enabled
                )
                """,
                values,
            )
            country_id = int(cursor.lastrowid)

        if owns_connection:
            conn.commit()
        return country_id
    finally:
        if owns_connection:
            conn.close()


def metadata_summary(
    conn: sqlite3.Connection | None = None,
    source_id: int | None = None,
) -> dict[str, int]:
    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        params: tuple[Any, ...] = ()
        where = ""
        if source_id is not None:
            where = "WHERE source_id = ?"
            params = (source_id,)
        topics = conn.execute(
            f"SELECT COUNT(*) AS count FROM topics {where}",
            params,
        ).fetchone()["count"]
        indicators = conn.execute(
            f"SELECT COUNT(*) AS count FROM indicators {where}",
            params,
        ).fetchone()["count"]
        countries = conn.execute(
            "SELECT COUNT(*) AS count FROM countries WHERE enabled = 1"
        ).fetchone()["count"]
        return {
            "topics": int(topics),
            "indicators": int(indicators),
            "countries": int(countries),
        }
    finally:
        if owns_connection:
            conn.close()


def _resolve_indicator_id(payload: dict[str, Any], conn: sqlite3.Connection) -> int | None:
    indicator_id = payload.get("id")
    if indicator_id is not None:
        row = conn.execute(
            "SELECT id FROM indicators WHERE id = ?",
            (indicator_id,),
        ).fetchone()
        if row:
            return int(row["id"])

    row = conn.execute(
        """
        SELECT id
        FROM indicators
        WHERE source_id = ? AND code = ?
        """,
        (payload["source_id"], payload["code"]),
    ).fetchone()
    return int(row["id"]) if row else None


def _build_country_seed_rows() -> list[dict[str, Any]]:
    try:
        import pycountry
    except ImportError:
        return [dict(country) for country in FALLBACK_COUNTRIES]

    territory_names: dict[str, str] = {}
    try:
        from babel import Locale

        territory_names = dict(Locale.parse("fr").territories)
    except Exception:
        territory_names = {}

    rows: list[dict[str, Any]] = []
    for country in sorted(pycountry.countries, key=lambda item: getattr(item, "alpha_3", "")):
        alpha_2 = getattr(country, "alpha_2", None)
        alpha_3 = getattr(country, "alpha_3", None)
        if not alpha_2 or not alpha_3:
            continue
        rows.append(
            {
                "code_iso3": str(alpha_3).upper(),
                "code_iso2": str(alpha_2).upper(),
                "wb_code": str(alpha_3).upper(),
                "name": territory_names.get(str(alpha_2).upper(), getattr(country, "name", str(alpha_3).upper())),
                "region": None,
                "enabled": 1,
            }
        )
    return rows or [dict(country) for country in FALLBACK_COUNTRIES]


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    init_db()
    summary = seed_metadata(force=False)
    print(summary)
