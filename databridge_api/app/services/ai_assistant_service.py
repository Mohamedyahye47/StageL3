from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import uuid
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.clients import generate_json, generate_text
from app.config import (
    AI_ENABLE_BUSINESS_RULES,
    AI_LOG_DECISIONS,
    AI_MAX_CANDIDATES,
    AI_MODEL as CONFIG_AI_MODEL,
    AI_PROVIDER as CONFIG_AI_PROVIDER,
    AI_TARGET_INDICATORS,
    AI_TEMPERATURE,
    get_source_indicator_limit,
    get_source_label,
)
from app.models import Country, Indicator, IndicatorTopic, Source, Topic
from app.services.ai_vocabulary import COUNTRY_ALIASES, expand_domain_terms, normalize_country_query
from app.services.ai_business_rules import (
    BUSINESS_RULES,
    GovernedCandidates,
    backend_business_state,
    exact_code_available,
    govern_indicator_candidates,
    normalize_for_rules,
    preferred_topic_names,
    required_missing,
)
from app.services.measure_service import enregistrer_mesure
from app.services.model_evaluation_service import enregistrer_journal_decision_ia_detaille


DEFAULT_AI_START_YEAR = 2000
DEFAULT_AI_END_YEAR = 2023
MIN_SAFE_YEAR = 1960
MAX_SAFE_YEAR = 2023
DEFAULT_COUNTRY_NAME = "Mauritanie"

MAX_CANDIDATES = AI_MAX_CANDIDATES
TARGET_INDICATORS = AI_TARGET_INDICATORS
AI_PROVIDER = CONFIG_AI_PROVIDER
AI_MODEL = CONFIG_AI_MODEL

COUNTRY_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "CHN": ("chine", "china", "chn", "cn", "republique populaire de chine"),
    "MRT": ("mauritanie", "mauritania", "mrt", "mr"),
    "MAR": ("maroc", "morocco", "mar", "ma"),
    "SEN": ("senegal", "sen", "sn"),
    "FRA": ("france", "fra", "fr"),
    "USA": ("etats unis", "united states", "united states of america", "usa", "us"),
}


class NormalizedRequest(BaseModel):
    source_code: str = Field(description="Recommended source code, usually WB")
    country_name: str = Field(description="Specific country name. Use Mauritanie if no specific country is provided.")
    start_year: int = Field(description="Recommended start year")
    end_year: int = Field(description="Recommended end year")
    topic_intent: str = Field(default="general", description="Business intent such as population, inflation, economic_growth")
    requested_concepts: list[str] = Field(default_factory=list, description="Requested concepts without indicator codes")
    title: str = Field(description="Suggested dataset title in French")
    description: str = Field(description="Suggested dataset description in French")
    search_keywords: list[str] = Field(description="Keywords to search the local metadata catalogue")
    ambiguity_level: Literal["low", "medium", "high"] = "medium"
    declared_confidence: Literal["low", "medium", "high"] = "medium"

    @property
    def confidence(self) -> Literal["low", "medium", "high"]:
        return self.declared_confidence


RequestAnalysis = NormalizedRequest


class CountryResolution(BaseModel):
    country_id: int | None = None
    country_code: str | None = None
    country_name: str | None = None
    matched_term: str | None = None
    confidence: Literal["high", "medium", "none"] = "none"
    explicit_country_mentioned: bool = False
    used_default_country: bool = False
    error: str | None = None


class SelectedCandidateIndicator(BaseModel):
    id: int | None = None
    indicator_id: int | None = Field(default=None)
    reason: str = Field(description="Why this local indicator is useful")

    def resolved_id(self) -> int | None:
        return self.indicator_id or self.id


class CandidateSelection(BaseModel):
    title: str = Field(description="Final dataset title in French")
    description: str = Field(description="Final dataset description in French")
    selected_indicators: list[SelectedCandidateIndicator] = Field(description="Selected indicators from candidate list only")
    not_selected: list[SelectedCandidateIndicator] = Field(default_factory=list)
    uncertainty: Literal["low", "medium", "high"] = "medium"
    confidence: Literal["low", "medium", "high"] = "medium"


class SingleCallDatasetRecommendation(BaseModel):
    source_code: str = Field(default="WB", description="Recommended source code, usually WB")
    country_id: int | None = Field(default=None, description="Local country ID if known from context")
    country_name: str = Field(description="Country name for the dataset")
    start_year: int = Field(description="Recommended start year")
    end_year: int = Field(description="Recommended end year")
    topic_id: int | None = Field(default=None, description="Local topic ID if known from context")
    topic_name: str | None = Field(default=None, description="Topic name if useful")
    topic_intent: str = Field(default="general", description="Business intent or theme")
    title: str = Field(description="Suggested dataset title in French")
    description: str = Field(description="Suggested dataset description in French")
    selected_indicators: list[SelectedCandidateIndicator] = Field(description="Selected local candidate indicators")
    not_selected: list[SelectedCandidateIndicator] = Field(default_factory=list)
    search_keywords: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"
    ambiguity_level: Literal["low", "medium", "high"] = "medium"


class ValidatedIndicatorSuggestion(BaseModel):
    id: int
    code: str
    name: str
    reason: str


class ValidatedDatasetRecommendation(BaseModel):
    source_code: str
    source_id: int | None = None
    topic_id: int | None = None
    topic_name: str | None = None
    country_id: int | None = None
    country_name: str
    country_code: str | None = None
    start_date: str
    end_date: str
    title: str
    description: str
    indicators: list[ValidatedIndicatorSuggestion]
    missing_indicator_codes: list[str]
    confidence: Literal["low", "medium", "high"]
    etat_technique: str = "reussi"
    etat_metier: str = "verification_humaine_requise"
    decision_evaluateur: str | None = None
    topic_intent: str | None = None
    source_execution: str = "unknown"
    ai_calls: int = 0
    fallback_used: bool = False
    fallback_reason: str = "none"
    provider_error_type: str | None = None
    correction_detectee: str | None = None
    country_resolution: dict[str, Any] = Field(default_factory=dict)
    fournisseur_ia: str = "local"
    modele_ia: str = "regles_metier_locales"
    tokens_utilises: int | None = None
    tokens_restants: int | None = None
    quota_requetes_atteint: bool = False
    retry_after_seconds: int | None = None


class AIQuotaExceeded(RuntimeError):
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        retry_after_seconds: int | None = None,
        fallback_available: bool = False,
    ) -> None:
        self.provider = provider
        self.model = model
        self.retry_after_seconds = retry_after_seconds
        self.fallback_available = fallback_available
        super().__init__("Quota IA atteint.")

    def to_payload(self) -> dict[str, Any]:
        return {
            "error_type": "ai_quota_exceeded",
            "message": "Quota IA atteint. Réessayez plus tard ou utilisez le mode local.",
            "provider": self.provider,
            "model": self.model,
            "retry_after_seconds": self.retry_after_seconds,
            "fallback_available": self.fallback_available,
        }


def get_gemini_client(provider: str | None = None):
    raise RuntimeError("Utilisez app.ai.clients.generate_json au lieu d'un client IA direct.")


def quick_ai_test() -> str:
    return generate_text(
        provider=AI_PROVIDER,
        model=AI_MODEL,
        prompt="Réponds en une phrase: le service IA de Richat DataBridge fonctionne.",
        temperature=AI_TEMPERATURE,
    )


def normalize_user_request(user_request: str) -> NormalizedRequest:
    """Compatibility wrapper: deterministic local parsing, no external IA call."""
    if not user_request.strip():
        raise ValueError("user_request is required")
    return build_initial_local_analysis(user_request, DEFAULT_COUNTRY_NAME)


def analyze_user_request(user_request: str) -> RequestAnalysis:
    return normalize_user_request(user_request)


