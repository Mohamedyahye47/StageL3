from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import db
from databridge_api.app.services.measure_service import enregistrer_mesure

try:
    from deep_translator import GoogleTranslator
except ImportError:  # pragma: no cover - optional dependency at runtime
    GoogleTranslator = None

WORLD_BANK_API_BASE = os.getenv("WB_API_BASE", "https://api.worldbank.org/v2").rstrip("/")
WORLD_BANK_SOURCE_ID = os.getenv("WB_SOURCE_ID", "2").strip() or "2"
WORLD_BANK_TIMEOUT_SECONDS = int(os.getenv("WB_API_TIMEOUT", "60"))
WORLD_BANK_CONNECT_TIMEOUT_SECONDS = int(os.getenv("WB_API_CONNECT_TIMEOUT", "15"))
WORLD_BANK_PAGE_SIZE = max(50, int(os.getenv("WB_METADATA_PAGE_SIZE", "100")))
TOPIC_PAGE_SIZE = max(25, int(os.getenv("WB_TOPIC_PAGE_SIZE", "50")))
COUNTRY_PAGE_SIZE = max(50, int(os.getenv("WB_COUNTRY_PAGE_SIZE", "100")))
TRANSLATION_BATCH_SIZE = max(1, int(os.getenv("WB_TRANSLATION_BATCH_SIZE", "50")))
TRANSLATE_MISSING = os.getenv("WB_TRANSLATE_MISSING", "1").strip() not in {"0", "false", "False"}
USER_AGENT = "RichatDataBridge/2.0 metadata-sync"
METRICS_PATH = Path(
    os.getenv(
        "WB_METADATA_METRICS_PATH",
        str(PROJECT_ROOT / "databridge_web" / "logs" / "wb_metadata_metrics.json"),
    )
)
_CURRENT_TIMING: MetadataSyncTiming | None = None

ENGLISH_HINTS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "share",
    "income",
    "rate",
    "index",
    "ratio",
    "population",
    "gross",
    "total",
    "average",
    "annual",
    "growth",
    "between",
    "using",
    "used",
    "imports",
    "exports",
    "current",
    "prices",
}
FRENCH_HINTS = {
    "le",
    "la",
    "les",
    "de",
    "du",
    "des",
    "pour",
    "avec",
    "dans",
    "par",
    "part",
    "revenu",
    "taux",
    "indice",
    "population",
    "croissance",
    "prix",
    "importations",
    "exportations",
}
MOJIBAKE_MARKERS = ("\u00c3", "\u00c2", "\ufffd", "\u00e2\u20ac")
SYSTEM_TOPIC_BASE_ID = 900000
SYSTEM_TOPICS: dict[str, tuple[int, str, str]] = {
    "a_classer": (SYSTEM_TOPIC_BASE_ID, "À classer", "Indicateurs à vérifier manuellement."),
    "population_demographie": (
        SYSTEM_TOPIC_BASE_ID + 1,
        "Population et démographie",
        "Population, habitants, urbanisation et démographie.",
    ),
    "main_oeuvre": (
        SYSTEM_TOPIC_BASE_ID + 2,
        "Main-d'œuvre et protection sociale",
        "Emploi, chômage, travail et protection sociale.",
    ),
    "education": (SYSTEM_TOPIC_BASE_ID + 3, "Éducation", "Éducation, scolarisation et apprentissage."),
    "sante": (SYSTEM_TOPIC_BASE_ID + 4, "Santé", "Santé, espérance de vie et couverture sanitaire."),
    "economie_croissance": (
        SYSTEM_TOPIC_BASE_ID + 5,
        "Économie et croissance",
        "PIB, croissance, revenus nationaux et prix macroéconomiques.",
    ),
    "secteur_financier": (
        SYSTEM_TOPIC_BASE_ID + 6,
        "Secteur financier",
        "Prix, inflation, monnaie et secteur financier.",
    ),
    "commerce": (
        SYSTEM_TOPIC_BASE_ID + 7,
        "Échanges commerciaux",
        "Commerce, importations, exportations et services.",
    ),
    "balance_paiements": (
        SYSTEM_TOPIC_BASE_ID + 8,
        "Balance des paiements",
        "Compte courant, transferts, flux financiers et investissements.",
    ),
    "dette_exterieure": (SYSTEM_TOPIC_BASE_ID + 9, "Dette extérieure", "Dette et financement extérieur."),
    "efficacite_aide": (
        SYSTEM_TOPIC_BASE_ID + 10,
        "Efficacité de l'aide",
        "Aide publique, donateurs, APD et agences internationales.",
    ),
    "environnement": (
        SYSTEM_TOPIC_BASE_ID + 11,
        "Environnement et climat",
        "Environnement, émissions, climat et ressources naturelles.",
    ),
    "energie_mines": (SYSTEM_TOPIC_BASE_ID + 12, "Énergie et mines", "Énergie, mines et ressources énergétiques."),
    "agriculture": (
        SYSTEM_TOPIC_BASE_ID + 13,
        "Agriculture et développement rural",
        "Agriculture, terres, production rurale et intrants.",
    ),
    "secteur_prive": (
        SYSTEM_TOPIC_BASE_ID + 14,
        "Secteur privé",
        "Entreprises, investissements privés et réglementation des affaires.",
    ),
    "secteur_public": (
        SYSTEM_TOPIC_BASE_ID + 15,
        "Secteur public",
        "Administration, finances publiques et institutions.",
    ),
    "pauvrete": (SYSTEM_TOPIC_BASE_ID + 16, "Pauvreté", "Pauvreté, inégalités et conditions de vie."),
    "developpement_social": (
        SYSTEM_TOPIC_BASE_ID + 17,
        "Développement social",
        "Genre, migration, réfugiés et inclusion sociale.",
    ),
    "genre": (
        SYSTEM_TOPIC_BASE_ID + 18,
        "Genre et parité hommes-femmes",
        "Genre, droits des femmes et égalité.",
    ),
}
# ---------------------------------------------------------------------------
# Règles de complétion DataBridge pour les indicateurs SANS thème officiel WB.
# Versionnées dans le code (aucune lecture de rapport_final.json pendant l'ETL).
# Elles pointent vers des thèmes OFFICIELS World Bank (id 1-21), mais la relation
# produite est marquée origin='databridge_prefix_rule' : ce n'est JAMAIS une
# relation officielle World Bank.
#   2=Aid Effectiveness 3=Economy&Growth 4=Education 8=Health
#   10=Social Protection&Labor 11=Poverty 12=Private Sector
#   15=Social Development 17=Gender
# ---------------------------------------------------------------------------
A_CLASSER_ID = SYSTEM_TOPICS["a_classer"][0]  # 900000

PRODUCTION_RELATIONS_TABLE = "indicator_topics"
INDICATOR_TOPICS_TABLE = PRODUCTION_RELATIONS_TABLE

