from __future__ import annotations

import fnmatch
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AI_MAX_CANDIDATES, AI_MIN_DIRECT_MATCHES
from app.models import Indicator, IndicatorTopic, Source, Topic


BUSINESS_RULES: dict[str, dict[str, Any]] = {
    "population": {
        "label": "Population et demographie",
        "triggers": ("population", "habitants", "individus", "demographie", "personnes"),
        "direct_codes": ("SP.POP.TOTL", "SP.POP.GROW", "SP.URB.TOTL", "SP.RUR.TOTL"),
        "required_if_available": ("SP.POP.TOTL",),
        "forbidden_if_not_requested": ("AG.CON.*", "AG.LND.*"),
    },
    "inflation": {
        "label": "Inflation et prix",
        "triggers": ("inflation", "prix", "cpi", "consommation"),
        "direct_codes": ("FP.CPI.TOTL.ZG", "FP.CPI.TOTL"),
        "required_if_available": ("FP.CPI.TOTL.ZG",),
        "forbidden_if_not_requested": ("EN.*", "EG.*"),
    },
    "economic_growth": {
        "label": "Croissance economique",
        "triggers": ("croissance economique", "croissance", "pib", "gdp", "economie"),
        "direct_codes": ("NY.GDP.MKTP.KD.ZG", "NY.GDP.MKTP.CD", "NY.GDP.PCAP.CD", "NY.GDP.PCAP.KD.ZG"),
        "required_if_available": ("NY.GDP.MKTP.KD.ZG",),
        "forbidden_if_not_requested": (),
    },
    "unemployment": {
        "label": "Chomage",
        "triggers": ("chomage", "emploi", "travail", "unemployment"),
        "direct_codes": ("SL.UEM.TOTL.ZS",),
        "required_if_available": ("SL.UEM.TOTL.ZS",),
        "forbidden_if_not_requested": (),
        "preferred_topics": (
            "Main-d'oeuvre et protection sociale",
            "Main-d'oeuvre",
            "Travail",
            "Emploi",
            "Chomage",
        ),
    },
    "trade_external_sector": {
        "label": "Commerce exterieur et secteur externe",
        "triggers": (
            "commerce exterieur",
            "commerce extérieur",
            "exportations",
            "importations",
            "trade",
            "exports",
            "imports",
            "external trade",
            "ouverture commerciale",
            "dependance commerciale",
            "dépendance commerciale",
            "balance commerciale",
            "solde courant",
            "compte courant",
            "reserves internationales",
            "réserves internationales",
        ),
        "direct_codes": (
            "NE.TRD.GNFS.ZS",
            "NE.EXP.GNFS.ZS",
            "NE.IMP.GNFS.ZS",
            "BX.GSR.GNFS.CD",
            "BM.GSR.GNFS.CD",
            "BN.CAB.XOKA.CD",
            "FI.RES.TOTL.CD",
        ),
        "required_if_available": (),
        "forbidden_if_not_requested": (),
    },
    "foreign_direct_investment": {
        "label": "Investissement direct etranger",
        "triggers": (
            "investissement direct etranger",
            "investissement direct étranger",
            "ide",
            "fdi",
            "foreign direct investment",
            "investissements etrangers",
            "investissements étrangers",
        ),
        "direct_codes": (
            "BX.KLT.DINV.CD.WD",
            "BX.KLT.DINV.WD.GD.ZS",
            "BM.KLT.DINV.CD.WD",
            "BM.KLT.DINV.WD.GD.ZS",
        ),
        "required_if_available": (),
        "forbidden_if_not_requested": (),
    },
    "health": {
        "label": "Sante",
        "triggers": ("sante", "mortalite", "esperance de vie", "health"),
        "direct_codes": ("SP.DYN.LE00.IN", "SH.XPD.CHEX.PC.CD"),
        "required_if_available": (),
        "forbidden_if_not_requested": (),
    },
    "education": {
        "label": "Education",
        "triggers": ("education", "scolarisation", "enseignement", "ecole"),
        "direct_codes": ("SE.PRM.ENRR", "SE.SEC.ENRR", "SE.XPD.TOTL.GD.ZS"),
        "required_if_available": (),
        "forbidden_if_not_requested": (),
    },
}