def normalize_recommended_dates(start_year: int | None, end_year: int | None) -> tuple[str, str]:
    """
    Normalize AI dates before returning them to Django.

    For WDI demo datasets:
    - 2000 is a useful default start.
    - 2023 is safer than 2024/2025/2026 because recent WDI data may be incomplete.
    """

    start = start_year or DEFAULT_AI_START_YEAR
    end = end_year or DEFAULT_AI_END_YEAR

    if start < MIN_SAFE_YEAR:
        start = DEFAULT_AI_START_YEAR

    if end > MAX_SAFE_YEAR:
        end = MAX_SAFE_YEAR

    if end < MIN_SAFE_YEAR:
        end = DEFAULT_AI_END_YEAR

    if start > end:
        start = DEFAULT_AI_START_YEAR
        end = DEFAULT_AI_END_YEAR

    return f"{start}-01-01", f"{end}-12-31"


def validate_source(db: Session, source_code: str) -> Source | None:
    clean_code = (source_code or "WB").strip().upper()
    return db.scalar(select(Source).where(Source.code == clean_code))


def validate_country(db: Session, country_name: str) -> Country | None:
    raw_country = (country_name or "").strip()
    normalized_country = normalize_country_query(country_name)
    clean_name = normalized_country.strip()

    country_query = select(Country).where(Country.enabled.is_(True))

    if clean_name:
        pattern = f"%{clean_name}%"
        country_query = country_query.where(
            or_(
                Country.name.ilike(pattern),
                Country.code_iso3.ilike(clean_name.upper()),
                Country.code_iso2.ilike(clean_name.upper()),
                Country.wb_code.ilike(clean_name.upper()),
            )
        )

    country = db.scalar(country_query.order_by(Country.name.asc()).limit(1))

    if country is not None:
        return country

    if raw_country:
        return None

    # Default only when no country is provided.
    return db.scalar(
        select(Country)
        .where(Country.enabled.is_(True))
        .where(
            or_(
                Country.name.ilike("%Mauritanie%"),
                Country.code_iso3 == "MRT",
                Country.wb_code == "MRT",
            )
        )
        .limit(1)
    )


def resolve_country_from_request(db: Session, user_request: str, *, default_country_name: str = DEFAULT_COUNTRY_NAME) -> CountryResolution:
    """
    Resolve a country mentioned in the user request without silently replacing it.
    """
    normalized_request = _normalize_country_text(user_request)
    countries = db.scalars(select(Country).where(Country.enabled.is_(True))).all()
    aliases = _country_alias_index(countries)

    best_match = _match_country_alias(normalized_request, aliases)
    if best_match is not None:
        country, matched_term = best_match
        return CountryResolution(
            country_id=country.id,
            country_code=country.code_iso3,
            country_name=country.name,
            matched_term=matched_term,
            confidence="high",
            explicit_country_mentioned=True,
            used_default_country=False,
        )

    explicit_term = _extract_explicit_country_term(normalized_request)
    if explicit_term:
        return CountryResolution(
            matched_term=explicit_term,
            confidence="none",
            explicit_country_mentioned=True,
            used_default_country=False,
            error="country_not_found",
        )

    default_country = validate_country(db, default_country_name)
    return CountryResolution(
        country_id=default_country.id if default_country else None,
        country_code=default_country.code_iso3 if default_country else None,
        country_name=default_country.name if default_country else default_country_name,
        confidence="medium" if default_country else "none",
        explicit_country_mentioned=False,
        used_default_country=True,
        error=None if default_country else "default_country_not_found",
    )


def _country_alias_index(countries: list[Country]) -> dict[str, Country]:
    aliases: dict[str, Country] = {}
    by_iso3 = {country.code_iso3.upper(): country for country in countries}

    for country in countries:
        for value in (country.name, country.code_iso3, country.code_iso2, country.wb_code):
            key = _normalize_country_text(value)
            if key:
                aliases[key] = country

    for iso3, terms in COUNTRY_ALIAS_GROUPS.items():
        country = by_iso3.get(iso3)
        if country is None:
            continue
        for term in terms:
            aliases[_normalize_country_text(term)] = country

    for alias, country_name in COUNTRY_ALIASES.items():
        country = _country_by_name(countries, country_name)
        if country is not None:
            aliases[_normalize_country_text(alias)] = country

    return aliases


def _country_by_name(countries: list[Country], country_name: str) -> Country | None:
    wanted = _normalize_country_text(country_name)
    return next((country for country in countries if _normalize_country_text(country.name) == wanted), None)


def _match_country_alias(normalized_request: str, aliases: dict[str, Country]) -> tuple[Country, str] | None:
    padded_request = f" {normalized_request} "
    for alias in sorted(aliases, key=len, reverse=True):
        if alias and f" {alias} " in padded_request:
            return aliases[alias], alias
    return None


def _extract_explicit_country_term(normalized_request: str) -> str | None:
    match = re.search(r"\b(?:en|au|aux|a|de|du|d)\s+([a-z][a-z0-9 ]{1,60})", normalized_request)
    if not match:
        return None
    stop_words = {
        "depuis", "entre", "pour", "sur", "avec", "sans", "par",
        "population", "pib", "gdp", "gpd", "inflation", "chomage", "croissance",
    }
    tokens = []
    for token in match.group(1).split():
        if token in stop_words or token.isdigit():
            break
        tokens.append(token)
    term = " ".join(tokens).strip()
    return term or None


def _normalize_country_text(value: str | None) -> str:
    text = (
        (value or "")
        .replace("\u0153", "oe")
        .replace("\u0152", "OE")
        .replace("\u2019", "'")
        .replace("\u2018", "'")
    )
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()


def tokenize_text(text: str) -> list[str]:
    return [
        token.strip().lower()
        for token in re.findall(r"[A-Za-zÀ-ÿ0-9_.%-]+", text)
        if len(token.strip()) >= 3
    ]


def build_search_terms(user_request: str, ai_keywords: list[str]) -> list[str]:
    """
    Combines:
    - AI-extracted keywords
    - raw tokens from user request
    - controlled vocabulary expansions

    Result:
    A safer search term list for the local DB.
    """

    raw_terms: list[str] = []
    raw_terms.extend(ai_keywords or [])
    raw_terms.extend(tokenize_text(user_request))

    expanded = expand_domain_terms(raw_terms)

    # Hard cap to avoid huge SQL queries
    return expanded[:40]


def get_topic_names_for_indicators(db: Session, indicator_ids: list[int]) -> dict[int, list[str]]:
    if not indicator_ids:
        return {}

    rows = db.execute(
        select(IndicatorTopic.indicator_id, Topic.name)
        .join(Topic, Topic.id == IndicatorTopic.topic_id)
        .where(IndicatorTopic.indicator_id.in_(indicator_ids))
    ).all()

    mapping: dict[int, list[str]] = {}

    for indicator_id, topic_name in rows:
        mapping.setdefault(int(indicator_id), []).append(topic_name)

    return mapping


def search_candidate_indicators(
    db: Session,
    *,
    source_id: int | None,
    keywords: list[str],
    limit: int = MAX_CANDIDATES,
) -> list[Indicator]:
    """
    Search local metadata before the single IA recommendation call.

    The final server validation still rejects invented or unknown indicators.
    """

    clean_keywords = [keyword.strip() for keyword in keywords if keyword.strip()]

    stmt = (
        select(Indicator)
        .outerjoin(IndicatorTopic, IndicatorTopic.indicator_id == Indicator.id)
        .outerjoin(Topic, Topic.id == IndicatorTopic.topic_id)
    )

    if source_id is not None:
        stmt = stmt.where(Indicator.source_id == source_id)

    conditions = []

    for keyword in clean_keywords:
        pattern = f"%{keyword}%"

        conditions.append(
            or_(
                Indicator.code.ilike(pattern),
                Indicator.name.ilike(pattern),
                Indicator.description.ilike(pattern),
                Topic.name.ilike(pattern),
            )
        )

    if conditions:
        stmt = stmt.where(or_(*conditions))

    stmt = stmt.order_by(Indicator.code.asc()).limit(limit * 3)

    rows = db.scalars(stmt).all()

    deduped: list[Indicator] = []
    seen_ids: set[int] = set()

    for indicator in rows:
        if indicator.id in seen_ids:
            continue

        seen_ids.add(indicator.id)
        deduped.append(indicator)

        if len(deduped) >= limit:
            break

    return deduped