# Cas sensibles -> toujours manual_review. Vérifiés EN PREMIER (ordre important).
DATABRIDGE_SENSITIVE_RULES: tuple[tuple[str, int, str], ...] = (
    ("HD_HCIP_OVRL_TO", A_CLASSER_ID, "manual_review"),  # indice TOTAL : pas de thème sûr
    ("HD_HCIP_OVRL_FE",            17, "manual_review"),  # Gender plausible, à confirmer
    ("HD_HCIP_OVRL_MA",            17, "manual_review"),
    ("IC.FRM.CO2",                 12, "manual_review"),  # firme mais contenu Environnement
    ("IC.FRM.ENGM",                12, "manual_review"),  # firme mais contenu Énergie
    ("DT.NFL",                      2, "manual_review"),  # net official flows = aide, pas dette
    ("IC.FRM",                     12, "manual_review"),  # firmes : Private Sector probable
)

# Cas solides -> review_status='none'. Préfixes longs avant préfixes courts.
DATABRIDGE_PREFIX_RULES_COMPLETION: tuple[tuple[str, int, str], ...] = (
    ("HD_HCIP_EDUC",  4, "none"),
    ("HD_HCIP_HLTH",  8, "none"),
    ("HD_HCIP_OTJL", 10, "none"),
    ("SH_UHC",        8, "none"),
    ("GD_WBL",       17, "none"),
    ("SM.POP",       15, "none"),
    ("SI.POV",       11, "none"),
    ("SI.SPR",       11, "none"),
    ("DC.DAC",        2, "none"),
    ("IC.BRE",       12, "none"),
    ("IC.CUS",       12, "none"),
    ("IC.MNG",       12, "none"),
    ("SE.",           4, "none"),
    ("SH_",           8, "none"),
    ("SH.",           8, "none"),
    ("NE.",           3, "none"),
    ("PA.",           3, "none"),
)


def _databridge_complement_for_code(code: str) -> tuple[int | None, str]:
    """Retourne (topic_id_officiel, review_status) pour un indicateur SANS thème
    officiel, ou (None, 'none') si aucune règle ne s'applique. topic_id peut être
    A_CLASSER_ID (900000) pour un cas à vérifier sans thème sûr."""
    up = (code or "").upper()
    for prefix, topic_id, status in DATABRIDGE_SENSITIVE_RULES:
        if up.startswith(prefix.upper()):
            return topic_id, status
    for prefix, topic_id, status in DATABRIDGE_PREFIX_RULES_COMPLETION:
        if up.startswith(prefix.upper()):
            return topic_id, status
    return None, "none"


@dataclass(slots=True)
class MetadataQualityTracker:
    fallbacks_anglais: int = 0
    traductions_echouees: int = 0
    champs_vides_corriges: int = 0
    themes_fallback: int = 0
    indicateurs_a_classer: list[str] = field(default_factory=list)
    doublons_ignores: int = 0


_CURRENT_QUALITY: MetadataQualityTracker | None = None


class MetadataSyncError(RuntimeError):
    pass


@dataclass(slots=True)
class MetadataSyncTiming:
    started_at_iso: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    _started_at: float = field(default_factory=time.perf_counter, repr=False)
    stages: dict[str, float] = field(default_factory=dict)
    api_calls: list[dict[str, Any]] = field(default_factory=list)
    batches: list[dict[str, Any]] = field(default_factory=list)

    @contextmanager
    def track(self, stage: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.add_stage(stage, time.perf_counter() - start)

    def add_stage(self, stage: str, duration_seconds: float) -> None:
        self.stages[stage] = round(self.stages.get(stage, 0.0) + duration_seconds, 4)

    def add_api_call(
        self,
        *,
        path: str,
        language: str | None,
        page: Any,
        attempt: int,
        duration_seconds: float,
        records: int,
    ) -> None:
        self.api_calls.append(
            {
                "path": path,
                "language": language or "en",
                "page": page,
                "attempt": attempt,
                "duration_seconds": round(duration_seconds, 4),
                "records": records,
            }
        )

    def add_batch(self, *, stage: str, batch_id: int, records: int, duration_seconds: float) -> None:
        throughput = records / duration_seconds if duration_seconds > 0 else 0
        self.batches.append(
            {
                "stage": stage,
                "batch_id": batch_id,
                "records": records,
                "duration_seconds": round(duration_seconds, 4),
                "throughput_records_per_second": round(throughput, 4),
            }
        )

    def export(self, summary: dict[str, Any]) -> dict[str, Any]:
        total_seconds = time.perf_counter() - self._started_at
        api_total = sum(item["duration_seconds"] for item in self.api_calls)
        batch_total = sum(item["duration_seconds"] for item in self.batches)
        payload = {
            "started_at": self.started_at_iso,
            "finished_at": datetime.now(UTC).isoformat(),
            "total_seconds": round(total_seconds, 4),
            "status": summary.get("status", "unknown"),
            "summary": summary,
            "stages": [
                {"name": name, "duration_seconds": duration}
                for name, duration in self.stages.items()
            ],
            "api_calls": self.api_calls,
            "batches": self.batches,
            "totals": {
                "api_call_seconds": round(api_total, 4),
                "batch_seconds": round(batch_total, 4),
                "api_call_count": len(self.api_calls),
                "batch_count": len(self.batches),
            },
        }
        return payload


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
    session.headers.update({"User-Agent": USER_AGENT})
    return session


@dataclass(slots=True)
class MetadataTranslator:
    enabled: bool = TRANSLATE_MISSING and GoogleTranslator is not None
    _cache: dict[str, str] = field(init=False, repr=False, default_factory=dict)
    _translator: Any = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        self._translator = GoogleTranslator(source="en", target="fr") if self.enabled else None

    def translate(self, text: str) -> str:
        clean = _clean_text(text)
        if not clean:
            return ""
        cached = self._cache.get(clean)
        if cached is not None:
            return cached
        if not self.enabled or self._translator is None:
            self._cache[clean] = clean
            return clean
        try:
            translated = _clean_text(self._translator.translate(clean))
        except Exception:
            if _CURRENT_QUALITY is not None:
                _CURRENT_QUALITY.traductions_echouees += 1
            translated = clean
        if translated == clean and _CURRENT_QUALITY is not None:
            _CURRENT_QUALITY.fallbacks_anglais += 1
        self._cache[clean] = translated or clean
        return self._cache[clean]

    def translate_batch(self, texts: list[str]) -> list[str]:
        return [self.translate(text) for text in texts]


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _run_local_relations_checks(conn, source_id: int) -> tuple[bool, list[str]]:
    """Contrôles de validation du fichier SQLite local avant import Turso."""
    table = PRODUCTION_RELATIONS_TABLE
    messages: list[str] = []

    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    sans_theme = conn.execute(
        f"""
        SELECT COUNT(*) FROM indicators i
        LEFT JOIN {table} it ON it.indicator_id = i.id
        WHERE i.source_id = ? AND it.indicator_id IS NULL
        """,
        (source_id,),
    ).fetchone()[0]
    by_origin = {
        row["origin"]: row["n"]
        for row in conn.execute(
            f"SELECT origin, COUNT(*) AS n FROM {table} GROUP BY origin"
        ).fetchall()
    }
    manual = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE review_status = 'manual_review'"
    ).fetchone()[0]

    n_official = by_origin.get("official_world_bank", 0)
    n_databridge = by_origin.get("databridge_prefix_rule", 0)

    messages.append(f"relations totales        : {total}")
    messages.append(f"indicateurs sans theme   : {sans_theme}")
    messages.append(f"official_world_bank      : {n_official}")
    messages.append(f"databridge_prefix_rule   : {n_databridge}")
    messages.append(f"manual_review            : {manual}")

    ok = True
    if total <= 0:
        ok = False; messages.append("ECHEC: aucune relation locale reconstruite.")
    if sans_theme != 0:
        ok = False; messages.append(f"ECHEC: {sans_theme} indicateur(s) sans theme.")
    if n_official <= 0:
        ok = False; messages.append("ECHEC: aucune relation official_world_bank.")
    if n_databridge <= 0:
        ok = False; messages.append("ECHEC: aucune relation databridge_prefix_rule.")
    if manual <= 0:
        messages.append("AVERTISSEMENT: aucun manual_review (verifie si attendu).")

    return ok, messages


