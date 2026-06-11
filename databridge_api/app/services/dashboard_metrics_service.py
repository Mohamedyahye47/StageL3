from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import mean
from typing import Any

from app import config
from app.services import ai_assistant_service
from app.services.measure_service import lire_dernieres_mesures


MONTHS_FR = (
    "Janv.",
    "Févr.",
    "Mars",
    "Avr.",
    "Mai",
    "Juin",
    "Juil.",
    "Août",
    "Sept.",
    "Oct.",
    "Nov.",
    "Dec.",
)

MIN_AI_CALLS_FOR_SUCCESS_RATE = 5


def build_dashboard_metrics(db: Any) -> dict[str, Any]:
    """Build dashboard data from real DB/log records only.

    The dashboard must stay honest: when a metric is not measured, the returned
    labels say so instead of replacing it silently with another metric.
    """

    dataset_events = lire_dernieres_mesures("datasets", limite=80)
    ai_events = lire_dernieres_mesures("ia", limite=120)
    etl_events = lire_dernieres_mesures("wb_metadata_quality", limite=20)

    timeline = _build_exports_timeline(db)
    recent_exports = _build_recent_exports(db)
    timeline_uses_logs = any(item.get("source") == "export_logs" for item in timeline)

    return {
        "summary": _build_summary(db),
        "catalog_metadata": _build_catalog_metadata(db, etl_events),
        "exports_timeline": timeline,
        "timeline_label": "Exports générés" if timeline_uses_logs else "Jeux de données exposés",
        "top_indicators": _build_top_indicators(db),
        "recent_exports": recent_exports,
        "process_performance": _build_process_performance(db, dataset_events, ai_events, etl_events),
        "ai_summary": _build_ai_summary(ai_events),
        "ai_models": _build_ai_models(ai_events),
        "opendatasoft": _build_opendatasoft_summary(),
        "warnings": _build_warnings(timeline, ai_events),
    }


def _build_summary(db: Any) -> dict[str, Any]:
    dataset_count = _scalar_int(db, "SELECT COUNT(*) FROM export_datasets")
    generation_count = _scalar_int(
        db,
        "SELECT COUNT(*) FROM export_logs WHERE action = ?",
        ("generation_liens",),
    )
    country_count = _scalar_int(
        db,
        "SELECT COUNT(DISTINCT country_id) FROM export_datasets",
    )
    indicator_count = _scalar_int(
        db,
        "SELECT COUNT(DISTINCT indicator_id) FROM export_dataset_indicators",
    )
    source_count = _scalar_int(db, "SELECT COUNT(*) FROM sources")
    last_export_at = _scalar_text(
        db,
        "SELECT MAX(created_at) FROM export_logs WHERE action = ?",
        ("generation_liens",),
    ) or _scalar_text(db, "SELECT MAX(created_at) FROM export_datasets")

    uses_dataset_fallback = generation_count == 0 and dataset_count > 0
    exports_value = dataset_count if uses_dataset_fallback else generation_count
    exports_label = "Jeux de données exposés" if uses_dataset_fallback else "Exports générés"
    exports_note = (
        "Aucun journal de génération disponible"
        if uses_dataset_fallback
        else "Générations journalisées"
    )

    return {
        "datasets": dataset_count,
        "exports": exports_value,
        "exports_logged": generation_count,
        "exports_uses_dataset_fallback": uses_dataset_fallback,
        "exports_label": exports_label,
        "exports_note": exports_note,
        "countries": country_count,
        "indicators": indicator_count,
        "sources": source_count,
        "last_export_at": last_export_at,
        "last_export_label": _format_date(last_export_at) if last_export_at else "Aucun export",
    }