def build_candidate_payload(db: Session, candidates: list[Indicator]) -> list[dict[str, Any]]:
    topic_map = get_topic_names_for_indicators(
        db,
        [indicator.id for indicator in candidates],
    )

    payload: list[dict[str, Any]] = []

    for indicator in candidates:
        payload.append(
            {
                "id": indicator.id,
                "code": indicator.code,
                "name": indicator.name,
                "description": indicator.description or "",
                "topics": topic_map.get(indicator.id, []),
            }
        )

    return payload


def choose_indicators_from_candidates(
    *,
    user_request: str,
    analysis: RequestAnalysis,
    candidates_payload: list[dict[str, Any]],
    max_indicators: int,
) -> CandidateSelection:
    """Compatibility wrapper: select local candidates without any IA call."""
    selected = [
        SelectedCandidateIndicator(
            indicator_id=int(item.get("indicator_id") or item.get("id")),
            reason="Indicateur retenu par les regles locales serveur.",
        )
        for item in candidates_payload[:max_indicators]
        if item.get("indicator_id") or item.get("id")
    ]
    return CandidateSelection(
        title=analysis.title,
        description=analysis.description,
        selected_indicators=selected,
        confidence=analysis.confidence,
    )


def recommend_dataset_from_local_candidates(
    *,
    user_request: str,
    initial_analysis: RequestAnalysis,
    local_context: dict[str, Any],
    candidates_payload: list[dict[str, Any]],
    max_indicators: int,
) -> SingleCallDatasetRecommendation:
    prompt = f"""
You are the single AI assistant call for Richat DataBridge.

Richat DataBridge builds World Bank datasets from a local metadata catalogue.

User request:
\"\"\"{user_request}\"\"\"

Server-side initial analysis:
{initial_analysis.model_dump_json(ensure_ascii=False)}

Local source/country/topic context:
{json.dumps(local_context, ensure_ascii=False, indent=2)}

Local candidate indicators prepared by the server:
{json.dumps(candidates_payload, ensure_ascii=False, indent=2)}

Task:
Return one structured dataset recommendation.

Strict rules:
- Use only the local candidate indicators listed above.
- Select indicators by local indicator_id/id only.
- Do not invent indicators, codes, sources, countries or dates.
- Choose at most {max_indicators} indicators.
- Prefer 3 to 5 relevant indicators, but use fewer for a precise request.
- If the exact variable is available, select it.
- If only proxy indicators are available, say so in the reason.
- Keep the title and description in French.
- source_code should usually be "WB".
- If country_id or topic_id is obvious from server context, include it.
- If the user did not provide a country, keep the server default country.
- Never use an end year after 2023.
- Return only structured data matching the schema.
"""

    return generate_json(
        layer="recommendation",
        provider=AI_PROVIDER,
        model=AI_MODEL,
        system_prompt="Tu produis une recommandation de jeu de donnees en un seul appel IA.",
        user_prompt=prompt,
        schema=SingleCallDatasetRecommendation,
        temperature=AI_TEMPERATURE,
    )


def infer_topic_from_indicators(
    db: Session,
    indicator_models: list[Indicator],
    *,
    intent: str | None = None,
    source_id: int | None = None,
) -> Topic | None:
    indicator_ids = [indicator.id for indicator in indicator_models]

    if not indicator_ids:
        return None

    preferred_names = preferred_topic_names(intent or "")
    if preferred_names:
        preferred_topic = _find_preferred_topic(db, preferred_names, source_id=source_id)
        if preferred_topic is not None:
            return preferred_topic

    topic_ids = db.scalars(
        select(IndicatorTopic.topic_id)
        .where(IndicatorTopic.indicator_id.in_(indicator_ids))
    ).all()
    topic_ids = [topic_id for topic_id in topic_ids if topic_id is not None]

    if not topic_ids:
        return None

    most_common_topic_id = Counter(topic_ids).most_common(1)[0][0]

    return db.scalar(
        select(Topic).where(Topic.id == most_common_topic_id)
    )


def _find_preferred_topic(db: Session, names: tuple[str, ...], *, source_id: int | None) -> Topic | None:
    stmt = select(Topic)
    if source_id is not None:
        stmt = stmt.where(Topic.source_id == source_id)
    topics = db.scalars(stmt.order_by(Topic.id.asc())).all()

    topic_pairs = [(topic, _topic_match_key(topic.name)) for topic in topics]
    for name in names:
        preferred = _topic_match_key(name)
        if not preferred:
            continue
        for topic, topic_name in topic_pairs:
            if preferred in topic_name or topic_name in preferred:
                return topic
    return None


def _topic_match_key(value: str | None) -> str:
    text = (
        (value or "")
        .replace("\u0153", "oe")
        .replace("\u0152", "OE")
        .replace("\u2019", "'")
        .replace("\u2018", "'")
    )
    normalized = normalize_for_rules(text)
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def fallback_selection_from_candidates(candidates: list[Indicator], *, max_indicators: int) -> list[Indicator]:
    """
    Last-resort fallback if the single IA call returns no valid local indicators.
    """
    return candidates[: min(max_indicators, len(candidates))]


def _single_call_local_context(
    *,
    source: Source | None,
    country: Country | None,
    candidates_payload: list[dict[str, Any]],
) -> dict[str, Any]:
    topic_names: list[str] = []
    for candidate in candidates_payload:
        for topic in candidate.get("topics") or []:
            if topic and topic not in topic_names:
                topic_names.append(str(topic))
    return {
        "sources": [
            {
                "source_code": source.code,
                "source_id": source.id,
                "name": source.name,
            }
        ] if source else [],
        "countries": [
            {
                "country_id": country.id,
                "country_name": country.name,
                "country_code": country.code_iso3,
            }
        ] if country else [],
        "topics": topic_names[:20],
    }


def build_initial_local_analysis(user_request: str, country_name: str) -> NormalizedRequest:
    local_intent = _detect_local_intent(user_request) or "general"
    correction_detectee = _detect_local_correction(user_request)
    start_year, end_year = _detect_year_range(user_request)
    concepts = _local_concepts_for_intent(local_intent, correction_detectee=correction_detectee)
    keywords = build_search_terms(user_request, [*concepts, local_intent])
    clean_country_name = country_name or DEFAULT_COUNTRY_NAME
    return NormalizedRequest(
        source_code="WB",
        country_name=clean_country_name,
        start_year=start_year,
        end_year=end_year,
        topic_intent=local_intent,
        requested_concepts=concepts,
        title=_local_title(local_intent, clean_country_name),
        description=_local_description(local_intent, clean_country_name, start_year),
        search_keywords=keywords[:20],
        ambiguity_level="medium" if local_intent == "general" else "low",
        declared_confidence="medium",
    )