@dataclass
class GovernedCandidates:
    intent: str
    intent_label: str
    found_before_limit: int
    after_rules_count: int
    sent_count: int
    limit: int
    candidates: list[Indicator]
    candidate_payload: list[dict[str, Any]]
    direct_indicators: list[Indicator] = field(default_factory=list)
    required_available: list[Indicator] = field(default_factory=list)
    blocked_indicators: list[dict[str, Any]] = field(default_factory=list)
    scores: dict[int, int] = field(default_factory=dict)
    rules_applied: list[str] = field(default_factory=list)
    expanded_terms: list[str] = field(default_factory=list)


def normalize_for_rules(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.lower()


def detect_business_intent(user_request: str, normalized_request: Any) -> str:
    declared_intent = normalize_for_rules(getattr(normalized_request, "topic_intent", ""))
    if declared_intent in BUSINESS_RULES:
        return declared_intent

    normalized_parts = [
        user_request,
        getattr(normalized_request, "topic_intent", ""),
        " ".join(getattr(normalized_request, "requested_concepts", []) or []),
        " ".join(getattr(normalized_request, "search_keywords", []) or []),
    ]
    text = normalize_for_rules(" ".join(normalized_parts))

    for intent, rule in BUSINESS_RULES.items():
        for trigger in rule.get("triggers", ()):
            if normalize_for_rules(trigger) in text:
                return intent

    return "general"


def govern_indicator_candidates(
    db: Session,
    *,
    source: Source | None,
    user_request: str,
    normalized_request: Any,
    text_candidates: list[Indicator],
    search_terms: list[str],
    limit: int = AI_MAX_CANDIDATES,
) -> GovernedCandidates:
    intent = detect_business_intent(user_request, normalized_request)
    rule = BUSINESS_RULES.get(intent, {})
    source_id = source.id if source else None

    direct_indicators = _find_indicators_by_codes(
        db,
        rule.get("direct_codes", ()),
        source_id=source_id,
    )
    required_available = _find_indicators_by_codes(
        db,
        rule.get("required_if_available", ()),
        source_id=source_id,
    )

    merged_by_id: dict[int, Indicator] = {}
    for indicator in [*direct_indicators, *text_candidates]:
        merged_by_id.setdefault(indicator.id, indicator)

    found_before_limit = len(merged_by_id)
    forbidden_patterns = tuple(rule.get("forbidden_if_not_requested", ()))
    blocked: list[dict[str, Any]] = []
    allowed: list[Indicator] = []
    scores: dict[int, int] = {}

    for indicator in merged_by_id.values():
        if _matches_any(indicator.code, forbidden_patterns):
            blocked.append(
                {
                    "id": indicator.id,
                    "code": indicator.code,
                    "nom": indicator.name,
                    "raison": f"Bloque par la regle metier '{intent}'",
                }
            )
            continue
        score = _score_indicator(indicator, intent=intent, rule=rule, search_terms=search_terms)
        scores[indicator.id] = score
        allowed.append(indicator)

    allowed.sort(key=lambda indicator: (-scores.get(indicator.id, 0), indicator.code))
    selected_for_ai = allowed[:limit]
    payload = build_governed_candidate_payload(db, selected_for_ai, scores)

    rules_applied = [
        "Normalisation de la demande sans codes indicateurs",
        "Recherche locale dans la base de metadonnees",
        f"Limite candidats IA appliquee a la fin : {limit}",
    ]
    if rule:
        rules_applied.append(f"Intention metier detectee : {rule.get('label', intent)}")
    if direct_indicators:
        rules_applied.append(
            "Codes directs ajoutes avant la limite : "
            + ", ".join(indicator.code for indicator in direct_indicators)
        )
    if required_available:
        rules_applied.append(
            "Indicateurs obligatoires disponibles : "
            + ", ".join(indicator.code for indicator in required_available)
        )
    if blocked:
        rules_applied.append(f"{len(blocked)} indicateur(s) bloque(s) par regle metier")

    return GovernedCandidates(
        intent=intent,
        intent_label=str(rule.get("label", intent if intent != "general" else "General")),
        found_before_limit=found_before_limit,
        after_rules_count=len(allowed),
        sent_count=len(selected_for_ai),
        limit=limit,
        candidates=selected_for_ai,
        candidate_payload=payload,
        direct_indicators=direct_indicators,
        required_available=required_available,
        blocked_indicators=blocked,
        scores=scores,
        rules_applied=rules_applied,
        expanded_terms=search_terms,
    )


def build_governed_candidate_payload(
    db: Session,
    candidates: list[Indicator],
    scores: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    topic_map = _topic_names_for_indicators(db, [indicator.id for indicator in candidates])
    scores = scores or {}
    return [
        {
            "indicator_id": indicator.id,
            "code": indicator.code,
            "name": indicator.name,
            "description": indicator.description or "",
            "topics": topic_map.get(indicator.id, []),
            "score_backend": scores.get(indicator.id, 0),
        }
        for indicator in candidates
    ]


def required_missing(required_available: list[Indicator], selected: list[Indicator]) -> list[Indicator]:
    selected_codes = {indicator.code for indicator in selected}
    return [indicator for indicator in required_available if indicator.code not in selected_codes]


def backend_business_state(
    *,
    intent: str,
    accepted: list[Indicator],
    required_available: list[Indicator],
    blocked_selected: list[dict[str, Any]],
    ambiguity_level: str,
    data_preview_available: bool = False,
) -> str:
    if blocked_selected:
        return "incoherent"
    if intent != "general" and required_missing(required_available, accepted):
        return "faible"
    if intent != "general":
        accepted_codes = {indicator.code for indicator in accepted}
        required_codes = {indicator.code for indicator in required_available}
        direct_matches = len(accepted_codes & required_codes)
        min_required_matches = min(AI_MIN_DIRECT_MATCHES, len(required_codes))
        if required_available and direct_matches < min_required_matches:
            return "faible"
    if ambiguity_level == "high":
        return "verification_humaine_requise"
    if accepted:
        return "valide" if data_preview_available else "valide_non_verifie"
    return "incoherent"


def preferred_topic_names(intent: str) -> tuple[str, ...]:
    rule = BUSINESS_RULES.get(intent, {})
    return tuple(rule.get("preferred_topics", ()))


def exact_code_available(db: Session, code: str, *, source_id: int | None = None) -> Indicator | None:
    stmt = select(Indicator).where(Indicator.code == code)
    if source_id is not None:
        stmt = stmt.where(Indicator.source_id == source_id)
    return db.scalar(stmt.limit(1))


def _find_indicators_by_codes(db: Session, codes: tuple[str, ...], *, source_id: int | None) -> list[Indicator]:
    indicators: list[Indicator] = []
    for code in codes:
        indicator = exact_code_available(db, code, source_id=source_id)
        if indicator is not None:
            indicators.append(indicator)
    return indicators


def _score_indicator(
    indicator: Indicator,
    *,
    intent: str,
    rule: dict[str, Any],
    search_terms: list[str],
) -> int:
    score = 0
    code = indicator.code
    searchable = normalize_for_rules(" ".join([indicator.code, indicator.name, indicator.description or ""]))

    if code in rule.get("required_if_available", ()):
        score += 220
    if code in rule.get("direct_codes", ()):
        score += 170

    for term in search_terms:
        clean = normalize_for_rules(term)
        if not clean:
            continue
        if clean in searchable:
            score += 6

    if intent == "inflation" and code.startswith("FP.CPI"):
        score += 80
    if intent == "population" and code.startswith("SP.POP"):
        score += 80
    if intent == "economic_growth" and code.startswith("NY.GDP"):
        score += 80
    if intent == "unemployment" and code.startswith("SL.UEM"):
        score += 80
    if intent == "trade_external_sector" and code.startswith(("NE.", "BX.GSR", "BM.GSR", "BN.CAB", "FI.RES")):
        score += 80
    if intent == "foreign_direct_investment" and "KLT.DINV" in code:
        score += 80

    return score


def _matches_any(code: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(code, pattern) for pattern in patterns)


def _topic_names_for_indicators(db: Session, indicator_ids: list[int]) -> dict[int, list[str]]:
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
