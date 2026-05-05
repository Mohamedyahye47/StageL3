from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Country, Indicator, IndicatorTopic, Source, Topic
from app.services.ai_vocabulary import expand_domain_terms, normalize_country_query


PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env", override=False)


DEFAULT_AI_START_YEAR = 2000
DEFAULT_AI_END_YEAR = 2023
MIN_SAFE_YEAR = 1960
MAX_SAFE_YEAR = 2023

MAX_CANDIDATES = 40
TARGET_INDICATORS = 5


class RequestAnalysis(BaseModel):
    source_code: str = Field(description="Recommended source code, usually WB")
    country_name: str = Field(description="Specific country name. Use Mauritanie if no specific country is provided.")
    start_year: int = Field(description="Recommended start year")
    end_year: int = Field(description="Recommended end year")
    title: str = Field(description="Suggested dataset title in French")
    description: str = Field(description="Suggested dataset description in French")
    search_keywords: list[str] = Field(description="Keywords to search the local metadata catalogue")
    confidence: Literal["low", "medium", "high"]


class SelectedCandidateIndicator(BaseModel):
    id: int = Field(description="The exact local database indicator id from the candidate list")
    reason: str = Field(description="Why this local indicator is useful")


class CandidateSelection(BaseModel):
    title: str = Field(description="Final dataset title in French")
    description: str = Field(description="Final dataset description in French")
    selected_indicators: list[SelectedCandidateIndicator] = Field(description="Selected indicators from candidate list only")
    confidence: Literal["low", "medium", "high"]


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
    start_date: str
    end_date: str
    title: str
    description: str
    indicators: list[ValidatedIndicatorSuggestion]
    missing_indicator_codes: list[str]
    confidence: Literal["low", "medium", "high"]


def get_gemini_client() -> genai.Client:
    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is missing in .env")

    return genai.Client()


def quick_ai_test() -> str:
    client = get_gemini_client()
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    response = client.models.generate_content(
        model=model_name,
        contents="Réponds en une phrase: le service AI de Richat DataBridge fonctionne.",
    )

    return response.text or ""


def analyze_user_request(user_request: str) -> RequestAnalysis:
    """
    Step 1:
    Gemini analyzes the user request.

    Important:
    Gemini is NOT allowed to recommend indicator codes here.
    It only returns:
    - source
    - country
    - dates
    - title
    - description
    - search keywords
    """

    if not user_request.strip():
        raise ValueError("user_request is required")

    client = get_gemini_client()
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    prompt = f"""
You are an assistant for Richat DataBridge.

Richat DataBridge creates economic and development datasets from a local metadata catalogue.

User request:
\"\"\"{user_request}\"\"\"

Task:
Analyze the request and return structured data.

Strict rules:
- Do NOT recommend indicator codes.
- Do NOT invent World Bank codes.
- Return only search keywords that can be used to search a local indicator catalogue.
- source_code should usually be "WB".
- If the user gives a specific country, use that country.
- If the user gives only a region or continent, use "Mauritanie" because the current builder supports one country at a time.
- If the user gives no country, use "Mauritanie".
- If the user gives a start year, respect it.
- If no start year is given, use 2000.
- Never use an end year after 2023.
- If no end year is given, use 2023.
- Title and description must be in French.
- Keywords should include French and English terms when useful.
- Return only structured data matching the schema.
"""

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": RequestAnalysis,
            "temperature": 0,
        },
    )

    if hasattr(response, "parsed") and response.parsed:
        return response.parsed

    return RequestAnalysis.model_validate_json(response.text)


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

    # Safe fallback for current project/demo
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
    Search LOCAL metadata first.

    Gemini will later choose only from these candidates.
    This prevents invented/non-existing indicator codes.
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
) -> CandidateSelection:
    """
    Step 2:
    Gemini chooses only from local DB candidates.

    It must return IDs, not invented codes.
    """

    client = get_gemini_client()
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    prompt = f"""
You are an assistant for Richat DataBridge.

User request:
\"\"\"{user_request}\"\"\"

Initial analysis:
{analysis.model_dump_json(ensure_ascii=False)}

Local candidate indicators:
{json.dumps(candidates_payload, ensure_ascii=False, indent=2)}

Task:
Choose the best indicators from the local candidate list.

Strict rules:
- You MUST choose indicators only from the candidate list.
- You MUST return only candidate IDs that exist in the candidate list.
- Do NOT invent codes.
- Do NOT invent IDs.
- Choose at most {TARGET_INDICATORS} indicators.
- If fewer than {TARGET_INDICATORS} are truly relevant, choose fewer.
- If the exact requested variable is not available, choose the closest useful proxy indicators and explain that clearly.
- Keep title and description in French.
- The title must match the selected local indicators, not imagined indicators.
- Return only structured data matching the schema.
"""

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": CandidateSelection,
            "temperature": 0,
        },
    )

    if hasattr(response, "parsed") and response.parsed:
        return response.parsed

    return CandidateSelection.model_validate_json(response.text)