def recommend_dataset_validated(
    db: Session,
    user_request: str,
    *,
    audit: bool = False,
    source_execution: str = "unknown",
    triggered_by: str = "unknown",
    run_id: str | None = None,
    local_only: bool = False,
) -> ValidatedDatasetRecommendation:
    run_id = run_id or f"ia-{uuid.uuid4().hex}"
    if local_only:
        local = recommend_dataset_local(
            db,
            user_request,
            source_execution="local_rules",
            triggered_by=triggered_by,
            run_id=run_id,
            fallback_used=False,
            fallback_reason="manual_local",
        )
        if local is not None:
            return local
        raise ValueError("Le mode local ne reconnait pas encore cette demande.")

    try:
        return _recommend_dataset_validated_with_ai(
            db,
            user_request,
            audit=audit,
            source_execution=source_execution,
            triggered_by=triggered_by,
            run_id=run_id,
        )
    except Exception as exc:
        if _is_ai_quota_error(exc):
            local = recommend_dataset_local(
                db,
                user_request,
                source_execution="local_rules",
                triggered_by=triggered_by,
                run_id=run_id,
                fallback_used=True,
                fallback_reason="quota_exceeded",
                provider_error_type="ai_quota_exceeded",
            )
            if local is not None:
                return local
            raise AIQuotaExceeded(
                provider=AI_PROVIDER,
                model=AI_MODEL,
                retry_after_seconds=_extract_retry_after_seconds(exc),
                fallback_available=True,
            ) from exc
        if not isinstance(exc, ValueError):
            local = recommend_dataset_local(
                db,
                user_request,
                source_execution="local_rules",
                triggered_by=triggered_by,
                run_id=run_id,
                fallback_used=True,
                fallback_reason="provider_error",
                provider_error_type=exc.__class__.__name__,
            )
            if local is not None:
                return local
        raise


