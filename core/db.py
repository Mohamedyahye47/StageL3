from __future__ import annotations

import os
import re
import sqlite3
import sys
import argparse
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.migrations import ensure_schema
from databridge_api.app.services.measure_service import enregistrer_mesure

try:
    from dotenv import load_dotenv

    load_dotenv(WORKSPACE_ROOT / ".env", override=False)
    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass


def _resolve_db_path(value: str | None) -> Path:
    if not value:
        return (PROJECT_ROOT / "databridge.db").resolve()
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


DB_BACKEND = (os.getenv("DATABRIDGE_DB_BACKEND") or "sqlite").strip().lower()
DB_PATH = _resolve_db_path(os.getenv("DATABRIDGE_DB_PATH") or os.getenv("DB_PATH"))
TURSO_DATABASE_URL = (os.getenv("TURSO_DATABASE_URL") or "").strip()
TURSO_AUTH_TOKEN = (os.getenv("TURSO_AUTH_TOKEN") or "").strip()


def configure_backend(
    *,
    backend: str | None = None,
    db_path: str | Path | None = None,
    turso_url: str | None = None,
    turso_token: str | None = None,
) -> None:
    """Configure le backend DB dans le processus courant.

    Utile pour lancer un build local propre depuis core/wb_metadata.py sans
    modifier les variables d'environnement globales du terminal.
    Exemple : configure_backend(backend="sqlite", db_path="databridge.db").
    """
    global DB_BACKEND, DB_PATH, TURSO_DATABASE_URL, TURSO_AUTH_TOKEN

    if backend is not None:
        DB_BACKEND = str(backend).strip().lower()
    if db_path is not None:
        DB_PATH = _resolve_db_path(str(db_path))
    if turso_url is not None:
        TURSO_DATABASE_URL = str(turso_url).strip()
    if turso_token is not None:
        TURSO_AUTH_TOKEN = str(turso_token).strip()


def _is_placeholder(value: str | None) -> bool:
    text = (value or "").strip().lower()
    return (
        not text
        or text.startswith("replace-with")
        or text.startswith("your-")
        or text in {"changeme", "change-me", "todo", "none", "null"}
    )


def _validate_db_backend() -> None:
    if DB_BACKEND not in {"sqlite", "turso"}:
        raise RuntimeError("DATABRIDGE_DB_BACKEND doit valoir 'sqlite' ou 'turso'.")
    if DB_BACKEND == "turso":
        if _is_placeholder(TURSO_DATABASE_URL):
            raise RuntimeError("TURSO_DATABASE_URL est obligatoire quand DATABRIDGE_DB_BACKEND=turso.")
        if _is_placeholder(TURSO_AUTH_TOKEN):
            raise RuntimeError("TURSO_AUTH_TOKEN est obligatoire quand DATABRIDGE_DB_BACKEND=turso.")


class DbRow:
    """Small row wrapper offering sqlite3.Row-style access by index or name."""

    def __init__(self, columns: Iterable[str], values: Iterable[Any] | dict[str, Any]):
        if isinstance(values, dict):
            self._data = dict(values)
            self._values = tuple(self._data.get(column) for column in columns)
        else:
            self._values = tuple(values)
            self._data = {column: self._values[index] for index, column in enumerate(columns)}

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def keys(self) -> list[str]:
        return list(self._data.keys())

    def items(self):
        return self._data.items()

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)


_NAMED_SQL_PARAMETER_RE = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