def sync_world_bank_metadata(force: bool = False, limit: int | None = None, *, require_sqlite: bool = True) -> dict[str, Any]:
    global _CURRENT_TIMING, _CURRENT_QUALITY

    timing = MetadataSyncTiming()
    _CURRENT_TIMING = timing
    _CURRENT_QUALITY = MetadataQualityTracker()
    summary: dict[str, Any] = {}

    if require_sqlite and getattr(db, "DB_BACKEND", "sqlite") != "sqlite":
        raise MetadataSyncError(
            "Mode local requis : ce script doit reconstruire databridge.db en SQLite. "
            "Passe DATABRIDGE_DB_BACKEND=sqlite ou lance avec --local-sqlite. "
            "Ne lance plus un ETL World Bank long directement sur Turso."
        )

    with timing.track("database_init"):
        db.init_db()
    with timing.track("translator_setup"):
        translator = MetadataTranslator()
    with timing.track("world_bank_session_setup"):
        session = _build_world_bank_session()

    try:
        with timing.track("metadata_sync_total"):
            with db.get_connection() as conn:
                with timing.track("source_registration"):
                    source_id = db.register_source(
                        db.WORLD_BANK_SOURCE["code"],
                        db.WORLD_BANK_SOURCE["name"],
                        db.WORLD_BANK_SOURCE["base_url"],
                        db.WORLD_BANK_SOURCE["description"],
                        conn=conn,
                    )
                if not force and db.source_has_metadata(source_id, conn=conn):
                    summary = db.metadata_summary(conn=conn, source_id=source_id)
                    quality_report = validate_metadata_quality(conn=conn, source_id=source_id, timing=timing)
                    summary.update(
                        {
                            "source_code": db.WORLD_BANK_SOURCE["code"],
                            "status": "skipped",
                            "translated_with_fallback": int(translator.enabled),
                            "quality_status": quality_report["etat"],
                        }
                    )
                    return summary

                topic_lookup = _sync_topics(session, translator, source_id, conn=conn)
                conn.commit()  # borne la transaction + rafraichit le stream Turso

                countries_count = _sync_countries(session, translator, conn=conn)
                conn.commit()  # borne la transaction + rafraichit le stream Turso

                # --- Build LOCAL SQLite ---
                # Dans la stratégie finale, le long ETL World Bank écrit uniquement
                # dans databridge.db local. Turso sera recréé/importé ensuite.
                indicators_count = _sync_indicators(
                    session,
                    translator,
                    source_id,
                    topic_lookup,
                    limit=limit,
                    conn=conn,
                )
                conn.commit()

                quality_report = validate_metadata_quality(
                    conn=conn, source_id=source_id, timing=timing
                )
                conn.commit()

                checks_ok, checks_msg = _run_local_relations_checks(conn, source_id)
                print("[WB metadata] controles build local:")
                for line in checks_msg:
                    print("   ", line)
                if not checks_ok:
                    raise RuntimeError(
                        "Controles build local non passes : databridge.db ne doit pas etre importe vers Turso. "
                        "Voir les messages ci-dessus."
                    )

                with timing.track("metadata_summary"):
                    summary = db.metadata_summary(conn=conn, source_id=source_id)
                summary.update(
                    {
                        "source_code": db.WORLD_BANK_SOURCE["code"],
                        "status": "synced",
                        "topics_synced": len(topic_lookup),
                        "countries_synced": countries_count,
                        "indicators_synced": indicators_count,
                        "translated_with_fallback": int(translator.enabled),
                        "quality_status": quality_report["etat"],
                    }
                )
                return summary
    finally:
        if summary:
            _write_timing_metrics(timing.export(summary))
        _CURRENT_TIMING = None
        _CURRENT_QUALITY = None


def _sync_topics(
    session: requests.Session,
    translator: MetadataTranslator,
    source_id: int,
    *,
    conn,
) -> dict[str, int]:
    existing_topics = _load_existing_topics(conn, source_id)
    with _timing_stage("topics_fetch_en"):
        topics_en = _fetch_paginated_safe(session, "topic", per_page=TOPIC_PAGE_SIZE)
    with _timing_stage("topics_fetch_fr"):
        topics_fr_lookup = {
            _clean_text(item.get("id")): item
            for item in _fetch_paginated_safe(session, "topic", language="fr", per_page=TOPIC_PAGE_SIZE)
        }

    topic_lookup: dict[str, int] = {}
    if not topics_en:
        print("[WB metadata] warning: topic endpoint unavailable, topics will be derived from indicators.")
        return topic_lookup

    with _timing_stage("topics_transform"):
        records = []
        for item in topics_en:
            topic_key = _clean_text(item.get("id"))
            if not topic_key.isdigit():
                continue
            french_item = topics_fr_lookup.get(topic_key, {})
            records.append(
                {
                    "record_key": topic_key,
                    "name_en": _clean_text(item.get("value")),
                    "description_en": _clean_text(item.get("sourceNote")),
                    "name_fr": _clean_text(french_item.get("value")),
                    "description_fr": _clean_text(french_item.get("sourceNote")),
                }
            )

    for batch_index, batch in enumerate(_chunked(records, TRANSLATION_BATCH_SIZE), start=1):
        translate_start = time.perf_counter()
        resolved, field_sources = _resolve_batch_fields(
            batch,
            field_specs=(
                ("name", "name_en", "name_fr"),
                ("description", "description_en", "description_fr"),
            ),
            translator=translator,
        )
        _record_timing_batch(
            stage="topics_translation",
            batch_id=batch_index,
            records=len(batch),
            duration_seconds=time.perf_counter() - translate_start,
        )

        write_start = time.perf_counter()
        for record in batch:
            topic_id = int(record["record_key"])
            existing = existing_topics.get(record["record_key"], {})
            name, _, _ = _choose_best_text(
                record["name_en"],
                resolved[record["record_key"]].get("name", ""),
                field_sources[record["record_key"]].get("name"),
                existing.get("name", ""),
                "name",
            )
            description, _, _ = _choose_best_text(
                record["description_en"],
                resolved[record["record_key"]].get("description", ""),
                field_sources[record["record_key"]].get("description"),
                existing.get("description", ""),
                "description",
            )
            final_name = name or record["name_en"] or f"Theme {topic_id}"
            db.upsert_topic(
                {
                    "id": topic_id,
                    "source_id": source_id,
                    "name": final_name,
                    "description": description or None,
                },
                conn=conn,
            )
            topic_lookup[record["record_key"]] = topic_id
            existing_topics[record["record_key"]] = {
                "name": final_name,
                "description": description or "",
            }
        _record_timing_batch(
            stage="topics_db_write",
            batch_id=batch_index,
            records=len(batch),
            duration_seconds=time.perf_counter() - write_start,
        )
    return topic_lookup