def _recommend_dataset_validated_with_ai(
    db: Session,
    user_request: str,
    *,
    audit: bool = False,
    source_execution: str = "unknown",
    triggered_by: str = "unknown",
    run_id: str | None = None,
) -> ValidatedDatasetRecommendation:
    """
    Safe AI workflow:
    1. Backend prepares local candidates and applies business rules.
    2. One AI call returns a structured recommendation from those candidates.
    3. Backend validates indicator IDs against the local database.
    4. The user can still adjust the result in the Django builder.
    """

    mesure_debut = time.perf_counter()
    run_id = run_id or f"ia-{uuid.uuid4().hex}"
    etapes: dict[str, dict[str, Any]] = {}
    nombre_appels_ia = 0

    debut = time.perf_counter()
    initial_country_resolution = resolve_country_from_request(db, user_request)
    if initial_country_resolution.explicit_country_mentioned and initial_country_resolution.error == "country_not_found":
        raise ValueError("Pays non reconnu. Veuillez selectionner le pays manuellement.")
    analysis = build_initial_local_analysis(
        user_request,
        initial_country_resolution.country_name or DEFAULT_COUNTRY_NAME,
    )
    etapes["preparation_locale"] = {
        "duree_secondes": round(time.perf_counter() - debut, 4),
        "resultat": "Candidats prepares sans appel IA preliminaire",
        "role_ia": "aucun_appel_ia",
        "taille_demande_caracteres": len(user_request),
    }

    debut = time.perf_counter()
    source = validate_source(db, analysis.source_code)
    country_resolution = resolve_country_from_request(db, user_request)
    if country_resolution.explicit_country_mentioned and country_resolution.error == "country_not_found":
        raise ValueError("Pays non reconnu. Veuillez sélectionner le pays manuellement.")
    if country_resolution.explicit_country_mentioned and country_resolution.country_id:
        country = db.get(Country, country_resolution.country_id)
        if country:
            analysis = analysis.model_copy(update={"country_name": country.name})
    else:
        country = validate_country(db, analysis.country_name)
        if country:
            country_resolution = CountryResolution(
                country_id=country.id,
                country_code=country.code_iso3,
                country_name=country.name,
                confidence="medium",
                explicit_country_mentioned=False,
                used_default_country=not bool((analysis.country_name or "").strip()),
            )
    etapes["validation_source_pays"] = {
        "duree_secondes": round(time.perf_counter() - debut, 4),
        "resultat": f"Source: {source.code if source else '-'}; Pays: {country.name if country else '-'}",
    }

    source_code_for_limits = source.code if source else (analysis.source_code or "WB")
    source_limit = get_source_indicator_limit(source_code_for_limits)
    source_label = get_source_label(source_code_for_limits)
    max_recommended_indicators = min(TARGET_INDICATORS, source_limit)

    debut = time.perf_counter()
    keywords = build_search_terms(
        user_request,
        [*analysis.search_keywords, *analysis.requested_concepts, analysis.topic_intent],
    )
    etapes["expansion_vocabulaire"] = {
        "duree_secondes": round(time.perf_counter() - debut, 4),
        "resultat": f"{len(keywords)} terme(s)",
    }

    debut = time.perf_counter()
    raw_candidates = search_candidate_indicators(
        db,
        source_id=source.id if source else None,
        keywords=keywords,
        limit=max(MAX_CANDIDATES * 4, MAX_CANDIDATES),
    )
    etapes["recherche_locale"] = {
        "duree_secondes": round(time.perf_counter() - debut, 4),
        "resultat": f"{len(raw_candidates)} candidat(s) texte avant regles",
    }

    debut = time.perf_counter()
    governed = govern_indicator_candidates(
        db,
        source=source,
        user_request=user_request,
        normalized_request=analysis,
        text_candidates=raw_candidates,
        search_terms=keywords,
        limit=MAX_CANDIDATES,
    ) if AI_ENABLE_BUSINESS_RULES else _governance_passthrough(db, raw_candidates, keywords)
    etapes["regles_metier"] = {
        "duree_secondes": round(time.perf_counter() - debut, 4),
        "resultat": (
            f"{governed.after_rules_count} candidat(s) apres regles; "
            f"{governed.sent_count} envoye(s); {len(governed.blocked_indicators)} bloque(s)"
        ),
        "intention": governed.intent,
    }

    candidate_by_id = {indicator.id: indicator for indicator in governed.candidates}
    selected_models: list[Indicator] = []
    reason_by_id: dict[int, str] = {}
    selection_items: list[SelectedCandidateIndicator] = []
    non_retenus: list[dict[str, Any]] = []
    invalides: list[dict[str, Any]] = []
    selection: SingleCallDatasetRecommendation | None = None

    title = analysis.title
    description = analysis.description
    confidence: Literal["low", "medium", "high"] = analysis.confidence

    should_call_external_ai = True
    if should_call_external_ai:
        debut = time.perf_counter()
        selection = recommend_dataset_from_local_candidates(
            user_request=user_request,
            initial_analysis=analysis,
            local_context=_single_call_local_context(source=source, country=country, candidates_payload=governed.candidate_payload),
            candidates_payload=governed.candidate_payload,
            max_indicators=max_recommended_indicators,
        )
        nombre_appels_ia += 1
        etapes["recommandation_ia"] = {
            "duree_secondes": round(time.perf_counter() - debut, 4),
            "resultat": f"{len(selection.selected_indicators)} indicateur(s) proposes",
            "role_ia": "recommandation",
            "modele": AI_MODEL,
            "fournisseur": AI_PROVIDER,
        }
        selected_source = validate_source(db, selection.source_code)
        if selected_source is not None:
            source = selected_source
        selected_country = db.get(Country, selection.country_id) if selection.country_id else None
        if selected_country is None:
            selected_country = validate_country(db, selection.country_name)
        if selected_country is not None and not country_resolution.explicit_country_mentioned:
            country = selected_country
            country_resolution = CountryResolution(
                country_id=selected_country.id,
                country_code=selected_country.code_iso3,
                country_name=selected_country.name,
                confidence="medium",
                explicit_country_mentioned=False,
                used_default_country=False,
            )
        analysis = analysis.model_copy(
            update={
                "source_code": source.code if source else selection.source_code,
                "country_name": country.name if country else selection.country_name,
                "start_year": selection.start_year,
                "end_year": selection.end_year,
                "topic_intent": selection.topic_intent or governed.intent,
                "title": selection.title,
                "description": selection.description,
                "search_keywords": selection.search_keywords or analysis.search_keywords,
                "ambiguity_level": selection.ambiguity_level,
                "declared_confidence": selection.confidence,
            }
        )
        selection_items = list(selection.selected_indicators)
        title = selection.title or analysis.title
        description = selection.description or analysis.description
        confidence = selection.confidence or analysis.confidence

        seen_selected_ids: set[int] = set()
        for item in selection.selected_indicators:
            item_id = item.resolved_id()
            indicator = candidate_by_id.get(item_id or -1)
            if indicator is None:
                invalides.append(
                    {
                        "id": item_id,
                        "code": None,
                        "raison": "Identifiant absent de la liste candidate envoyee au modele.",
                    }
                )
                continue
            if indicator.id in seen_selected_ids:
                non_retenus.append(
                    {
                        "id": indicator.id,
                        "code": indicator.code,
                        "nom": indicator.name,
                        "raison": "Doublon supprime par le serveur.",
                    }
                )
                continue
            seen_selected_ids.add(indicator.id)
            selected_models.append(indicator)
            reason_by_id[indicator.id] = item.reason
            if len(selected_models) >= max_recommended_indicators:
                break

        for item in selection.not_selected:
            item_id = item.resolved_id()
            indicator = candidate_by_id.get(item_id or -1)
            non_retenus.append(
                {
                    "id": item_id,
                    "code": indicator.code if indicator else None,
                    "nom": indicator.name if indicator else None,
                    "raison": item.reason,
                }
            )
    else:
        description = (
            analysis.description
            + " Aucun indicateur local suffisamment pertinent n'a ete trouve dans le catalogue."
        )
        confidence = "low"
        etapes["recommandation_ia"] = {
            "duree_secondes": 0,
            "resultat": "Aucun candidat envoye au modele",
            "role_ia": "aucun_appel_ia",
        }

    _ensure_required_indicators(
        selected_models=selected_models,
        reason_by_id=reason_by_id,
        governed=governed,
        max_indicators=max_recommended_indicators,
    )

    if governed.candidates and not selected_models:
        selected_models = fallback_selection_from_candidates(
            governed.candidates,
            max_indicators=max_recommended_indicators,
        )
        for indicator in selected_models:
            reason_by_id[indicator.id] = "Indicateur local retenu par secours serveur apres echec de recommandation IA."
        confidence = "low"

    debut = time.perf_counter()
    topic = infer_topic_from_indicators(
        db,
        selected_models,
        intent=governed.intent,
        source_id=source.id if source else None,
    )
    if selection and selection.topic_id:
        selected_topic = db.get(Topic, selection.topic_id)
        if selected_topic is not None and (source is None or selected_topic.source_id == source.id):
            topic = selected_topic
    elif selection and selection.topic_name and topic is None:
        topic = _find_preferred_topic(db, (selection.topic_name,), source_id=source.id if source else None)
    etapes["validation_backend"] = {
        "duree_secondes": round(time.perf_counter() - debut, 4),
        "resultat": topic.name if topic else "Aucun theme dominant",
    }

    debut = time.perf_counter()
    start_date, end_date = normalize_recommended_dates(analysis.start_year, analysis.end_year)
    etapes["normalisation_dates"] = {
        "duree_secondes": round(time.perf_counter() - debut, 4),
        "resultat": f"{start_date} - {end_date}",
    }

    etat_technique = "reussi" if source and country and selected_models else "echoue"
    etat_metier = backend_business_state(
        intent=governed.intent,
        accepted=selected_models,
        required_available=governed.required_available,
        blocked_selected=[],
        ambiguity_level=analysis.ambiguity_level,
        data_preview_available=False,
    )

    validated_indicators = [
        ValidatedIndicatorSuggestion(
            id=indicator.id,
            code=indicator.code,
            name=indicator.name,
            reason=reason_by_id.get(indicator.id, "Indicateur valide par la base locale."),
        )
        for indicator in selected_models
    ]

    evidence_pack = _build_evidence_pack(
        user_request=user_request,
        analysis=analysis,
        governed=governed,
        selected_models=selected_models,
        non_retenus=non_retenus,
        invalides=invalides,
        source=source,
        country=country,
        topic=topic,
        etat_technique=etat_technique,
        etat_metier=etat_metier,
    )

    debut = time.perf_counter()
    evaluation = _server_validation_evaluation(evidence_pack)
    etapes["validation_serveur"] = {
        "duree_secondes": round(time.perf_counter() - debut, 4),
        "resultat": evaluation.get("result", {}).get("judge_decision", "unknown"),
        "role_ia": "aucun_appel_ia",
    }

    duree_totale = round(time.perf_counter() - mesure_debut, 4)
    decision_evaluateur = evaluation.get("result", {}).get("judge_decision")
    provider_summary = AI_PROVIDER
    model_summary = AI_MODEL

    recommendation = ValidatedDatasetRecommendation(
        source_code=source.code if source else (analysis.source_code or "WB").upper(),
        source_id=source.id if source else None,
        topic_id=topic.id if topic else None,
        topic_name=topic.name if topic else None,
        country_id=country.id if country else None,
        country_name=country.name if country else normalize_country_query(analysis.country_name),
        country_code=country.code_iso3 if country else country_resolution.country_code,
        start_date=start_date,
        end_date=end_date,
        title=title,
        description=description,
        indicators=validated_indicators,
        missing_indicator_codes=[],
        confidence=confidence,
        etat_technique=etat_technique,
        etat_metier=etat_metier,
        decision_evaluateur=decision_evaluateur,
        topic_intent=governed.intent,
        source_execution=source_execution,
        ai_calls=nombre_appels_ia,
        fallback_used=False,
        fallback_reason="none",
        country_resolution=country_resolution.model_dump(),
        fournisseur_ia=provider_summary,
        modele_ia=model_summary,
        tokens_utilises=None,
        tokens_restants=None,
        quota_requetes_atteint=False,
    )

    journal = _build_decision_journal(
        user_request=user_request,
        analysis=analysis,
        governed=governed,
        selected_models=selected_models,
        non_retenus=non_retenus,
        invalides=invalides,
        source=source,
        country=country,
        topic=topic,
        start_date=start_date,
        end_date=end_date,
        etapes=etapes,
        duree_totale_secondes=duree_totale,
        source_limit=source_limit,
        confidence=confidence,
        etat_technique=etat_technique,
        etat_metier=etat_metier,
        evaluation=evaluation,
        source_execution=source_execution,
        triggered_by=triggered_by,
        run_id=run_id,
    )
    journal["country_resolution"] = country_resolution.model_dump()
    if AI_LOG_DECISIONS:
        enregistrer_journal_decision_ia_detaille(journal)

    enregistrer_mesure(
        "ia",
        {
            "type": "assistant_ia_audit" if audit else "assistant_ia_normal",
            "source_execution": source_execution,
            "triggered_by": triggered_by,
            "pipeline_version": "single_ai_v1",
            "run_id": run_id,
            "etat": etat_technique,
            "etat_technique": etat_technique,
            "etat_metier": etat_metier,
            "decision_evaluateur": decision_evaluateur,
            "fournisseur": provider_summary,
            "modele": model_summary,
            "nombre_appels_ia": nombre_appels_ia,
            "ai_calls": nombre_appels_ia,
            "fallback_used": False,
            "fallback_reason": "none",
            "provider_error_type": None,
            "roles_ia": ["recommandation"],
            "fournisseurs_roles": {
                "recommandation": AI_PROVIDER,
            },
            "modeles_roles": {
                "recommandation": AI_MODEL,
            },
            "taille_demande_caracteres": len(user_request),
            "nombre_candidats": governed.found_before_limit,
            "nombre_candidats_apres_regles": governed.after_rules_count,
            "nombre_candidats_envoyes": governed.sent_count,
            "limite_candidats_ia": governed.limit,
            "nombre_indicateurs_selectionnes": len(selection_items),
            "nombre_indicateurs_valides": len(validated_indicators),
            "nombre_indicateurs_bloques": len(governed.blocked_indicators),
            "limite_source": source_limit,
            "source_limite_label": source_label,
            "duree_totale_secondes": duree_totale,
            "etapes": etapes,
        },
    )
    return recommendation


def _chain_provider_summary(*, include_details: bool = False) -> str:
    return AI_PROVIDER