def infer_topic_from_indicators(
    db: Session,
    indicator_models: list[Indicator],
) -> Topic | None:
    indicator_ids = [indicator.id for indicator in indicator_models]

    if not indicator_ids:
        return None

    rows = db.execute(
        select(IndicatorTopic.topic_id)
        .where(IndicatorTopic.indicator_id.in_(indicator_ids))
    ).all()

    topic_ids = [row[0] for row in rows if row[0] is not None]

    if not topic_ids:
        return None

    most_common_topic_id = Counter(topic_ids).most_common(1)[0][0]

    return db.scalar(
        select(Topic).where(Topic.id == most_common_topic_id)
    )


def fallback_selection_from_candidates(candidates: list[Indicator]) -> list[Indicator]:
    """
    Last-resort fallback if Gemini cannot choose but local candidates exist.
    """
    return candidates[: min(TARGET_INDICATORS, len(candidates))]


def recommend_dataset_validated(db: Session, user_request: str) -> ValidatedDatasetRecommendation:
    """
    Final safe workflow:
    1. Gemini analyzes intent only.
    2. Backend expands keywords using controlled vocabulary.
    3. Backend searches local metadata DB.
    4. Gemini chooses only from local candidate IDs.
    5. Backend validates selected IDs again.
    6. No invented indicator codes should appear.
    """

    analysis = analyze_user_request(user_request)

    source = validate_source(db, analysis.source_code)
    country = validate_country(db, analysis.country_name)

    keywords = build_search_terms(user_request, analysis.search_keywords)

    candidates = search_candidate_indicators(
        db,
        source_id=source.id if source else None,
        keywords=keywords,
        limit=MAX_CANDIDATES,
    )

    candidate_by_id = {indicator.id: indicator for indicator in candidates}

    selected_models: list[Indicator] = []
    reason_by_id: dict[int, str] = {}

    title = analysis.title
    description = analysis.description
    confidence: Literal["low", "medium", "high"] = analysis.confidence

    if candidates:
        candidate_payload = build_candidate_payload(db, candidates)

        selection = choose_indicators_from_candidates(
            user_request=user_request,
            analysis=analysis,
            candidates_payload=candidate_payload,
        )

        seen_selected_ids: set[int] = set()

        for item in selection.selected_indicators:
            indicator = candidate_by_id.get(item.id)

            if indicator is None:
                continue

            if indicator.id in seen_selected_ids:
                continue

            seen_selected_ids.add(indicator.id)
            selected_models.append(indicator)
            reason_by_id[indicator.id] = item.reason

            if len(selected_models) >= TARGET_INDICATORS:
                break

        title = selection.title or analysis.title
        description = selection.description or analysis.description
        confidence = selection.confidence or analysis.confidence

    else:
        description = (
            analysis.description
            + " Aucun indicateur local suffisamment pertinent n’a été trouvé dans le catalogue."
        )
        confidence = "low"

    if candidates and not selected_models:
        selected_models = fallback_selection_from_candidates(candidates)

        for indicator in selected_models:
            reason_by_id[indicator.id] = "Indicateur local pertinent trouvé dans le catalogue."

        confidence = "low"

    validated_indicators = [
        ValidatedIndicatorSuggestion(
            id=indicator.id,
            code=indicator.code,
            name=indicator.name,
            reason=reason_by_id.get(
                indicator.id,
                "Indicateur sélectionné depuis le catalogue local.",
            ),
        )
        for indicator in selected_models
    ]

    topic = infer_topic_from_indicators(db, selected_models)

    start_date, end_date = normalize_recommended_dates(
        analysis.start_year,
        analysis.end_year,
    )

    return ValidatedDatasetRecommendation(
        source_code=source.code if source else (analysis.source_code or "WB").upper(),
        source_id=source.id if source else None,
        topic_id=topic.id if topic else None,
        topic_name=topic.name if topic else None,
        country_id=country.id if country else None,
        country_name=country.name if country else normalize_country_query(analysis.country_name),
        start_date=start_date,
        end_date=end_date,
        title=title,
        description=description,
        indicators=validated_indicators,
        missing_indicator_codes=[],
        confidence=confidence,
    )