def _sync_countries(
    session: requests.Session,
    translator: MetadataTranslator,
    *,
    conn,
) -> int:
    with _timing_stage("countries_fetch_en"):
        countries_en = _fetch_paginated_safe(session, "country", per_page=COUNTRY_PAGE_SIZE)
    with _timing_stage("countries_fetch_fr"):
        countries_fr_lookup = {
            _clean_text(item.get("id")).upper(): item
            for item in _fetch_paginated_safe(session, "country", language="fr", per_page=COUNTRY_PAGE_SIZE)
        }

    if not countries_en:
        print("[WB metadata] warning: country endpoint unavailable, keeping locally seeded countries.")
        return 0

    synced = 0
    transform_write_start = time.perf_counter()
    for item in countries_en:
        code_iso3 = _clean_text(item.get("id")).upper()
        code_iso2 = _clean_text(item.get("iso2Code")).upper()
        if not _is_valid_country_codes(code_iso2, code_iso3):
            continue
        french_item = countries_fr_lookup.get(code_iso3, {})
        region_en = _nested_value(item, "region")
        region_fr = _nested_value(french_item, "region")
        name = _resolve_french_text(
            _clean_text(item.get("name")),
            _clean_text(french_item.get("name")),
            translator,
        ) or code_iso3
        region = _resolve_french_text(region_en, region_fr, translator) or None
        enabled = int(region_en.lower() != "aggregates")
        db.upsert_country(
            {
                "code_iso3": code_iso3,
                "code_iso2": code_iso2,
                "wb_code": code_iso3,
                "name": name,
                "region": region,
                "enabled": enabled,
            },
            conn=conn,
        )
        synced += 1
    _record_timing_batch(
        stage="countries_transform_write",
        batch_id=1,
        records=synced,
        duration_seconds=time.perf_counter() - transform_write_start,
    )
    return synced


def _sync_indicators(
    session: requests.Session,
    translator: MetadataTranslator,
    source_id: int,
    topic_lookup: dict[str, int],
    *,
    limit: int | None,
    conn,
) -> int:
    path = f"source/{WORLD_BANK_SOURCE_ID}/indicator"
    existing_indicators = _load_existing_indicators(conn, source_id)
    with _timing_stage("indicators_fetch_en"):
        indicators_en = _fetch_paginated(session, path, per_page=WORLD_BANK_PAGE_SIZE)
    if limit is not None:
        indicators_en = indicators_en[: max(0, limit)]
    with _timing_stage("indicators_fetch_fr"):
        indicators_fr_lookup = {
            _clean_text(item.get("id")): item
            for item in _fetch_paginated_safe(session, path, language="fr", per_page=min(WORLD_BANK_PAGE_SIZE, 150))
        }

    with _timing_stage("indicators_transform"):
        records = []
        for item in indicators_en:
            code = _clean_text(item.get("id"))
            if not code:
                continue
            french_item = indicators_fr_lookup.get(code, {})
            records.append(
                {
                    "record_key": code,
                    "code": code,
                    "name_en": _clean_text(item.get("name")),
                    "description_en": _clean_text(item.get("sourceNote")),
                    "name_fr": _clean_text(french_item.get("name")),
                    "description_fr": _clean_text(french_item.get("sourceNote")),
                    "unit": _first_non_empty(_clean_text(french_item.get("unit")), _clean_text(item.get("unit"))) or None,
                    "periodicity": _first_non_empty(
                        _clean_text(french_item.get("periodicity")),
                        _clean_text(item.get("periodicity")),
                        _clean_text(item.get("sourcePeriodic")),
                    )
                    or None,
                    "indicator_en": item,
                    "indicator_fr": french_item,
                }
            )

    synced = 0
    for batch_index, batch in enumerate(_chunked(records, TRANSLATION_BATCH_SIZE), start=1):
        translate_start = time.perf_counter()
        resolved, field_sources = _resolve_batch_fields(
            batch,
            field_specs=(
                ("name", "name_en", "name_fr"),
                ("description", "description_en", "description_fr"),
            ),
            translator=translator,
        )
        _record_timing_batch(
            stage="indicators_translation",
            batch_id=batch_index,
            records=len(batch),
            duration_seconds=time.perf_counter() - translate_start,
        )

        write_start = time.perf_counter()
        for record in batch:
            code = record["code"]
            existing = existing_indicators.get(code, {})
            name, _, _ = _choose_best_text(
                record["name_en"],
                resolved[code].get("name", ""),
                field_sources[code].get("name"),
                existing.get("name", ""),
                "name",
            )
            description, _, _ = _choose_best_text(
                record["description_en"],
                resolved[code].get("description", ""),
                field_sources[code].get("description"),
                existing.get("description", ""),
                "description",
            )
            final_name = name or record["name_en"] or code
            topic_ids = _extract_topic_ids(
                record["indicator_en"],
                record["indicator_fr"],
                topic_lookup,
                source_id,
                translator,
                conn,
            )
            indicator_id = db.upsert_indicator(
                {
                    "source_id": source_id,
                    "code": code,
                    "name": final_name,
                    "description": description or None,
                    "unit": record["unit"],
                    "periodicity": record["periodicity"],
                },
                conn=conn,
            )
            link_start = time.perf_counter()
            db.replace_indicator_topics(
                indicator_id, topic_ids, conn=conn, table=INDICATOR_TOPICS_TABLE
            )
            if _CURRENT_TIMING is not None:
                _CURRENT_TIMING.add_stage("liens_indicateur_theme", time.perf_counter() - link_start)
            existing_indicators[code] = {
                "name": final_name,
                "description": description or "",
            }
            synced += 1
        _record_timing_batch(
            stage="indicators_db_write",
            batch_id=batch_index,
            records=len(batch),
            duration_seconds=time.perf_counter() - write_start,
        )
        # Persiste chaque lot : si le stream Turso expire pendant la traduction
        # du lot suivant, les lots deja ecrits sont conserves (pas de grosse
        # transaction unique qui se perdrait entierement).
        conn.commit()
    return synced