def _chain_model_summary(*, include_details: bool = False) -> str:
    return AI_MODEL


def recommend_dataset_local(
    db: Session,
    user_request: str,
    *,
    source_execution: str = "local_rules",
    triggered_by: str = "unknown",
    run_id: str | None = None,
    fallback_used: bool = False,
    fallback_reason: str = "none",
    provider_error_type: str | None = None,
) -> ValidatedDatasetRecommendation | None:
    """
    Deterministic recommendation for simple requests.

    No external AI call is made here. The function only uses:
    - local countries,
    - local indicators,
    - business rules and controlled vocabulary.
    """
    mesure_debut = time.perf_counter()
    run_id = run_id or f"local-{uuid.uuid4().hex}"
    local_intent = _detect_local_intent(user_request)
    if local_intent is None:
        return None

    correction_detectee = _detect_local_correction(user_request)
    source = validate_source(db, "WB")
    country_resolution = resolve_country_from_request(db, user_request)
    if country_resolution.error == "country_not_found":
        raise ValueError("Pays non reconnu. Veuillez sélectionner le pays manuellement.")
    if country_resolution.error:
        raise ValueError("Pays non reconnu. Veuillez sélectionner le pays manuellement.")
    country = db.get(Country, country_resolution.country_id) if country_resolution.country_id else None
    country_name = country.name if country else (country_resolution.country_name or DEFAULT_COUNTRY_NAME)
    source_limit = get_source_indicator_limit("WB")
    source_label = get_source_label("WB")
    rule = BUSINESS_RULES.get(local_intent, {})
    selected_models = [
        indicator
        for code in rule.get("direct_codes", ())
        if (indicator := exact_code_available(db, code, source_id=source.id if source else None)) is not None
    ][: min(TARGET_INDICATORS, source_limit)]
    if not selected_models:
        return None

    start_year, end_year = _detect_year_range(user_request)
    concepts = _local_concepts_for_intent(local_intent, correction_detectee=correction_detectee)
    keywords = build_search_terms(user_request, [*concepts, local_intent])
    raw_candidates = search_candidate_indicators(
        db,
        source_id=source.id if source else None,
        keywords=keywords,
        limit=max(MAX_CANDIDATES * 4, MAX_CANDIDATES),
    )
    governed = govern_indicator_candidates(
        db,
        source=source,
        user_request=user_request,
        normalized_request=NormalizedRequest(
            source_code="WB",
            country_name=country_name,
            start_year=start_year,
            end_year=end_year,
            topic_intent=local_intent,
            requested_concepts=concepts,
            title=_local_title(local_intent, country_name),
            description=_local_description(local_intent, country_name, start_year),
            search_keywords=keywords[:12],
            ambiguity_level="low",
            declared_confidence="medium",
        ),
        text_candidates=raw_candidates,
        search_terms=keywords,
        limit=MAX_CANDIDATES,
    )

    # Keep deterministic direct indicators in business-rule order.
    reason_by_id = {
        indicator.id: _local_reason(local_intent, indicator, correction_detectee=correction_detectee)
        for indicator in selected_models
    }
    topic = infer_topic_from_indicators(
        db,
        selected_models,
        intent=local_intent,
        source_id=source.id if source else None,
    )
    start_date, end_date = normalize_recommended_dates(start_year, end_year)
    etat_technique = "reussi" if source and country and selected_models else "echoue"
    etat_metier = backend_business_state(
        intent=local_intent,
        accepted=selected_models,
        required_available=governed.required_available,
        blocked_selected=[],
        ambiguity_level="low",
        data_preview_available=False,
    )
    analysis = NormalizedRequest(
        source_code="WB",
        country_name=country_name,
        start_year=start_year,
        end_year=end_year,
        topic_intent=local_intent,
        requested_concepts=concepts,
        title=_local_title(local_intent, country_name),
        description=_local_description(local_intent, country_name, start_year),
        search_keywords=keywords[:12],
        ambiguity_level="low",
        declared_confidence="medium",
    )
    etapes = {
        "detection_locale": {
            "duree_secondes": 0,
            "resultat": f"Intention locale : {local_intent}",
            "role_ia": "aucun_appel_ia",
        },
        "recherche_locale": {
            "duree_secondes": 0,
            "resultat": f"{len(raw_candidates)} candidat(s) texte avant regles",
        },
        "regles_metier": {
            "duree_secondes": 0,
            "resultat": (
                f"{governed.after_rules_count} candidat(s) apres regles; "
                f"{governed.sent_count} disponible(s) pour controle local; "
                f"{len(governed.blocked_indicators)} bloque(s)"
            ),
            "intention": local_intent,
        },
        "validation_backend": {
            "duree_secondes": 0,
            "resultat": topic.name if topic else "Aucun theme dominant",
        },
        "normalisation_dates": {
            "duree_secondes": 0,
            "resultat": f"{start_date} - {end_date}",
        },
        "evaluation_backend": {
            "duree_secondes": 0,
            "resultat": "Evaluation deterministe sans fournisseur IA",
        },
    }
    evidence_pack = _build_evidence_pack(
        user_request=user_request,
        analysis=analysis,
        governed=governed,
        selected_models=selected_models,
        non_retenus=[],
        invalides=[],
        source=source,
        country=country,
        topic=topic,
        etat_technique=etat_technique,
        etat_metier=etat_metier,
    )
    evaluation = _server_validation_evaluation(evidence_pack)
    duree_totale = round(time.perf_counter() - mesure_debut, 4)
    recommendation = ValidatedDatasetRecommendation(
        source_code="WB",
        source_id=source.id if source else None,
        topic_id=topic.id if topic else None,
        topic_name=topic.name if topic else None,
        country_id=country.id if country else None,
        country_name=country_name,
        country_code=country.code_iso3 if country else country_resolution.country_code,
        start_date=start_date,
        end_date=end_date,
        title=analysis.title,
        description=analysis.description,
        indicators=[
            ValidatedIndicatorSuggestion(
                id=indicator.id,
                code=indicator.code,
                name=indicator.name,
                reason=reason_by_id[indicator.id],
            )
            for indicator in selected_models
        ],
        missing_indicator_codes=[],
        confidence="medium",
        etat_technique=etat_technique,
        etat_metier=etat_metier,
        decision_evaluateur=evaluation.get("result", {}).get("judge_decision", "unknown"),
        topic_intent=local_intent,
        source_execution=source_execution,
        ai_calls=0,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        provider_error_type=provider_error_type,
        correction_detectee=correction_detectee,
        country_resolution=country_resolution.model_dump(),
        fournisseur_ia="local",
        modele_ia="regles_metier_locales",
        tokens_utilises=None,
        tokens_restants=None,
        quota_requetes_atteint=False,
    )
    journal = _build_decision_journal(
        user_request=user_request,
        analysis=analysis,
        governed=governed,
        selected_models=selected_models,
        non_retenus=[],
        invalides=[],
        source=source,
        country=country,
        topic=topic,
        start_date=start_date,
        end_date=end_date,
        etapes=etapes,
        duree_totale_secondes=duree_totale,
        source_limit=source_limit,
        confidence="medium",
        etat_technique=etat_technique,
        etat_metier=etat_metier,
        evaluation=evaluation,
        source_execution=source_execution,
        triggered_by=triggered_by,
        run_id=run_id,
    )
    journal["fallback_used"] = fallback_used
    journal["fallback_reason"] = fallback_reason
    journal["provider_error_type"] = provider_error_type
    journal["ai_calls"] = 0
    journal["correction_detectee"] = correction_detectee
    journal["country_resolution"] = country_resolution.model_dump()
    if AI_LOG_DECISIONS:
        enregistrer_journal_decision_ia_detaille(journal)

    enregistrer_mesure(
        "ia",
        {
            "type": "assistant_ia_local",
            "source_execution": source_execution,
            "triggered_by": triggered_by,
            "pipeline_version": "single_ai_v1",
            "run_id": run_id,
            "etat": etat_technique,
            "etat_technique": etat_technique,
            "etat_metier": etat_metier,
            "decision_evaluateur": recommendation.decision_evaluateur,
            "fournisseur": "local",
            "modele": "regles_metier_locales",
            "nombre_appels_ia": 0,
            "ai_calls": 0,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "provider_error_type": provider_error_type,
            "roles_ia": [],
            "modeles_roles": {},
            "taille_demande_caracteres": len(user_request),
            "nombre_candidats": governed.found_before_limit,
            "nombre_candidats_apres_regles": governed.after_rules_count,
            "nombre_candidats_envoyes": 0,
            "limite_candidats_ia": governed.limit,
            "nombre_indicateurs_selectionnes": len(selected_models),
            "nombre_indicateurs_valides": len(selected_models),
            "nombre_indicateurs_bloques": len(governed.blocked_indicators),
            "limite_source": source_limit,
            "source_limite_label": source_label,
            "duree_totale_secondes": duree_totale,
            "etapes": etapes,
        },
    )
    return recommendation


