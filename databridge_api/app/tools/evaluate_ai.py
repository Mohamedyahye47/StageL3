from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from app.database import SessionLocal
from app.services.ai_assistant_service import recommend_dataset_validated
from app.services.measure_service import enregistrer_mesure


DEFAULT_CASES_PATH = Path(__file__).with_name("tests_ai_cases.json")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Évalue l'assistant IA avec des cas connus et affiche un résultat en français."
    )
    parser.add_argument(
        "--cases",
        default=str(DEFAULT_CASES_PATH),
        help="Chemin du fichier JSON des cas à tester.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Utilise uniquement les règles locales, sans appel IA externe.",
    )
    return parser.parse_args()


def _load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"Fichier de cas introuvable : {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _codes_from_recommendation(recommendation: Any) -> list[str]:
    return [indicator.code for indicator in recommendation.indicators]


def _evaluate_case(case: dict[str, Any], recommendation: Any) -> tuple[str, list[str]]:
    errors: list[str] = []
    codes = set(_codes_from_recommendation(recommendation))
    country_name = recommendation.country_name or ""
    country_code = recommendation.country_code or ""
    start_year = int(str(recommendation.start_date)[:4])
    intent = recommendation.topic_intent or ""

    expected_error = case.get("expected_error")
    if expected_error:
        errors.append(f"Erreur attendue mais une proposition a été retournée : {recommendation.title}")

    expected_intent = case.get("expected_intent")
    if expected_intent and intent != expected_intent:
        errors.append(f"Intention attendue : {expected_intent}, obtenue : {intent}")

    for code in case.get("must_include", []):
        if code not in codes:
            errors.append(f"Indicateur attendu absent : {code}")

    include_any = case.get("must_include_any", [])
    if include_any and not any(code in codes for code in include_any):
        errors.append(f"Aucun indicateur attendu trouvé parmi : {', '.join(include_any)}")

    for code in case.get("must_not_include", []):
        if code in codes:
            errors.append(f"Indicateur interdit présent : {code}")

    forbidden_only = set(case.get("must_not_only_use", []))
    if forbidden_only and codes and codes.issubset(forbidden_only):
        errors.append("La sélection utilise uniquement des proxys interdits pour ce cas.")

    expected_country = case.get("expected_country")
    if expected_country and _normalize_test_text(expected_country) not in _normalize_test_text(country_name):
        errors.append(f"Pays attendu : {expected_country}, obtenu : {country_name}")

    expected_country_code = case.get("expected_country_code")
    if expected_country_code and expected_country_code.upper() != str(country_code).upper():
        errors.append(f"Code pays attendu : {expected_country_code}, obtenu : {country_code or '-'}")

    expected_start_year = case.get("expected_start_year")
    if expected_start_year and start_year != int(expected_start_year):
        errors.append(f"Année de début attendue : {expected_start_year}, obtenue : {start_year}")

    preferred_topic = case.get("preferred_topic")
    topic_name = recommendation.topic_name or ""
    if preferred_topic and _normalize_test_text(preferred_topic) not in _normalize_test_text(topic_name):
        errors.append(f"Thème attendu : {preferred_topic}, obtenu : {topic_name or '-'}")

    if recommendation.etat_metier not in {"valide", "valide_non_verifie", "faible"}:
        errors.append(f"État métier non acceptable pour le test : {recommendation.etat_metier}")

    return ("Réussi" if not errors else "À vérifier"), errors