def _normalize_turso_parameters(
    sql: str,
    parameters: Iterable[Any] | dict[str, Any] | None,
) -> tuple[str, tuple[Any, ...]]:
    if parameters is None:
        return sql, ()

    if isinstance(parameters, dict):
        ordered_values: list[Any] = []
        missing_parameters: list[str] = []

        def replace_named_parameter(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in parameters:
                missing_parameters.append(name)
            else:
                ordered_values.append(parameters[name])
            return "?"

        normalized_sql = _NAMED_SQL_PARAMETER_RE.sub(replace_named_parameter, sql)
        if missing_parameters:
            missing = ", ".join(dict.fromkeys(missing_parameters))
            raise ValueError(f"Parametre SQL manquant pour Turso: {missing}")
        return normalized_sql, tuple(ordered_values)

    if isinstance(parameters, tuple):
        return sql, parameters
    if isinstance(parameters, list):
        return sql, tuple(parameters)
    return sql, tuple(parameters)


class TursoCursor:
    def __init__(self, connection: "TursoConnection", cursor: Any, sql: str):
        self._connection = connection
        self._cursor = cursor
        self._sql = sql
        self.lastrowid = getattr(cursor, "lastrowid", None)
        if self.lastrowid is None and sql.lstrip().lower().startswith("insert"):
            self.lastrowid = self._connection.last_insert_rowid()

    @property
    def rowcount(self) -> int:
        return int(getattr(self._cursor, "rowcount", -1) or -1)

    def fetchone(self) -> DbRow | None:
        row = self._cursor.fetchone()
        return self._wrap_row(row)

    def fetchall(self) -> list[DbRow]:
        return [wrapped for row in self._cursor.fetchall() if (wrapped := self._wrap_row(row)) is not None]

    def all(self) -> list[DbRow]:
        return self.fetchall()

    def __iter__(self):
        return iter(self.fetchall())

    def _columns(self) -> list[str]:
        description = getattr(self._cursor, "description", None) or []
        return [str(item[0]) for item in description]

    def _wrap_row(self, row: Any) -> DbRow | None:
        if row is None:
            return None
        if isinstance(row, DbRow):
            return row
        columns = self._columns()
        if hasattr(row, "keys"):
            try:
                row_columns = list(row.keys())
                return DbRow(row_columns, {column: row[column] for column in row_columns})
            except Exception:
                pass
        if isinstance(row, dict):
            return DbRow(row.keys(), row)
        return DbRow(columns or [str(index) for index, _ in enumerate(row)], row)


class TursoConnection:
    """DB-API compatibility shim around libsql for the subset used by DataBridge.

    Robustesse Turso/Hrana : un stream libSQL est ephemere et peut expirer
    pendant les longues phases reseau (World Bank / traduction). Cette classe
    detecte les erreurs "stream not found" / 404 et rouvre automatiquement une
    connexion brute, puis rejoue l'instruction. rollback()/close() sont rendus
    defensifs pour ne jamais masquer l'erreur d'origine.
    """

    def __init__(self, raw_connection: Any, reopen: "Callable[[], Any] | None" = None):
        self._raw = raw_connection
        self._reopen = reopen  # fabrique une nouvelle connexion brute libsql

    def __enter__(self) -> "TursoConnection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        # Defensif : ni commit ni rollback ne doivent relancer une exception
        # (sinon un stream deja mort masque l'erreur metier d'origine).
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        except Exception:
            pass
        finally:
            self.close()

    @staticmethod
    def _is_stream_dead(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "stream not found" in text
            or "404 not found" in text
            or "status=404" in text
        )

    def _reconnect(self) -> bool:
        """Rouvre une connexion brute. Retourne True si reussi."""
        if self._reopen is None:
            return False
        try:
            self._raw = self._reopen()
            return True
        except Exception:
            return False

    def execute(self, sql: str, parameters: Iterable[Any] | dict[str, Any] | None = None) -> TursoCursor:
        normalized_sql, normalized_parameters = _normalize_turso_parameters(sql, parameters)
        try:
            cursor = self._raw.execute(normalized_sql, normalized_parameters)
        except Exception as exc:
            # Stream expire : on rouvre un stream neuf et on rejoue une fois.
            if self._is_stream_dead(exc) and self._reconnect():
                cursor = self._raw.execute(normalized_sql, normalized_parameters)
            else:
                raise
        return TursoCursor(self, cursor, normalized_sql)

    def executemany(self, sql: str, seq_of_parameters: Iterable[Iterable[Any] | dict[str, Any]]) -> None:
        for parameters in seq_of_parameters:
            self.execute(sql, parameters)

    def executescript(self, script: str) -> None:
        for statement in _split_sql_script(script):
            self.execute(statement)

    def commit(self) -> None:
        commit = getattr(self._raw, "commit", None)
        if commit is None:
            return
        try:
            commit()
        except Exception as exc:
            # Si le stream est mort, le commit n'a plus de sens (les ecritures
            # non encore persistees sont perdues) : on rouvre pour la suite,
            # sans relancer l'exception qui ferait planter tout le script.
            if self._is_stream_dead(exc):
                self._reconnect()
            else:
                raise

    def rollback(self) -> None:
        rollback = getattr(self._raw, "rollback", None)
        if rollback is None:
            return
        try:
            rollback()
        except Exception:
            # Defensif : un rollback sur stream mort ne doit jamais crasher.
            self._reconnect()

    def close(self) -> None:
        close = getattr(self._raw, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception:
            pass

    def cursor(self) -> "TursoConnection":
        return self

    def last_insert_rowid(self) -> int | None:
        row = self.execute("SELECT last_insert_rowid()").fetchone()
        if row is None:
            return None
        return int(row[0])


def _split_sql_script(script: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer += line + "\n"
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            buffer = ""
    trailing = buffer.strip().rstrip(";").strip()
    if trailing:
        statements.append(trailing)
    return statements


def _open_sqlite_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _open_turso_connection() -> TursoConnection:
    try:
        import libsql
    except ImportError as exc:
        raise RuntimeError(
            "Le package Python 'libsql' est requis quand DATABRIDGE_DB_BACKEND=turso."
        ) from exc

    def _open_raw():
        return libsql.connect(database=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)

    return TursoConnection(_open_raw(), reopen=_open_raw)

WORLD_BANK_SOURCE = {
    "code": "WB",
    "name": "Banque mondiale",
    "base_url": "https://api.worldbank.org/v2",
    "description": "Catalogue metadata des indicateurs de la Banque mondiale pour Richat DataBridge.",
}

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


def get_connection() -> sqlite3.Connection | TursoConnection:
    _validate_db_backend()
    if DB_BACKEND == "turso":
        return _open_turso_connection()
    return _open_sqlite_connection()


def init_db() -> None:
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
    origin: str = "official_world_bank",
    table: str = "indicator_topics",
    conn: sqlite3.Connection | None = None,
) -> None:
    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        # Ne supprime QUE les relations de cette origine : les compléments
        # databridge survivent aux resynchros officielles, et inversement.
        conn.execute(
            f"DELETE FROM {table} WHERE indicator_id = ? AND origin = ?",
            (indicator_id, origin),
        )
        for topic_id in sorted(set(topic_ids)):
            # Upgrade : si World Bank fournit officiellement un thème déjà posé
            # par databridge, on PROMEUT la relation au lieu de la dupliquer.
            # La clé primaire (indicator_id, topic_id) empêche tout doublon, donc
            # un simple UPDATE ciblé suffit (pas de GROUP BY / HAVING).
            if origin == "official_world_bank":
                conn.execute(
                    f"""
                    UPDATE {table}
                    SET origin = 'official_world_bank', review_status = 'none'
                    WHERE indicator_id = ? AND topic_id = ?
                      AND origin = 'databridge_prefix_rule'
                    """,
                    (indicator_id, topic_id),
                )
            conn.execute(
                f"""
                INSERT OR IGNORE INTO {table}
                    (indicator_id, topic_id, origin)
                VALUES (?, ?, ?)
                """,
                (indicator_id, topic_id, origin),
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialise ou mesure la base de metadonnees Richat DataBridge.")
    parser.add_argument("--mesure", action="store_true", help="Mesure une initialisation sure dans une base temporaire.")
    parser.add_argument("--base-temporaire", action="store_true", help="Obligatoire avec --mesure pour eviter la vraie base.")
    parser.add_argument("--measure-fresh-run", action="store_true", help="Alias de --mesure --base-temporaire.")
    parser.add_argument("--limit", type=int, default=None, help="Limite optionnelle du nombre d'indicateurs pendant la mesure.")
    return parser.parse_args()


def _mesurer_premier_lancement(limit: int | None = None) -> dict[str, Any]:
    global DB_PATH

    vraie_base = DB_PATH
    debut_total = time.perf_counter()
    lignes_console: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="richat_databridge_mesure_") as temp_dir:
        DB_PATH = Path(temp_dir) / "databridge_mesure.db"
        os.environ["DATABRIDGE_DB_PATH"] = str(DB_PATH)

        with get_connection() as conn:
            debut_schema = time.perf_counter()
            ensure_schema(conn)
            conn.commit()
            duree_schema = time.perf_counter() - debut_schema
            nombre_tables = conn.execute(
                "SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'table'"
            ).fetchone()["count"]

        lignes_console.append(
            {
                "etape": "Création du schéma",
                "duree": duree_schema,
                "elements": f"{nombre_tables} tables",
                "etat": "Réussi",
            }
        )

        summary = seed_metadata(force=True, limit=limit)

        with get_connection() as conn:
            sources = conn.execute("SELECT COUNT(*) AS count FROM sources").fetchone()["count"]
            liens = conn.execute("SELECT COUNT(*) AS count FROM indicator_topics").fetchone()["count"]

        metrics = _lire_mesures_wb()
        stages = {item["name"]: float(item["duration_seconds"]) for item in metrics.get("stages", [])}

        lignes_console.extend(
            [
                {
                    "etape": "Sources",
                    "duree": stages.get("source_registration", 0),
                    "elements": f"{sources} source(s)",
                    "etat": "Réussi",
                },
                {
                    "etape": "Thèmes",
                    "duree": _somme_etapes(stages, "topics"),
                    "elements": f"{summary.get('topics', 0)} theme(s)",
                    "etat": "Réussi",
                },
                {
                    "etape": "Pays",
                    "duree": _somme_etapes(stages, "countries"),
                    "elements": f"{summary.get('countries', 0)} pays",
                    "etat": "Réussi",
                },
                {
                    "etape": "Indicateurs",
                    "duree": _somme_etapes(stages, "indicators"),
                    "elements": f"{summary.get('indicators', 0)} indicateur(s)",
                    "etat": "Réussi",
                },
                {
                    "etape": "Liens indicateur-thème",
                    "duree": stages.get("liens_indicateur_theme", 0),
                    "elements": f"{liens} lien(s)",
                    "etat": "Réussi",
                },
            ]
        )

        duree_totale = time.perf_counter() - debut_total
        lignes_console.append(
            {
                "etape": "Durée totale",
                "duree": duree_totale,
                "elements": "-",
                "etat": "Réussi",
            }
        )

        evenement = {
            "type": "mesure_premier_lancement_base_temporaire",
            "etat": "Réussi",
            "base_temporaire": str(DB_PATH),
            "duree_totale_secondes": round(duree_totale, 4),
            "nombre_sources": int(sources),
            "nombre_themes": int(summary.get("topics", 0)),
            "nombre_pays": int(summary.get("countries", 0)),
            "nombre_indicateurs": int(summary.get("indicators", 0)),
            "nombre_liens_indicateur_theme": int(liens),
            "etapes": [
                {
                    "nom": ligne["etape"],
                    "duree_secondes": round(float(ligne["duree"]), 4),
                    "elements_traites": ligne["elements"],
                    "etat": ligne["etat"],
                }
                for ligne in lignes_console
            ],
        }
        enregistrer_mesure("base", evenement)
        _afficher_table_mesure(lignes_console)

    DB_PATH = vraie_base
    os.environ["DATABRIDGE_DB_PATH"] = str(vraie_base)
    return evenement


def _lire_mesures_wb() -> dict[str, Any]:
    path = PROJECT_ROOT / "databridge_web" / "logs" / "wb_metadata_metrics.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _somme_etapes(stages: dict[str, float], prefix: str) -> float:
    return sum(duration for name, duration in stages.items() if name.startswith(prefix))


def _afficher_table_mesure(lignes: list[dict[str, Any]]) -> None:
    print("Étape | Durée | Éléments traités | État")
    print("-" * 58)
    for ligne in lignes:
        print(
            f"{ligne['etape']} | {float(ligne['duree']):.2f} s | "
            f"{ligne['elements']} | {ligne['etat']}"
        )


if __name__ == "__main__":
    args = _parse_args()
    if args.measure_fresh_run:
        args.mesure = True
        args.base_temporaire = True
    if args.mesure:
        if not args.base_temporaire:
            raise SystemExit("Utilisez --base-temporaire avec --mesure pour proteger databridge.db.")
        _mesurer_premier_lancement(limit=args.limit)
    else:
        init_db()
        summary = seed_metadata(force=False)
        print(summary)