def _detect_local_intent(user_request: str) -> str | None:
    text = normalize_for_rules(user_request)

    if any(term in text for term in ("gpd", "gdp", "pib", "croissance economique")):
        return "economic_growth"

    if "inflation" in text or "prix a la consommation" in text or "cpi" in text:
        return "inflation"

    if any(term in text for term in ("pauvrete", "poverty", "seuil de pauvrete", "conditions de vie", "inegalite", "revenu")):
        return "poverty"

    if any(term in text for term in ("population", "habitants", "individus", "demographie")):
        return "population"

    if any(term in text for term in ("chomage", "chomeur", "chomeurs", "unemployment")):
        return "unemployment"

    return None


def _detect_local_correction(user_request: str) -> str | None:
    text = normalize_for_rules(user_request)
    if re.search(r"\bgpd\b", text):
        return "Correction detectee : GPD -> GDP / PIB"
    return None


def _detect_year_range(user_request: str) -> tuple[int, int]:
    years = [
        int(match)
        for match in re.findall(r"\b(?:19|20)\d{2}\b", user_request)
        if MIN_SAFE_YEAR <= int(match) <= MAX_SAFE_YEAR
    ]
    start = years[0] if years else DEFAULT_AI_START_YEAR
    end = years[-1] if len(years) > 1 else DEFAULT_AI_END_YEAR
    if start > end:
        start, end = end, start
    return start, min(end, DEFAULT_AI_END_YEAR)


def _local_concepts_for_intent(intent: str, *, correction_detectee: str | None) -> list[str]:
    concepts = {
        "economic_growth": ["PIB", "GDP", "croissance economique"],
        "inflation": ["inflation", "prix a la consommation", "indice des prix"],
        "poverty": ["pauvreté", "poverty", "seuil de pauvreté", "revenu", "inégalité", "conditions de vie"],
        "population": ["population totale", "habitants", "demographie"],
        "unemployment": ["chomage", "emploi", "population active"],
    }.get(intent, [intent])

    if correction_detectee:
        concepts = [*concepts, "correction GPD vers GDP"]

    return concepts


def _local_title(intent: str, country_name: str) -> str:
    labels = {
        "economic_growth": f"PIB et croissance economique - {country_name}",
        "inflation": f"Inflation - {country_name}",
        "poverty": f"Pauvreté et conditions de vie - {country_name}",
        "population": f"Population - {country_name}",
        "unemployment": f"Taux de chomage - {country_name}",
    }
    return labels.get(intent, f"Jeu de donnees - {country_name}")


def _local_description(intent: str, country_name: str, start_year: int) -> str:
    descriptions = {
        "economic_growth": "Ce jeu de donnees presente les indicateurs principaux du PIB et de la croissance economique.",
        "inflation": "Ce jeu de donnees presente l'inflation mesuree par les prix a la consommation.",
        "poverty": "Ce jeu de donnees presente les indicateurs de pauvreté, de revenu et de conditions de vie disponibles dans le catalogue local.",
        "population": "Ce jeu de donnees presente l'evolution de la population totale et des indicateurs demographiques associes.",
        "unemployment": "Ce jeu de donnees presente les indicateurs du chomage et de l'emploi disponibles dans le catalogue local.",
    }
    return descriptions.get(
        intent,
        f"Ce jeu de donnees presente les indicateurs disponibles pour {country_name} depuis {start_year}.",
    )


def _local_reason(intent: str, indicator: Indicator, *, correction_detectee: str | None) -> str:
    reasons = {
        "economic_growth": "Indicateur direct du PIB ou de la croissance economique.",
        "inflation": "Indicateur direct de l'inflation ou des prix a la consommation.",
        "population": "Indicateur direct de population ou de demographie.",
        "unemployment": "Indicateur direct du taux de chomage.",
    }
    reason = reasons.get(intent, "Indicateur retenu par les regles locales.")
    if correction_detectee and indicator.code.startswith("NY.GDP"):
        reason = f"{reason} {correction_detectee}."
    return reason


def _is_ai_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(fragment in text for fragment in ("429", "resource_exhausted", "quota exceeded", "retrydelay"))