def _extract_topic_ids(
    item_en: dict[str, Any],
    item_fr: dict[str, Any],
    topic_lookup: dict[str, int],
    source_id: int,
    translator: MetadataTranslator,
    conn,
) -> list[int]:
    topic_ids: list[int] = []
    topics_fr_by_id = {
        _clean_text(topic.get("id")): topic
        for topic in (item_fr.get("topics") or [])
        if _clean_text(topic.get("id"))
    }

    for topic in item_en.get("topics") or []:
        topic_key = _clean_text(topic.get("id"))
        topic_id = topic_lookup.get(topic_key)
        if topic_id is None and topic_key.isdigit():
            topic_fr = topics_fr_by_id.get(topic_key, {})
            topic_id = int(topic_key)
            db.upsert_topic(
                {
                    "id": topic_id,
                    "source_id": source_id,
                    "name": _resolve_french_text(
                        _clean_text(topic.get("value")),
                        _clean_text(topic_fr.get("value")),
                        translator,
                    )
                    or f"Theme {topic_id}",
                    "description": None,
                },
                conn=conn,
            )
            topic_lookup[topic_key] = topic_id
        if topic_id is not None:
            topic_ids.append(topic_id)
    # Plus de fallback ici : les indicateurs sans thème officiel restent SANS
    # relation après la synchro. Ils sont complétés (avec une origine explicite
    # databridge_prefix_rule) en phase de validation via
    # _repair_missing_indicator_topics. Cela évite qu'une relation de complément
    # soit écrite via replace_indicator_topics avec l'origine officielle par défaut.
    return sorted(set(topic_ids))


def _ensure_system_topic(topic_key: str, *, source_id: int, conn) -> int:
    topic_id, name, description = SYSTEM_TOPICS.get(topic_key, SYSTEM_TOPICS["a_classer"])
    existing = conn.execute(
        """
        SELECT id
        FROM topics
        WHERE source_id = ? AND LOWER(TRIM(name)) = LOWER(TRIM(?))
        LIMIT 1
        """,
        (source_id, name),
    ).fetchone()
    if existing is not None:
        return int(existing["id"])

    id_owner = conn.execute(
        "SELECT name FROM topics WHERE id = ? LIMIT 1",
        (topic_id,),
    ).fetchone()
    if id_owner is not None and _clean_text(id_owner["name"]).lower() != _clean_text(name).lower():
        max_row = conn.execute(
            "SELECT COALESCE(MAX(id), ?) AS max_id FROM topics",
            (SYSTEM_TOPIC_BASE_ID,),
        ).fetchone()
        topic_id = max(int(max_row["max_id"] or SYSTEM_TOPIC_BASE_ID) + 1, SYSTEM_TOPIC_BASE_ID + 100)

    db.upsert_topic(
        {
            "id": topic_id,
            "source_id": source_id,
            "name": name,
            "description": description,
        },
        conn=conn,
    )
    return topic_id


def normalize_for_classification(value: str | None) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.lower()


def _fetch_paginated(
    session: requests.Session,
    path: str,
    *,
    language: str | None = None,
    per_page: int,
) -> list[dict[str, Any]]:
    sizes = [per_page]
    if per_page > 50:
        sizes.append(50)

    last_error: MetadataSyncError | None = None
    for size in sizes:
        items: list[dict[str, Any]] = []
        page = 1
        total_pages = 1
        try:
            while page <= total_pages:
                payload = _wb_get(
                    session,
                    path,
                    language=language,
                    params={"page": page, "per_page": size, "format": "json"},
                )
                meta = payload[0] or {}
                total_pages = int(meta.get("pages", 1) or 1)
                page_items = payload[1] or []
                items.extend(page_items)
                page += 1
            return items
        except MetadataSyncError as exc:
            last_error = exc
            if size != sizes[-1]:
                scope = f"{path} [{language}]" if language else path
                print(f"[WB metadata] warning: retrying {scope} with per_page=50 after error: {exc}")
    if last_error is not None:
        raise last_error
    return []


def _fetch_paginated_safe(
    session: requests.Session,
    path: str,
    *,
    language: str | None = None,
    per_page: int,
) -> list[dict[str, Any]]:
    try:
        return _fetch_paginated(
            session,
            path,
            language=language,
            per_page=per_page,
        )
    except MetadataSyncError as exc:
        scope = f"{path} [{language}]" if language else path
        print(f"[WB metadata] warning: optional fetch failed for {scope}: {exc}")
        return []