def main() -> None:
    args = _parse_args()
    cases = _load_cases(Path(args.cases))

    print("Évaluation de l'assistant IA")
    if args.local_only:
        print("Mode local uniquement : 0 appel IA prévu.")
    else:
        print("Mode standard : un seul appel IA maximum par cas, avec fallback local si nécessaire.")
    print("Cas | Résultat | État technique | État métier | Attendus | Obtenus | Durée | Erreurs")

    with SessionLocal() as db:
        for index, case in enumerate(cases, start=1):
            start = time.perf_counter()
            run_id = f"test-ia-{uuid.uuid4().hex}"
            try:
                recommendation = _recommend_with_retry(db, case["input"], run_id=run_id, local_only=args.local_only)
            except Exception as exc:
                duration = round(time.perf_counter() - start, 4)
                expected_error = case.get("expected_error")
                expected_error_matched = bool(expected_error and _normalize_test_text(expected_error) in _normalize_test_text(str(exc)))
                result = "Réussi" if expected_error_matched else "Erreur"
                errors = [] if expected_error_matched else [f"Erreur pendant le cas : {exc}"]
                enregistrer_mesure(
                    "tests_ia",
                    {
                        "type": "test_assistant_ia",
                        "source_execution": "tests_ai",
                        "triggered_by": "command_line",
                        "pipeline_version": "single_ai_v1",
                        "run_id": run_id,
                        "cas": index,
                        "requete": case["input"],
                        "resultat": result,
                        "etat_technique": "echoue",
                        "etat_metier": "verification_humaine_requise",
                        "decision_evaluateur": "unknown",
                        "ai_calls": 0,
                        "fallback_used": False,
                        "fallback_reason": "none",
                        "attendus": case.get("must_include") or case.get("must_include_any") or [],
                        "obtenus": [],
                        "erreurs": errors,
                        "duree_totale_secondes": duration,
                    },
                )
                print(
                    f"{index} | {result} | echoue | verification_humaine_requise | "
                    f"{', '.join(case.get('must_include') or case.get('must_include_any') or []) or '-'} | - | "
                    f"{duration:.2f} s | {'Erreur attendue : ' + str(exc) if expected_error_matched else '; '.join(errors)}"
                )
                continue

            duration = round(time.perf_counter() - start, 4)
            result, errors = _evaluate_case(case, recommendation)
            expected = case.get("must_include") or case.get("must_include_any") or []
            obtained = _codes_from_recommendation(recommendation)
            enregistrer_mesure(
                "tests_ia",
                {
                    "type": "test_assistant_ia",
                    "source_execution": "tests_ai",
                    "triggered_by": "command_line",
                    "pipeline_version": "single_ai_v1",
                    "run_id": run_id,
                    "cas": index,
                    "requete": case["input"],
                    "resultat": result,
                    "etat_technique": recommendation.etat_technique,
                    "etat_metier": recommendation.etat_metier,
                    "decision_evaluateur": recommendation.decision_evaluateur,
                    "ai_calls": recommendation.ai_calls,
                    "fallback_used": recommendation.fallback_used,
                    "fallback_reason": recommendation.fallback_reason,
                    "attendus": expected,
                    "obtenus": obtained,
                    "erreurs": errors,
                    "duree_totale_secondes": duration,
                },
            )
            print(
                f"{index} | {result} | {recommendation.etat_technique} | {recommendation.etat_metier} | "
                f"{', '.join(expected) or '-'} | {', '.join(obtained) or '-'} | "
                f"{duration:.2f} s | {'; '.join(errors) or '-'}"
            )

    print("Les journaux de tests sont enregistrés dans logs/mesures/ia_tests.jsonl")
    print("Les journaux de décision sont enregistrés dans logs/mesures/ia_decisions.jsonl")


def _recommend_with_retry(db, user_request: str, *, run_id: str, local_only: bool = False, attempts: int = 2):
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return recommend_dataset_validated(
                db,
                user_request,
                audit=True,
                source_execution="tests_ai",
                triggered_by="command_line",
                run_id=run_id,
                local_only=local_only,
            )
        except Exception as exc:
            last_error = exc
            if attempt < attempts and _is_transient_ai_error(exc):
                time.sleep(4)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("Recommandation IA indisponible.")


def _is_transient_ai_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(fragment in text for fragment in ("503", "unavailable", "high demand", "timeout", "temporarily"))


def _normalize_test_text(value: str | None) -> str:
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


if __name__ == "__main__":
    main()
