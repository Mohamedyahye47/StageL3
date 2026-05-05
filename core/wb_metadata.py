from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import db

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


class MetadataSyncError(RuntimeError):
    pass


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
            translated = clean
        self._cache[clean] = translated or clean
        return self._cache[clean]

    def translate_batch(self, texts: list[str]) -> list[str]:
        return [self.translate(text) for text in texts]


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def sync_world_bank_metadata(force: bool = False, limit: int | None = None) -> dict[str, Any]:
    db.init_db()
    translator = MetadataTranslator()
    session = _build_world_bank_session()

    with db.get_connection() as conn:
        source_id = db.register_source(
            db.WORLD_BANK_SOURCE["code"],
            db.WORLD_BANK_SOURCE["name"],
            db.WORLD_BANK_SOURCE["base_url"],
            db.WORLD_BANK_SOURCE["description"],
            conn=conn,
        )
        if not force and db.source_has_metadata(source_id, conn=conn):
            summary = db.metadata_summary(conn=conn, source_id=source_id)
            summary.update(
                {
                    "source_code": db.WORLD_BANK_SOURCE["code"],
                    "status": "skipped",
                    "translated_with_fallback": int(translator.enabled),
                }
            )
            return summary

        topic_lookup = _sync_topics(session, translator, source_id, conn=conn)
        countries_count = _sync_countries(session, translator, conn=conn)
        indicators_count = _sync_indicators(
            session,
            translator,
            source_id,
            topic_lookup,
            limit=limit,
            conn=conn,
        )
        summary = db.metadata_summary(conn=conn, source_id=source_id)
        summary.update(
            {
                "source_code": db.WORLD_BANK_SOURCE["code"],
                "status": "synced",
                "topics_synced": len(topic_lookup),
                "countries_synced": countries_count,
                "indicators_synced": indicators_count,
                "translated_with_fallback": int(translator.enabled),
            }
        )
        return summary


def _sync_topics(
    session: requests.Session,
    translator: MetadataTranslator,
    source_id: int,
    *,
    conn,
) -> dict[str, int]:
    existing_topics = _load_existing_topics(conn, source_id)
    topics_en = _fetch_paginated_safe(session, "topic", per_page=TOPIC_PAGE_SIZE)
    topics_fr_lookup = {
        _clean_text(item.get("id")): item
        for item in _fetch_paginated_safe(session, "topic", language="fr", per_page=TOPIC_PAGE_SIZE)
    }

    topic_lookup: dict[str, int] = {}
    if not topics_en:
        print("[WB metadata] warning: topic endpoint unavailable, topics will be derived from indicators.")
        return topic_lookup

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

    for batch in _chunked(records, TRANSLATION_BATCH_SIZE):
        resolved, field_sources = _resolve_batch_fields(
            batch,
            field_specs=(
                ("name", "name_en", "name_fr"),
                ("description", "description_en", "description_fr"),
            ),
            translator=translator,
        )
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
    return topic_lookup


def _sync_countries(
    session: requests.Session,
    translator: MetadataTranslator,
    *,
    conn,
) -> int:
    countries_en = _fetch_paginated_safe(session, "country", per_page=COUNTRY_PAGE_SIZE)
    countries_fr_lookup = {
        _clean_text(item.get("id")).upper(): item
        for item in _fetch_paginated_safe(session, "country", language="fr", per_page=COUNTRY_PAGE_SIZE)
    }

    if not countries_en:
        print("[WB metadata] warning: country endpoint unavailable, keeping locally seeded countries.")
        return 0

    synced = 0
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
    indicators_en = _fetch_paginated(session, path, per_page=WORLD_BANK_PAGE_SIZE)
    if limit is not None:
        indicators_en = indicators_en[: max(0, limit)]
    indicators_fr_lookup = {
        _clean_text(item.get("id")): item
        for item in _fetch_paginated_safe(session, path, language="fr", per_page=min(WORLD_BANK_PAGE_SIZE, 150))
    }

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
    for batch in _chunked(records, TRANSLATION_BATCH_SIZE):
        resolved, field_sources = _resolve_batch_fields(
            batch,
            field_specs=(
                ("name", "name_en", "name_fr"),
                ("description", "description_en", "description_fr"),
            ),
            translator=translator,
        )
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
            db.replace_indicator_topics(indicator_id, topic_ids, conn=conn)
            existing_indicators[code] = {
                "name": final_name,
                "description": description or "",
            }
            synced += 1
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
    return sorted(set(topic_ids))


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
    parser = argparse.ArgumentParser(description="Synchronise les metadonnees World Bank dans databridge.db.")
    parser.add_argument("--force", action="store_true", help="Refait la synchronisation meme si des indicateurs existent deja.")
    parser.add_argument("--limit", type=int, default=None, help="Limite le nombre d'indicateurs synchronises.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = sync_world_bank_metadata(force=args.force, limit=args.limit)
    print(summary)


if __name__ == "__main__":
    main()