def _resolve_batch_fields(
    records: list[dict[str, Any]],
    *,
    field_specs: tuple[tuple[str, str, str], ...],
    translator: MetadataTranslator,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    resolved: dict[str, dict[str, str]] = {}
    field_sources: dict[str, dict[str, str]] = {}
    pending_texts: list[str] = []
    pending_targets: list[tuple[str, str, str]] = []

    for record in records:
        record_key = record["record_key"]
        resolved[record_key] = {}
        field_sources[record_key] = {}
        for field_name, source_key, official_key in field_specs:
            source_text = _clean_text(record.get(source_key))
            official_text = _clean_text(record.get(official_key))
            if not source_text:
                resolved[record_key][field_name] = ""
                field_sources[record_key][field_name] = "empty_source"
                continue
            if official_text and not _should_translate(source_text, official_text, field_name):
                resolved[record_key][field_name] = official_text
                field_sources[record_key][field_name] = "official_world_bank"
                continue
            pending_texts.append(source_text)
            pending_targets.append((record_key, field_name, source_text))

    if pending_texts:
        translated = translator.translate_batch(pending_texts)
        for (record_key, field_name, source_text), translated_text in zip(pending_targets, translated):
            candidate = _clean_text(translated_text)
            resolved[record_key][field_name] = candidate
            field_sources[record_key][field_name] = (
                "google_translate" if candidate and candidate != _clean_text(source_text) else "fallback_english"
            )

    return resolved, field_sources


def _wb_get(
    session: requests.Session,
    path: str,
    *,
    language: str | None = None,
    params: dict[str, Any] | None = None,
) -> list[Any]:
    params = dict(params or {})
    params.setdefault("format", "json")
    url = _world_bank_url(path, language=language)
    last_error: Exception | None = None

    for attempt in range(1, 6):
        started = time.perf_counter()
        try:
            response = session.get(
                url,
                params=params,
                timeout=(WORLD_BANK_CONNECT_TIMEOUT_SECONDS, WORLD_BANK_TIMEOUT_SECONDS),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or len(payload) < 2:
                raise MetadataSyncError(f"Reponse World Bank invalide pour {path}.")
            _record_api_timing(
                path=path,
                language=language,
                page=params.get("page"),
                attempt=attempt,
                duration_seconds=time.perf_counter() - started,
                records=len(payload[1] or []),
            )
            return payload
        except Exception as exc:  # pragma: no cover - network failures are environment-specific
            last_error = exc
            if attempt == 5:
                break
            time.sleep(min(4, attempt))

    raise MetadataSyncError(f"Echec World Bank sur {path}: {last_error}") from last_error


def _world_bank_url(path: str, language: str | None = None) -> str:
    clean_path = path.lstrip("/")
    if language:
        return f"{WORLD_BANK_API_BASE}/{language.strip('/')}/{clean_path}"
    return f"{WORLD_BANK_API_BASE}/{clean_path}"


@contextmanager
def _timing_stage(name: str):
    timing = _CURRENT_TIMING
    if timing is None:
        yield
        return
    with timing.track(name):
        yield


def _record_timing_batch(*, stage: str, batch_id: int, records: int, duration_seconds: float) -> None:
    if _CURRENT_TIMING is not None:
        _CURRENT_TIMING.add_batch(
            stage=stage,
            batch_id=batch_id,
            records=records,
            duration_seconds=duration_seconds,
        )


def _record_api_timing(
    *,
    path: str,
    language: str | None,
    page: Any,
    attempt: int,
    duration_seconds: float,
    records: int,
) -> None:
    if _CURRENT_TIMING is not None:
        _CURRENT_TIMING.add_api_call(
            path=path,
            language=language,
            page=page,
            attempt=attempt,
            duration_seconds=duration_seconds,
            records=records,
        )


def _write_timing_metrics(payload: dict[str, Any]) -> None:
    try:
        METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        METRICS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[WB metadata] warning: could not write timing metrics to {METRICS_PATH}: {exc}")
    quality_status = payload.get("summary", {}).get("quality_status")
    etat_mesure = "Réussi" if payload.get("status") in {"synced", "skipped"} else "Inconnu"
    if quality_status in {"Échec qualité", "Réussi avec avertissements"}:
        etat_mesure = quality_status
    enregistrer_mesure(
        "base",
        {
            "type": "synchronisation_banque_mondiale",
            "etat": etat_mesure,
            "etat_qualite": quality_status,
            "duree_totale_secondes": payload.get("total_seconds"),
            "nombre_appels_http": payload.get("totals", {}).get("api_call_count", 0),
            "nombre_batches": payload.get("totals", {}).get("batch_count", 0),
            "nombre_themes": payload.get("summary", {}).get("topics"),
            "nombre_pays": payload.get("summary", {}).get("countries"),
            "nombre_indicateurs": payload.get("summary", {}).get("indicators"),
            "etapes": payload.get("stages", []),
            "appels_banque_mondiale": _resume_appels_banque_mondiale(payload.get("api_calls", [])),
            "batches": payload.get("batches", []),
        },
    )


def _resume_appels_banque_mondiale(api_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groupes: dict[str, list[dict[str, Any]]] = {}
    for appel in api_calls:
        path = str(appel.get("path", "autre"))
        if path == "topic":
            label = "Thèmes"
        elif path == "country":
            label = "Pays"
        elif "indicator" in path:
            label = "Indicateurs"
        else:
            label = path
        groupes.setdefault(label, []).append(appel)

    resume = []
    for label, appels in groupes.items():
        durees = [float(appel.get("duration_seconds") or 0) for appel in appels]
        resume.append(
            {
                "type_requete": label,
                "nombre_appels": len(appels),
                "duree_moyenne_secondes": round(sum(durees) / len(durees), 4) if durees else 0,
                "duree_minimale_secondes": round(min(durees), 4) if durees else 0,
                "duree_maximale_secondes": round(max(durees), 4) if durees else 0,
                "elements_recus": sum(int(appel.get("records") or 0) for appel in appels),
                "nombre_pages": len({appel.get("page") for appel in appels}),
            }
        )
    return resume


def validate_metadata_quality(*, conn, source_id: int, timing: MetadataSyncTiming | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    _repair_visible_texts(conn=conn, source_id=source_id)
    _repair_missing_indicator_topics(conn=conn, source_id=source_id)
    _remove_resolved_a_classer_links(conn=conn, source_id=source_id)
    _merge_duplicate_topics(conn=conn, source_id=source_id)
    conn.commit()

    report = _build_metadata_quality_report(conn=conn, source_id=source_id)
    report["duree_controle_secondes"] = round(time.perf_counter() - started, 4)
    if timing is not None:
        report["appels_http"] = len(timing.api_calls)
        report["duree_totale_secondes"] = round(time.perf_counter() - timing._started_at, 4)
    else:
        report["appels_http"] = 0
        report["duree_totale_secondes"] = report["duree_controle_secondes"]

    _write_quality_report(report)
    _print_quality_report(report)
    return report


def _repair_visible_texts(*, conn, source_id: int) -> None:
    rows = conn.execute(
        """
        SELECT id, code
        FROM indicators
        WHERE source_id = ? AND (name IS NULL OR TRIM(name) = '')
        """,
        (source_id,),
    ).fetchall()
    for row in rows:
        fallback = f"Indicateur World Bank {row['code'] or row['id']}"
        conn.execute("UPDATE indicators SET name = ? WHERE id = ?", (fallback, row["id"]))
        if _CURRENT_QUALITY is not None:
            _CURRENT_QUALITY.champs_vides_corriges += 1

    topic_rows = conn.execute(
        """
        SELECT id
        FROM topics
        WHERE source_id = ? AND (name IS NULL OR TRIM(name) = '')
        """,
        (source_id,),
    ).fetchall()
    for row in topic_rows:
        conn.execute("UPDATE topics SET name = ? WHERE id = ?", (f"Thème World Bank {row['id']}", row["id"]))
        if _CURRENT_QUALITY is not None:
            _CURRENT_QUALITY.champs_vides_corriges += 1

    country_rows = conn.execute(
        """
        SELECT id, code_iso3
        FROM countries
        WHERE name IS NULL OR TRIM(name) = ''
        """
    ).fetchall()
    for row in country_rows:
        conn.execute("UPDATE countries SET name = ? WHERE id = ?", (row["code_iso3"] or f"Pays {row['id']}", row["id"]))
        if _CURRENT_QUALITY is not None:
            _CURRENT_QUALITY.champs_vides_corriges += 1


def _repair_missing_indicator_topics(*, conn, source_id: int) -> None:
    """Complète les indicateurs SANS aucune relation (donc sans thème officiel WB)
    vers un thème officiel 1-21, via les règles versionnées. La relation est
    marquée origin='databridge_prefix_rule' (+ review_status). Ne touche jamais
    aux indicateurs qui ont déjà une relation (les officiels)."""
    rows = conn.execute(
        f"""
        SELECT i.id, i.code, i.name, i.description
        FROM indicators i
        LEFT JOIN {INDICATOR_TOPICS_TABLE} it ON it.indicator_id = i.id
        WHERE i.source_id = ? AND it.indicator_id IS NULL
        """,
        (source_id,),
    ).fetchall()
    for row in rows:
        code = _clean_text(row["code"])
        topic_id, review_status = _databridge_complement_for_code(code)

        if topic_id is None or topic_id == A_CLASSER_ID:
            # aucune règle, ou cas sensible sans thème sûr -> bucket "À classer" visible
            topic_id = _ensure_system_topic("a_classer", source_id=source_id, conn=conn)
            if review_status == "none":
                review_status = "manual_review"

        conn.execute(
            f"""
            INSERT OR IGNORE INTO {INDICATOR_TOPICS_TABLE}
                (indicator_id, topic_id, origin, review_status)
            VALUES (?, ?, 'databridge_prefix_rule', ?)
            """,
            (row["id"], topic_id, review_status),
        )
        if _CURRENT_QUALITY is not None:
            _CURRENT_QUALITY.themes_fallback += 1
            if review_status == "manual_review":
                _CURRENT_QUALITY.indicateurs_a_classer.append(code)


def _remove_resolved_a_classer_links(*, conn, source_id: int) -> None:
    a_classer_id = SYSTEM_TOPICS["a_classer"][0]
    conn.execute(
        f"""
        DELETE FROM {INDICATOR_TOPICS_TABLE}
        WHERE topic_id = ?
          AND indicator_id IN (
              SELECT i.id
              FROM indicators i
              JOIN {INDICATOR_TOPICS_TABLE} it ON it.indicator_id = i.id
              WHERE i.source_id = ?
              GROUP BY i.id
              HAVING SUM(CASE WHEN it.topic_id <> ? THEN 1 ELSE 0 END) > 0
          )
        """,
        (a_classer_id, source_id, a_classer_id),
    )


def _merge_duplicate_topics(*, conn, source_id: int) -> None:
    rows = conn.execute(
        "SELECT id, name FROM topics WHERE source_id = ? ORDER BY id",
        (source_id,),
    ).fetchall()
    groups: dict[str, list[tuple[int, str]]] = {}
    for row in rows:
        key = _normalize_topic_name_for_dedup(row["name"])
        if key:
            groups.setdefault(key, []).append((int(row["id"]), row["name"]))

    for items in groups.values():
        if len(items) < 2:
            continue
        canonical_id = min(topic_id for topic_id, _ in items)
        duplicate_ids = [topic_id for topic_id, _ in items if topic_id != canonical_id]
        for duplicate_id in duplicate_ids:
            linked_indicators = conn.execute(
                "SELECT indicator_id FROM indicator_topics WHERE topic_id = ?",
                (duplicate_id,),
            ).fetchall()
            for linked in linked_indicators:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO indicator_topics (indicator_id, topic_id)
                    VALUES (?, ?)
                    """,
                    (linked["indicator_id"], canonical_id),
                )
            conn.execute("DELETE FROM indicator_topics WHERE topic_id = ?", (duplicate_id,))
            conn.execute("DELETE FROM topics WHERE id = ?", (duplicate_id,))
            if _CURRENT_QUALITY is not None:
                _CURRENT_QUALITY.doublons_ignores += 1


def _normalize_topic_name_for_dedup(value: str | None) -> str:
    text = normalize_for_classification(value)
    text = text.replace("œ", "oe").replace("’", "'").replace("`", "'")
    return re.sub(r"[^a-z0-9]+", "", text)


def _build_metadata_quality_report(*, conn, source_id: int) -> dict[str, Any]:
    a_classer_id = SYSTEM_TOPICS["a_classer"][0]
    tracker = _CURRENT_QUALITY or MetadataQualityTracker()
    indicateurs_sans_theme = _count_scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM indicators i
        LEFT JOIN {INDICATOR_TOPICS_TABLE} it ON it.indicator_id = i.id
        WHERE i.source_id = ? AND it.indicator_id IS NULL
        """,
        (source_id,),
    )
    indicateurs_sans_nom = _count_scalar(
        conn,
        "SELECT COUNT(*) FROM indicators WHERE source_id = ? AND (name IS NULL OR TRIM(name) = '')",
        (source_id,),
    )
    pays_sans_nom = _count_scalar(conn, "SELECT COUNT(*) FROM countries WHERE name IS NULL OR TRIM(name) = ''")
    themes_sans_nom = _count_scalar(
        conn,
        "SELECT COUNT(*) FROM topics WHERE source_id = ? AND (name IS NULL OR TRIM(name) = '')",
        (source_id,),
    )
    indicateurs_a_classer = _count_scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM {INDICATOR_TOPICS_TABLE} it
        JOIN indicators i ON i.id = it.indicator_id
        WHERE i.source_id = ? AND it.topic_id = ?
        """,
        (source_id, a_classer_id),
    )
    source_summary = db.metadata_summary(conn=conn, source_id=source_id)
    liens = _count_scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM {INDICATOR_TOPICS_TABLE} it
        JOIN indicators i ON i.id = it.indicator_id
        WHERE i.source_id = ?
        """,
        (source_id,),
    )
    pays_totaux = _count_scalar(conn, "SELECT COUNT(*) FROM countries")
    pays_actifs = _count_scalar(conn, "SELECT COUNT(*) FROM countries WHERE enabled = 1")
    aggregats = max(0, pays_totaux - pays_actifs)
    etat = "Réussi"
    if indicateurs_sans_theme or indicateurs_sans_nom or pays_sans_nom or themes_sans_nom:
        etat = "Échec qualité"
    elif indicateurs_a_classer:
        etat = "Réussi avec avertissements"

    premiers_sans_theme = [
        row["code"]
        for row in conn.execute(
            f"""
            SELECT i.code
            FROM indicators i
            LEFT JOIN {INDICATOR_TOPICS_TABLE} it ON it.indicator_id = i.id
            WHERE i.source_id = ? AND it.indicator_id IS NULL
            ORDER BY i.code
            LIMIT 20
            """,
            (source_id,),
        ).fetchall()
    ]
    return {
        "type": "qualite_metadata_banque_mondiale",
        "etat": etat,
        "sources": _count_scalar(conn, "SELECT COUNT(*) FROM sources"),
        "themes": source_summary.get("topics", 0),
        "pays": pays_totaux,
        "pays_actifs": pays_actifs,
        "aggregats": aggregats,
        "indicateurs": source_summary.get("indicators", 0),
        "liens_indicateur_theme": liens,
        "indicateurs_sans_nom": indicateurs_sans_nom,
        "indicateurs_sans_theme": indicateurs_sans_theme,
        "indicateurs_a_classer": indicateurs_a_classer,
        "pays_sans_nom": pays_sans_nom,
        "themes_sans_nom": themes_sans_nom,
        "traductions_echouees": tracker.traductions_echouees,
        "fallbacks_anglais": tracker.fallbacks_anglais,
        "champs_vides_corriges": tracker.champs_vides_corriges,
        "themes_fallback": tracker.themes_fallback,
        "doublons_ignores": tracker.doublons_ignores,
        "premiers_indicateurs_sans_theme": premiers_sans_theme,
        "premiers_indicateurs_a_classer": tracker.indicateurs_a_classer[:20],
    }


def _write_quality_report(report: dict[str, Any]) -> None:
    enregistrer_mesure("wb_metadata_quality", report)


def _print_quality_report(report: dict[str, Any]) -> None:
    print("Résumé qualité metadata World Bank")
    print(f"- Sources : {report['sources']}")
    print(f"- Thèmes : {report['themes']}")
    print(f"- Pays : {report['pays']}")
    print(f"- Pays actifs : {report['pays_actifs']}")
    print(f"- Indicateurs : {report['indicateurs']}")
    print(f"- Liens indicateur-thème : {report['liens_indicateur_theme']}")
    print(f"- Indicateurs sans nom : {report['indicateurs_sans_nom']}")
    print(f"- Indicateurs sans thème : {report['indicateurs_sans_theme']}")
    print(f"- Indicateurs dans À classer : {report['indicateurs_a_classer']}")
    print(f"- Pays sans nom : {report['pays_sans_nom']}")
    print(f"- Thèmes sans nom : {report['themes_sans_nom']}")
    print(f"- Traductions échouées : {report['traductions_echouees']}")
    print(f"- Fallbacks anglais : {report['fallbacks_anglais']}")
    print(f"- Doublons ignorés : {report['doublons_ignores']}")
    print(f"- Appels HTTP : {report.get('appels_http', 0)}")
    print(f"- Durée totale : {report.get('duree_totale_secondes', 0)} secondes")
    print(f"- État : {report['etat']}")
    if report["indicateurs_sans_theme"]:
        print("- Premiers indicateurs sans thème : " + ", ".join(report["premiers_indicateurs_sans_theme"]))


def _count_scalar(conn, query: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(query, params).fetchone()
    return int(row[0] if row else 0)


def _resolve_french_text(source_text: str, official_text: str, translator: MetadataTranslator) -> str:
    clean_source = _clean_text(source_text)
    clean_official = _clean_text(official_text)
    if clean_official and not _should_translate(clean_source, clean_official, "name"):
        return clean_official
    if not clean_source:
        return clean_official
    translated = translator.translate(clean_source)
    return translated or clean_official or clean_source


def _load_existing_topics(conn, source_id: int) -> dict[str, dict[str, str]]:
    rows = conn.execute(
        """
        SELECT id, name, description
        FROM topics
        WHERE source_id = ?
        """,
        (source_id,),
    ).fetchall()
    return {
        str(row["id"]): {
            "name": _clean_text(row["name"]),
            "description": _clean_text(row["description"]),
        }
        for row in rows
    }


def _load_existing_indicators(conn, source_id: int) -> dict[str, dict[str, str]]:
    rows = conn.execute(
        """
        SELECT code, name, description
        FROM indicators
        WHERE source_id = ?
        """,
        (source_id,),
    ).fetchall()
    return {
        _clean_text(row["code"]): {
            "name": _clean_text(row["name"]),
            "description": _clean_text(row["description"]),
        }
        for row in rows
        if _clean_text(row["code"])
    }


def _contains_mojibake(text: str) -> bool:
    return any(marker in text for marker in MOJIBAKE_MARKERS)


def _looks_untranslated(source_text: str, candidate_text: str, field_name: str) -> bool:
    source_norm = _clean_text(source_text).lower()
    candidate_norm = _clean_text(candidate_text).lower()
    if not candidate_norm:
        return True

    alpha_source_tokens = [token for token in _tokenize(source_norm) if len(token) >= 4]
    if source_norm == candidate_norm and len(alpha_source_tokens) >= 2:
        return True

    if field_name == "description":
        return _english_score(candidate_text) >= 3 and _french_score(candidate_text) == 0

    return _english_score(candidate_text) >= 2 and _french_score(candidate_text) == 0


def _looks_truncated(source_text: str, candidate_text: str) -> bool:
    source_clean = _clean_text(source_text)
    candidate_clean = _clean_text(candidate_text)
    if len(source_clean) < 120:
        return False
    return len(candidate_clean) < max(32, int(len(source_clean) * 0.25))


def _detect_issues(source_text: str, candidate_text: str, field_name: str) -> list[str]:
    issues: list[str] = []
    clean_candidate = _clean_text(candidate_text)
    if not clean_candidate:
        issues.append("empty")
        return issues

    if _contains_mojibake(clean_candidate):
        issues.append("encoding")
    if _looks_truncated(source_text, clean_candidate):
        issues.append("truncated")
    if _looks_untranslated(source_text, clean_candidate, field_name):
        issues.append("mixed_english")
    return issues


def _should_translate(source_text: str, official_text: str, field_name: str) -> bool:
    blocking_issues = {"empty", "encoding", "truncated", "mixed_english"}
    return bool(blocking_issues.intersection(_detect_issues(source_text, official_text, field_name)))


def _choose_best_text(
    source_text: str,
    candidate_text: str,
    candidate_source: str | None,
    existing_text: str,
    field_name: str,
) -> tuple[str, str, list[str]]:
    clean_source = _clean_text(source_text)
    clean_candidate = _clean_text(candidate_text)
    clean_existing = _clean_text(existing_text)

    candidate_issues = _detect_issues(clean_source, clean_candidate, field_name) if clean_candidate else ["empty"]
    existing_issues = _detect_issues(clean_source, clean_existing, field_name) if clean_existing else ["empty"]

    if clean_candidate and not candidate_issues:
        return clean_candidate, candidate_source or "unknown", []

    if clean_candidate and clean_existing and len(existing_issues) < len(candidate_issues):
        return clean_existing, "existing_db", existing_issues

    if clean_candidate:
        return clean_candidate, candidate_source or "unknown", candidate_issues

    if clean_existing:
        return clean_existing, "existing_db", existing_issues

    fallback_text = clean_source
    fallback_issues = _detect_issues(clean_source, fallback_text, field_name)
    if "fallback_english" not in fallback_issues:
        fallback_issues.append("fallback_english")
    return fallback_text, "fallback_english", fallback_issues


def _english_score(text: str) -> int:
    return sum(1 for token in _tokenize(text) if token in ENGLISH_HINTS)


def _french_score(text: str) -> int:
    return sum(1 for token in _tokenize(text) if token in FRENCH_HINTS)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z\u00C0-\u024F']+", text.lower())


def _nested_value(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if isinstance(value, dict):
        return _clean_text(value.get("value"))
    return _clean_text(value)


def _first_non_empty(*values: str) -> str:
    for value in values:
        clean = _clean_text(value)
        if clean:
            return clean
    return ""


def _is_valid_country_codes(code_iso2: str, code_iso3: str) -> bool:
    return len(code_iso2) == 2 and code_iso2.isalpha() and len(code_iso3) == 3 and code_iso3.isalpha()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Construit localement databridge.db avec les metadonnees World Bank.")
    parser.add_argument("--force", action="store_true", help="Refait la synchronisation meme si des indicateurs existent deja.")
    parser.add_argument("--limit", type=int, default=None, help="Limite le nombre d'indicateurs synchronises.")
    parser.add_argument("--local-sqlite", action="store_true", help="Force le backend SQLite dans ce processus.")
    parser.add_argument("--sqlite-path", default=None, help="Chemin du fichier SQLite local a construire, ex: databridge.db.")
    parser.add_argument("--allow-turso", action="store_true", help="Option de secours seulement : autorise un run direct Turso (deconseille).")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.local_sqlite or args.sqlite_path:
        db.configure_backend(backend="sqlite", db_path=args.sqlite_path or "databridge.db")
    summary = sync_world_bank_metadata(
        force=args.force,
        limit=args.limit,
        require_sqlite=not args.allow_turso,
    )
    print(summary)


if __name__ == "__main__":
    main()