def _extract_retry_after_seconds(exc: Exception) -> int | None:
    text = str(exc)
    patterns = [
        r"retryDelay['\"]?\s*:\s*['\"]?(\d+)",
        r"retryDelay['\"]?\s*:\s*['\"]?(\d+)s",
        r"retry in\s+(\d+)",
        r"retryDelay.*?(\d+)s",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


def _governance_passthrough(db: Session, candidates: list[Indicator], keywords: list[str]) -> GovernedCandidates:
    payload = build_candidate_payload(db, candidates[:MAX_CANDIDATES])
    for item in payload:
        item["indicator_id"] = item.pop("id")
        item["score_backend"] = 0
    return GovernedCandidates(
        intent="general",
        intent_label="General",
        found_before_limit=len(candidates),
        after_rules_count=len(candidates),
        sent_count=len(payload),
        limit=MAX_CANDIDATES,
        candidates=candidates[:MAX_CANDIDATES],
        candidate_payload=payload,
        rules_applied=["Regles metier desactivees par configuration"],
        expanded_terms=keywords,
    )


def _ensure_required_indicators(
    *,
    selected_models: list[Indicator],
    reason_by_id: dict[int, str],
    governed: GovernedCandidates,
    max_indicators: int,
) -> None:
    selected_ids = {indicator.id for indicator in selected_models}
    for indicator in governed.required_available:
        if indicator.id in selected_ids:
            continue
        selected_models.insert(0, indicator)
        reason_by_id[indicator.id] = (
            "Indicateur direct obligatoire ajoute par les regles metier serveur."
        )
        selected_ids.add(indicator.id)
    del selected_models[max_indicators:]


def _indicator_log(indicator: Indicator) -> dict[str, Any]:
    return {
        "id": indicator.id,
        "code": indicator.code,
        "nom": indicator.name,
    }


def _build_evidence_pack(
    *,
    user_request: str,
    analysis: NormalizedRequest,
    governed: GovernedCandidates,
    selected_models: list[Indicator],
    non_retenus: list[dict[str, Any]],
    invalides: list[dict[str, Any]],
    source: Source | None,
    country: Country | None,
    topic: Topic | None,
    etat_technique: str,
    etat_metier: str,
) -> dict[str, Any]:
    return {
        "user_request": user_request,
        "normalized_request": {
            "source_code": analysis.source_code,
            "country_name": analysis.country_name,
            "start_year": analysis.start_year,
            "end_year": analysis.end_year,
            "topic_intent": analysis.topic_intent,
            "requested_concepts": analysis.requested_concepts,
            "search_keywords": analysis.search_keywords,
            "ambiguity_level": analysis.ambiguity_level,
            "declared_confidence": analysis.declared_confidence,
        },
        "business_rules_applied": governed.rules_applied,
        "required_direct_indicators": [_indicator_log(indicator) for indicator in governed.required_available],
        "candidate_summary": {
            "found_before_limit": governed.found_before_limit,
            "after_business_rules": governed.after_rules_count,
            "sent_to_model": governed.sent_count,
            "blocked_count": len(governed.blocked_indicators),
            "limit": governed.limit,
        },
        "selected_indicators": [_indicator_log(indicator) for indicator in selected_models],
        "not_selected_indicators": non_retenus,
        "invalid_indicators": invalides,
        "blocked_indicators": governed.blocked_indicators,
        "backend_validation": {
            "source_valid": source is not None,
            "country_valid": country is not None,
            "topic_valid": topic is not None,
            "indicators_exist_in_db": bool(selected_models),
            "data_preview_available": False,
            "technical_state": etat_technique,
        },
        "backend_business_state": etat_metier,
    }


def _server_validation_evaluation(evidence_pack: dict[str, Any]) -> dict[str, Any]:
    backend_validation = evidence_pack.get("backend_validation") or {}
    business_state = str(evidence_pack.get("backend_business_state") or "unknown")
    technical_ok = bool(
        backend_validation.get("source_valid")
        and backend_validation.get("country_valid")
        and backend_validation.get("indicators_exist_in_db")
    )
    decision = "approved" if technical_ok and business_state.startswith("valide") else "review"
    return {
        "mode": "server_validation",
        "result": {
            "judge_decision": decision,
            "relevance_score": 80 if technical_ok else 40,
            "directness_score": 80 if evidence_pack.get("selected_indicators") else 0,
            "data_availability_score": 0,
            "explanation": "Validation serveur locale sans appel IA evaluateur.",
            "strengths": ["Les indicateurs retenus existent dans la base locale."] if technical_ok else [],
            "weaknesses": [] if technical_ok else ["La recommandation doit etre verifiee dans le builder."],
            "requires_human_review": business_state != "valide",
            "explanation_affichee": "Validation serveur locale sans appel IA evaluateur.",
        },
        "error": None,
    }


def _build_decision_journal(
    *,
    user_request: str,
    analysis: NormalizedRequest,
    governed: GovernedCandidates,
    selected_models: list[Indicator],
    non_retenus: list[dict[str, Any]],
    invalides: list[dict[str, Any]],
    source: Source | None,
    country: Country | None,
    topic: Topic | None,
    start_date: str,
    end_date: str,
    etapes: dict[str, Any],
    duree_totale_secondes: float,
    source_limit: int,
    confidence: str,
    etat_technique: str,
    etat_metier: str,
    evaluation: dict[str, Any],
    source_execution: str,
    triggered_by: str,
    run_id: str,
) -> dict[str, Any]:
    missing_required = required_missing(governed.required_available, selected_models)
    points_forts = _backend_strengths(
        source=source,
        country=country,
        topic=topic,
        selected_models=selected_models,
        governed=governed,
        missing_required=missing_required,
    )
    points_faibles = _backend_weaknesses(
        analysis=analysis,
        governed=governed,
        selected_models=selected_models,
        missing_required=missing_required,
        invalides=invalides,
    )
    evaluation_result = evaluation.get("result") or {}
    return {
        "type": "evaluation_assistant_ia",
        "source_execution": source_execution,
        "triggered_by": triggered_by,
        "pipeline_version": "single_ai_v1",
        "run_id": run_id,
        "etat": etat_technique,
        "etat_technique": etat_technique,
        "etat_metier": etat_metier,
        "decision_evaluateur": evaluation_result.get("judge_decision"),
        "demande_utilisateur": user_request,
        "analyse_normalisee": {
            "source": analysis.source_code,
            "pays": analysis.country_name,
            "annee_debut": analysis.start_year,
            "annee_fin": analysis.end_year,
            "intention": governed.intent,
            "concepts": analysis.requested_concepts,
            "mots_cles": analysis.search_keywords,
            "ambiguite": analysis.ambiguity_level,
            "confiance_declaree_ia": analysis.declared_confidence,
        },
        "analyse_ia": {
            "source": analysis.source_code,
            "pays": analysis.country_name,
            "annee_debut": analysis.start_year,
            "annee_fin": analysis.end_year,
            "mots_cles": analysis.search_keywords,
            "confiance": confidence,
        },
        "recherche_locale": {
            "termes_expanses": governed.expanded_terms,
            "nombre_candidats_avant_limite": governed.found_before_limit,
            "nombre_candidats_apres_regles": governed.after_rules_count,
            "nombre_candidats_envoyes": governed.sent_count,
            "limite_candidats_ia": governed.limit,
            "nombre_bloques": len(governed.blocked_indicators),
            "candidats_principaux": governed.candidate_payload[:10],
        },
        "regles_metier": governed.rules_applied,
        "selection_finale": {
            "acceptes": [_indicator_log(indicator) for indicator in selected_models],
            "non_retenus": non_retenus,
            "bloques": governed.blocked_indicators,
            "invalides": invalides,
            "rejetes": [*non_retenus, *governed.blocked_indicators, *invalides],
        },
        "validation": {
            "source_valide": source is not None,
            "pays_valide": country is not None,
            "theme_valide": topic is not None,
            "donnees_verifiees": False,
            "message_donnees": "Données non vérifiées : aucun aperçu réel n'est exécuté pendant la recommandation IA.",
            "theme_final": topic.name if topic else None,
            "pays_final": country.name if country else None,
            "source_finale": source.code if source else None,
            "date_debut": start_date,
            "date_fin": end_date,
            "indicateurs_valides": len(selected_models),
            "limite_source": source_limit,
            "dans_limite_source": len(selected_models) <= source_limit,
        },
        "evaluation_ia": evaluation,
        "points_forts": points_forts,
        "points_faibles": points_faibles,
        "etapes": etapes,
        "duree_totale_secondes": duree_totale_secondes,
    }


def _backend_strengths(
    *,
    source: Source | None,
    country: Country | None,
    topic: Topic | None,
    selected_models: list[Indicator],
    governed: GovernedCandidates,
    missing_required: list[Indicator],
) -> list[str]:
    points: list[str] = []
    if source is not None:
        points.append("Source validee dans la base locale.")
    if country is not None:
        points.append("Pays valide dans la base locale.")
    if topic is not None:
        points.append("Theme coherent avec les indicateurs retenus.")
    if governed.required_available and not missing_required:
        points.append("Indicateur direct obligatoire retenu.")
    if selected_models:
        points.append("Tous les indicateurs acceptes existent dans la base locale.")
    if not governed.blocked_indicators:
        points.append("Aucun indicateur hors sujet bloque n'a ete retenu.")
    return points or ["Aucun point fort automatique detecte."]


def _backend_weaknesses(
    *,
    analysis: NormalizedRequest,
    governed: GovernedCandidates,
    selected_models: list[Indicator],
    missing_required: list[Indicator],
    invalides: list[dict[str, Any]],
) -> list[str]:
    points: list[str] = []
    if analysis.ambiguity_level != "low":
        points.append(f"Ambiguite detectee dans la demande : {analysis.ambiguity_level}.")
    if missing_required:
        points.append(
            "Indicateur direct obligatoire manquant : "
            + ", ".join(indicator.code for indicator in missing_required)
        )
    if governed.blocked_indicators:
        points.append(f"{len(governed.blocked_indicators)} indicateur(s) bloque(s) par les regles metier.")
    if invalides:
        points.append(f"{len(invalides)} identifiant(s) IA invalide(s) ignores par le serveur.")
    if not selected_models:
        points.append("Aucun indicateur valide n'a ete retenu.")
    if governed.sent_count == governed.limit and governed.found_before_limit > governed.limit:
        points.append("La liste candidate a ete coupee apres application des regles metier.")
    if selected_models:
        points.append("Données non vérifiées : la recommandation IA n'exécute pas l'aperçu réel.")
    return points or ["Aucun point faible automatique detecte."]
