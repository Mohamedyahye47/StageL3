from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EvaluationResult(BaseModel):
    judge_decision: Literal["acceptable", "weak", "incoherent", "unknown"] = "unknown"
    relevance_score: int = Field(default=0, ge=0, le=100)
    directness_score: int = Field(default=0, ge=0, le=100)
    data_availability_score: int = Field(default=0, ge=0, le=100)
    explanation: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


def should_run_ai_evaluator(*, audit: bool) -> bool:
    return False


def evaluate_ai_recommendation(evidence_pack: dict[str, Any], *, use_ai: bool) -> dict[str, Any]:
    backend_state = str(evidence_pack.get("backend_business_state") or "unknown")
    backend_result = _backend_evaluation(evidence_pack, backend_state)
    return {
        "mode": "server_validation",
        "result": backend_result.model_dump(),
        "error": None,
    }


def _call_ai_evaluator(evidence_pack: dict[str, Any]) -> EvaluationResult:
    raise RuntimeError("L'évaluation IA séparée a été remplacée par la validation serveur.")


def _backend_evaluation(evidence_pack: dict[str, Any], backend_state: str) -> EvaluationResult:
    selected = evidence_pack.get("selected_indicators") or []
    required = evidence_pack.get("required_direct_indicators") or []
    blocked = evidence_pack.get("blocked_indicators") or []
    validation = evidence_pack.get("backend_validation") or {}

    strengths: list[str] = []
    weaknesses: list[str] = []

    if validation.get("source_valid"):
        strengths.append("Source validee dans la base locale.")
    if validation.get("country_valid"):
        strengths.append("Pays valide dans la base locale.")
    if validation.get("topic_valid"):
        strengths.append("Theme coherent avec les indicateurs retenus.")
    if required and _has_required_selected(required, selected):
        strengths.append("Indicateur direct obligatoire selectionne.")
    if selected:
        strengths.append("Les indicateurs retenus existent dans la base locale.")
    if validation.get("data_preview_available"):
        strengths.append("Les données réelles ont été vérifiées par un aperçu.")

    if required and not _has_required_selected(required, selected):
        weaknesses.append("Un indicateur direct obligatoire disponible n'a pas ete retenu.")
    if blocked:
        weaknesses.append("Des indicateurs ont ete bloques par les regles metier.")
    if not selected:
        weaknesses.append("Aucun indicateur valide n'a ete retenu.")
    if not validation.get("topic_valid"):
        weaknesses.append("Theme final non valide ou non detecte.")
    if not validation.get("data_preview_available"):
        weaknesses.append("Données non vérifiées : aucun aperçu réel n'est disponible dans les preuves.")

    decision = {
        "valide": "acceptable",
        "valide_non_verifie": "unknown",
        "faible": "weak",
        "incoherent": "incoherent",
        "verification_humaine_requise": "unknown",
    }.get(backend_state, "unknown")

    return EvaluationResult(
        judge_decision=decision,
        relevance_score=90 if decision == "acceptable" else 70 if backend_state == "valide_non_verifie" else 55 if decision == "weak" else 15 if decision == "incoherent" else 0,
        directness_score=95 if required and _has_required_selected(required, selected) else 50 if selected else 0,
        data_availability_score=80 if validation.get("data_preview_available") else 0,
        explanation=f"Evaluation deterministe basee sur l'etat metier serveur : {backend_state}.",
        strengths=strengths,
        weaknesses=weaknesses,
        requires_human_review=decision in {"weak", "incoherent", "unknown"},
    )


def _merge_with_backend_state(ai_result: EvaluationResult, backend_state: str) -> EvaluationResult:
    if backend_state == "incoherent" and ai_result.judge_decision == "acceptable":
        ai_result.judge_decision = "incoherent"
        ai_result.requires_human_review = True
        ai_result.weaknesses.append("Decision IA corrigee : le serveur a classe la recommandation incoherente.")
    if backend_state == "faible" and ai_result.judge_decision == "acceptable":
        ai_result.judge_decision = "weak"
        ai_result.requires_human_review = True
        ai_result.weaknesses.append("Decision IA corrigee : le serveur exige une verification metier.")
    if backend_state == "valide_non_verifie" and ai_result.judge_decision == "acceptable":
        ai_result.judge_decision = "unknown"
        ai_result.requires_human_review = True
        ai_result.weaknesses.append("Decision IA corrigee : les donnees reelles ne sont pas encore verifiees.")
    return ai_result


def _ensure_french_result(result: EvaluationResult) -> EvaluationResult:
    """Keep the interface French even if the evaluator slips into English."""
    if _looks_english(result.explanation):
        result.explanation = _fallback_french_explanation(result.judge_decision)
    result.strengths = [
        item if not _looks_english(item) else "Point favorable detecte, mais reformulation automatique necessaire."
        for item in result.strengths
    ]
    result.weaknesses = [
        item if not _looks_english(item) else "Risque detecte, mais reformulation automatique necessaire."
        for item in result.weaknesses
    ]
    return result


def _looks_english(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    markers = ("the user", "selected indicators", "data preview", "backend", "request", "however")
    return any(marker in lowered for marker in markers)


def _fallback_french_explanation(decision: str) -> str:
    labels = {
        "acceptable": "L'evaluateur juge la recommandation acceptable selon les preuves fournies.",
        "weak": "L'evaluateur signale une recommandation plausible mais encore fragile.",
        "incoherent": "L'evaluateur signale une incoherence entre la demande et la selection.",
        "unknown": "L'evaluateur n'a pas assez de preuves pour conclure.",
    }
    return labels.get(decision, "L'evaluateur n'a pas fourni d'explication exploitable en francais.")


def _has_required_selected(required: list[dict[str, Any]], selected: list[dict[str, Any]]) -> bool:
    selected_codes = {str(item.get("code")) for item in selected}
    required_codes = {str(item.get("code")) for item in required}
    return bool(required_codes & selected_codes)