def _build_catalog_metadata(db: Any, etl_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Return World Bank metadata catalogue state from the business database.

    These metrics describe the local DataBridge catalogue built from World Bank
    metadata. They are deliberately separated from export metrics: a catalogue
    may be complete even when no dataset has been generated yet.
    """

    sources = _scalar_int(db, "SELECT COUNT(*) FROM sources")
    topics = _scalar_int(db, "SELECT COUNT(*) FROM topics")
    countries = _scalar_int(db, "SELECT COUNT(*) FROM countries")
    indicators = _scalar_int(db, "SELECT COUNT(*) FROM indicators")
    indicator_topics = _scalar_int(db, "SELECT COUNT(*) FROM indicator_topics")
    indicators_without_topic = _scalar_int(
        db,
        """
        SELECT COUNT(*)
        FROM indicators i
        WHERE NOT EXISTS (
            SELECT 1 FROM indicator_topics it WHERE it.indicator_id = i.id
        )
        """,
    )
    indicators_without_name = _scalar_int(
        db,
        "SELECT COUNT(*) FROM indicators WHERE name IS NULL OR TRIM(name) = ''",
    )
    indicators_without_description = _scalar_int(
        db,
        "SELECT COUNT(*) FROM indicators WHERE description IS NULL OR TRIM(description) = ''",
    )
    # The current production schema has created_at but not updated_at on indicators.
    # We therefore expose the last metadata insertion date from the business DB
    # instead of depending on local JSONL logs that Render cannot read.
    last_indicator_update = _scalar_text(db, "SELECT MAX(created_at) FROM indicators")
    last_etl_event = _latest_event(etl_events)
    last_etl_date = (
        last_etl_event.get("date")
        or last_etl_event.get("created_at")
        or last_etl_event.get("finished_at")
        if last_etl_event
        else None
    )

    is_populated = any([sources, topics, countries, indicators, indicator_topics])
    if not last_etl_date and is_populated:
        last_etl_date = last_indicator_update
    quality_warnings = []
    if is_populated and indicators_without_topic:
        quality_warnings.append(f"{indicators_without_topic} indicateur(s) sans thème")
    if is_populated and indicators_without_name:
        quality_warnings.append(f"{indicators_without_name} indicateur(s) sans nom")
    if is_populated and indicators_without_description:
        quality_warnings.append(f"{indicators_without_description} indicateur(s) sans description")

    return {
        "sources": sources,
        "topics": topics,
        "countries": countries,
        "indicators": indicators,
        "indicator_topics": indicator_topics,
        "indicators_without_topic": indicators_without_topic,
        "indicators_without_name": indicators_without_name,
        "indicators_without_description": indicators_without_description,
        "is_populated": is_populated,
        "quality_warnings": quality_warnings,
        "last_indicator_update": last_indicator_update,
        "last_indicator_update_label": _format_date(last_indicator_update) if last_indicator_update else "Non renseignée",
        "last_etl_label": _format_date(last_etl_date) if last_etl_date else ("Catalogue mesuré depuis Turso" if is_populated else "Non mesurée"),
        "source_label": "Base métier DataBridge",
    }


def _build_exports_timeline(db: Any) -> list[dict[str, Any]]:
    source = "export_logs"
    rows = _rows(
        db,
        """
        SELECT
            substr(created_at, 1, 10) AS event_date,
            COUNT(*) AS export_count,
            COALESCE(SUM(row_count), 0) AS row_count
        FROM export_logs
        WHERE action = ? AND created_at IS NOT NULL
        GROUP BY substr(created_at, 1, 10)
        ORDER BY event_date ASC
        """,
        ("generation_liens",),
    )
    if not rows:
        source = "export_datasets"
        rows = _rows(
            db,
            """
            SELECT
                substr(created_at, 1, 10) AS event_date,
                COUNT(*) AS export_count,
                0 AS row_count
            FROM export_datasets
            WHERE created_at IS NOT NULL
            GROUP BY substr(created_at, 1, 10)
            ORDER BY event_date ASC
            """,
        )

    daily = [
        {
            "date": str(row.get("event_date") or ""),
            "exports": _to_int(row.get("export_count")),
            "rows": _to_int(row.get("row_count")),
            "source": source,
        }
        for row in rows
        if row.get("event_date")
    ]
    if not daily:
        return []

    month_keys = {item["date"][:7] for item in daily if len(item["date"]) >= 7}
    if len(month_keys) > 1:
        monthly: dict[str, dict[str, Any]] = {}
        for item in daily:
            key = item["date"][:7]
            bucket = monthly.setdefault(
                key,
                {"date": key, "label": _format_month_label(key), "exports": 0, "rows": 0, "source": source},
            )
            bucket["exports"] += item["exports"]
            bucket["rows"] += item["rows"]
        return list(monthly.values())[-12:]

    return [
        {
            "date": item["date"],
            "label": _format_short_date(item["date"]),
            "exports": item["exports"],
            "rows": item["rows"],
            "source": item["source"],
        }
        for item in daily[-14:]
    ]


def _build_top_indicators(db: Any) -> list[dict[str, Any]]:
    rows = _rows(
        db,
        """
        SELECT
            i.code AS code,
            i.name AS name,
            COUNT(*) AS usage_count
        FROM export_dataset_indicators edi
        JOIN indicators i ON i.id = edi.indicator_id
        GROUP BY i.id, i.code, i.name
        ORDER BY usage_count DESC, i.name ASC
        LIMIT 8
        """,
    )
    return [
        {
            "code": str(row.get("code") or ""),
            "name": str(row.get("name") or row.get("code") or "Indicateur"),
            "count": _to_int(row.get("usage_count")),
        }
        for row in rows
    ]


def _build_recent_exports(db: Any) -> list[dict[str, Any]]:
    rows = _rows(
        db,
        """
        SELECT
            ed.slug,
            ed.title,
            ed.description,
            ed.status,
            ed.start_date,
            ed.end_date,
            ed.created_at,
            ed.updated_at,
            c.name AS country_name,
            c.code_iso3 AS country_code,
            s.name AS source_name,
            s.code AS source_code,
            COUNT(edi.indicator_id) AS indicator_count
        FROM export_datasets ed
        LEFT JOIN countries c ON c.id = ed.country_id
        LEFT JOIN sources s ON s.id = ed.source_id
        LEFT JOIN export_dataset_indicators edi ON edi.export_dataset_id = ed.id
        GROUP BY
            ed.id, ed.slug, ed.title, ed.description, ed.status, ed.start_date,
            ed.end_date, ed.created_at, ed.updated_at, c.name, c.code_iso3,
            s.name, s.code
        ORDER BY ed.updated_at DESC, ed.created_at DESC
        LIMIT 6
        """,
    )
    return [
        {
            "slug": str(row.get("slug") or ""),
            "title": str(row.get("title") or "Jeu de données"),
            "description": str(row.get("description") or ""),
            "status": _status_label(row.get("status")),
            "status_class": _status_class(row.get("status")),
            "country_name": str(row.get("country_name") or "Non renseigné"),
            "country_code": str(row.get("country_code") or ""),
            "source_name": str(row.get("source_name") or row.get("source_code") or "Source"),
            "source_code": str(row.get("source_code") or ""),
            "period": _format_period(row.get("start_date"), row.get("end_date")),
            "indicator_count": _to_int(row.get("indicator_count")),
            "updated_label": _format_date(row.get("updated_at") or row.get("created_at")),
        }
        for row in rows
    ]


def _build_process_performance(
    db: Any,
    dataset_events: list[dict[str, Any]],
    ai_events: list[dict[str, Any]],
    etl_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        _process_metric(
            "Aperçu dataset",
            _filter_events(dataset_events, "apercu_dataset"),
            "logs/mesures/mesures_datasets.jsonl",
        ),
        _process_metric(
            "Génération CSV/JSON",
            _filter_events(dataset_events, "generation_liens_dataset"),
            "logs/mesures/mesures_datasets.jsonl",
            fallback=_export_log_duration_summary(db),
        ),
        _process_metric("Assistant IA", ai_events, "logs/mesures/mesures_ia.jsonl"),
        _metadata_catalog_metric(db, etl_events),
    ]


def _build_ai_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    duration_summary = _duration_summary(events)
    return {
        "provider": ai_assistant_service.AI_PROVIDER,
        "model": ai_assistant_service.AI_MODEL,
        "calls": len(events),
        "measured": bool(events),
        "avg_duration": duration_summary["avg_duration"],
        "avg_duration_label": _format_duration(duration_summary["avg_duration"]),
    }


def _build_ai_models(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        provider = str(event.get("fournisseur") or event.get("provider") or "non renseigné")
        model = str(event.get("modele") or event.get("model") or "non renseigné")
        grouped[(provider, model)].append(event)

    rows = []
    for (provider, model), model_events in grouped.items():
        duration_summary = _duration_summary(model_events)
        success_rate = _success_rate(model_events)
        success_rate_label = ""
        success_note = ""
        if success_rate is not None and len(model_events) >= MIN_AI_CALLS_FOR_SUCCESS_RATE:
            success_rate_label = f"{success_rate}% réussite"
        elif success_rate is not None:
            success_note = f"Taux non affiché : moins de {MIN_AI_CALLS_FOR_SUCCESS_RATE} appels"
        rows.append(
            {
                "provider": provider,
                "model": model,
                "calls": len(model_events),
                "avg_duration": duration_summary["avg_duration"],
                "avg_duration_label": _format_duration(duration_summary["avg_duration"]),
                "success_rate": success_rate if len(model_events) >= MIN_AI_CALLS_FOR_SUCCESS_RATE else None,
                "success_rate_label": success_rate_label,
                "success_note": success_note,
            }
        )
    return sorted(rows, key=lambda item: item["calls"], reverse=True)[:6]


def _build_opendatasoft_summary() -> dict[str, Any]:
    mode = getattr(config, "ODS_PUBLISH_MODE", "manual_url")
    if mode == "manual_url":
        label = "Liens CSV/JSON préparés"
        description = "Les URL CSV/JSON sont préparées pour être utilisées dans OpenDataSoft / Richat Data Hub."
    else:
        label = "Publication OpenDataSoft"
        description = "Un mode de publication OpenDataSoft est configuré côté serveur. Vérifier le flux réel avant de parler de publication automatique."
    return {
        "mode": mode,
        "label": label,
        "description": description,
        "public_base_url": getattr(config, "PUBLIC_API_BASE_URL", ""),
    }


def _build_warnings(timeline: list[dict[str, Any]], ai_events: list[dict[str, Any]]) -> list[str]:
    warnings = []
    if 0 < len(timeline) < 3:
        warnings.append("Nombre d'exports encore limité pour analyser une tendance fiable.")
    if not ai_events:
        warnings.append("Aucune mesure IA récente n'est disponible dans les logs locaux.")
    return warnings


def _metadata_catalog_metric(db: Any, etl_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a durable ETL/catalogue metric based on Turso when local logs are absent.

    ETL is executed from CMD against the remote Turso database. Render cannot read
    the JSONL file created on the administrator machine, so the dashboard must not
    rely only on local logs. This fallback is honest: it measures the catalogue
    state stored in the business database, not the exact ETL duration.
    """

    metric = _process_metric("Metadata World Bank", etl_events, "logs/mesures/wb_metadata_quality.jsonl")
    if metric.get("measured"):
        return metric

    row = _one(
        db,
        """
        SELECT
            (SELECT COUNT(*) FROM sources) AS sources,
            (SELECT COUNT(*) FROM topics) AS topics,
            (SELECT COUNT(*) FROM countries) AS countries,
            (SELECT COUNT(*) FROM indicators) AS indicators,
            (SELECT COUNT(*) FROM indicator_topics) AS indicator_topics,
            (SELECT MAX(created_at) FROM indicators) AS last_indicator_created_at
        """,
    )
    sources = _to_int(row.get("sources")) if row else 0
    topics = _to_int(row.get("topics")) if row else 0
    countries = _to_int(row.get("countries")) if row else 0
    indicators = _to_int(row.get("indicators")) if row else 0
    indicator_topics = _to_int(row.get("indicator_topics")) if row else 0

    if not any([sources, topics, countries, indicators, indicator_topics]):
        return metric

    return {
        "label": "Metadata World Bank",
        "source": "base métier Turso",
        "measured": True,
        "count": 1,
        "avg_duration": None,
        "avg_duration_label": "Catalogue mesuré",
        "max_duration_label": "Durée ETL non disponible",
        "bar_percent": 100,
        "note": (
            f"Catalogue chargé : {sources} source(s), {topics} thème(s), "
            f"{countries} pays, {indicators} indicateur(s), "
            f"{indicator_topics} relation(s) indicateur-thème."
        ),
    }


def _process_metric(
    label: str,
    events: list[dict[str, Any]],
    source: str,
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = _duration_summary(events)
    if summary["count"] == 0 and fallback:
        summary = fallback
        source = "export_logs"
    measured = summary["count"] > 0
    return {
        "label": label,
        "source": source,
        "measured": measured,
        "count": summary["count"],
        "avg_duration": summary["avg_duration"],
        "avg_duration_label": _format_duration(summary["avg_duration"]),
        "max_duration_label": _format_duration(summary["max_duration"]),
        "bar_percent": min(100, int((summary["avg_duration"] or 0) * 18)) if measured else 0,
        "note": "",
    }


def _export_log_duration_summary(db: Any) -> dict[str, Any]:
    row = _one(
        db,
        """
        SELECT
            COUNT(*) AS count,
            AVG(duration_seconds) AS avg_duration,
            MAX(duration_seconds) AS max_duration
        FROM export_logs
        WHERE action = ? AND duration_seconds IS NOT NULL
        """,
        ("generation_liens",),
    )
    return {
        "count": _to_int(row.get("count") if row else 0),
        "avg_duration": _to_float(row.get("avg_duration") if row else None),
        "max_duration": _to_float(row.get("max_duration") if row else None),
    }


def _latest_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not events:
        return None
    return events[0]


def _duration_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    values = []
    for event in events:
        value = (
            event.get("duree_totale_secondes")
            or event.get("duration_seconds")
            or event.get("duration")
        )
        numeric = _to_float(value)
        if numeric is not None:
            values.append(numeric)
    if not values:
        return {"count": 0, "avg_duration": None, "max_duration": None}
    return {
        "count": len(values),
        "avg_duration": round(mean(values), 4),
        "max_duration": round(max(values), 4),
    }


def _success_rate(events: list[dict[str, Any]]) -> int | None:
    statuses = [
        str(event.get("etat_technique") or event.get("etat") or event.get("status") or "").strip().lower()
        for event in events
    ]
    statuses = [status for status in statuses if status]
    if not statuses:
        return None
    successes = sum(1 for status in statuses if status in {"reussi", "réussi", "success", "succeeded"})
    return round((successes / len(statuses)) * 100)


def _filter_events(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [
        event for event in events
        if str(event.get("type") or "").strip().lower() == event_type
    ]


def _rows(db: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [_row_to_dict(row) for row in db.execute(sql, params).fetchall()]


def _one(db: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    row = db.execute(sql, params).fetchone()
    return _row_to_dict(row) if row is not None else None


def _scalar_int(db: Any, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = _one(db, sql, params)
    if row is None:
        return 0
    return _to_int(_first_row_value(row))


def _scalar_text(db: Any, sql: str, params: tuple[Any, ...] = ()) -> str | None:
    row = _one(db, sql, params)
    value = _first_row_value(row) if row is not None else None
    if value in (None, ""):
        return None
    return str(value)


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "as_dict"):
        return row.as_dict()
    if hasattr(row, "keys"):
        keys = list(row.keys())
        return {str(key): row[key] for key in keys}
    values = tuple(row)
    return {str(index): value for index, value in enumerate(values)}


def _first_row_value(row: dict[str, Any] | None) -> Any:
    if not row:
        return None
    return next(iter(row.values()))


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_duration(value: float | None) -> str:
    if value is None:
        return "Non mesuré"
    if value < 1:
        return f"{round(value * 1000)} ms"
    return f"{value:.2f} s"


def _format_period(start_date: Any, end_date: Any) -> str:
    start_label = _format_date(start_date)
    end_label = _format_date(end_date)
    if start_label == "Non renseigné" and end_label == "Non renseigné":
        return "Période non renseignée"
    return f"{start_label} - {end_label}"


def _format_date(value: Any) -> str:
    parsed = _parse_date(value)
    if parsed is None:
        return "Non renseigné"
    return f"{parsed.day:02d}/{parsed.month:02d}/{parsed.year}"


def _format_short_date(value: Any) -> str:
    parsed = _parse_date(value)
    if parsed is None:
        return str(value or "")
    return f"{parsed.day:02d}/{parsed.month:02d}"


def _format_month_label(key: str) -> str:
    try:
        year, month = key.split("-", 1)
        index = max(1, min(12, int(month))) - 1
        return f"{MONTHS_FR[index]} {year}"
    except Exception:
        return key


def _parse_date(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromisoformat(text[:10])
        except ValueError:
            return None


def _status_label(value: Any) -> str:
    status = str(value or "").strip().lower()
    return {
        "export_links_ready": "Liens générés",
        "success": "Succès",
        "refused": "Refusé",
        "error": "Erreur",
    }.get(status, status.capitalize() if status else "État non renseigné")


def _status_class(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in {"success", "export_links_ready"}:
        return "success"
    if status in {"refused", "error"}:
        return "danger"
    return "neutral"
