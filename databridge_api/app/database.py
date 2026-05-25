from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import create_engine
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import DeclarativeBase, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DB_PATH, DATABRIDGE_DB_BACKEND
from core import db as core_db


class Base(DeclarativeBase):
    pass


if DATABRIDGE_DB_BACKEND == "sqlite":
    DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}, future=True)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
else:
    DATABASE_URL = "libsql://turso"
    engine = None

    class _UnavailableSessionFactory:
        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(
                "SessionLocal SQLAlchemy n'est pas disponible en mode Turso. "
                "Utilisez get_db(), qui passe par core.db/libsql."
            )

    SessionLocal = _UnavailableSessionFactory()


class CoreResult:
    def __init__(self, session: "CoreDbSession", cursor: Any, statement: Any):
        self._session = session
        self._cursor = cursor
        self._statement = statement

    def all(self) -> list[Any]:
        return [self._map_row(row) for row in self._cursor.fetchall()]

    def fetchall(self) -> list[Any]:
        return self.all()

    def first(self) -> Any | None:
        row = self._cursor.fetchone()
        return self._map_row(row) if row is not None else None

    def fetchone(self) -> Any | None:
        return self.first()

    def scalar_one_or_none(self) -> Any | None:
        value = self.first()
        if isinstance(value, tuple):
            return value[0] if value else None
        return value

    def _map_row(self, row: Any) -> Any:
        descriptions = getattr(self._statement, "column_descriptions", None) or []
        if not descriptions:
            return row

        values = tuple(row)
        mapped: list[Any] = []
        offset = 0
        for description in descriptions:
            expr = description.get("expr")
            if _is_model_class(expr):
                columns = _model_columns(expr)
                segment = values[offset : offset + len(columns)]
                offset += len(columns)
                mapped.append(self._session._model_from_values(expr, columns, segment))
            else:
                mapped.append(values[offset] if offset < len(values) else None)
                offset += 1

        if len(mapped) == 1:
            return mapped[0]
        return tuple(mapped)


class CoreScalarResult:
    def __init__(self, result: CoreResult):
        self._result = result

    def all(self) -> list[Any]:
        return [self._scalar(value) for value in self._result.all()]

    def first(self) -> Any | None:
        return self._scalar(self._result.first())

    @staticmethod
    def _scalar(value: Any) -> Any:
        if isinstance(value, tuple):
            return value[0] if value else None
        return value


class CoreDbSession:
    """Small SQLAlchemy-compatibility facade backed by core.db/libSQL."""

    def __init__(self, connection: Any):
        self._connection = connection
        self._pending: list[Any] = []
        self._deleted: list[Any] = []
        self._loaded: dict[tuple[type[Any], Any], Any] = {}

    def execute(self, statement: Any, params: Iterable[Any] | dict[str, Any] | None = None) -> CoreResult:
        if isinstance(statement, str):
            return CoreResult(self, self._connection.execute(statement, params or ()), statement)

        sql, ordered_params = _compile_statement(statement)
        cursor = self._connection.execute(sql, ordered_params)
        return CoreResult(self, cursor, statement)

    def scalars(self, statement: Any) -> CoreScalarResult:
        return CoreScalarResult(self.execute(statement))

    def scalar(self, statement: Any) -> Any:
        return self.scalars(statement).first()

    def get(self, model_cls: type[Any], primary_key: Any) -> Any | None:
        pk_columns = _primary_key_columns(model_cls)
        if len(pk_columns) != 1:
            raise RuntimeError(f"get() ne supporte qu'une clé primaire simple pour {model_cls.__name__}.")
        table = model_cls.__table__.name
        column = pk_columns[0].name
        row = self._connection.execute(f"SELECT * FROM {table} WHERE {column} = ? LIMIT 1", (primary_key,)).fetchone()
        if row is None:
            return None
        values = tuple(row)
        return self._model_from_values(model_cls, _model_columns(model_cls), values)

    def add(self, obj: Any) -> None:
        if obj not in self._pending:
            self._pending.append(obj)

    def delete(self, obj: Any) -> None:
        if obj not in self._deleted:
            self._deleted.append(obj)

    def flush(self) -> None:
        for obj in list(self._pending):
            self._insert_model(obj)
            self._pending.remove(obj)
        for obj in list(self._deleted):
            self._delete_model(obj)
            self._deleted.remove(obj)
        for obj in list(self._loaded.values()):
            self._update_model(obj)

    def commit(self) -> None:
        self.flush()
        self._connection.commit()

    def rollback(self) -> None:
        self._pending.clear()
        self._deleted.clear()
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def _model_from_values(self, model_cls: type[Any], columns: list[str], values: Iterable[Any]) -> Any:
        data = dict(zip(columns, values, strict=False))
        obj = model_cls(**data)
        self._track_loaded(obj)
        self._attach_relationships(obj)
        return obj

    def _track_loaded(self, obj: Any) -> None:
        identity = _model_identity(obj)
        if identity is not None:
            self._loaded[(type(obj), identity)] = obj

    def _insert_model(self, obj: Any) -> None:
        table = obj.__table__
        pk_columns = _primary_key_columns(type(obj))
        columns = []
        values = []
        for column in table.columns:
            value = getattr(obj, column.name, None)
            if column in pk_columns and value is None:
                continue
            columns.append(column.name)
            values.append(value)

        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO {table.name} ({', '.join(columns)}) VALUES ({placeholders})"
        cursor = self._connection.execute(sql, values)
        if len(pk_columns) == 1 and getattr(obj, pk_columns[0].name, None) is None:
            setattr(obj, pk_columns[0].name, cursor.lastrowid)
        self._track_loaded(obj)

    def _update_model(self, obj: Any) -> None:
        table = obj.__table__
        pk_columns = _primary_key_columns(type(obj))
        if not pk_columns:
            return
        pk_values = [getattr(obj, column.name, None) for column in pk_columns]
        if any(value is None for value in pk_values):
            return
        set_columns = [column for column in table.columns if column not in pk_columns]
        assignments = ", ".join(f"{column.name} = ?" for column in set_columns)
        where_clause = " AND ".join(f"{column.name} = ?" for column in pk_columns)
        values = [getattr(obj, column.name, None) for column in set_columns] + pk_values
        self._connection.execute(f"UPDATE {table.name} SET {assignments} WHERE {where_clause}", values)

    def _delete_model(self, obj: Any) -> None:
        table = obj.__table__
        pk_columns = _primary_key_columns(type(obj))
        pk_values = [getattr(obj, column.name, None) for column in pk_columns]
        if not pk_columns or any(value is None for value in pk_values):
            return
        where_clause = " AND ".join(f"{column.name} = ?" for column in pk_columns)
        self._connection.execute(f"DELETE FROM {table.name} WHERE {where_clause}", pk_values)

    def _attach_relationships(self, obj: Any) -> None:
        from app.models import Country, ExportDataset, ExportDatasetIndicator, Indicator, IndicatorTopic, Source, Topic

        if isinstance(obj, Indicator):
            obj.topic_links = self._load_indicator_topic_links([obj.id]).get(obj.id, [])
        elif isinstance(obj, ExportDataset):
            obj.source = self.get(Source, obj.source_id)
            obj.topic = self.get(Topic, obj.topic_id)
            obj.country = self.get(Country, obj.country_id)
            obj.indicator_links = self._load_export_indicator_links(obj.id)
            obj.export_logs = []
        elif isinstance(obj, ExportDatasetIndicator):
            obj.indicator = self.get(Indicator, obj.indicator_id)
        elif isinstance(obj, IndicatorTopic):
            obj.topic = self.get(Topic, obj.topic_id)

    def _load_indicator_topic_links(self, indicator_ids: list[int]) -> dict[int, list[Any]]:
        from app.models import IndicatorTopic, Topic

        if not indicator_ids:
            return {}
        placeholders = ", ".join("?" for _ in indicator_ids)
        rows = self._connection.execute(
            f"""
            SELECT it.indicator_id, it.topic_id, t.id, t.source_id, t.name, t.description
            FROM indicator_topics it
            JOIN topics t ON t.id = it.topic_id
            WHERE it.indicator_id IN ({placeholders})
            """,
            indicator_ids,
        ).fetchall()
        mapping: dict[int, list[Any]] = {}
        for row in rows:
            indicator_id, topic_id, topic_db_id, source_id, name, description = tuple(row)
            link = IndicatorTopic(indicator_id=indicator_id, topic_id=topic_id)
            link.topic = Topic(id=topic_db_id, source_id=source_id, name=name, description=description)
            mapping.setdefault(int(indicator_id), []).append(link)
        return mapping

    def _load_export_indicator_links(self, export_dataset_id: int) -> list[Any]:
        from app.models import ExportDatasetIndicator, Indicator

        rows = self._connection.execute(
            """
            SELECT
                edi.id AS link_id,
                edi.export_dataset_id,
                edi.indicator_id,
                i.id,
                i.source_id,
                i.code,
                i.name,
                i.description,
                i.unit,
                i.periodicity,
                i.created_at
            FROM export_dataset_indicators edi
            JOIN indicators i ON i.id = edi.indicator_id
            WHERE edi.export_dataset_id = ?
            ORDER BY edi.id ASC
            """,
            (export_dataset_id,),
        ).fetchall()
        links = []
        for row in rows:
            values = tuple(row)
            link = ExportDatasetIndicator(
                id=values[0],
                export_dataset_id=values[1],
                indicator_id=values[2],
            )
            indicator = Indicator(
                id=values[3],
                source_id=values[4],
                code=values[5],
                name=values[6],
                description=values[7],
                unit=values[8],
                periodicity=values[9],
                created_at=values[10],
            )
            indicator.topic_links = self._load_indicator_topic_links([indicator.id]).get(indicator.id, [])
            link.indicator = indicator
            links.append(link)
        return links


def get_db():
    connection = core_db.get_connection()
    db = CoreDbSession(connection)
    try:
        yield db
    finally:
        db.close()


def _compile_statement(statement: Any) -> tuple[str, tuple[Any, ...]]:
    compiled = statement.compile(
        dialect=sqlite.dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    params = compiled.params
    ordered_names = getattr(compiled, "positiontup", None) or []
    ordered_params = tuple(params[name] for name in ordered_names)
    return str(compiled), ordered_params


def _is_model_class(value: Any) -> bool:
    return isinstance(value, type) and hasattr(value, "__table__")


def _model_columns(model_cls: type[Any]) -> list[str]:
    return [column.name for column in model_cls.__table__.columns]


def _primary_key_columns(model_cls: type[Any]) -> list[Any]:
    return list(model_cls.__table__.primary_key.columns)


def _model_identity(obj: Any) -> Any | None:
    pk_columns = _primary_key_columns(type(obj))
    values = tuple(getattr(obj, column.name, None) for column in pk_columns)
    if not values or any(value is None for value in values):
        return None
    return values[0] if len(values) == 1 else values
